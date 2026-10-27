"""In-memory ScheduleHub + CWOW stand-in. Synthetic only. No PHI."""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "clinic.json"

WORKLIST_NAME = "PATIENT_RESCHEDULE_WORKLIST"
AI_SCHEDULER = "Maya (AI teammate)"
SEVERITIES = ("none", "mild", "concerning", "emergent")
LANGUAGES = ("en", "es")
UNREACHED_OUTCOMES = {"UNREACHED"}

_SEVERITY_ACTION = {
    "none": "Proceed with makeup booking.",
    "mild": "Book makeup run; RN FYI at chairside.",
    "concerning": "Escalate to RN before or alongside booking.",
    "emergent": "Stop booking. Advise 911 and flag_emergency.",
}

# Keyword hints to refine the broad barrier enum into a specific MTX code.
_DETAIL_HINTS: dict[str, list[tuple[tuple[str, ...], str]]] = {
    "transport": [
        (("snow", "ice", "storm", "flood", "weather", "rain"), "TR-WEATHER"),
        (("broke down", "breakdown", "flat tire", "car trouble", "won't start", "battery"), "TR-VEHICLE"),
        (("afford", "cost", "gas money", "money", "fare"), "TR-COST"),
        (("no ride", "nobody", "no one", "no way to get"), "TR-NORIDE"),
        (("van", "broker", "pickup", "never showed", "didn't show", "late"), "TR-BROKER"),
    ],
    "life": [
        (("mom", "mother", "dad", "father", "grand", "caregiv", "babysit", "kids", "child", "aide"), "SC-CAREGIVING"),
        (("work", "job", "shift", "boss"), "SC-WORK"),
        (("travel", "out of town", "trip", "vacation"), "SC-TRAVEL"),
    ],
    "illness": [
        (("fistula", "graft", "catheter", "access", "clot", "no thrill"), "MD-ACCESS"),
        (("emergency room", " er ", "er visit", "urgent care"), "MD-ER"),
        (("appointment", "doctor", "procedure", "surgery"), "MD-OUTPT"),
    ],
    "hospital": [
        (("emergency room", " er ", "er visit", "sent home"), "MD-ER"),
    ],
    "declined": [
        (("tired", "fatigue", "worn", "need a day", "needed a day", "break", "sick of", "burn"), "PR-FATIGUE"),
    ],
}

_lock = Lock()
_state: dict[str, Any] = {}
_pristine: dict[str, Any] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_clock(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%-I:%M %p")


def load() -> None:
    raw = json.loads(DATA_PATH.read_text())
    with _lock:
        _pristine.clear()
        _pristine.update(copy.deepcopy(raw))
        _state.clear()
        _state.update(copy.deepcopy(raw))
        _state["sessions"] = {}
        for patient in _state["patients"]:
            _reset_runtime(patient)


def _reset_runtime(patient: dict[str, Any]) -> None:
    patient["runtime"] = {
        "identity_verified": False,
        "barrier": None,
        "barrier_detail": None,
        "mtx": None,
        "offered_slots": [],
        "booked_slot": None,
        "transport_status": patient["transportation"]["status"],
        "outcome": "OPEN",
        "outcome_summary": None,
        "escalations": [],
        "emergency": False,
        "outreach_attempts": 0,
        "outreach_notes": [],
        "call_attempts": 0,
        "call_in_progress": False,
        "contact_log": [],
        "next_attempt": None,
        "symptom_screen": None,
        "language": patient.get("preferred_language", "en"),
        "confirmation_sent": False,
        "confirmation": None,
        "last_event_at": None,
    }


def _release_slot(slot_id: str | None) -> None:
    """Mark a booked chair available again (restore from pristine copy)."""
    if not slot_id:
        return
    original = next((s for s in _pristine.get("open_chairs", []) if s["id"] == slot_id), None)
    for i, slot in enumerate(_state.get("open_chairs", [])):
        if slot["id"] == slot_id:
            _state["open_chairs"][i] = copy.deepcopy(original) if original else {**slot, "available": True}
            return


def _restore_patient(patient_id: str) -> dict[str, Any]:
    for i, patient in enumerate(_state.get("patients", [])):
        if patient["id"] != patient_id:
            continue
        booked = (patient.get("runtime") or {}).get("booked_slot")
        if booked:
            _release_slot(booked.get("id"))
        original = next((p for p in _pristine["patients"] if p["id"] == patient_id), None)
        fresh = copy.deepcopy(original) if original else patient
        _reset_runtime(fresh)
        _state["patients"][i] = fresh
        return fresh
    raise KeyError(patient_id)


def reset_patient(patient_id: str) -> dict[str, Any]:
    with _lock:
        patient = _restore_patient(patient_id)
        return public_patient(patient)


def reset_all() -> list[dict[str, Any]]:
    """Restore every patient and every open chair to the JSON baseline (keeps sessions)."""
    with _lock:
        _state["open_chairs"] = copy.deepcopy(_pristine["open_chairs"])
        _state["patients"] = copy.deepcopy(_pristine["patients"])
        for patient in _state["patients"]:
            _reset_runtime(patient)
        return [public_patient(p) for p in _state["patients"]]


def facility() -> dict[str, Any]:
    with _lock:
        return copy.deepcopy(_state["facility"])


def list_patients() -> list[dict[str, Any]]:
    with _lock:
        return [public_patient(p) for p in _state["patients"]]


def get_patient(patient_id: str) -> dict[str, Any] | None:
    with _lock:
        patient = _find(patient_id)
        return public_patient(patient) if patient else None


def snapshot_for_prompt(patient_id: str) -> dict[str, Any]:
    with _lock:
        patient = _find(patient_id)
        if not patient:
            raise KeyError(patient_id)
        snap_patient = copy.deepcopy(patient)
        snap_patient["worklist_row"] = _worklist_row(patient)
        snap_patient["miss_history"] = _labelled_history(patient)
        return {
            "facility": copy.deepcopy(_state["facility"]),
            "sister_facilities": copy.deepcopy(_state.get("sister_facilities", [])),
            "patient": snap_patient,
            "open_chairs": [copy.deepcopy(s) for s in _state["open_chairs"] if s["available"]],
            "mtx_reason_codes": copy.deepcopy(_state.get("mtx_reason_codes", [])),
            "barrier_code_map": copy.deepcopy(_state.get("barrier_code_map", {})),
        }


def open_chairs() -> list[dict[str, Any]]:
    with _lock:
        return [copy.deepcopy(s) for s in _state["open_chairs"] if s["available"]]


def apply_event(patient_id: str, event: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    with _lock:
        patient = _find(patient_id)
        if not patient:
            raise KeyError(patient_id)
        runtime = patient["runtime"]
        runtime["last_event_at"] = _now_iso()
        runtime["outreach_attempts"] += 1 if event in {"log_barrier", "write_call_outcome"} else 0
        if not runtime["call_in_progress"]:
            runtime["call_in_progress"] = True
            runtime["call_attempts"] += 1

        if event == "start_call":
            runtime["identity_verified"] = False
            runtime["next_attempt"] = None
        elif event == "verify_identity":
            runtime["identity_verified"] = bool(payload.get("verified", True))
        elif event == "check_open_chairs":
            runtime["offered_slots"] = [
                copy.deepcopy(s) for s in _state["open_chairs"] if s["available"]
            ]
        elif event == "log_barrier":
            barrier = payload.get("barrier")
            detail = payload.get("detail")
            runtime["barrier"] = barrier
            runtime["barrier_detail"] = detail
            patient["missed"]["reason_code"] = barrier or "unknown"
            mtx = _resolve_mtx(barrier, detail, payload.get("reason_code"))
            runtime["mtx"] = mtx
            patient["missed"]["mtx_code"] = mtx["code"] if mtx else None
            runtime["outreach_notes"].append({"at": _now_iso(), "text": detail or barrier})
        elif event == "book_makeup_treatment":
            slot = _slot(payload.get("slot_id"))
            if not slot:
                raise ValueError("unknown_slot")
            previous = runtime.get("booked_slot")
            if previous and previous.get("id") == slot["id"]:
                return public_patient(patient)
            if not slot["available"]:
                raise ValueError("slot_taken")
            if previous:
                _release_slot(previous.get("id"))
            slot["available"] = False
            runtime["booked_slot"] = copy.deepcopy(slot)
            runtime["outcome"] = "RESCHEDULED"
            runtime["confirmation_sent"] = False
            runtime["confirmation"] = None
            patient["missed"]["status"] = "rescheduled"
        elif event == "confirm_transport":
            runtime["transport_status"] = payload.get("status", "confirmed")
            patient["transportation"]["status"] = runtime["transport_status"]
        elif event == "request_ride_assist":
            runtime["transport_status"] = "msw_tasked"
            patient["transportation"]["status"] = "msw_tasked"
            runtime["escalations"].append(
                {
                    "owner": "MSW",
                    "name": _state["facility"]["social_worker"],
                    "reason": payload.get("reason") or "Arrange makeup-day ride",
                    "urgency": "urgent",
                    "at": _now_iso(),
                }
            )
        elif event == "escalate_care_team":
            runtime["escalations"].append(
                {
                    "owner": payload.get("owner"),
                    "name": _owner_name(payload.get("owner")),
                    "reason": payload.get("reason"),
                    "urgency": payload.get("urgency") or "routine",
                    "at": _now_iso(),
                }
            )
            if payload.get("owner") in {"RN", "NEPH"} and runtime["outcome"] == "OPEN":
                runtime["outcome"] = "CLINICAL_ESCALATION"
        elif event == "flag_emergency":
            runtime["emergency"] = True
            runtime["outcome"] = "CLINICAL_ESCALATION"
            runtime["escalations"].append(
                {
                    "owner": "RN",
                    "name": _state["facility"]["charge_nurse"],
                    "reason": payload.get("reason") or "Emergent symptoms on outreach call",
                    "urgency": "stat",
                    "at": _now_iso(),
                }
            )
        elif event == "screen_symptoms":
            severity = (payload.get("severity") or "none").lower()
            if severity not in SEVERITIES:
                raise ValueError("invalid_severity")
            symptoms = payload.get("symptoms") or []
            if isinstance(symptoms, str):
                symptoms = [symptoms]
            runtime["symptom_screen"] = {
                "symptoms": [str(s) for s in symptoms],
                "severity": severity,
                "fluid_note": payload.get("fluid_note"),
                "recommended_action": _SEVERITY_ACTION[severity],
                "at": _now_iso(),
            }
        elif event == "set_language":
            language = (payload.get("language") or "").lower()
            if language not in LANGUAGES:
                raise ValueError("unsupported_language")
            runtime["language"] = language
        elif event == "send_confirmation":
            channel = (payload.get("channel") or "sms").lower()
            if channel != "sms":
                raise ValueError("unsupported_channel")
            if not runtime.get("booked_slot"):
                raise ValueError("no_booked_slot")
            consent = patient.get("consent_status")
            if isinstance(consent, dict) and consent.get("sms_outreach") is False:
                raise ValueError("no_sms_consent")
            runtime["confirmation_sent"] = True
            runtime["confirmation"] = {
                "channel": "sms",
                "to": patient["phone"],
                "language": runtime.get("language", "en"),
                "text": _sms_text(patient),
                "at": _now_iso(),
                "status": "delivered (simulated)",
            }
        elif event == "write_call_outcome":
            runtime["outcome"] = payload.get("outcome") or runtime["outcome"]
            runtime["outcome_summary"] = payload.get("summary")
            runtime["outreach_notes"].append({"at": _now_iso(), "text": payload.get("summary")})
            if runtime["outcome"] in UNREACHED_OUTCOMES and not runtime.get("mtx"):
                runtime["mtx"] = _mtx_entry("UR-UNREACH")
                patient["missed"]["mtx_code"] = "UR-UNREACH"
            runtime["call_in_progress"] = False
        elif event == "record_contact_attempt":
            _record_contact_attempt(patient, payload)

        return public_patient(patient)


_ATTEMPT_LABELS = {
    "no_response": "answered, then went silent",
    "no_answer": "no answer",
    "busy": "line busy",
    "unavailable": "phone unavailable",
    "timeout": "phone unreachable",
    "phone_offline": "phone offline, could not dial",
}


def _record_contact_attempt(patient: dict[str, Any], payload: dict[str, Any]) -> None:
    runtime = patient["runtime"]
    result = payload.get("result") or "no_response"
    attempt = len(runtime["contact_log"]) + 1
    entry = {
        "attempt": attempt,
        "at": _now_iso(),
        "session_id": payload.get("session_id"),
        "result": result,
        "label": _ATTEMPT_LABELS.get(result, result.replace("_", " ")),
        "last_topic": payload.get("last_topic"),
    }
    runtime["contact_log"].append(entry)
    runtime["call_in_progress"] = False
    runtime["next_attempt"] = payload.get("next_attempt")
    plan = f"; calling back at {local_clock(payload['next_attempt']['at'])}" if payload.get("next_attempt") else ""
    runtime["outreach_notes"].append({"at": entry["at"], "text": f"Call attempt {attempt}: {entry['label']}{plan}"})
    if not payload.get("exhausted"):
        return
    tries = f"{attempt} call attempt{'s' if attempt > 1 else ''}"
    reached = bool(runtime.get("barrier") or runtime.get("identity_verified"))
    runtime["escalations"].append(
        {
            "owner": "AA",
            "name": _owner_name("AA"),
            "reason": f"{tries} without finishing the makeup booking"
            + (" (patient reached earlier, line went quiet)" if reached else " (patient not reached)")
            + ". Call the patient, then the emergency contact.",
            "urgency": "urgent",
            "at": entry["at"],
        }
    )
    if not reached and runtime["outcome"] == "OPEN":
        runtime["outcome"] = "UNREACHED"
        runtime["outcome_summary"] = f"Not reached after {tries}; handed to the reschedule lead."
        runtime["outreach_notes"].append({"at": entry["at"], "text": runtime["outcome_summary"]})
        if not runtime.get("mtx"):
            runtime["mtx"] = _mtx_entry("UR-UNREACH")
            patient["missed"]["mtx_code"] = "UR-UNREACH"


def public_patient(patient: dict[str, Any]) -> dict[str, Any]:
    out = {k: copy.deepcopy(v) for k, v in patient.items() if k != "runtime"}
    out["miss_history"] = _labelled_history(patient)
    out["runtime"] = copy.deepcopy(patient["runtime"])
    out["worklist_row"] = _worklist_row(patient)
    out["facility"] = copy.deepcopy(_state["facility"])
    out["open_chairs"] = [copy.deepcopy(s) for s in _state["open_chairs"] if s["available"]]
    return out


# --- CWOW worklist / MTX helpers -------------------------------------------------


def _mtx_entry(code: str | None) -> dict[str, Any] | None:
    if not code:
        return None
    for item in _state.get("mtx_reason_codes", []):
        if item["code"] == code:
            return copy.deepcopy(item)
    return None


def _resolve_mtx(barrier: str | None, detail: str | None, explicit: str | None) -> dict[str, Any] | None:
    if explicit:
        entry = _mtx_entry(explicit)
        if entry:
            return entry
    if not barrier:
        return None
    text = f" {(detail or '').lower()} "
    for keywords, code in _DETAIL_HINTS.get(barrier, []):
        if any(k in text for k in keywords):
            entry = _mtx_entry(code)
            if entry:
                return entry
    return _mtx_entry(_state.get("barrier_code_map", {}).get(barrier)) or _mtx_entry("OT-OTHER")


def _labelled_history(patient: dict[str, Any]) -> list[Any]:
    out = []
    for item in patient.get("miss_history", []):
        if isinstance(item, dict):
            row = copy.deepcopy(item)
            entry = _mtx_entry(row.get("reason_code"))
            row["reason_label"] = entry["label"] if entry else row.get("reason_code")
            out.append(row)
        else:
            out.append(item)
    return out


def _worklist_row(patient: dict[str, Any]) -> dict[str, Any]:
    runtime = patient["runtime"]
    booked = runtime.get("booked_slot")
    closed = bool(booked) or runtime.get("outcome_summary") is not None
    if closed:
        status = "Completed"
    elif runtime.get("last_event_at"):
        status = "In Progress"
    else:
        status = "Open"
    mtx = runtime.get("mtx")
    prior = (patient.get("worklist") or {}).get("prior_attempts", 0)
    missed = patient["missed"]
    return {
        "worklist": WORKLIST_NAME,
        "worklist_label": "Reschedule and Missed Treatment Worklist",
        "row_id": f"WL-{patient['mpi']}-{missed.get('date_iso', missed.get('date', ''))}",
        "status": status,
        "reason_code": mtx["label"] if mtx else "Pending - not yet captured",
        "reason_code_id": mtx["code"] if mtx else None,
        "reason_category": mtx["category"] if mtx else None,
        "recommended_action": mtx["next_action"] if mtx else None,
        "action_owner": mtx["owner"] if mtx else None,
        "attempts": prior + runtime.get("call_attempts", 0),
        "last_attempt_at": runtime.get("last_event_at"),
        "opened_at": (patient.get("worklist") or {}).get("opened_at"),
        "scheduled_by": AI_SCHEDULER if status == "Completed" else None,
        "outcome": runtime.get("outcome"),
        "missed_run": {
            "date": missed.get("date_iso", missed.get("date")),
            "weekday": missed.get("weekday"),
            "time": missed.get("time"),
            "chair": missed.get("chair"),
        },
        "linked_run": _linked_run(booked),
        "confirmation_sent": runtime.get("confirmation_sent", False),
        "next_attempt": runtime.get("next_attempt"),
        "contact_log": copy.deepcopy(runtime.get("contact_log") or []),
    }


def _linked_run(slot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not slot:
        return None
    return {
        "slot_id": slot["id"],
        "facility": slot.get("facility", _state["facility"]["name"]),
        "date": slot.get("date"),
        "weekday": slot.get("weekday"),
        "time": slot.get("time"),
        "shift": slot.get("shift"),
        "chair": slot.get("chair"),
        "pod": slot.get("pod"),
        "spoken": slot.get("spoken"),
    }


_ES_WEEKDAYS = {
    "Monday": "lunes",
    "Tuesday": "martes",
    "Wednesday": "miércoles",
    "Thursday": "jueves",
    "Friday": "viernes",
    "Saturday": "sábado",
    "Sunday": "domingo",
}
_ES_MONTHS = ["ene.", "feb.", "mar.", "abr.", "may.", "jun.", "jul.", "ago.", "sep.", "oct.", "nov.", "dic."]


def _sms_text(patient: dict[str, Any]) -> str:
    runtime = patient["runtime"]
    slot = runtime["booked_slot"]
    home = _state["facility"]
    fac_name = slot.get("facility") or home["name"]
    address = slot.get("facility_address") or home["address"]
    phone = home["phone"]
    name = patient["preferred_name"]
    try:
        day = date.fromisoformat(slot["date"]) if slot.get("date") else None
    except ValueError:
        day = None
    if runtime.get("language") == "es":
        when = (
            f"{_ES_WEEKDAYS.get(slot['weekday'], slot['weekday'])} {day.day} de {_ES_MONTHS[day.month - 1]}"
            if day
            else _ES_WEEKDAYS.get(slot["weekday"], slot["weekday"])
        )
        chair = slot["chair"].replace("Chair", "Sillón")
        article = "a la" if str(slot["time"]).startswith("1:") else "a las"
        return (
            f"{home['name']}: Hola {name}, su tratamiento de reposición es el {when} {article} {slot['time']}, "
            f"{chair}, {fac_name}, {address}. Llegue 15 min antes. Preguntas: {phone}. "
            f"Responda C para confirmar. STOP para no recibir mensajes."
        )
    when = f"{slot['weekday'][:3]}, {day.strftime('%b')} {day.day}" if day else slot["weekday"]
    return (
        f"{home['name']}: Hi {name}, your makeup treatment is set for {when} at {slot['time']}, "
        f"{slot['chair']} ({slot.get('pod', '')}), {fac_name}, {address}. Please arrive 15 min early. "
        f"Questions: {phone}. Reply C to confirm, STOP to opt out."
    )


def _find(patient_id: str) -> dict[str, Any] | None:
    for patient in _state.get("patients", []):
        if patient["id"] == patient_id:
            return patient
    return None


def _slot(slot_id: str | None) -> dict[str, Any] | None:
    if not slot_id:
        return None
    for slot in _state["open_chairs"]:
        if slot["id"] == slot_id:
            return slot
    return None


def _owner_name(owner: str | None) -> str:
    mapping = {
        "RN": _state["facility"]["charge_nurse"],
        "NEPH": _state["facility"]["nephrologist"],
        "MSW": _state["facility"]["social_worker"],
        "FA": _state["facility"]["facility_admin"],
        "AA": _state["facility"]["reschedule_lead"],
    }
    return mapping.get(owner or "", owner or "")


load()
