"""One human-readable transcript file per call: logs/calls/<YYYY-MM-DD_HH-MM-SS>_<patient>_<channel>.log.

Fed by the event bus, so every channel (Linphone, browser, chat) is logged the same way.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from . import bus, clinic

LOG_DIR = Path(os.environ.get("CALL_LOG_DIR") or Path(__file__).resolve().parents[2] / "logs" / "calls")
SPEAKERS = {"agent": "Maya", "patient": "Patient"}
STATUS = {"ringing": "ringing the patient", "live": "call connected"}

_files: dict[str, Path] = {}
_started: dict[str, float] = {}
_status: dict[str, str] = {}


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _compact(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _open(event: dict[str, Any]) -> Path:
    sid = event["session_id"]
    ts = event["ts"]
    started = datetime.fromtimestamp(ts).astimezone()
    patient_id = event.get("patient_id") or "unknown"
    channel = event.get("channel") or sid.split("-", 1)[0]
    stem = f"{started:%Y-%m-%d_%H-%M-%S}_{patient_id}_{channel}"
    path = LOG_DIR / f"{stem}.log"
    n = 2
    while path.exists():
        path = LOG_DIR / f"{stem}-{n}.log"
        n += 1
    patient = clinic.get_patient(patient_id) or {}
    name = " ".join(filter(None, [patient.get("first_name"), patient.get("last_name")])) or patient_id
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Village Line call log (synthetic data)\n"
        f"Session:   {sid}\n"
        f"Patient:   {name} ({patient_id}, MPI {patient.get('mpi', 'n/a')})\n"
        f"Channel:   {channel} / {event.get('direction') or 'outbound'}\n"
        f"Started:   {started:%Y-%m-%d %H:%M:%S %Z}\n"
        + "-" * 60 + "\n",
        encoding="utf-8",
    )
    _files[sid] = path
    _started[sid] = ts
    logger.info("Call log: {}", path)
    return path


def _line(event: dict[str, Any]) -> str | None:
    kind = event.get("type")
    ts = event["ts"]
    if kind == "transcript" and event.get("final", True):
        return f"[{_clock(ts)}] {SPEAKERS.get(event.get('role'), event.get('role'))}: {event.get('text', '').strip()}"
    if kind == "tool":
        return f"[{_clock(ts)}]   (tool) {event.get('name')} {_compact(event.get('args'))} -> {_compact(event.get('result'))}"
    if kind == "session":
        status = event.get("status")
        sms = event.get("channel") == "sms"
        if status == "ended":
            secs = round(ts - _started.get(event["session_id"], ts))
            reason = f", {event['reason']}" if event.get("reason") else ""
            return f"[{_clock(ts)}] -- {'conversation' if sms else 'call'} ended ({secs // 60}m {secs % 60:02d}s{reason})"
        if status == "live" and sms:
            return f"[{_clock(ts)}] -- " + ("patient texted the clinic" if event.get("direction") == "inbound"
                                            else "texting the patient")
        if status == "ringing" and event.get("direction") == "inbound":
            return f"[{_clock(ts)}] -- incoming call from the patient"
        if status == "ringing" and event.get("callback"):
            return f"[{_clock(ts)}] -- calling the patient back (attempt {event.get('attempt')} of {event.get('of')})"
        return f"[{_clock(ts)}] -- {STATUS.get(status, status)}"
    if kind == "summary":
        return "-" * 60 + "\nPost-call note\n" + str(event.get("note", "")).strip()
    return None


def _on_event(event: dict[str, Any]) -> None:
    sid = event.get("session_id")
    kind = event.get("type")
    if not sid or kind not in ("session", "transcript", "tool", "summary"):
        return
    if kind == "session":
        last = _status.get(sid)
        if last == "ended" or last == event.get("status"):
            return  # SIP gateway and pipeline both report live/ended
        _status[sid] = event.get("status") or ""
    line = _line(event)
    if line is None:
        return
    path = _files.get(sid) or _open(event)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if kind == "summary":
        _files.pop(sid, None)
        _started.pop(sid, None)
        _status.pop(sid, None)


def install() -> None:
    bus.add_listener(_on_event)
    logger.info("Call logs -> {}", LOG_DIR)
