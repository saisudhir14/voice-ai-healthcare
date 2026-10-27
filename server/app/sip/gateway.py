"""SipGateway: registrar + UAC/UAS + RTP <-> Twilio-Media-Streams websocket bridge.

Pure asyncio, UDP only. See AGENTS.md "Bridge protocol".
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import re
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import numpy as np
import websockets
from loguru import logger

from .registrar import (
    ALLOW,
    USER_AGENT,
    Registrar,
    SipMessage,
    build_request,
    build_response,
    extract_uri,
    get_tag,
    new_branch,
    parse_message,
    parse_uri,
    rand_token,
)
from .rtp import (
    FRAME_BYTES,
    RtpSession,
    build_answer_sdp,
    build_offer_sdp,
    choose_g711,
    parse_sdp,
    ulaw_to_pcm16,
)

T1 = 0.5
T2 = 4.0
TIMER_B = 32.0
RING_TIMEOUT = 60.0
INBOUND_RING_SECS = 0.6
ENDED_LINGER = 32.0
WS_QUEUE_MAX = 500  # 10 s of 20 ms frames; drop beyond (bot ws stalled)
VOICED_RMS = 300  # int16 RMS of a 20 ms frame; phone speech is ~1000-5000, line noise < 100
SILENT_WARN_FRAMES = 1000  # 20 s of RTP
TIMER_F = 32.0  # non-INVITE transaction timeout (MESSAGE)

_STOP = object()

FAIL_REASONS = {
    404: "not_found",
    408: "timeout",
    480: "unavailable",
    486: "busy",
    487: "cancelled",
    488: "codec_mismatch",
    600: "busy",
    603: "declined",
    401: "auth_required",
    407: "auth_required",
}


def detect_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("0."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def _mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _unwrap_cpim(body: bytes) -> tuple[str, bytes]:
    """message/cpim (RFC 3862): CPIM headers, blank line, MIME headers, blank line, content."""
    parts = re.split(rb"\r?\n\r?\n", body, maxsplit=2)
    if len(parts) < 3:
        return "", b""
    ctype = ""
    for line in parts[1].decode("utf-8", "replace").splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-type":
            ctype = value
    return _mime(ctype), parts[2]


@dataclass
class ImPeer:
    """Who texted us last, and the identity they addressed (so replies land in the same Linphone conversation)."""

    uri: str  # their From URI
    local_uri: str  # our URI as they addressed it (their To)
    addr: tuple[str, int]


@dataclass(eq=False)
class Call:
    session_id: str
    patient_id: str
    direction: str  # outbound | inbound
    call_id: str
    local_tag: str
    local_hdr: str = ""  # our From (UAC) / To+tag (UAS)
    remote_hdr: str = ""  # their To(+tag) (UAC) / From (UAS)
    remote_target: str = ""  # request-URI for in-dialog requests
    sip_addr: tuple[str, int] = ("0.0.0.0", 0)
    local_cseq: int = 0
    remote_cseq: int = 0
    state: str = "init"  # calling | ringing | live | ended
    end_reason: str | None = None
    # UAC
    invite_uri: str = ""
    invite_bytes: bytes = b""
    invite_via: str = ""
    invite_cseq: int = 1
    provisional: bool = False
    ack_bytes: bytes | None = None
    # UAS
    invite_req: SipMessage | None = None
    last_invite_resp: bytes | None = None
    answered: bool = False
    acked: bool = False
    last_reinvite: tuple[int, bytes] | None = None
    # media / bridge
    rtp: RtpSession | None = None
    local_sdp: bytes = b""
    sdp_sess: int = field(default_factory=lambda: random.randint(10**6, 10**9))
    stream_sid: str = field(default_factory=lambda: "MZ" + uuid.uuid4().hex)
    call_sid: str = field(default_factory=lambda: "CA" + uuid.uuid4().hex)
    ws_q: asyncio.Queue = field(default_factory=asyncio.Queue)
    rx_buf: bytearray = field(default_factory=bytearray)
    bye_acked: bool = False
    tasks: set = field(default_factory=set)
    bridge_task: asyncio.Task | None = None
    ws_frames_out: int = 0
    ws_frames_in: int = 0
    in_frames: int = 0
    in_peak_rms: float = 0.0
    in_voiced_frames: int = 0


class _SipProtocol(asyncio.DatagramProtocol):
    def __init__(self, gw: "SipGateway") -> None:
        self.gw = gw

    def datagram_received(self, data: bytes, addr) -> None:
        self.gw._on_datagram(data, addr)

    def error_received(self, exc) -> None:
        logger.debug(f"SIP socket error: {exc}")


class SipGateway:
    def __init__(
        self,
        host_ip: str | None = None,
        sip_port: int = 5060,
        bot_ws_url: str = "ws://127.0.0.1:7860/ws",
        on_event: Callable[[dict], Awaitable[None]] | None = None,
        inbound_patient: Callable[[], str] | None = None,
        on_message: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.host_ip = host_ip
        self.ip = host_ip or "127.0.0.1"
        self.sip_port = sip_port
        self.bot_ws_url = bot_ws_url
        self.on_event = on_event
        self.inbound_patient = inbound_patient
        self.on_message = on_message  # (user, text) for each inbound SIP MESSAGE, in arrival order
        self.registrar = Registrar()
        self.calls: dict[str, Call] = {}  # Call-ID -> live call
        self.sessions: dict[str, Call] = {}  # session_id -> live call
        self._ended: dict[str, Call] = {}  # recently ended (late retransmissions)
        self._transport: asyncio.DatagramTransport | None = None
        self._events: asyncio.Queue | None = None
        self._inbox: asyncio.Queue | None = None
        self._tasks: set[asyncio.Task] = set()
        self._im_peers: dict[str, ImPeer] = {}  # user -> last texting peer
        self._im_seen: dict[tuple[str, int], float] = {}  # (Call-ID, CSeq) of inbound MESSAGEs, for retransmits
        self._im_pending: dict[str, asyncio.Event] = {}  # Call-ID of outbound MESSAGE -> final response seen

    # ------------------------------------------------------------------ public API
    @property
    def domain(self) -> str:
        return f"{self.ip}:{self.sip_port}"

    @property
    def im_identity(self) -> str:
        """Our chat address. Linphone users type "maya", which becomes sip:maya@<account domain> (no port)."""
        return f"sip:maya@{self.ip}" + ("" if self.sip_port == 5060 else f":{self.sip_port}")

    def send_message(self, user: str, text: str) -> bool:
        """Text the registered (or last texting) Linphone user with a SIP MESSAGE. False if we can't reach them."""
        text = text.strip()
        b = self.registrar.bindings.get(user)
        if b is not None and b.expires_at <= time.monotonic():
            b = None
        peer = self._im_peers.get(user)
        if not text or self._transport is None or (b is None and peer is None):
            return False
        target, addr = (b.contact, b.addr) if b else (peer.uri, peer.addr)
        to_uri = extract_uri(b.aor) if b else peer.uri
        call_id = f"{rand_token(10)}@{self.ip}"
        data = build_request(
            "MESSAGE",
            target,
            [
                ("via", f"SIP/2.0/UDP {self.domain};branch={new_branch()};rport"),
                ("max-forwards", "70"),
                ("from", f'"Maya" <{peer.local_uri if peer else self.im_identity}>;tag={rand_token(4)}'),
                ("to", f"<{to_uri}>"),
                ("call-id", call_id),
                ("cseq", "1 MESSAGE"),
                ("user-agent", USER_AGENT),
                ("content-type", "text/plain;charset=UTF-8"),
            ],
            text.encode("utf-8"),
        )
        done = self._im_pending[call_id] = asyncio.Event()
        logger.info(f"SIP MESSAGE -> {user} ({len(text)} chars)")
        self._send(data, addr)
        self._spawn(self._retransmit_message(call_id, data, addr, done))
        return True

    async def _retransmit_message(self, call_id: str, data: bytes, addr, done: asyncio.Event) -> None:
        interval, waited = T1, 0.0
        try:
            while waited < TIMER_F:
                try:
                    await asyncio.wait_for(done.wait(), interval)
                    return
                except asyncio.TimeoutError:
                    waited += interval
                    interval = min(interval * 2, T2)
                    self._send(data, addr)
            logger.warning(f"SIP MESSAGE {call_id} got no response from {addr}")
        finally:
            self._im_pending.pop(call_id, None)

    async def start(self) -> None:
        if self._transport is not None:
            return
        self.ip = self.host_ip or detect_lan_ip()
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _SipProtocol(self), local_addr=("0.0.0.0", self.sip_port)
        )
        self._events = asyncio.Queue()
        self._inbox = asyncio.Queue()
        self._spawn(self._event_loop())
        self._spawn(self._inbox_loop())
        self._spawn(self._expiry_loop())
        logger.info(f"SIP gateway listening udp/0.0.0.0:{self.sip_port} (domain {self.domain}, bot {self.bot_ws_url})")

    async def stop(self) -> None:
        for call in list(self.calls.values()):
            self._terminate(call, "server_shutdown", send_bye=True)
        await asyncio.sleep(0.1)
        for call in list(self._ended.values()):
            for t in list(call.tasks):
                t.cancel()
        for t in list(self._tasks):
            t.cancel()
        self._tasks.clear()
        if self._transport:
            self._transport.close()
            self._transport = None
        logger.info("SIP gateway stopped")

    def status(self) -> dict:
        b = self.registrar.latest()
        return {
            "registered": b is not None,
            "user": b.user if b else None,
            "contact": b.contact if b else None,
            "server_ip": self.ip,
            "sip_port": self.sip_port,
            "domain": self.domain,
        }

    async def call(self, patient_id: str, session_id: str) -> None:
        b = self.registrar.latest()
        if b is None or self._transport is None:
            raise RuntimeError("not_registered")
        rtp = await RtpSession.open(payload_type=0)
        call = Call(
            session_id=session_id,
            patient_id=patient_id,
            direction="outbound",
            call_id=f"{rand_token(10)}@{self.ip}",
            local_tag=rand_token(4),
        )
        rtp.on_audio = lambda p, c=call: self._on_rtp_audio(c, p)
        call.rtp = rtp
        call.local_hdr = f'"Maya" <sip:maya@{self.domain}>;tag={call.local_tag}'
        call.remote_hdr = f"<sip:{b.user}@{self.domain}>"
        call.remote_target = call.invite_uri = b.contact
        call.sip_addr = b.addr
        call.invite_cseq = call.local_cseq = 1
        call.local_sdp = build_offer_sdp(self.ip, rtp.port, call.sdp_sess, 1, payloads=(0, 8), te_pt=101)
        call.invite_via = f"SIP/2.0/UDP {self.domain};branch={new_branch()};rport"
        call.invite_bytes = build_request(
            "INVITE",
            call.invite_uri,
            [
                ("via", call.invite_via),
                ("max-forwards", "70"),
                ("from", call.local_hdr),
                ("to", call.remote_hdr),
                ("call-id", call.call_id),
                ("cseq", f"{call.invite_cseq} INVITE"),
                ("contact", f"<sip:maya@{self.domain}>"),
                ("allow", ALLOW),
                ("subject", "Village Line care team"),
                ("user-agent", USER_AGENT),
                ("content-type", "application/sdp"),
            ],
            call.local_sdp,
        )
        self.calls[call.call_id] = call
        self.sessions[session_id] = call
        call.state = "calling"
        self._emit_session(call, "ringing")
        logger.info(f"SIP INVITE -> {b.contact} via {b.addr} (session {session_id}, rtp {rtp.port})")
        self._send(call.invite_bytes, call.sip_addr)
        self._call_task(call, self._uac_timers(call))

    async def hangup(self, session_id: str) -> None:
        call = self.sessions.get(session_id)
        if call is None or call.state == "ended":
            return
        if call.direction == "outbound" and call.state in ("calling", "ringing"):
            self._send_cancel(call)
            self._terminate(call, "local_hangup", send_bye=False)
        elif call.direction == "inbound" and not call.answered:
            if call.invite_req is not None:
                self._send(
                    build_response(call.invite_req, 487, to_tag=call.local_tag, received=call.sip_addr), call.sip_addr
                )
            self._terminate(call, "local_hangup", send_bye=False)
        else:
            self._terminate(call, "local_hangup", send_bye=True)

    # ------------------------------------------------------------------ plumbing
    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _call_task(self, call: Call, coro) -> asyncio.Task:
        t = asyncio.get_running_loop().create_task(coro)
        call.tasks.add(t)
        t.add_done_callback(call.tasks.discard)
        return t

    def _send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(data, addr)
        except OSError as e:
            logger.warning(f"SIP send to {addr} failed: {e}")
            return
        first = data.split(b"\r\n", 1)[0].decode(errors="replace")
        logger.debug(f"SIP >> {addr[0]}:{addr[1]} {first}")

    def _emit(self, ev: dict) -> None:
        if self._events is not None:
            self._events.put_nowait(ev)

    def _emit_session(self, call: Call, status: str, reason: str | None = None) -> None:
        self._emit(
            {
                "type": "session",
                "session_id": call.session_id,
                "patient_id": call.patient_id,
                "channel": "linphone",
                "direction": call.direction,
                "status": status,
                "reason": reason,
            }
        )

    async def _event_loop(self) -> None:
        assert self._events is not None
        while True:
            ev = await self._events.get()
            if self.on_event is None:
                continue
            try:
                await self.on_event(ev)
            except Exception:
                logger.exception("SIP on_event handler failed")

    async def _inbox_loop(self) -> None:
        assert self._inbox is not None
        while True:
            user, text = await self._inbox.get()
            if self.on_message is None:
                continue
            try:
                await self.on_message(user, text)
            except Exception:
                logger.exception("SIP on_message handler failed")

    async def _expiry_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            for b in self.registrar.expire():
                logger.info(f"SIP registration expired: {b.user} {b.contact}")
                self._emit({"type": "sip", "registered": False, "user": b.user, "contact": b.contact})

    def _find(self, call_id: str) -> Call | None:
        return self.calls.get(call_id) or self._ended.get(call_id)

    def _respond(self, req: SipMessage, addr, code: int, reason: str | None = None, **kw) -> bytes:
        data = build_response(req, code, reason, received=addr, **kw)
        self._send(data, addr)
        return data

    # ------------------------------------------------------------------ dispatch
    def _on_datagram(self, data: bytes, addr) -> None:
        msg = parse_message(data)
        if msg is None:
            return
        addr = (addr[0], addr[1])
        logger.debug(f"SIP << {addr[0]}:{addr[1]} {msg.first_line()}")
        try:
            if msg.is_request:
                self._handle_request(msg, addr)
            else:
                self._handle_response(msg, addr)
        except Exception:
            logger.exception(f"SIP handling failed for {msg.first_line()}")

    def _handle_request(self, msg: SipMessage, addr) -> None:
        m = msg.method
        if m == "REGISTER":
            self._on_register(msg, addr)
        elif m == "INVITE":
            self._on_invite(msg, addr)
        elif m == "ACK":
            call = self._find(msg.call_id)
            if call is not None:
                call.acked = True
        elif m == "BYE":
            self._on_bye(msg, addr)
        elif m == "CANCEL":
            self._on_cancel(msg, addr)
        elif m == "UPDATE":
            self._on_update(msg, addr)
        elif m == "OPTIONS":
            self._respond(
                msg,
                addr,
                200,
                to_tag=rand_token(4),
                headers=[("allow", ALLOW), ("accept", "application/sdp"), ("supported", "")],
            )
        elif m == "MESSAGE":
            self._on_message(msg, addr)
        elif m in ("NOTIFY", "INFO"):
            self._respond(msg, addr, 200, to_tag=rand_token(4))
        elif m == "SUBSCRIBE":
            self._respond(msg, addr, 489, to_tag=rand_token(4))
        elif m == "PUBLISH":
            self._respond(
                msg,
                addr,
                200,
                to_tag=rand_token(4),
                headers=[("sip-etag", rand_token(6)), ("expires", msg.get("expires") or "3600")],
            )
        else:
            self._respond(msg, addr, 501, to_tag=rand_token(4), headers=[("allow", ALLOW)])

    # ------------------------------------------------------------------ registrar
    def _on_register(self, msg: SipMessage, addr) -> None:
        headers, ev = self.registrar.handle_register(msg, addr)
        headers.append(("date", time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())))
        self._respond(msg, addr, 200, to_tag=rand_token(4), headers=headers)
        if ev:
            logger.info(f"SIP {'REGISTER' if ev['registered'] else 'UNREGISTER'} {ev['user']} -> {ev['contact']} from {addr}")
            self._emit(ev)

    # ------------------------------------------------------------------ instant messages (Linphone chat)
    def _on_message(self, msg: SipMessage, addr) -> None:
        self._respond(msg, addr, 200, to_tag=rand_token(4))
        key, now = (msg.call_id, msg.cseq[0]), time.monotonic()
        if key in self._im_seen:
            return  # retransmission (our 200 was lost)
        self._im_seen = {k: t for k, t in self._im_seen.items() if now - t < 2 * TIMER_F}
        self._im_seen[key] = now
        ctype, body = _mime(msg.get("content-type")) or "text/plain", msg.body
        if ctype == "message/cpim":
            ctype, body = _unwrap_cpim(body)
        if ctype != "text/plain":
            logger.debug(f"SIP MESSAGE {ctype or 'unknown'} ignored (typing indicator / receipt / file)")
            return
        text = body.decode("utf-8", "replace").strip()
        from_uri = extract_uri(msg.get("from"))
        user = parse_uri(from_uri)[0] or "anonymous"
        if not text:
            return
        self._im_peers[user] = ImPeer(uri=from_uri, local_uri=extract_uri(msg.get("to")) or self.im_identity, addr=addr)
        logger.info(f"SIP MESSAGE <- {user} ({len(text)} chars)")
        if self._inbox is not None:
            self._inbox.put_nowait((user, text))

    # ------------------------------------------------------------------ UAS (inbound)
    def _on_invite(self, msg: SipMessage, addr) -> None:
        cid = msg.call_id
        to_tag = get_tag(msg.get("to"))
        call = self._find(cid)
        cseq_n, _ = msg.cseq
        if call is not None:
            if call.direction == "inbound" and not to_tag:  # retransmitted initial INVITE
                if call.last_invite_resp:
                    self._send(call.last_invite_resp, addr)
                return
            if call.state == "ended":
                self._respond(msg, addr, 481)
                return
            self._on_reinvite(call, msg, addr)
            return
        if to_tag:
            self._respond(msg, addr, 481)
            return
        offer = parse_sdp(msg.body)
        audio = offer.audio() if offer else None
        pt = choose_g711(audio) if audio else None
        if offer is None or audio is None or pt is None:
            logger.warning(f"SIP inbound INVITE without usable G.711 audio offer from {addr}")
            self._respond(msg, addr, 488, to_tag=rand_token(4))
            return
        patient_id = ""
        if self.inbound_patient:
            try:
                patient_id = self.inbound_patient() or ""
            except Exception:
                logger.exception("inbound_patient() failed")
        call = Call(
            session_id="sip-" + uuid.uuid4().hex[:12],
            patient_id=patient_id,
            direction="inbound",
            call_id=cid,
            local_tag=rand_token(4),
        )
        call.invite_req = msg
        call.remote_hdr = msg.get("from") or ""
        call.local_hdr = f"{msg.get('to') or ''};tag={call.local_tag}"
        call.remote_target = extract_uri(msg.get("contact")) or extract_uri(msg.get("from"))
        call.sip_addr = addr
        call.remote_cseq = cseq_n
        call.state = "calling"
        self.calls[cid] = call
        self.sessions[call.session_id] = call
        call.last_invite_resp = self._respond(msg, addr, 100)
        logger.info(f"SIP inbound INVITE {extract_uri(msg.get('from'))} -> {msg.uri} (session {call.session_id})")
        self._call_task(call, self._answer_inbound(call, offer, pt))

    async def _answer_inbound(self, call: Call, offer, pt: int) -> None:
        msg = call.invite_req
        assert msg is not None
        try:
            call.rtp = await RtpSession.open(
                payload_type=pt, remote=offer.audio_addr(), on_audio=lambda p, c=call: self._on_rtp_audio(c, p)
            )
        except OSError as e:
            logger.error(f"RTP allocation failed: {e}")
            self._send(build_response(msg, 503, to_tag=call.local_tag, received=call.sip_addr), call.sip_addr)
            self._terminate(call, "no_rtp_port", send_bye=False)
            return
        if call.state == "ended":  # cancelled while we were binding
            call.rtp.close()
            return
        contact = ("contact", f"<sip:maya@{self.domain}>")
        call.last_invite_resp = self._respond(msg, call.sip_addr, 180, to_tag=call.local_tag, headers=[contact])
        call.state = "ringing"
        self._emit_session(call, "ringing")
        await asyncio.sleep(INBOUND_RING_SECS)
        if call.state == "ended":
            return
        call.local_sdp = build_answer_sdp(self.ip, call.rtp.port, offer, pt, call.sdp_sess, 1)
        call.last_invite_resp = self._respond(
            msg,
            call.sip_addr,
            200,
            to_tag=call.local_tag,
            headers=[contact, ("allow", ALLOW), ("content-type", "application/sdp")],
            body=call.local_sdp,
        )
        call.answered = True
        self._go_live(call)
        # retransmit 200 until ACK (don't block audio on it)
        interval, waited = T1, 0.0
        while waited < TIMER_B and not call.acked and call.state == "live":
            await asyncio.sleep(interval)
            waited += interval
            if call.acked or call.state != "live":
                break
            self._send(call.last_invite_resp, call.sip_addr)
            interval = min(interval * 2, T2)
        if not call.acked and call.state == "live":
            logger.warning(f"SIP no ACK for 200 OK (session {call.session_id}); keeping call up")

    def _on_reinvite(self, call: Call, msg: SipMessage, addr) -> None:
        cseq_n, _ = msg.cseq
        if call.last_reinvite and call.last_reinvite[0] == cseq_n:
            self._send(call.last_reinvite[1], addr)
            return
        call.remote_cseq = max(call.remote_cseq, cseq_n)
        self._apply_remote_sdp(call, msg.body)
        tc = msg.get("contact")
        if tc and extract_uri(tc):
            call.remote_target = extract_uri(tc)
        resp = self._respond(
            msg,
            addr,
            200,
            to_tag=call.local_tag,
            headers=[
                ("contact", f"<sip:maya@{self.domain}>"),
                ("allow", ALLOW),
                ("content-type", "application/sdp"),
            ],
            body=call.local_sdp,
        )
        call.last_reinvite = (cseq_n, resp)
        logger.info(f"SIP re-INVITE answered (session {call.session_id})")

    def _on_update(self, msg: SipMessage, addr) -> None:
        call = self._find(msg.call_id)
        if call is None or call.state == "ended":
            self._respond(msg, addr, 481)
            return
        if msg.body:
            self._apply_remote_sdp(call, msg.body)
            self._respond(
                msg,
                addr,
                200,
                to_tag=call.local_tag,
                headers=[("contact", f"<sip:maya@{self.domain}>"), ("content-type", "application/sdp")],
                body=call.local_sdp,
            )
        else:
            self._respond(msg, addr, 200, to_tag=call.local_tag)

    def _apply_remote_sdp(self, call: Call, body: bytes) -> None:
        sdp = parse_sdp(body)
        if not sdp or not call.rtp:
            return
        audio = sdp.audio()
        if audio is None:
            return
        pt = choose_g711(audio, prefer_offer_order=call.direction == "outbound")
        call.rtp.set_remote(sdp.audio_addr(), pt if pt is not None else None)

    def _on_bye(self, msg: SipMessage, addr) -> None:
        call = self._find(msg.call_id)
        if call is None:
            self._respond(msg, addr, 481)
            return
        self._respond(msg, addr, 200)
        logger.info(f"SIP BYE from phone (session {call.session_id})")
        self._terminate(call, "remote_hangup", send_bye=False)

    def _on_cancel(self, msg: SipMessage, addr) -> None:
        call = self._find(msg.call_id)
        if call is None:
            self._respond(msg, addr, 481)
            return
        self._respond(msg, addr, 200, to_tag=call.local_tag)
        if call.direction == "inbound" and not call.answered and call.invite_req is not None:
            call.last_invite_resp = self._respond(call.invite_req, addr, 487, to_tag=call.local_tag)
            self._terminate(call, "cancelled", send_bye=False)

    # ------------------------------------------------------------------ UAC (outbound)
    async def _uac_timers(self, call: Call) -> None:
        interval, elapsed = T1, 0.0
        while elapsed < TIMER_B and not call.provisional and call.state in ("calling", "ringing"):
            await asyncio.sleep(interval)
            elapsed += interval
            if call.provisional or call.state not in ("calling", "ringing"):
                break
            self._send(call.invite_bytes, call.sip_addr)
            interval = min(interval * 2, T2)
        if call.state not in ("calling", "ringing"):
            return
        if not call.provisional:
            logger.warning(f"SIP INVITE timed out (session {call.session_id})")
            self._terminate(call, "timeout", send_bye=False)
            return
        deadline = time.monotonic() + max(0.0, RING_TIMEOUT - elapsed)
        while time.monotonic() < deadline and call.state in ("calling", "ringing"):
            await asyncio.sleep(0.25)
        if call.state in ("calling", "ringing"):
            logger.info(f"SIP no answer (session {call.session_id}); cancelling")
            self._send_cancel(call)
            self._terminate(call, "no_answer", send_bye=False)

    def _send_cancel(self, call: Call) -> None:
        data = build_request(
            "CANCEL",
            call.invite_uri,
            [
                ("via", call.invite_via),
                ("max-forwards", "70"),
                ("from", call.local_hdr),
                ("to", f"<{extract_uri(call.remote_hdr)}>" if call.remote_hdr else ""),
                ("call-id", call.call_id),
                ("cseq", f"{call.invite_cseq} CANCEL"),
                ("user-agent", USER_AGENT),
            ],
        )
        self._send(data, call.sip_addr)

    def _handle_response(self, msg: SipMessage, addr) -> None:
        _, method = msg.cseq
        if method == "MESSAGE":
            done = self._im_pending.get(msg.call_id)
            if done is not None and msg.status >= 200:
                if msg.status >= 300:
                    logger.warning(f"SIP MESSAGE rejected by phone: {msg.status} {msg.reason}")
                done.set()
            return
        call = self._find(msg.call_id)
        if call is None:
            return
        if method == "INVITE" and call.direction == "outbound":
            self._on_invite_response(call, msg, addr)
        elif method == "BYE":
            call.bye_acked = True

    def _on_invite_response(self, call: Call, msg: SipMessage, addr) -> None:
        code = msg.status
        if code < 200:
            if not call.provisional:
                logger.info(f"SIP {code} {msg.reason} (session {call.session_id})")
            call.provisional = True
            if code in (180, 183) and call.state == "calling":
                call.state = "ringing"
            return

        if call.ack_bytes is not None:  # retransmitted final response -> re-ACK
            self._send(call.ack_bytes, call.sip_addr)
            return

        call.provisional = True
        if 200 <= code < 300:
            call.remote_hdr = msg.get("to") or call.remote_hdr
            target = extract_uri(msg.get("contact"))
            if target:
                call.remote_target = target
            call.sip_addr = addr
            call.ack_bytes = build_request(
                "ACK",
                call.remote_target,
                [
                    ("via", f"SIP/2.0/UDP {self.domain};branch={new_branch()};rport"),
                    ("max-forwards", "70"),
                    ("from", call.local_hdr),
                    ("to", call.remote_hdr),
                    ("call-id", call.call_id),
                    ("cseq", f"{call.invite_cseq} ACK"),
                    ("user-agent", USER_AGENT),
                ],
            )
            self._send(call.ack_bytes, call.sip_addr)
            if call.state == "ended":  # we gave up (cancel/timeout) but it got answered anyway
                self._send_bye(call)
                return
            self._apply_remote_sdp(call, msg.body)
            call.answered = call.acked = True
            logger.info(f"SIP call answered (session {call.session_id}), rtp -> {call.rtp.remote if call.rtp else None}")
            self._go_live(call)
            return

        # non-2xx final: ACK in the INVITE transaction (same branch)
        call.ack_bytes = build_request(
            "ACK",
            call.invite_uri,
            [
                ("via", call.invite_via),
                ("max-forwards", "70"),
                ("from", call.local_hdr),
                ("to", msg.get("to") or call.remote_hdr),
                ("call-id", call.call_id),
                ("cseq", f"{call.invite_cseq} ACK"),
                ("user-agent", USER_AGENT),
            ],
        )
        self._send(call.ack_bytes, call.sip_addr)
        reason = FAIL_REASONS.get(code, f"sip_{code}")
        logger.info(f"SIP call failed {code} {msg.reason} (session {call.session_id})")
        self._terminate(call, reason, send_bye=False)

    # ------------------------------------------------------------------ dialog teardown
    def _send_bye(self, call: Call) -> None:
        call.local_cseq += 1
        data = build_request(
            "BYE",
            call.remote_target,
            [
                ("via", f"SIP/2.0/UDP {self.domain};branch={new_branch()};rport"),
                ("max-forwards", "70"),
                ("from", call.local_hdr),
                ("to", call.remote_hdr),
                ("call-id", call.call_id),
                ("cseq", f"{call.local_cseq} BYE"),
                ("user-agent", USER_AGENT),
            ],
        )
        self._send(data, call.sip_addr)

        async def retransmit() -> None:
            for d in (0.5, 1.0, 2.0, 4.0):
                await asyncio.sleep(d)
                if call.bye_acked:
                    return
                self._send(data, call.sip_addr)

        self._spawn(retransmit())

    def _terminate(self, call: Call, reason: str, send_bye: bool) -> None:
        if call.state == "ended":
            return
        was_live = call.state == "live"
        call.state = "ended"
        call.end_reason = reason
        if send_bye and was_live:
            self._send_bye(call)
        if call.rtp:
            logger.info(
                f"SIP call ended ({reason}) session {call.session_id}: rtp rx={call.rtp.rx_packets} "
                f"tx={call.rtp.tx_packets} ws out={call.ws_frames_out} in={call.ws_frames_in} "
                f"inbound peak_rms={call.in_peak_rms:.0f} voiced={call.in_voiced_frames * FRAME_BYTES / 8000:.1f}s"
            )
            call.rtp.close()
        else:
            logger.info(f"SIP call ended ({reason}) session {call.session_id}")
        call.ws_q.put_nowait(_STOP)
        for t in list(call.tasks):
            if t is not asyncio.current_task() and t is not call.bridge_task:
                t.cancel()
        self.calls.pop(call.call_id, None)
        self.sessions.pop(call.session_id, None)
        self._ended[call.call_id] = call
        self._spawn(self._forget(call))
        self._emit_session(call, "ended", reason)

    async def _forget(self, call: Call) -> None:
        await asyncio.sleep(ENDED_LINGER)
        if self._ended.get(call.call_id) is call:
            del self._ended[call.call_id]

    # ------------------------------------------------------------------ media bridge
    def _go_live(self, call: Call) -> None:
        if call.state == "live":
            return
        call.state = "live"
        if call.rtp:
            call.rtp.start()
        self._emit_session(call, "live")
        # bridge task survives _terminate so it can send "stop" and close the ws itself
        call.bridge_task = self._call_task(call, self._bridge(call))

    def _on_rtp_audio(self, call: Call, payload: bytes) -> None:
        if call.state != "live":
            return
        buf = call.rx_buf
        buf += payload
        while len(buf) >= FRAME_BYTES:
            frame = bytes(buf[:FRAME_BYTES])
            del buf[:FRAME_BYTES]
            self._meter(call, frame)
            if call.ws_q.qsize() < WS_QUEUE_MAX:
                call.ws_q.put_nowait(frame)

    @staticmethod
    def _meter(call: Call, frame: bytes) -> None:
        rms = float(np.sqrt(np.mean(ulaw_to_pcm16(frame).astype(np.float32) ** 2)))
        call.in_frames += 1
        call.in_peak_rms = max(call.in_peak_rms, rms)
        call.in_voiced_frames += rms >= VOICED_RMS
        if call.in_frames == SILENT_WARN_FRAMES and call.in_voiced_frames == 0:
            logger.warning(
                f"SIP inbound audio is silent after {SILENT_WARN_FRAMES * FRAME_BYTES // 8000}s "
                f"(peak RMS {call.in_peak_rms:.0f}, session {call.session_id}). The phone is sending RTP but no "
                "speech: check the softphone's mute button, its input device, and the OS mic level."
            )

    async def _bridge(self, call: Call) -> None:
        sid = call.stream_sid
        if call.state == "ended":
            return
        try:
            async with websockets.connect(self.bot_ws_url, max_size=None, open_timeout=10, close_timeout=3) as ws:
                await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
                await ws.send(
                    json.dumps(
                        {
                            "event": "start",
                            "sequenceNumber": "1",
                            "streamSid": sid,
                            "start": {
                                "streamSid": sid,
                                "callSid": call.call_sid,
                                "accountSid": "AC" + "0" * 32,
                                "tracks": ["inbound"],
                                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                                "customParameters": {
                                    "patient_id": call.patient_id,
                                    "session_id": call.session_id,
                                    "channel": "linphone",
                                    "direction": call.direction,
                                },
                            },
                        }
                    )
                )
                logger.info(f"SIP bridge connected to bot (session {call.session_id}, stream {sid})")
                sender = asyncio.get_running_loop().create_task(self._ws_sender(call, ws))
                try:
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            continue
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        ev = m.get("event")
                        if ev == "media" and call.rtp is not None:
                            payload = (m.get("media") or {}).get("payload")
                            if payload:
                                try:
                                    call.rtp.enqueue(base64.b64decode(payload))
                                    call.ws_frames_in += 1
                                except (ValueError, TypeError):
                                    pass
                        elif ev == "clear" and call.rtp is not None:
                            call.rtp.clear()
                except websockets.ConnectionClosed:
                    pass
                finally:
                    if call.state == "ended":
                        try:
                            await asyncio.wait_for(sender, timeout=3)
                        except (asyncio.TimeoutError, Exception):
                            sender.cancel()
                    else:
                        sender.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"SIP bridge error (session {call.session_id}): {e!r}")
            if call.state != "ended":
                self._terminate(call, "bridge_error", send_bye=True)
            return
        if call.state != "ended":
            logger.info(f"Bot closed websocket -> BYE (session {call.session_id})")
            self._terminate(call, "bot_hangup", send_bye=True)

    async def _ws_sender(self, call: Call, ws) -> None:
        chunk = 0
        sid = call.stream_sid
        try:
            while True:
                item = await call.ws_q.get()
                if item is _STOP:
                    await ws.send(
                        json.dumps(
                            {
                                "event": "stop",
                                "sequenceNumber": str(chunk + 2),
                                "streamSid": sid,
                                "stop": {"accountSid": "AC" + "0" * 32, "callSid": call.call_sid},
                            }
                        )
                    )
                    await ws.close()
                    return
                chunk += 1
                await ws.send(
                    json.dumps(
                        {
                            "event": "media",
                            "sequenceNumber": str(chunk + 1),
                            "streamSid": sid,
                            "media": {
                                "track": "inbound",
                                "chunk": str(chunk),
                                "timestamp": str(chunk * 20),
                                "payload": base64.b64encode(item).decode(),
                            },
                        }
                    )
                )
                call.ws_frames_out += 1
        except websockets.ConnectionClosed:
            return
