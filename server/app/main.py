"""Village Line API: synthetic chart, live events (SSE), Linphone SIP gateway (calls + SMS), WebRTC, chat."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from pipecat.runner.utils import parse_telephony_websocket
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from . import bot, bus, call_log, clinic, outreach
from .sip import SipGateway

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist" / "village-line" / "browser"
API_PORT = int(os.environ.get("API_PORT", "7860"))
SIP_PORT = int(os.environ.get("SIP_PORT", "5060"))
DEFAULT_PATIENT = "james-cole"

webrtc_handler = SmallWebRTCRequestHandler(ice_servers=[IceServer(urls="stun:stun.l.google.com:19302")])
_last_patient = {"id": os.environ.get("INBOUND_PATIENT") or DEFAULT_PATIENT}
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _on_sip_event(event: dict) -> None:
    event = dict(event)
    sid = event.get("session_id", "")
    if event.get("type") == "session" and sid in outreach.callback_sessions:
        event.update(callback=True, attempt=outreach.callback_sessions[sid], of=outreach.MAX_CALL_ATTEMPTS)
    if event.get("type") == "session" and event.get("status") == "ended":
        session = bot.sessions.get(sid)
        if session and not session.ended:
            return  # the pipeline publishes its own "ended" once it stops
        bus.publish(event)
        if not session and event.get("direction") == "outbound" and event.get("reason") in outreach.RETRY_REASONS:
            outreach.record_failed_attempt(event["patient_id"], sid, event["reason"])
        return
    bus.publish(event)


def _can_dial(patient_id: str) -> bool:
    busy = any(c.patient_id == patient_id for c in sip_gateway.sessions.values()) or any(
        s.patient_id == patient_id and not s.ended for s in bot.sessions.values()
    )
    return not busy and bool(sip_gateway.status().get("registered"))


async def _dial(patient_id: str, session_id: str) -> None:
    if not sip_gateway.status().get("registered"):
        raise RuntimeError("not_registered")
    await sip_gateway.call(patient_id, session_id)


async def _ready(session: bot.Session) -> None:
    for _ in range(20):
        if session.worker:
            return
        await asyncio.sleep(0.1)


def _start_sms(user: str, patient_id: str, direction: str) -> bot.Session:
    """One SMS thread per Linphone user; Maya's replies go back as SIP MESSAGEs."""
    session = bot.Session(session_id="sms-" + uuid.uuid4().hex[:12], patient_id=patient_id, channel="sms",
                          direction=direction)

    def deliver(text: str) -> None:
        if not sip_gateway.send_message(user, text):
            logger.warning("SMS to {} not sent (phone not registered)", user)

    session.outbox = deliver
    bot.sessions[session.session_id] = session
    _sms_threads[user] = session
    _spawn(bot.run_chat(session))
    return session


async def _on_sip_message(user: str, text: str) -> None:
    session = _sms_threads.get(user)
    if session is None or session.ended:
        session = _start_sms(user, _last_patient["id"], "inbound")
        logger.info("SMS thread {} opened by {} for {}", session.session_id, user, session.patient_id)
    await _ready(session)
    await bot.send_text(session, text)


_sms_threads: dict[str, bot.Session] = {}
sip_gateway = SipGateway(
    host_ip=os.environ.get("SIP_HOST_IP") or None,
    sip_port=SIP_PORT,
    bot_ws_url=f"ws://127.0.0.1:{API_PORT}/ws",
    on_event=_on_sip_event,
    inbound_patient=lambda: _last_patient["id"],
    on_message=_on_sip_message,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    clinic.load()
    call_log.install()
    outreach.set_dialer(_dial, _can_dial)
    try:
        await sip_gateway.start()
    except OSError as exc:
        logger.error("SIP gateway could not bind udp/{} ({}). Close other softphones.", SIP_PORT, exc)
    yield
    await sip_gateway.stop()
    await webrtc_handler.close()


app = FastAPI(title="Village Line", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class OfferBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    sdp: str
    type: str
    pc_id: str | None = Field(default=None, alias="pcId")
    restart_pc: bool | None = Field(default=None, alias="restartPc")
    request_data: dict | None = Field(default=None, alias="requestData")


class PatchBody(BaseModel):
    pc_id: str
    candidates: list[dict]


class PatientBody(BaseModel):
    patient_id: str


class HangupBody(BaseModel):
    session_id: str


class MessageBody(BaseModel):
    text: str


def _patient_or_400(patient_id: str) -> str:
    if not clinic.get_patient(patient_id):
        raise HTTPException(400, "Unknown synthetic patient")
    _last_patient["id"] = patient_id
    return patient_id


@app.get("/api/health")
async def health():
    voice = bot.DEEPGRAM_VOICE if bot.TTS_PROVIDER == "deepgram" else bot.KOKORO_VOICE
    return {"ok": True, "agent": "maya", "use_case": "missed_treatment_recovery", "sessions": len(bot.sessions),
            "tts": bot.TTS_PROVIDER, "voice": voice}


@app.get("/api/clinic")
async def clinic_index():
    return {"facility": clinic.facility(), "patients": clinic.list_patients()}


@app.get("/api/clinic/{patient_id}")
async def clinic_patient(patient_id: str):
    patient = clinic.get_patient(patient_id)
    if not patient:
        raise HTTPException(404, "Unknown synthetic patient")
    return patient


@app.post("/api/clinic/{patient_id}/reset")
async def reset_patient(patient_id: str):
    outreach.cancel(patient_id)
    try:
        patient = clinic.reset_patient(patient_id)
    except KeyError:
        raise HTTPException(404, "Unknown synthetic patient")
    _last_patient["id"] = patient_id
    return patient


@app.post("/api/clinic/reset")
async def reset_all():
    outreach.cancel()
    clinic.reset_all()
    return {"ok": True}


@app.get("/api/events")
async def events():
    async def stream():
        yield "retry: 1500\n\n"
        async for event in bus.subscribe():
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/client-log")
async def client_log(body: dict):
    logger.error("UI {}: {} {}\n{}", body.get("kind"), body.get("message"), body.get("url"), body.get("stack"))
    return {"ok": True}


@app.get("/api/sip/status")
async def sip_status():
    return sip_gateway.status()


@app.post("/api/call")
async def place_call(body: PatientBody):
    patient_id = _patient_or_400(body.patient_id)
    if not sip_gateway.status().get("registered"):
        raise HTTPException(409, "Linphone isn't connected. Add the SIP account on your phone first.")
    session_id = "sip-" + uuid.uuid4().hex[:12]
    outreach.cancel(patient_id)
    try:
        await sip_gateway.call(patient_id, session_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"session_id": session_id}


@app.post("/api/chat/start")
async def chat_start(body: PatientBody):
    patient_id = _patient_or_400(body.patient_id)
    session = bot.Session(session_id="chat-" + uuid.uuid4().hex[:12], patient_id=patient_id, channel="chat")
    bot.sessions[session.session_id] = session
    _spawn(bot.run_chat(session))
    return {"session_id": session.session_id}


@app.post("/api/sms/start")
async def sms_start(body: PatientBody):
    patient_id = _patient_or_400(body.patient_id)
    user = sip_gateway.status().get("user")
    if not user:
        raise HTTPException(409, "Linphone isn't connected. Add the SIP account on your phone first.")
    previous = _sms_threads.get(user)
    if previous and not previous.ended:
        await bot.hangup(previous)
    return {"session_id": _start_sms(user, patient_id, "outbound").session_id}


@app.post("/api/session/{session_id}/message")
async def session_message(session_id: str, body: MessageBody):
    session = bot.sessions.get(session_id)
    if not session or session.ended:
        raise HTTPException(404, "No live session with that id")
    await _ready(session)
    await bot.send_text(session, body.text)
    return {"ok": True}


@app.post("/api/hangup")
async def hangup(body: HangupBody):
    session = bot.sessions.get(body.session_id)
    if body.session_id.startswith("sip-"):
        await sip_gateway.hangup(body.session_id)
    if session:
        await bot.hangup(session)
    return {"ok": True}


@app.post("/api/offer")
async def offer(body: OfferBody):
    data = body.request_data or {}
    patient_id = _patient_or_400(data.get("patient_id") or DEFAULT_PATIENT)
    session_id = data.get("session_id") or "web-" + uuid.uuid4().hex[:12]

    async def on_connection(connection: SmallWebRTCConnection):
        session = bot.Session(session_id=session_id, patient_id=patient_id, channel="browser")
        bot.sessions[session_id] = session
        _spawn(bot.run_webrtc_bot(connection, session))

    request = SmallWebRTCRequest(
        sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc, request_data=data
    )
    return await webrtc_handler.handle_web_request(request, on_connection)


@app.patch("/api/offer")
async def offer_patch(body: PatchBody):
    await webrtc_handler.handle_patch_request(
        SmallWebRTCPatchRequest(
            pc_id=body.pc_id,
            candidates=[
                IceCandidate(
                    candidate=c.get("candidate", ""),
                    sdp_mid=c.get("sdp_mid") or c.get("sdpMid") or "0",
                    sdp_mline_index=c.get("sdp_mline_index") or c.get("sdpMLineIndex") or 0,
                )
                for c in body.candidates
            ],
        )
    )
    return {"ok": True}


@app.websocket("/ws")
async def phone_ws(websocket: WebSocket):
    """Twilio-Media-Streams-shaped audio from app.sip (or, later, a real carrier)."""
    await websocket.accept()
    _, call_data = await parse_telephony_websocket(websocket)
    params = call_data.get("body") or {}
    patient_id = params.get("patient_id") if clinic.get_patient(params.get("patient_id") or "") else DEFAULT_PATIENT
    session = bot.Session(
        session_id=params.get("session_id") or "sip-" + uuid.uuid4().hex[:12],
        patient_id=patient_id,
        channel=params.get("channel") or "linphone",
        direction=params.get("direction") or "outbound",
    )
    logger.info("Phone stream {} for {} ({})", call_data.get("stream_id"), patient_id, session.direction)
    await bot.run_phone_bot(websocket, call_data["stream_id"], call_data.get("call_id"), session)


if WEB_DIST.exists():
    # Angular emits scripts and styles at the browser output root. StaticFiles
    # serves the dashboard index and assets after the API/WebSocket routes.
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="dashboard")
