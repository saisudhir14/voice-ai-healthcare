#!/usr/bin/env python3
"""Fake Linphone: a scriptable SIP softphone for testing the Village Line SIP gateway without a phone.

    uv run python tools/fake_linphone.py --server 127.0.0.1:5060 --user sai \
        --say patient1.wav --say patient2.wav --record out.wav --seconds 40 [--dial maya]

REGISTERs, then waits for an INVITE (auto-answers with PCMU) or dials --dial. Streams each --say WAV as
RTP once the far end has spoken and then gone quiet for --gap seconds (first one not before --say-after),
records everything received to --record (8 kHz 16-bit mono), sends BYE after --seconds of media.

SMS mode (SIP MESSAGE, like Linphone's Conversations tab):

    uv run python tools/fake_linphone.py --server 127.0.0.1:5060 --text "hey maya" --text "yes it's me" ...

sends each --text to --text-to (maya) after Maya's previous reply has gone quiet; --await-text waits for Maya's
first text before sending anything (clinic-started thread); --cpim wraps texts in message/cpim like liblinphone can.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import socket
import struct
import sys
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sip.registrar import (  # noqa: E402
    SipMessage,
    build_request,
    build_response,
    extract_uri,
    get_tag,
    new_branch,
    parse_message,
    rand_token,
)
from app.sip.rtp import (  # noqa: E402
    SILENCE_ULAW,
    ULAW_TO_ALAW,
    alaw_to_pcm16,
    build_answer_sdp,
    build_offer_sdp,
    choose_g711,
    parse_rtp,
    parse_sdp,
    pcm16_to_ulaw,
    ulaw_to_pcm16,
)

UA = "FakeLinphone/1.0"
LOUD_RMS = 500.0


def log(msg: str) -> None:
    print(f"[fake {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_wav_ulaw(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) * 256.0
    elif width == 2:
        x = np.frombuffer(raw, "<i2").astype(np.float32)
    elif width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3)
        x = ((b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)) << 8 >> 8)
        x = x.astype(np.float32) / 256.0
    elif width == 4:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 65536.0
    else:
        raise ValueError(f"unsupported sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 8000 and len(x):
        n_out = int(round(len(x) * 8000 / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    pcm = np.clip(x, -32768, 32767).astype(np.int16)
    return pcm16_to_ulaw(pcm)


def local_ip_for(host: str) -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# --------------------------------------------------------------------------- media

class Media(asyncio.DatagramProtocol):
    def __init__(self, echo_gain: float = 0.0, echo_delay: float = 0.24) -> None:
        self.echo_gain = echo_gain
        self.echo: deque[np.ndarray] = deque([np.zeros(160, np.int16)] * round(echo_delay / 0.02))
        self.transport = None
        self.remote: tuple[str, int] | None = None
        self.pt = 0
        self.port = 0
        self.rx: list[np.ndarray] = []
        self.rx_packets = 0
        self.tx_packets = 0
        self.last_loud = 0.0
        self.heard = False  # far end got loud since our last utterance ended
        self.play = bytearray()
        self.seq = random.getrandbits(16)
        self.ts = random.getrandbits(32)
        self.ssrc = random.getrandbits(32)
        self._task: asyncio.Task | None = None
        self.peak_rms = 0.0

    def connection_made(self, transport) -> None:
        self.transport = transport

    def error_received(self, exc) -> None:
        pass

    def datagram_received(self, data, addr) -> None:
        pkt = parse_rtp(data)
        if pkt is None or pkt.pt not in (0, 8):
            return
        pcm = alaw_to_pcm16(pkt.payload) if pkt.pt == 8 else ulaw_to_pcm16(pkt.payload)
        self.rx.append(pcm)
        if self.echo_gain:
            self.echo.append(pcm)
        self.rx_packets += 1
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2))) if len(pcm) else 0.0
        self.peak_rms = max(self.peak_rms, rms)
        if rms > LOUD_RMS:
            self.last_loud = asyncio.get_running_loop().time()
            if not self.play:
                self.heard = True

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._send_loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.transport:
            self.transport.close()

    async def _send_loop(self) -> None:
        loop = asyncio.get_running_loop()
        nxt = loop.time()
        first = True
        while True:
            if len(self.play) >= 160:
                frame = bytes(self.play[:160])
                del self.play[:160]
            elif self.play:
                frame = bytes(self.play) + SILENCE_ULAW[len(self.play):]
                self.play.clear()
            else:
                frame = SILENCE_ULAW
            if self.echo_gain and len(self.echo) > 1:
                leak = self.echo.popleft().astype(np.float32) * self.echo_gain
                mixed = ulaw_to_pcm16(frame).astype(np.float32)[: len(leak)] + leak
                frame = pcm16_to_ulaw(np.clip(mixed, -32768, 32767).astype(np.int16))
            if self.pt == 8:
                frame = frame.translate(ULAW_TO_ALAW)
            if self.remote and self.transport:
                hdr = struct.pack("!BBHII", 0x80, (0x80 if first else 0) | self.pt, self.seq, self.ts, self.ssrc)
                self.transport.sendto(hdr + frame, self.remote)
                self.tx_packets += 1
                first = False
            self.seq = (self.seq + 1) & 0xFFFF
            self.ts = (self.ts + 160) & 0xFFFFFFFF
            nxt += 0.02
            await asyncio.sleep(max(0.0, nxt - loop.time()))

    def recording(self) -> np.ndarray:
        return np.concatenate(self.rx) if self.rx else np.zeros(0, np.int16)


# --------------------------------------------------------------------------- SIP phone

class Phone(asyncio.DatagramProtocol):
    def __init__(self, args, ip: str) -> None:
        self.args = args
        self.ip = ip
        self.port = 0
        self.transport = None
        host, _, port = args.server.partition(":")
        self.server = (host, int(port or 5060))
        self.domain = args.server
        self.reg_callid = rand_token(8) + "@fake"
        self.reg_cseq = 0
        self.reg_fut: asyncio.Future | None = None
        self.media: Media | None = None
        # dialog
        self.call_id = ""
        self.local_hdr = ""
        self.remote_hdr = ""
        self.remote_target = ""
        self.dialog_addr: tuple[str, int] | None = None
        self.local_cseq = 1
        self.local_sdp = b""
        self.last_invite_resp: bytes | None = None
        self.invite_req: SipMessage | None = None
        self.call_up = asyncio.Event()
        self.call_down = asyncio.Event()
        self.down_reason = ""
        self.bye_ok = asyncio.Event()
        self.invite_resp_fut: asyncio.Future | None = None
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.texts_in = 0
        self.msg_futs: dict[str, asyncio.Future] = {}

    @property
    def contact(self) -> str:
        return f"<sip:{self.args.user}@{self.ip}:{self.port};transport=udp>"

    def connection_made(self, transport) -> None:
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def error_received(self, exc) -> None:
        log(f"SIP socket error {exc}")

    def send(self, data: bytes, addr=None) -> None:
        self.transport.sendto(data, addr or self.server)
        if self.args.verbose:
            log(">> " + data.split(b"\r\n", 1)[0].decode())

    def via(self) -> str:
        return f"SIP/2.0/UDP {self.ip}:{self.port};branch={new_branch()};rport"

    # ---- REGISTER
    async def register(self, expires: int) -> bool:
        self.reg_cseq += 1
        aor = f"<sip:{self.args.user}@{self.domain}>"
        data = build_request(
            "REGISTER",
            f"sip:{self.domain}",
            [
                ("via", self.via()),
                ("max-forwards", "70"),
                ("from", f"{aor};tag={rand_token(4)}"),
                ("to", aor),
                ("call-id", self.reg_callid),
                ("cseq", f"{self.reg_cseq} REGISTER"),
                ("contact", f"{self.contact};expires={expires}"),
                ("expires", str(expires)),
                ("user-agent", UA),
                ("allow", "INVITE, ACK, CANCEL, BYE, OPTIONS, UPDATE, INFO"),
            ],
        )
        self.reg_fut = asyncio.get_running_loop().create_future()
        delay = 0.5
        for _ in range(6):
            self.send(data)
            try:
                return await asyncio.wait_for(asyncio.shield(self.reg_fut), delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, 4)
        return False

    # ---- dispatch
    def datagram_received(self, data, addr) -> None:
        msg = parse_message(data)
        if msg is None:
            return
        if self.args.verbose:
            log("<< " + msg.first_line())
        try:
            if msg.is_request:
                self.on_request(msg, (addr[0], addr[1]))
            else:
                self.on_response(msg, (addr[0], addr[1]))
        except Exception as e:  # pragma: no cover
            log(f"error handling {msg.first_line()}: {e!r}")

    def on_response(self, msg: SipMessage, addr) -> None:
        _, method = msg.cseq
        if method == "REGISTER" and msg.status >= 200 and self.reg_fut and not self.reg_fut.done():
            self.reg_fut.set_result(200 <= msg.status < 300)
        elif method == "INVITE" and msg.status >= 200:
            if self.invite_resp_fut and not self.invite_resp_fut.done():
                self.invite_resp_fut.set_result((msg, addr))
            elif 200 <= msg.status < 300 and self.ack_bytes:  # retransmitted 200
                self.send(self.ack_bytes, addr)
        elif method == "BYE":
            self.bye_ok.set()
        elif method == "MESSAGE" and msg.status >= 200:
            fut = self.msg_futs.pop(msg.call_id, None)
            if fut and not fut.done():
                fut.set_result(msg.status)

    ack_bytes: bytes | None = None

    # ---- SMS (SIP MESSAGE)
    async def send_text(self, target: str, text: str, cpim: bool = False) -> int:
        call_id = rand_token(8) + "@fake"
        body, ctype = text.encode(), "text/plain;charset=UTF-8"
        if cpim:
            body = (f"From: <sip:{self.args.user}@{self.domain}>\r\nTo: <sip:{target}@{self.domain}>\r\n"
                    f"DateTime: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\r\n\r\n"
                    f"Content-Type: text/plain;charset=UTF-8\r\nContent-Length: {len(body)}\r\n\r\n").encode() + body
            ctype = "Message/CPIM"
        data = build_request(
            "MESSAGE",
            f"sip:{target}@{self.domain}",
            [
                ("via", self.via()),
                ("max-forwards", "70"),
                ("from", f"<sip:{self.args.user}@{self.domain}>;tag={rand_token(4)}"),
                ("to", f"<sip:{target}@{self.domain}>"),
                ("call-id", call_id),
                ("cseq", "20 MESSAGE"),
                ("user-agent", UA),
                ("content-type", ctype),
            ],
            body,
        )
        fut = self.msg_futs[call_id] = asyncio.get_running_loop().create_future()
        delay = 0.5
        for _ in range(6):
            self.send(data)
            try:
                return await asyncio.wait_for(asyncio.shield(fut), delay)
            except asyncio.TimeoutError:
                delay = min(delay * 2, 4)
        return 0

    async def replies(self, first_within: float, quiet: float) -> list[str]:
        """Maya's texts: the first within first_within s, then any more until quiet s pass without one."""
        got: list[str] = []
        try:
            got.append(await asyncio.wait_for(self.inbox.get(), first_within))
            while True:
                got.append(await asyncio.wait_for(self.inbox.get(), quiet))
        except asyncio.TimeoutError:
            return got

    def on_request(self, msg: SipMessage, addr) -> None:
        m = msg.method
        if m == "INVITE":
            if msg.call_id == self.call_id:
                if get_tag(msg.get("to")):  # re-INVITE
                    self.send(
                        build_response(
                            msg, 200, headers=[("contact", self.contact), ("content-type", "application/sdp")],
                            body=self.local_sdp,
                        ),
                        addr,
                    )
                elif self.last_invite_resp:
                    self.send(self.last_invite_resp, addr)
                return
            if self.call_id:
                self.send(build_response(msg, 486, to_tag=rand_token(4)), addr)
                return
            self.call_id = msg.call_id
            asyncio.get_running_loop().create_task(self.answer(msg, addr))
        elif m == "ACK":
            if self.args.verbose:
                log("got ACK")
        elif m == "BYE":
            self.send(build_response(msg, 200), addr)
            if msg.call_id == self.call_id:
                self.down_reason = "remote_bye"
                self.call_down.set()
        elif m == "CANCEL":
            self.send(build_response(msg, 200, to_tag=rand_token(4)), addr)
            if self.invite_req is not None and msg.call_id == self.call_id and not self.call_up.is_set():
                self.send(build_response(self.invite_req, 487, to_tag=get_tag(self.local_hdr)), addr)
                self.down_reason = "cancelled"
                self.call_down.set()
        elif m == "MESSAGE":
            self.send(build_response(msg, 200, to_tag=rand_token(4)), addr)
            if (msg.get("content-type") or "").lower().startswith("text/plain"):
                text = msg.body.decode("utf-8", "replace")
                self.texts_in += 1
                log(f"TEXT from {extract_uri(msg.get('from'))}: {text}")
                self.inbox.put_nowait(text)
        else:
            self.send(build_response(msg, 200, to_tag=rand_token(4)), addr)

    # ---- answer inbound INVITE
    async def answer(self, msg: SipMessage, addr) -> None:
        self.call_id = msg.call_id
        self.invite_req = msg
        tag = rand_token(4)
        self.local_hdr = f"{msg.get('to')};tag={tag}"
        self.remote_hdr = msg.get("from") or ""
        self.remote_target = extract_uri(msg.get("contact")) or extract_uri(msg.get("from"))
        self.dialog_addr = addr
        log(f"incoming INVITE from {extract_uri(msg.get('from'))}")
        self.last_invite_resp = build_response(msg, 100)
        self.send(self.last_invite_resp, addr)
        self.last_invite_resp = build_response(msg, 180, to_tag=tag, headers=[("contact", self.contact)])
        self.send(self.last_invite_resp, addr)
        offer = parse_sdp(msg.body)
        audio = offer.audio() if offer else None
        pt = choose_g711(audio) if audio else None
        if pt is None:
            self.send(build_response(msg, 488, to_tag=tag), addr)
            self.down_reason = "no_codec"
            self.call_down.set()
            return
        await asyncio.sleep(self.args.answer_after)
        if self.call_down.is_set():
            return
        self.media.pt = pt
        self.media.remote = offer.audio_addr()
        self.local_sdp = build_answer_sdp(self.ip, self.media.port, offer, pt, random.randint(1, 10**9), 1, user="fake")
        self.last_invite_resp = build_response(
            msg, 200, to_tag=tag, headers=[("contact", self.contact), ("content-type", "application/sdp")],
            body=self.local_sdp,
        )
        self.send(self.last_invite_resp, addr)
        log(f"answered (PT {pt}), sending RTP to {self.media.remote}")
        self.call_up.set()

    # ---- dial outbound
    async def dial(self, target: str) -> bool:
        self.call_id = rand_token(8) + "@fake"
        tag = rand_token(4)
        self.local_hdr = f"<sip:{self.args.user}@{self.domain}>;tag={tag}"
        to = f"<sip:{target}@{self.domain}>"
        uri = f"sip:{target}@{self.domain}"
        self.local_sdp = build_offer_sdp(self.ip, self.media.port, random.randint(1, 10**9), 1, payloads=(0,), user="fake")
        via = self.via()
        invite = build_request(
            "INVITE",
            uri,
            [
                ("via", via),
                ("max-forwards", "70"),
                ("from", self.local_hdr),
                ("to", to),
                ("call-id", self.call_id),
                ("cseq", "1 INVITE"),
                ("contact", self.contact),
                ("user-agent", UA),
                ("content-type", "application/sdp"),
            ],
            self.local_sdp,
        )
        self.invite_resp_fut = asyncio.get_running_loop().create_future()
        log(f"dialing {uri}")
        delay, waited = 0.5, 0.0
        while not self.invite_resp_fut.done() and waited < 60:
            self.send(invite)
            try:
                await asyncio.wait_for(asyncio.shield(self.invite_resp_fut), delay)
            except asyncio.TimeoutError:
                waited += delay
                delay = min(delay * 2, 4)
        if not self.invite_resp_fut.done():
            log("no final response to INVITE")
            return False
        resp, addr = self.invite_resp_fut.result()
        self.remote_hdr = resp.get("to") or to
        if 200 <= resp.status < 300:
            self.remote_target = extract_uri(resp.get("contact")) or uri
            self.dialog_addr = addr
            self.ack_bytes = build_request(
                "ACK",
                self.remote_target,
                [
                    ("via", self.via()),
                    ("max-forwards", "70"),
                    ("from", self.local_hdr),
                    ("to", self.remote_hdr),
                    ("call-id", self.call_id),
                    ("cseq", "1 ACK"),
                    ("user-agent", UA),
                ],
            )
            self.send(self.ack_bytes, addr)
            ans = parse_sdp(resp.body)
            audio = ans.audio() if ans else None
            self.media.pt = choose_g711(audio, prefer_offer_order=True) if audio else 0
            self.media.remote = ans.audio_addr() if ans else None
            log(f"call answered, RTP -> {self.media.remote} PT {self.media.pt}")
            self.call_up.set()
            return True
        ack = build_request(
            "ACK", uri,
            [("via", via), ("max-forwards", "70"), ("from", self.local_hdr), ("to", self.remote_hdr),
             ("call-id", self.call_id), ("cseq", "1 ACK")],
        )
        self.send(ack, addr)
        log(f"call failed: {resp.status} {resp.reason}")
        return False

    async def bye(self) -> None:
        self.local_cseq += 1
        data = build_request(
            "BYE",
            self.remote_target,
            [
                ("via", self.via()),
                ("max-forwards", "70"),
                ("from", self.local_hdr),
                ("to", self.remote_hdr),
                ("call-id", self.call_id),
                ("cseq", f"{self.local_cseq} BYE"),
                ("user-agent", UA),
            ],
        )
        for d in (0.5, 1.0, 2.0):
            self.send(data, self.dialog_addr)
            try:
                await asyncio.wait_for(self.bye_ok.wait(), d)
                return
            except asyncio.TimeoutError:
                pass


# --------------------------------------------------------------------------- script

async def conversation(media: Media, says: list[tuple[str, bytes]], say_after: float, gap: float) -> None:
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for i, (name, ulaw) in enumerate(says):
        wait_start = loop.time()
        while True:
            await asyncio.sleep(0.05)
            now = loop.time()
            if i == 0 and now - t0 < say_after:
                continue
            quiet = now - media.last_loud >= gap
            if quiet and (media.heard or now - wait_start > 20.0):
                break
        log(f"saying #{i + 1} {name} ({len(ulaw) / 8000:.1f}s)")
        media.heard = False
        media.play += ulaw
        while media.play:
            await asyncio.sleep(0.05)
        media.heard = False
    log("script finished")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="127.0.0.1:5060")
    ap.add_argument("--user", default="sai")
    ap.add_argument("--ip", default=None, help="IP to advertise (default: route to server)")
    ap.add_argument("--port", type=int, default=0, help="local SIP port (0=auto)")
    ap.add_argument("--say", action="append", default=[], help="WAV to speak (repeatable, in order)")
    ap.add_argument("--say-after", type=float, default=6.0, help="earliest seconds before first --say")
    ap.add_argument("--gap", type=float, default=1.2, help="far-end quiet seconds before speaking")
    ap.add_argument("--record", default=None, help="write received audio to this WAV")
    ap.add_argument("--seconds", type=float, default=40.0, help="hang up after this many seconds of media")
    ap.add_argument("--dial", default=None, help="dial this user (e.g. maya) instead of waiting for INVITE")
    ap.add_argument("--wait", type=float, default=120.0, help="max seconds to wait for an INVITE")
    ap.add_argument("--answer-after", type=float, default=1.0, help="ring this long before answering")
    ap.add_argument("--expires", type=int, default=600)
    ap.add_argument("--echo", type=float, default=0.0,
                    help="leak received audio back at this gain after 240 ms, like a laptop speaker into its mic")
    ap.add_argument("--text", action="append", default=[], help="SMS to send (repeatable, in order)")
    ap.add_argument("--text-to", default="maya", help="user to text")
    ap.add_argument("--await-text", action="store_true", help="wait for Maya's first text before sending")
    ap.add_argument("--cpim", action="store_true", help="wrap texts in message/cpim")
    ap.add_argument("--reply-wait", type=float, default=60.0, help="max seconds for Maya's reply to each text")
    ap.add_argument("--reply-quiet", type=float, default=8.0, help="reply is done after this many quiet seconds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    says = [(p, load_wav_ulaw(p)) for p in args.say]
    host = args.server.partition(":")[0]
    ip = args.ip or local_ip_for(host)
    loop = asyncio.get_running_loop()

    media = Media(echo_gain=args.echo)
    for port in range(41000, 41200, 2):
        try:
            await loop.create_datagram_endpoint(lambda: media, local_addr=("0.0.0.0", port))
            media.port = port
            break
        except OSError:
            continue
    phone = Phone(args, ip)
    phone.media = media
    await loop.create_datagram_endpoint(lambda: phone, local_addr=("0.0.0.0", args.port))
    log(f"SIP on {ip}:{phone.port}, RTP on {media.port}, server {args.server}")

    if not await phone.register(args.expires):
        log("REGISTER failed")
        print("RESULT registered=0 answered=0 rx_packets=0 tx_packets=0 recorded_s=0 peak_rms=0", flush=True)
        return 2
    log(f"registered as sip:{args.user}@{args.server}")

    if args.text or args.await_text:
        if args.await_text:
            log("waiting for Maya's first text ...")
            await phone.replies(args.wait, args.reply_quiet)
        sent = 0
        for i, text in enumerate(args.text):
            status = await phone.send_text(args.text_to, text, cpim=args.cpim)
            log(f"texted #{i + 1} -> {args.text_to} ({status or 'no response'}): {text}")
            sent += 200 <= status < 300
            await phone.replies(args.reply_wait, args.reply_quiet)
        await phone.register(0)
        print(f"RESULT registered=1 texts_sent={sent} texts_received={phone.texts_in}", flush=True)
        return 0 if sent == len(args.text) and phone.texts_in else 1

    answered = False
    if args.dial:
        answered = await phone.dial(args.dial)
    else:
        log("waiting for INVITE ...")
        done = asyncio.ensure_future(phone.call_down.wait())
        up = asyncio.ensure_future(phone.call_up.wait())
        await asyncio.wait({done, up}, timeout=args.wait, return_when=asyncio.FIRST_COMPLETED)
        done.cancel()
        up.cancel()
        answered = phone.call_up.is_set()

    if answered:
        media.start()
        script = asyncio.ensure_future(conversation(media, says, args.say_after, args.gap)) if says else None
        try:
            await asyncio.wait_for(phone.call_down.wait(), args.seconds)
            log(f"call ended by far end ({phone.down_reason})")
        except asyncio.TimeoutError:
            log("time up -> BYE")
            await phone.bye()
        if script:
            script.cancel()
        media.stop()
    else:
        log(f"no call ({phone.down_reason or 'timeout'})")

    rec = media.recording()
    if args.record:
        with wave.open(args.record, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(rec.astype("<i2").tobytes())
        log(f"wrote {args.record} ({len(rec) / 8000:.1f}s)")
    await phone.register(0)
    nonsilent = float(np.mean(np.abs(rec.astype(np.int32)) > 300)) if len(rec) else 0.0
    print(
        f"RESULT registered=1 answered={int(answered)} rx_packets={media.rx_packets} tx_packets={media.tx_packets} "
        f"recorded_s={len(rec) / 8000:.1f} peak_rms={media.peak_rms:.0f} nonsilent_frac={nonsilent:.3f} "
        f"end={phone.down_reason or 'local_bye'}",
        flush=True,
    )
    return 0 if answered else 1


async def selftest() -> int:
    """In-process SipGateway + echo bot on spare ports, then this script as the phone (outbound + inbound)."""
    import base64
    import json
    import math
    import tempfile

    import websockets

    from app.sip import SipGateway

    sip_port, ws_port = int(os.getenv("SELFTEST_SIP_PORT", "5071")), int(os.getenv("SELFTEST_WS_PORT", "7871"))
    tmp = Path(tempfile.mkdtemp(prefix="sipselftest"))
    say, rec_path = tmp / "say.wav", tmp / "out.wav"
    stats: dict = {"connected": 0, "start": [], "media": 0, "stop": 0}
    events: list[dict] = []

    def tone(freq: float, secs: float, rate: int = 8000, amp: int = 8000) -> np.ndarray:
        t = np.arange(int(secs * rate)) / rate
        return (amp * np.sin(2 * math.pi * freq * t)).astype(np.int16)

    async def bot(ws) -> None:
        greeting = pcm16_to_ulaw(tone(440, 1.5))
        async for raw in ws:
            m = json.loads(raw)
            ev = m.get("event")
            if ev == "connected":
                stats["connected"] += 1
            elif ev == "start":
                stats["start"].append(m["start"]["customParameters"])
                for i in range(0, len(greeting), 640):  # 80 ms chunks, ~real time
                    await ws.send(json.dumps({"event": "media", "streamSid": m["streamSid"],
                                              "media": {"payload": base64.b64encode(greeting[i:i + 640]).decode()}}))
                    await asyncio.sleep(0.08)
            elif ev == "media":
                stats["media"] += 1
                await ws.send(json.dumps({"event": "media", "streamSid": m["streamSid"], "media": m["media"]}))
            elif ev == "stop":
                stats["stop"] += 1
                break

    async def on_event(ev: dict) -> None:
        events.append(ev)
        log(f"EVENT {ev}")

    async def phone(*extra: str) -> str:
        p = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--server", f"127.0.0.1:{sip_port}", "--user", "sai", "--say", str(say),
            "--say-after", "1", "--record", str(rec_path), "--seconds", "8", *extra,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await p.communicate()
        text = out.decode(errors="replace")
        print(text, flush=True)
        return next((ln for ln in text.splitlines() if ln.startswith("RESULT")), "")

    def rec_peak() -> int:
        with wave.open(str(rec_path)) as w:
            rec = np.frombuffer(w.readframes(w.getnframes()), "<i2")
        return int(np.abs(rec.astype(np.int32)).max()) if len(rec) else 0

    with wave.open(str(say), "wb") as w:  # 16 kHz to exercise resampling
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(tone(660, 2.0, rate=16000).tobytes())

    server = await websockets.serve(bot, "127.0.0.1", ws_port)
    gw = SipGateway(host_ip="127.0.0.1", sip_port=sip_port, bot_ws_url=f"ws://127.0.0.1:{ws_port}",
                    on_event=on_event, inbound_patient=lambda: "james-cole")
    await gw.start()
    checks: dict[str, bool] = {}

    # outbound: gateway INVITEs the registered fake phone
    task = asyncio.ensure_future(phone())
    for _ in range(50):
        if gw.status()["registered"]:
            break
        await asyncio.sleep(0.1)
    log(f"STATUS {gw.status()}")
    checks["registered"] = gw.status()["registered"]
    try:
        await gw.call("james-cole", "s-out")
    except RuntimeError as e:
        log(f"call failed: {e}")
    res = await task
    await asyncio.sleep(0.5)
    peak = rec_peak()
    st = [e["status"] for e in events if e.get("session_id") == "s-out"]
    log(f"OUTBOUND {res} bot={stats} rec_peak={peak} statuses={st}")
    checks["out_answered"] = "answered=1" in res
    checks["out_audio_roundtrip"] = peak > 2000
    checks["out_bot_media"] = stats["media"] > 200
    checks["out_stop"] = stats["stop"] == 1
    checks["out_events"] = st == ["ringing", "live", "ended"]
    checks["out_cleanup"] = not gw.sessions and not gw.calls

    # inbound: fake phone dials maya
    stats.update(media=0, stop=0)
    res = await phone("--dial", "maya")
    await asyncio.sleep(0.5)
    inb = [e for e in events if e.get("type") == "session" and e.get("direction") == "inbound"]
    log(f"INBOUND {res} bot={stats} rec_peak={rec_peak()} statuses={[e['status'] for e in inb]}")
    checks["in_answered"] = "answered=1" in res
    checks["in_audio_roundtrip"] = rec_peak() > 2000
    checks["in_bot_media"] = stats["media"] > 200
    checks["in_stop"] = stats["stop"] == 1
    checks["in_events"] = [e["status"] for e in inb] == ["ringing", "live", "ended"]
    checks["in_patient"] = bool(inb) and inb[0]["patient_id"] == "james-cole"
    checks["start_params"] = len(stats["start"]) == 2 and stats["start"][0].get("channel") == "linphone"
    log(f"start customParameters: {stats['start']}")

    # SMS: phone texts maya (plain, then CPIM-wrapped); the gateway texts each one back
    texts: list[tuple[str, str]] = []

    async def on_message(user: str, text: str) -> None:
        texts.append((user, text))
        gw.send_message(user, f"echo: {text}")

    gw.on_message = on_message
    sms_results = []
    for extra in ((), ("--cpim",)):
        p = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--server", f"127.0.0.1:{sip_port}", "--user", "sai", "--text", "hello maya",
            "--reply-wait", "3", "--reply-quiet", "0.5", *extra,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await p.communicate()
        print(out.decode(errors="replace"), flush=True)
        sms_results.append(next((ln for ln in out.decode().splitlines() if ln.startswith("RESULT")), ""))
    log(f"SMS {sms_results} gateway got {texts}")
    checks["sms_in"] = texts == [("sai", "hello maya")] * 2
    checks["sms_out"] = all("texts_sent=1 texts_received=1" in r for r in sms_results)

    await gw.stop()
    server.close()
    for k, v in checks.items():
        print(f"  {'ok ' if v else 'FAIL'} {k}")
    ok = all(checks.values())
    print("SELFTEST", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            sys.exit(asyncio.run(selftest()))
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
