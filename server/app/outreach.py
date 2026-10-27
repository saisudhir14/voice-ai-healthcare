"""Retry policy for missed-treatment outreach: log every failed attempt on the worklist row and
call the patient back, up to MAX_CALL_ATTEMPTS, then hand the row to the reschedule lead (AA)."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from loguru import logger

from . import bus, clinic

CALLBACK_DELAY_SECS = float(os.getenv("CALLBACK_DELAY_SECS", "60"))
MAX_CALL_ATTEMPTS = int(os.getenv("MAX_CALL_ATTEMPTS", "3"))
DIAL_WAIT_SECS = float(os.getenv("CALLBACK_DIAL_WAIT_SECS", "600"))
# SIP end reasons for an outbound call the patient never picked up.
RETRY_REASONS = {"no_answer", "busy", "unavailable", "timeout"}

Dialer = Callable[[str, str], Awaitable[None]]

_dialer: Dialer | None = None
_ready: Callable[[str], bool] = lambda _pid: True
_pending: dict[str, asyncio.Task] = {}
callback_sessions: dict[str, int] = {}  # session_id -> attempt number


def set_dialer(dial: Dialer, ready: Callable[[str], bool]) -> None:
    global _dialer, _ready
    _dialer, _ready = dial, ready


def cancel(patient_id: str | None = None) -> None:
    for pid in [patient_id] if patient_id else list(_pending):
        task = _pending.pop(pid, None)
        if task:
            task.cancel()


def record_failed_attempt(
    patient_id: str, session_id: str | None, result: str, *, last_topic: str | None = None, can_redial: bool = True
) -> dict[str, Any]:
    """Write the attempt to the worklist row and schedule the next call if any are left."""
    patient = clinic.get_patient(patient_id)
    if not patient:
        return {"ok": False, "error": "unknown_patient"}
    runtime = patient["runtime"]
    attempt = len(runtime.get("contact_log") or []) + 1
    closed = bool(runtime.get("outcome_summary"))
    eligible = can_redial and not closed and _dialer is not None
    redial = eligible and attempt < MAX_CALL_ATTEMPTS
    next_attempt = None
    if redial:
        at = datetime.now(timezone.utc) + timedelta(seconds=CALLBACK_DELAY_SECS)
        next_attempt = {"at": at.isoformat(timespec="seconds"), "attempt": attempt + 1, "of": MAX_CALL_ATTEMPTS}
    exhausted = eligible and not redial
    chart = clinic.apply_event(
        patient_id,
        "record_contact_attempt",
        {"session_id": session_id, "result": result, "last_topic": last_topic,
         "next_attempt": next_attempt, "exhausted": exhausted},
    )
    if redial:
        cancel(patient_id)
        _pending[patient_id] = asyncio.create_task(_call_back(patient_id, attempt + 1))
    outcome = {"ok": True, "attempt": attempt, "of": MAX_CALL_ATTEMPTS, "result": result,
               "callback_at": clinic.local_clock(next_attempt["at"]) if next_attempt else None,
               "handed_to_aa": exhausted}
    logger.info("Outreach {} attempt {}/{}: {} -> {}", patient_id, attempt, MAX_CALL_ATTEMPTS, result,
                f"callback in {CALLBACK_DELAY_SECS:.0f}s" if redial else "AA escalation" if exhausted else "no redial")
    if session_id:
        bus.publish({"session_id": session_id, "type": "tool", "name": "record_contact_attempt",
                     "args": {"result": result}, "result": outcome})
    bus.publish({"session_id": session_id, "type": "chart", "patient": chart})
    return outcome


async def _call_back(patient_id: str, attempt: int) -> None:
    """Our phone line being down isn't the patient's missed attempt: wait for it (and for any call still
    tearing down) before dialing, and only give up after DIAL_WAIT_SECS."""
    try:
        await asyncio.sleep(CALLBACK_DELAY_SECS)
        waited = 0.0
        while not _ready(patient_id) and waited < DIAL_WAIT_SECS:
            if waited == 0:
                logger.info("Outreach {}: callback due, waiting for the phone line", patient_id)
            await asyncio.sleep(2)
            waited += 2
    except asyncio.CancelledError:
        return
    if _pending.get(patient_id) is asyncio.current_task():
        _pending.pop(patient_id, None)
    session_id = "sip-" + uuid.uuid4().hex[:12]
    callback_sessions[session_id] = attempt
    logger.info("Outreach {}: calling back (attempt {}/{}, session {})", patient_id, attempt, MAX_CALL_ATTEMPTS, session_id)
    try:
        await _dialer(patient_id, session_id)
    except Exception as exc:
        logger.warning("Outreach {}: callback could not dial: {}", patient_id, exc)
        callback_sessions.pop(session_id, None)
        record_failed_attempt(patient_id, None, "phone_offline")
