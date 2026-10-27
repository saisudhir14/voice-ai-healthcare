"""SIP message parsing/building helpers + a tiny in-memory registrar.

RFC 3261 subset, UDP only, deliberately lenient (Linphone-friendly). No auth.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass

USER_AGENT = "VillageLine-SIP/1.0"
ALLOW = "INVITE, ACK, CANCEL, BYE, OPTIONS, INFO, UPDATE, NOTIFY, MESSAGE"
DEFAULT_EXPIRES = 3600
MAX_EXPIRES = 86400

_COMPACT = {
    "v": "via",
    "f": "from",
    "t": "to",
    "i": "call-id",
    "m": "contact",
    "l": "content-length",
    "c": "content-type",
    "k": "supported",
    "s": "subject",
    "e": "content-encoding",
    "o": "event",
    "u": "allow-events",
    "r": "refer-to",
    "b": "referred-by",
    "x": "session-expires",
}
_CANON = {
    "call-id": "Call-ID",
    "cseq": "CSeq",
    "www-authenticate": "WWW-Authenticate",
    "sip-etag": "SIP-ETag",
    "sip-if-match": "SIP-If-Match",
    "mime-version": "MIME-Version",
}

REASONS = {
    100: "Trying",
    180: "Ringing",
    181: "Call Is Being Forwarded",
    183: "Session Progress",
    200: "OK",
    202: "Accepted",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    480: "Temporarily Unavailable",
    481: "Call/Transaction Does Not Exist",
    486: "Busy Here",
    487: "Request Terminated",
    488: "Not Acceptable Here",
    489: "Bad Event",
    500: "Server Internal Error",
    501: "Not Implemented",
    503: "Service Unavailable",
    600: "Busy Everywhere",
    603: "Decline",
}


def rand_token(nbytes: int = 6) -> str:
    return secrets.token_hex(nbytes)


def new_branch() -> str:
    return "z9hG4bK" + secrets.token_hex(8)


def canon_header(name: str) -> str:
    name = name.lower()
    if name in _CANON:
        return _CANON[name]
    return "-".join(p.capitalize() for p in name.split("-"))


def split_header_values(value: str) -> list[str]:
    """Split a comma-joined header value, ignoring commas inside <> or quotes."""
    out, cur, depth, quoted = [], [], 0, False
    for ch in value:
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch == "<":
            depth += 1
        elif not quoted and ch == ">":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0 and not quoted:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


class SipMessage:
    def __init__(self) -> None:
        self.method: str | None = None
        self.uri: str | None = None
        self.status: int = 0
        self.reason: str = ""
        self.headers: list[tuple[str, str]] = []  # (lowercase name, value)
        self.body: bytes = b""

    @property
    def is_request(self) -> bool:
        return self.method is not None

    def get(self, name: str) -> str | None:
        name = name.lower()
        for k, v in self.headers:
            if k == name:
                return v
        return None

    def get_all(self, name: str) -> list[str]:
        name = name.lower()
        return [v for k, v in self.headers if k == name]

    def vias(self) -> list[str]:
        out: list[str] = []
        for v in self.get_all("via"):
            out.extend(split_header_values(v))
        return out

    def contacts(self) -> list[str]:
        out: list[str] = []
        for v in self.get_all("contact"):
            out.extend(split_header_values(v))
        return out

    @property
    def call_id(self) -> str:
        return (self.get("call-id") or "").strip()

    @property
    def cseq(self) -> tuple[int, str]:
        raw = (self.get("cseq") or "").split()
        try:
            return int(raw[0]), (raw[1].upper() if len(raw) > 1 else "")
        except (ValueError, IndexError):
            return 0, ""

    def first_line(self) -> str:
        if self.is_request:
            return f"{self.method} {self.uri}"
        return f"{self.status} {self.reason}"


def parse_message(data: bytes) -> SipMessage | None:
    if not data or not data.strip():
        return None  # CRLF keep-alive
    sep, seplen = data.find(b"\r\n\r\n"), 4
    if sep < 0:
        sep, seplen = data.find(b"\n\n"), 2
    if sep < 0:
        sep, seplen = len(data), 0
    head = data[:sep].decode("utf-8", "replace").replace("\r\n", "\n")
    lines = head.lstrip("\n").split("\n")
    if not lines:
        return None
    first = lines[0].strip()
    msg = SipMessage()
    if first.upper().startswith("SIP/2.0"):
        parts = first.split(" ", 2)
        try:
            msg.status = int(parts[1])
        except (ValueError, IndexError):
            return None
        msg.reason = parts[2] if len(parts) > 2 else ""
    else:
        parts = first.split()
        if len(parts) < 3 or not parts[2].upper().startswith("SIP/"):
            return None
        msg.method, msg.uri = parts[0].upper(), parts[1]
    for line in lines[1:]:
        if not line.strip():
            continue
        if line[0] in " \t" and msg.headers:
            k, v = msg.headers[-1]
            msg.headers[-1] = (k, v + " " + line.strip())
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        msg.headers.append((_COMPACT.get(k, k), v.strip()))
    body = data[sep + seplen :] if seplen else b""
    cl = (msg.get("content-length") or "").strip()
    if cl.isdigit():
        body = body[: int(cl)]
    msg.body = body
    return msg


def build_message(start_line: str, headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
    lines = [start_line]
    for k, v in headers:
        if k.lower() == "content-length":
            continue
        lines.append(f"{canon_header(k)}: {v}")
    lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def build_request(method: str, uri: str, headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
    return build_message(f"{method} {uri} SIP/2.0", headers, body)


def _fix_via(via: str, addr: tuple[str, int] | None) -> str:
    if not addr:
        return via
    ip, port = addr[0], addr[1]
    via = re.sub(r";\s*rport(?![=\w-])", f";rport={port}", via, count=1, flags=re.I)
    if not re.search(r";\s*received=", via, re.I):
        via += f";received={ip}"
    return via


def build_response(
    req: SipMessage,
    code: int,
    reason: str | None = None,
    *,
    to_tag: str | None = None,
    headers: list[tuple[str, str]] | tuple = (),
    body: bytes = b"",
    received: tuple[str, int] | None = None,
) -> bytes:
    hs: list[tuple[str, str]] = []
    for i, via in enumerate(req.vias()):
        hs.append(("via", _fix_via(via, received) if i == 0 else via))
    hs.append(("from", req.get("from") or ""))
    to = req.get("to") or ""
    if to_tag and code > 100 and not get_tag(to):
        to = f"{to};tag={to_tag}"
    hs.append(("to", to))
    hs.append(("call-id", req.call_id))
    hs.append(("cseq", req.get("cseq") or ""))
    hs.extend(headers)
    if not any(k.lower() == "server" for k, _ in headers):
        hs.append(("server", USER_AGENT))
    return build_message(f"SIP/2.0 {code} {reason or REASONS.get(code, 'Unknown')}", hs, body)


# --------------------------------------------------------------------------- header helpers

def _after_uri(header: str) -> str:
    """Header-param section: text after the name-addr's closing '>' (or after first ';' in addr-spec form)."""
    lt = header.find("<")
    if lt >= 0:
        gt = header.find(">", lt)
        return header[gt + 1 :] if gt >= 0 else ""
    return header.split(";", 1)[1] if ";" in header else ""


def get_tag(header: str | None) -> str:
    if not header:
        return ""
    tail = _after_uri(header)
    m = re.search(r"(?:^|;)\s*tag=([^;,\s>]+)", tail, re.I)
    return m.group(1) if m else ""


def extract_uri(header: str | None) -> str:
    if not header:
        return ""
    h = header.strip()
    if "<" in h and ">" in h:
        return h[h.index("<") + 1 : h.index(">", h.index("<"))].strip()
    return h.split(";")[0].strip()


def header_param(header: str | None, name: str) -> str | None:
    """Header param (after the URI), e.g. Contact ...;expires=60."""
    if not header:
        return None
    tail = _after_uri(header)
    m = re.search(rf"(?:^|;)\s*{re.escape(name)}\s*=\s*\"?([^;\",\s]+)", tail, re.I)
    return m.group(1) if m else None


def parse_uri(uri: str) -> tuple[str, str, int | None]:
    """sip:user@host:port;params -> (user, host, port)."""
    u = uri.strip()
    for scheme in ("sips:", "sip:", "tel:"):
        if u.lower().startswith(scheme):
            u = u[len(scheme) :]
            break
    u = u.split("?", 1)[0].split(";", 1)[0]
    user, hostport = ("", u)
    if "@" in u:
        user, hostport = u.rsplit("@", 1)
        user = user.split(":", 1)[0]  # drop password
    host, port = hostport, None
    if hostport.startswith("["):
        end = hostport.find("]")
        host = hostport[1:end]
        rest = hostport[end + 1 :]
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    elif ":" in hostport:
        host, p = hostport.rsplit(":", 1)
        port = int(p) if p.isdigit() else None
    return user, host, port


# --------------------------------------------------------------------------- registrar

@dataclass
class Binding:
    user: str
    aor: str
    contact: str  # contact URI (request-URI for INVITE)
    addr: tuple[str, int]  # observed source address (where we actually send)
    expires_at: float
    registered_at: float
    user_agent: str = ""

    def public(self) -> dict:
        return {
            "user": self.user,
            "contact": self.contact,
            "addr": f"{self.addr[0]}:{self.addr[1]}",
            "expires_in": max(0, int(self.expires_at - time.monotonic())),
            "user_agent": self.user_agent,
        }


class Registrar:
    def __init__(self) -> None:
        self.bindings: dict[str, Binding] = {}

    def handle_register(
        self, msg: SipMessage, addr: tuple[str, int]
    ) -> tuple[list[tuple[str, str]], dict | None]:
        """Process a REGISTER. Returns (extra response headers, event-or-None)."""
        aor = extract_uri(msg.get("to")) or extract_uri(msg.get("from"))
        user = parse_uri(aor)[0] or parse_uri(extract_uri(msg.get("from")))[0] or "anonymous"
        try:
            hdr_exp = int((msg.get("expires") or str(DEFAULT_EXPIRES)).strip())
        except ValueError:
            hdr_exp = DEFAULT_EXPIRES
        contacts = msg.contacts()
        now = time.monotonic()
        existing = self.bindings.get(user)

        if any(c.strip() == "*" for c in contacts):
            self.bindings.pop(user, None)
            ev = {"type": "sip", "registered": False, "user": user, "contact": existing.contact} if existing else None
            return [("expires", "0")], ev

        if not contacts:  # query
            if existing:
                exp = max(0, int(existing.expires_at - now))
                return [("contact", f"<{existing.contact}>;expires={exp}")], None
            return [], None

        c = contacts[0]
        uri = extract_uri(c)
        try:
            exp = int(header_param(c, "expires") or hdr_exp)
        except ValueError:
            exp = hdr_exp
        exp = max(0, min(exp, MAX_EXPIRES))

        if exp == 0:
            self.bindings.pop(user, None)
            ev = {"type": "sip", "registered": False, "user": user, "contact": uri}
            return [("contact", f"<{uri}>;expires=0"), ("expires", "0")], ev

        changed = existing is None or existing.contact != uri or existing.addr != tuple(addr)
        self.bindings[user] = Binding(
            user=user,
            aor=aor,
            contact=uri,
            addr=(addr[0], addr[1]),
            expires_at=now + exp,
            registered_at=now if changed or existing is None else existing.registered_at,
            user_agent=msg.get("user-agent") or "",
        )
        if changed:
            # most recent (re)binding wins for outbound calls
            self.bindings[user].registered_at = now
        ev = {"type": "sip", "registered": True, "user": user, "contact": uri} if changed else None
        return [("contact", f"<{uri}>;expires={exp}"), ("expires", str(exp))], ev

    def expire(self) -> list[Binding]:
        now = time.monotonic()
        gone = [b for b in self.bindings.values() if b.expires_at <= now]
        for b in gone:
            self.bindings.pop(b.user, None)
        return gone

    def latest(self) -> Binding | None:
        now = time.monotonic()
        live = [b for b in self.bindings.values() if b.expires_at > now]
        return max(live, key=lambda b: b.registered_at) if live else None
