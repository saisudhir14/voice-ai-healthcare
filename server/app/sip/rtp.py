"""G.711 codecs (numpy/table based, no audioop), SDP helpers and a paced RTP session."""

from __future__ import annotations

import asyncio
import random
import struct
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------------------- G.711

SILENCE_ULAW = b"\xff" * 160
FRAME_BYTES = 160  # 20 ms @ 8 kHz
FRAME_SECS = 0.02


def _ulaw_decode_byte(u: int) -> int:
    u = ~u & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t <<= (u & 0x70) >> 4
    return (0x84 - t) if (u & 0x80) else (t - 0x84)


def _alaw_decode_byte(a: int) -> int:
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if (a & 0x80) else -t


ULAW_DECODE = np.array([_ulaw_decode_byte(i) for i in range(256)], dtype=np.int16)
ALAW_DECODE = np.array([_alaw_decode_byte(i) for i in range(256)], dtype=np.int16)

_SEG_UEND = np.array([0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF])
_SEG_AEND = np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF])


def ulaw_encode(pcm: np.ndarray) -> np.ndarray:
    """int16 linear PCM -> uint8 mu-law (Sun g711.c algorithm, vectorised)."""
    x = np.asarray(pcm).astype(np.int64) >> 2
    mask = np.where(x < 0, 0x7F, 0xFF)
    x = np.minimum(np.abs(x), 8159) + 33
    seg = np.searchsorted(_SEG_UEND, x, side="left")
    uval = (seg << 4) | ((x >> (seg + 1)) & 0x0F)
    uval = np.where(seg >= 8, 0x7F, uval)
    return ((uval ^ mask) & 0xFF).astype(np.uint8)


def alaw_encode(pcm: np.ndarray) -> np.ndarray:
    """int16 linear PCM -> uint8 A-law."""
    x = np.asarray(pcm).astype(np.int64) >> 3
    mask = np.where(x >= 0, 0xD5, 0x55)
    x = np.where(x >= 0, x, -x - 1)
    seg = np.searchsorted(_SEG_AEND, x, side="left")
    shift = np.where(seg < 2, 1, seg)
    aval = (seg << 4) | ((x >> shift) & 0x0F)
    aval = np.where(seg >= 8, 0x7F, aval)
    return ((aval ^ mask) & 0xFF).astype(np.uint8)


# 256-byte translate tables for direct transcoding (bytes.translate is very fast)
ULAW_TO_ALAW = alaw_encode(ULAW_DECODE).tobytes()
ALAW_TO_ULAW = ulaw_encode(ALAW_DECODE).tobytes()


def ulaw_to_pcm16(data: bytes) -> np.ndarray:
    return ULAW_DECODE[np.frombuffer(data, dtype=np.uint8)]


def alaw_to_pcm16(data: bytes) -> np.ndarray:
    return ALAW_DECODE[np.frombuffer(data, dtype=np.uint8)]


def pcm16_to_ulaw(pcm: np.ndarray) -> bytes:
    return ulaw_encode(pcm).tobytes()


# --------------------------------------------------------------------------- RTP parsing

RTP_PORT_MIN = 40000
RTP_PORT_MAX = 40100
_used_ports: set[int] = set()


@dataclass
class RtpPacket:
    pt: int
    seq: int
    ts: int
    ssrc: int
    marker: bool
    payload: bytes


def parse_rtp(data: bytes) -> RtpPacket | None:
    if len(data) < 12 or (data[0] >> 6) != 2:
        return None
    pt = data[1] & 0x7F
    if 72 <= pt <= 76:  # RTCP (SR/RR/SDES/BYE/APP) multiplexed on the RTP port
        return None
    cc = data[0] & 0x0F
    off = 12 + 4 * cc
    if data[0] & 0x10:  # header extension
        if len(data) < off + 4:
            return None
        ext_words = struct.unpack_from("!H", data, off + 2)[0]
        off += 4 + 4 * ext_words
    end = len(data)
    if data[0] & 0x20 and end > off:  # padding
        end -= data[-1]
    if end <= off:
        return None
    seq, ts, ssrc = struct.unpack_from("!HII", data, 2)
    return RtpPacket(pt, seq, ts, ssrc, bool(data[1] & 0x80), bytes(data[off:end]))


# --------------------------------------------------------------------------- SDP

@dataclass
class SdpMedia:
    kind: str
    port: int
    proto: str
    fmts: list[str]
    ip: str | None = None
    rtpmap: dict[str, str] = field(default_factory=dict)
    direction: str = "sendrecv"


@dataclass
class SdpInfo:
    session_ip: str | None
    media: list[SdpMedia]

    def audio(self) -> SdpMedia | None:
        for m in self.media:
            if m.kind == "audio" and m.port:
                return m
        return None

    def audio_addr(self) -> tuple[str, int] | None:
        a = self.audio()
        if not a:
            return None
        ip = a.ip or self.session_ip
        if not ip:
            return None
        return ip, a.port


def parse_sdp(body: bytes | str) -> SdpInfo | None:
    if not body:
        return None
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    session_ip: str | None = None
    media: list[SdpMedia] = []
    cur: SdpMedia | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 2 or line[1] != "=":
            continue
        k, v = line[0], line[2:].strip()
        if k == "c":
            parts = v.split()
            ip = parts[2].split("/")[0] if len(parts) >= 3 else None
            if cur is None:
                session_ip = ip
            else:
                cur.ip = ip
        elif k == "m":
            parts = v.split()
            if len(parts) < 3:
                continue
            try:
                port = int(parts[1].split("/")[0])
            except ValueError:
                port = 0
            cur = SdpMedia(kind=parts[0].lower(), port=port, proto=parts[2], fmts=parts[3:])
            media.append(cur)
        elif k == "a" and cur is not None:
            if v.startswith("rtpmap:"):
                try:
                    pt, enc = v[7:].split(None, 1)
                    cur.rtpmap[pt] = enc.strip().lower()
                except ValueError:
                    pass
            elif v in ("sendrecv", "sendonly", "recvonly", "inactive"):
                cur.direction = v
    if not media:
        return None
    return SdpInfo(session_ip, media)


def choose_g711(m: SdpMedia, prefer_offer_order: bool = False) -> int | None:
    """PCMU (0) if offered else PCMA (8). If prefer_offer_order, first G.711 in the m-line."""
    found: list[int] = []
    for f in m.fmts:
        enc = m.rtpmap.get(f, "")
        if f == "0" or enc.startswith("pcmu/8000"):
            found.append(0)
        elif f == "8" or enc.startswith("pcma/8000"):
            found.append(8)
    if not found:
        return None
    if prefer_offer_order:
        return found[0]
    return 0 if 0 in found else 8


def telephone_event_pt(m: SdpMedia) -> int | None:
    for f in m.fmts:
        if m.rtpmap.get(f, "").startswith("telephone-event/8000"):
            try:
                return int(f)
            except ValueError:
                return None
    return None


def _audio_lines(port: int, proto: str, payloads: list[int], te_pt: int | None) -> list[str]:
    fmts = [str(p) for p in payloads] + ([str(te_pt)] if te_pt is not None else [])
    lines = [f"m=audio {port} {proto} {' '.join(fmts)}"]
    for p in payloads:
        lines.append(f"a=rtpmap:{p} {'PCMU' if p == 0 else 'PCMA'}/8000")
    if te_pt is not None:
        lines += [f"a=rtpmap:{te_pt} telephone-event/8000", f"a=fmtp:{te_pt} 0-16"]
    lines += ["a=ptime:20", "a=sendrecv"]
    return lines


def _sdp_head(ip: str, sess_id: int, version: int, user: str) -> list[str]:
    return ["v=0", f"o={user} {sess_id} {version} IN IP4 {ip}", "s=VillageLine", f"c=IN IP4 {ip}", "t=0 0"]


def build_offer_sdp(
    ip: str, port: int, sess_id: int, version: int = 1, payloads=(0, 8), te_pt: int | None = 101, user: str = "maya"
) -> bytes:
    lines = _sdp_head(ip, sess_id, version, user) + _audio_lines(port, "RTP/AVP", list(payloads), te_pt)
    return ("\r\n".join(lines) + "\r\n").encode()


def build_answer_sdp(
    ip: str, port: int, offer: SdpInfo, pt: int, sess_id: int, version: int = 1, user: str = "maya"
) -> bytes:
    """Answer keeping the offer's m-line order: chosen audio accepted, everything else port 0."""
    lines = _sdp_head(ip, sess_id, version, user)
    audio = offer.audio()
    for m in offer.media:
        if m is audio:
            lines += _audio_lines(port, m.proto, [pt], telephone_event_pt(m))
        else:
            lines.append(f"m={m.kind} 0 {m.proto} {' '.join(m.fmts) or '0'}")
    return ("\r\n".join(lines) + "\r\n").encode()


# --------------------------------------------------------------------------- RTP session

class _Sink(asyncio.DatagramProtocol):
    """Swallows RTCP on port+1."""

    def datagram_received(self, data, addr):  # noqa: D401
        pass

    def error_received(self, exc):
        pass


class RtpSession(asyncio.DatagramProtocol):
    """One UDP socket per call. Receives G.711 (-> PCMU callback); sends paced 20 ms PCMU/PCMA."""

    PREBUFFER = 480  # 60 ms of bot audio before (re)starting playout -> absorbs ws jitter
    TAIL_WAIT = 0.06  # flush a partial frame if nothing new arrived for this long

    def __init__(
        self,
        payload_type: int = 0,
        remote: tuple[str, int] | None = None,
        on_audio: Callable[[bytes], None] | None = None,
    ) -> None:
        self.pt = payload_type
        self.remote = remote
        self.latched = False
        self.on_audio = on_audio
        self.port = 0
        self.transport: asyncio.DatagramTransport | None = None
        self._rtcp: asyncio.DatagramTransport | None = None
        self._out = bytearray()
        self._ssrc = random.getrandbits(32)
        self._seq = random.getrandbits(16)
        self._ts = random.getrandbits(32)
        self._first = True
        self._playing = False
        self._last_enqueue = 0.0
        self._task: asyncio.Task | None = None
        self._closed = False
        self.rx_packets = 0
        self.tx_packets = 0

    # ---- lifecycle
    @classmethod
    async def open(cls, bind_ip: str = "0.0.0.0", **kw) -> "RtpSession":
        loop = asyncio.get_running_loop()
        for port in range(RTP_PORT_MIN, RTP_PORT_MAX, 2):
            if port in _used_ports:
                continue
            try:
                _t, proto = await loop.create_datagram_endpoint(lambda: cls(**kw), local_addr=(bind_ip, port))
            except OSError:
                continue
            _used_ports.add(port)
            proto.port = port
            try:
                proto._rtcp, _ = await loop.create_datagram_endpoint(_Sink, local_addr=(bind_ip, port + 1))
            except OSError:
                proto._rtcp = None
            return proto
        raise OSError("no free RTP port in range")

    def connection_made(self, transport) -> None:
        self.transport = transport

    def error_received(self, exc) -> None:  # ICMP unreachable before the far end is ready
        pass

    def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = asyncio.get_running_loop().create_task(self._send_loop())

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task:
            self._task.cancel()
        if self.transport:
            self.transport.close()
        if self._rtcp:
            self._rtcp.close()
        _used_ports.discard(self.port)

    # ---- control
    def set_remote(self, addr: tuple[str, int] | None, pt: int | None = None) -> None:
        if pt is not None:
            self.pt = pt
        if addr and not self.latched and addr[0] not in ("0.0.0.0", "") and addr[1]:
            self.remote = (addr[0], int(addr[1]))

    def enqueue(self, ulaw: bytes) -> None:
        if self._closed or not ulaw:
            return
        self._out += ulaw
        self._last_enqueue = asyncio.get_running_loop().time()

    def clear(self) -> None:
        self._out.clear()
        self._playing = False

    @property
    def queued_ms(self) -> int:
        return len(self._out) // 8

    # ---- receive
    def datagram_received(self, data: bytes, addr) -> None:
        pkt = parse_rtp(data)
        if pkt is None or pkt.pt not in (0, 8):
            return  # RTCP, telephone-event, CN, ... ignored
        if not self.latched or addr != self.remote:  # symmetric RTP
            self.remote = (addr[0], addr[1])
            self.latched = True
        payload = pkt.payload.translate(ALAW_TO_ULAW) if pkt.pt == 8 else pkt.payload
        self.rx_packets += 1
        if self.on_audio:
            try:
                self.on_audio(payload)
            except Exception:  # never let a consumer bug kill the socket
                pass

    # ---- send
    def _next_frame(self, now: float) -> bytes:
        buf = self._out
        if not self._playing:
            if len(buf) >= self.PREBUFFER or (buf and now - self._last_enqueue > self.TAIL_WAIT):
                self._playing = True
            else:
                return SILENCE_ULAW
        if len(buf) >= FRAME_BYTES:
            frame = bytes(buf[:FRAME_BYTES])
            del buf[:FRAME_BYTES]
            return frame
        if buf and now - self._last_enqueue > self.TAIL_WAIT:
            frame = bytes(buf) + SILENCE_ULAW[len(buf) :]
            buf.clear()
            self._playing = False
            return frame
        self._playing = False  # underrun -> re-prebuffer
        return SILENCE_ULAW

    def _send_frame(self, payload: bytes) -> None:
        if self.transport is not None and self.remote is not None:
            if self.pt == 8:
                payload = payload.translate(ULAW_TO_ALAW)
            hdr = struct.pack(
                "!BBHII", 0x80, (0x80 if self._first else 0) | self.pt, self._seq, self._ts, self._ssrc
            )
            try:
                self.transport.sendto(hdr + payload, self.remote)
                self.tx_packets += 1
                self._first = False
            except OSError:
                pass
        self._seq = (self._seq + 1) & 0xFFFF
        self._ts = (self._ts + FRAME_BYTES) & 0xFFFFFFFF

    async def _send_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        while not self._closed:
            self._send_frame(self._next_frame(loop.time()))
            next_t += FRAME_SECS
            delay = next_t - loop.time()
            if delay < -0.2:  # event loop stalled; resync instead of bursting
                next_t = loop.time()
                delay = 0.0
            await asyncio.sleep(max(0.0, delay))
