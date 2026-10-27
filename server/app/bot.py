"""Maya, the missed-treatment recovery agent, as Pipecat pipelines.

One brain (DaVita LLM API Service, Claude Sonnet 4.6) and one tool set, four channels:
Linphone phone call (SIP gateway → Twilio-style /ws), Linphone SMS (SIP MESSAGE), browser WebRTC, and typed chat.
Speech in: Silero VAD + MLX Whisper on the laptop. Speech out: Deepgram Aura-2 (when DEEPGRAM_API_KEY is set)
or Kokoro on device.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger
from starlette.websockets import WebSocket

from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    Frame,
    FunctionCallResultProperties,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.whisper.stt import MLXModel, WhisperSTTServiceMLX
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from . import bus, clinic, llm_gateway, outreach
from .llm_gateway import DaVitaGatewayLLMService
from .prompts import (
    AGENT_NAME,
    SUMMARY_PROMPT,
    build_system_prompt,
    no_response_line,
    opening_line,
    sms_opening_line,
    still_there_line,
)
from .speech import clean_for_text, normalize_for_speech

AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 48000  # Opus in Firefox wants 48 kHz
TTS_RATE = 24000
KOKORO_VOICE = os.getenv("KOKORO_VOICE", "af_heart")
KOKORO_SPEED = float(os.getenv("KOKORO_SPEED", "1.0"))
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_VOICE = os.getenv("DEEPGRAM_VOICE", "aura-2-helena-en")
DEEPGRAM_SPEED = float(os.getenv("DEEPGRAM_SPEED", "1.0"))
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "deepgram" if DEEPGRAM_API_KEY else "kokoro").lower()
ECHO_GATE = os.getenv("ECHO_GATE", "true").lower() == "true"
ECHO_TAIL_SECS = float(os.getenv("ECHO_TAIL_SECS", "1.0"))
# Browsers cancel echo themselves, so the caller can cut in there.
BROWSER_ECHO_GATE = os.getenv("BROWSER_ECHO_GATE", "false").lower() == "true"
# Silero must hear this much quiet before a pause counts. A breath is shorter.
PAUSE_SECS = 0.85
# Dead air after Maya stops talking before she checks in; after IDLE_MAX_NUDGES unanswered check-ins she wraps up.
IDLE_NUDGE_SECS = float(os.getenv("IDLE_NUDGE_SECS", "4.0"))
IDLE_MAX_NUDGES = int(os.getenv("IDLE_MAX_NUDGES", "2"))
# An SMS thread stays open this long after the last text; the next text after that starts a new conversation.
SMS_IDLE_SECS = float(os.getenv("SMS_IDLE_SECS", "1800"))

_NONWORD = re.compile(r"[^a-z0-9' ]+")
_SOUNDS = {"mm", "mmm", "hmm", "uh", "um", "mmhm", "mhm", "mhmm"}


def _is_phantom(text: str, last_agent: str) -> bool:
    """Drop mic noise and the agent's own last line coming back through the speaker."""
    cleaned = _NONWORD.sub("", text.lower()).strip()
    if not cleaned or cleaned in _SOUNDS:
        return True
    agent = _NONWORD.sub("", last_agent.lower())
    return bool(agent) and cleaned in agent and len(cleaned.split()) <= 4


@dataclass
class Session:
    session_id: str
    patient_id: str
    channel: str
    direction: str = "outbound"
    worker: PipelineWorker | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    ended: bool = False
    closing: bool = False
    end_reason: str = "completed"
    nudger: Any = None
    outbox: Callable[[str], Any] | None = None  # SMS: delivers each of Maya's replies to the phone


sessions: dict[str, Session] = {}


def _publish(session: Session, **event: Any) -> None:
    bus.publish({"session_id": session.session_id, **event})


def _say(session: Session, role: str, text: str, final: bool = True) -> None:
    text = text.strip()
    if not text:
        return
    if final:
        session.transcript.append({"role": role, "text": text})
        if role == "agent" and session.outbox:
            session.outbox(text)
    _publish(session, type="transcript", role=role, text=text, final=final)


# ---------------------------------------------------------------- tools


def _tools(session: Session, llm: DaVitaGatewayLLMService, end_call: Callable[[], Awaitable[None]]) -> list[FunctionSchema]:
    """Writes skip the second gateway round trip only when Maya's reply already handed the turn over (asked
    something). A reply like "Let me get that noted." plus a tool call needs a follow-up, or the line goes dead."""
    pid = session.patient_id

    def handler(name: str, run: Callable[[dict[str, Any]], dict[str, Any]], follow_up: bool = False):
        async def _h(params: FunctionCallParams):
            args = dict(params.arguments or {})
            try:
                result = run(args)
            except Exception as exc:
                logger.warning("tool {} failed: {}", name, exc)
                result = {"ok": False, "error": str(exc)}
            session.tools.append({"name": name, "args": args, "result": result})
            _publish(session, type="tool", name=name, args=args, result=result)
            _publish(session, type="chart", patient=clinic.get_patient(pid))
            run_llm = (
                name != "end_call"
                and "end_call" not in llm.turn_tools
                and (follow_up or not llm.asked_this_turn or result.get("ok") is False)
            )
            await params.result_callback(result, properties=FunctionCallResultProperties(run_llm=run_llm))
            if name == "end_call":
                await end_call()

        return _h

    def apply(event: str, payload: dict[str, Any]) -> dict[str, Any]:
        clinic.apply_event(pid, event, payload)
        return {"ok": True}

    def book(args: dict) -> dict:
        chart = clinic.apply_event(pid, "book_makeup_treatment", {"slot_id": args.get("slot_id")})
        slot = chart["runtime"]["booked_slot"]
        return {"ok": True, "say": f"{slot['chair']} {slot['spoken']} at {slot.get('facility', 'the clinic')}"}

    def confirmation(args: dict) -> dict:
        chart = clinic.apply_event(pid, "send_confirmation", {"channel": "sms"})
        return {"ok": True, "sent_to": "phone on file", "text": (chart["runtime"].get("confirmation") or {}).get("text")}

    def spec(name, description, properties, required, run, follow_up=False):
        return FunctionSchema(
            name=name,
            description=description,
            properties=properties,
            required=required,
            handler=handler(name, run, follow_up),
        )

    s = {"type": "string"}
    return [
        spec("verify_identity", "The person confirmed they are the patient (or said they are not).",
             {"verified": {"type": "boolean"}}, ["verified"],
             lambda a: apply("verify_identity", {"verified": a.get("verified", True) not in (False, "false")})),
        spec("log_barrier", "Write the missed-treatment reason into the CWOW worklist row.",
             {"barrier": {"type": "string", "enum": ["transport", "illness", "life", "declined", "hospital", "other"]},
              "detail": {"type": "string", "description": "Patient's reason in a few words"}},
             ["barrier", "detail"], lambda a: apply("log_barrier", a)),
        spec("screen_symptoms", "Record a brief symptom/fluid screen.",
             {"symptoms": {"type": "array", "items": s},
              "severity": {"type": "string", "enum": ["none", "mild", "concerning", "emergent"]},
              "fluid_note": s},
             ["symptoms", "severity"], lambda a: apply("screen_symptoms", a)),
        spec("book_makeup_treatment", "Hold a specific open chair after the patient accepts it.",
             {"slot_id": {"type": "string", "description": "One slot_id from the open chairs list"}}, ["slot_id"], book),
        spec("confirm_transport", "Record how the patient gets to the makeup chair.",
             {"status": {"type": "string", "enum": ["confirmed", "self", "family", "broker"]}}, ["status"],
             lambda a: apply("confirm_transport", a)),
        spec("request_ride_assist", "Task the social worker to arrange a ride for the makeup treatment.",
             {"reason": s}, ["reason"], lambda a: apply("request_ride_assist", a)),
        spec("send_confirmation", "Text the patient the makeup appointment details.", {}, [], confirmation),
        spec("escalate_care_team", "Create a task for RN, nephrologist, social work, facility admin or scheduler.",
             {"owner": {"type": "string", "enum": ["RN", "NEPH", "MSW", "FA", "AA"]}, "reason": s,
              "urgency": {"type": "string", "enum": ["routine", "urgent", "stat"]}},
             ["owner", "reason"], lambda a: apply("escalate_care_team", a)),
        spec("flag_emergency", "Chest pain, trouble breathing, or confusion. Stops booking, pages the charge nurse.",
             {"reason": s}, ["reason"], lambda a: apply("flag_emergency", a)),
        spec("write_call_outcome", "Close the worklist row.",
             {"outcome": {"type": "string", "enum": ["RESCHEDULED", "FORFEITED", "UNREACHED", "DIALYZED_ELSEWHERE", "CLINICAL_ESCALATION"]},
              "summary": s},
             ["outcome", "summary"], lambda a: apply("write_call_outcome", a)),
        spec("end_call", "Hang up after you have said goodbye in the same reply.", {}, [],
             lambda a: {"ok": True}),
    ]


# ---------------------------------------------------------------- processors / observers


class EchoGate(FrameProcessor):
    """Half-duplex input for phone calls: the caller's line is silenced while the bot is talking and for a
    short tail after, so a speakerphone/softphone without echo cancellation can't feed the bot its own voice."""

    def __init__(self, tail_secs: float = ECHO_TAIL_SECS):
        super().__init__()
        self._tail = tail_secs
        self._speaking = False
        self._open_at = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._speaking = False
            self._open_at = time.monotonic() + self._tail
        elif isinstance(frame, InputAudioRawFrame) and (self._speaking or time.monotonic() < self._open_at):
            frame.audio = bytes(len(frame.audio))
        await self.push_frame(frame, direction)


class _Heard:
    last_agent: str = ""


class RememberAgent(FrameProcessor):
    """Keep the line she just said, so an echo of it can be ignored."""

    def __init__(self, heard: _Heard):
        super().__init__()
        self._heard = heard
        self._buf: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame) and frame.text:
            self._heard.last_agent = frame.text
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._buf = []
        elif isinstance(frame, LLMTextFrame):
            self._buf.append(frame.text or "")
        elif isinstance(frame, LLMFullResponseEndFrame):
            spoken = normalize_for_speech("".join(self._buf))
            if spoken:
                self._heard.last_agent = spoken
        await self.push_frame(frame, direction)


class CallerWords(FrameProcessor):
    """Ignore a transcript when it is silence, a hum, or her own last sentence echoed back."""

    def __init__(self, heard: _Heard):
        super().__init__()
        self._heard = heard

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSSpeakFrame) and frame.text:
            self._heard.last_agent = frame.text
        elif isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            if _is_phantom(frame.text or "", self._heard.last_agent):
                logger.info("Dropped phantom transcript: {}", (frame.text or "").strip())
                return
        await self.push_frame(frame, direction)


class IdleNudger:
    """Fired by Pipecat's user-idle timer (IDLE_NUDGE_SECS of silence after Maya stops talking). Checks in with a
    scripted line (no LLM round trip, so it plays at once); after IDLE_MAX_NUDGES unanswered check-ins it logs the
    attempt on the worklist row, tells the patient she'll call back, schedules the callback and hangs up."""

    def __init__(self, session: Session, snap: dict, llm: DaVitaGatewayLLMService, heard: _Heard,
                 worker_ref: list, can_redial: bool):
        self._s = session
        self._snap = snap
        self._llm = llm
        self._heard = heard
        self._worker_ref = worker_ref
        self._can_redial = can_redial
        self._count = 0
        self._topic = ""

    def heard_patient(self) -> None:
        self._count = 0

    async def on_idle(self) -> None:
        s = self._s
        if s.closing or s.ended or self._llm.busy or not self._worker_ref:
            return
        worker = self._worker_ref[0]
        self._count += 1
        if self._count == 1:
            self._topic = self._heard.last_agent
        logger.info("Line quiet for {:.1f}s (check-in {} of {}) session {}", IDLE_NUDGE_SECS, self._count,
                    IDLE_MAX_NUDGES, s.session_id)
        if self._count <= IDLE_MAX_NUDGES:
            verified = ((clinic.get_patient(s.patient_id) or {}).get("runtime") or {}).get("identity_verified", False)
            await worker.queue_frames([TTSSpeakFrame(text=still_there_line(self._snap, self._count, verified))])
            return
        s.closing = True
        s.end_reason = "no_response"
        plan = outreach.record_failed_attempt(
            s.patient_id, s.session_id, "no_response", last_topic=self._topic, can_redial=self._can_redial
        )
        s.tools.append({"name": "record_contact_attempt", "args": {"result": "no_response"}, "result": plan})
        goodbye = TTSSpeakFrame(text=no_response_line(self._snap, plan))
        goodbye.interruptible = False
        await worker.queue_frames([goodbye, EndWorkerFrame()])


def _speech_transforms():
    async def transform(text: str, _text_type: str) -> str:
        return normalize_for_speech(text)

    return [("*", transform)]


class DashboardObserver(BaseObserver):
    def __init__(self, session: Session, llm: FrameProcessor):
        super().__init__(observe_every_push=False)
        self._s = session
        self._llm = llm
        self._agent: list[str] = []

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        if isinstance(frame, TranscriptionFrame):
            _say(self._s, "patient", frame.text)
        elif isinstance(frame, InterimTranscriptionFrame):
            _say(self._s, "patient", frame.text, final=False)
        elif isinstance(frame, TTSSpeakFrame):
            _say(self._s, "agent", frame.text)
        elif data.source is self._llm:
            if isinstance(frame, LLMFullResponseStartFrame):
                self._agent = []
            elif isinstance(frame, LLMTextFrame):
                self._agent.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                clean = clean_for_text if self._s.channel == "sms" else normalize_for_speech
                _say(self._s, "agent", clean("".join(self._agent)))
                self._agent = []


def _latency_observer(session: Session) -> UserBotLatencyObserver:
    obs = UserBotLatencyObserver()

    @obs.event_handler("on_latency_breakdown")
    async def _on(_obs, breakdown):
        ms = {"stt_ms": 0, "llm_ms": 0, "tts_ms": 0}
        for t in breakdown.ttfb:
            name = t.processor.lower()
            key = "stt_ms" if "stt" in name or "whisper" in name else "tts_ms" if "tts" in name or "kokoro" in name else "llm_ms"
            ms[key] = max(ms[key], round(t.duration_secs * 1000))
        _publish(session, type="latency", total_ms=round(breakdown.total_secs * 1000), **ms)

    return obs


# ---------------------------------------------------------------- pipelines


def _services():
    stt = WhisperSTTServiceMLX(
        settings=WhisperSTTServiceMLX.Settings(model=MLXModel.LARGE_V3_TURBO_Q4.value, language=Language.EN)
    )
    return stt, DaVitaGatewayLLMService(), _tts()


def _tts():
    if TTS_PROVIDER == "deepgram":
        if not DEEPGRAM_API_KEY:
            raise RuntimeError("TTS_PROVIDER=deepgram needs DEEPGRAM_API_KEY (server/.env)")
        return DeepgramTTSService(
            api_key=DEEPGRAM_API_KEY,
            sample_rate=TTS_RATE,
            settings=DeepgramTTSService.Settings(voice=DEEPGRAM_VOICE, speed=DEEPGRAM_SPEED),
            text_transforms=_speech_transforms(),
        )
    return KokoroTTSService(
        sample_rate=TTS_RATE,
        settings=KokoroTTSService.Settings(voice=KOKORO_VOICE, language=Language.EN, speed=KOKORO_SPEED),
        text_transforms=_speech_transforms(),
    )


def _context(session: Session, llm: DaVitaGatewayLLMService, worker_ref: list) -> tuple[LLMContext, dict]:
    outreach.cancel(session.patient_id)
    clinic.apply_event(session.patient_id, "start_call", {})
    clinic.apply_event(session.patient_id, "check_open_chairs", {})
    snap = clinic.snapshot_for_prompt(session.patient_id)

    async def end_call():
        session.closing = True
        if worker_ref:
            await worker_ref[0].queue_frame(EndWorkerFrame())

    context = LLMContext(
        messages=[{"role": "system", "content": build_system_prompt(snap, session.direction, session.channel)}],
        tools=_tools(session, llm, end_call),
    )
    return context, snap


async def _run_voice(transport: BaseTransport, session: Session, audio_out_rate: int, echo_gate: bool = False) -> None:
    worker_ref: list[PipelineWorker] = []
    stt, llm, tts = _services()
    context, snap = _context(session, llm, worker_ref)
    heard = _Heard()
    pause = VADParams(confidence=0.75, start_secs=0.2, stop_secs=PAUSE_SECS, min_volume=0.6)
    rtvi = RTVIProcessor()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.35)],
            ),
            user_idle_timeout=IDLE_NUDGE_SECS,
        ),
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=pause))
    nudger = IdleNudger(session, snap, llm, heard, worker_ref,
                        can_redial=session.channel == "linphone" and session.direction != "inbound")
    session.nudger = nudger

    @aggregators.user().event_handler("on_user_turn_idle")
    async def _line_quiet(_aggregator):
        await nudger.on_idle()

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def _patient_spoke(_aggregator, _strategy, message):
        if (message.content or "").strip():
            nudger.heard_patient()

    pipeline = Pipeline(
        [
            transport.input(),
            *([EchoGate()] if echo_gate else []),
            rtvi,
            vad,
            stt,
            CallerWords(heard),
            aggregators.user(),
            llm,
            RememberAgent(heard),
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True, audio_in_sample_rate=AUDIO_IN_RATE, audio_out_sample_rate=audio_out_rate
        ),
        observers=[RTVIObserver(rtvi), DashboardObserver(session, llm), _latency_observer(session)],
        idle_timeout_secs=300,
    )
    worker_ref.append(worker)
    session.worker = worker

    @transport.event_handler("on_client_connected")
    async def _connected(_t, _c):
        _publish(session, type="session", patient_id=session.patient_id, channel=session.channel,
                 direction=session.direction, status="live")
        greeting = TTSSpeakFrame(text=opening_line(snap, session.direction), append_to_context=True)
        greeting.interruptible = False
        await worker.queue_frames([greeting])

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(_t, _c):
        await worker.cancel()

    await _run(session, worker)


async def _run(session: Session, worker: PipelineWorker) -> None:
    sessions[session.session_id] = session
    try:
        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        await runner.run()
    except Exception as exc:
        logger.exception("session {} crashed: {}", session.session_id, exc)
    finally:
        await finish(session, session.end_reason)


async def finish(session: Session, reason: str = "completed") -> None:
    if session.ended:
        return
    session.ended = True
    _publish(session, type="session", patient_id=session.patient_id, channel=session.channel,
             direction=session.direction, status="ended", reason=reason)
    asyncio.create_task(_summarize(session))


async def _summarize(session: Session) -> None:
    if not any(t["role"] == "patient" for t in session.transcript):
        return
    transcript = "\n".join(f"{AGENT_NAME if t['role'] == 'agent' else 'Patient'}: {t['text']}" for t in session.transcript)
    chart = clinic.get_patient(session.patient_id) or {}
    try:
        note = await llm_gateway.complete(
            [
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript}\n\nTool calls:\n{json.dumps(session.tools)}\n\n"
                                            f"Worklist row:\n{json.dumps(chart.get('worklist_row'))}"},
            ]
        )
    except Exception as exc:
        logger.warning("summary failed: {}", exc)
        return
    _publish(session, type="summary", note=note.strip())


async def run_phone_bot(websocket: WebSocket, stream_sid: str, call_sid: str | None, session: Session) -> None:
    """Linphone call bridged by app.sip as a Twilio Media Stream (8 kHz PCMU over /ws)."""
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=TwilioFrameSerializer(
                stream_sid=stream_sid,
                call_sid=call_sid,
                params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
            ),
        ),
    )
    await _run_voice(transport, session, TTS_RATE, echo_gate=ECHO_GATE)


async def run_webrtc_bot(connection: SmallWebRTCConnection, session: Session) -> None:
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=AUDIO_IN_RATE,
            audio_out_sample_rate=AUDIO_OUT_RATE,
        ),
    )
    await _run_voice(transport, session, AUDIO_OUT_RATE, echo_gate=BROWSER_ECHO_GATE)


async def run_chat(session: Session) -> None:
    """Typed conversation (dashboard chat, or SMS when session.channel == "sms"): same tools, chart writes; no audio.
    An SMS thread the patient started has no scripted opener; Maya answers their first text."""
    sms = session.channel == "sms"
    worker_ref: list[PipelineWorker] = []
    llm = DaVitaGatewayLLMService()
    context, snap = _context(session, llm, worker_ref)
    greeting = (
        None if sms and session.direction == "inbound"
        else sms_opening_line(snap) if sms
        else opening_line(snap, session.direction)
    )
    if greeting:
        context.add_message({"role": "assistant", "content": greeting})
    aggregators = LLMContextAggregatorPair(context)
    worker = PipelineWorker(
        Pipeline([aggregators.user(), llm, aggregators.assistant()]),
        params=PipelineParams(enable_metrics=True),
        observers=[DashboardObserver(session, llm)],
        idle_timeout_secs=SMS_IDLE_SECS if sms else 600,
        idle_timeout_frames=(LLMMessagesAppendFrame, LLMFullResponseEndFrame),
        cancel_on_idle_timeout=True,
    )
    worker_ref.append(worker)
    session.worker = worker
    _publish(session, type="session", patient_id=session.patient_id, channel=session.channel,
             direction=session.direction, status="live")
    if greeting:
        _say(session, "agent", greeting)
    await _run(session, worker)


async def send_text(session: Session, text: str) -> None:
    """Typed line from the dashboard; reaches Maya as if the patient said it."""
    if not session.worker:
        raise RuntimeError("session_not_ready")
    _say(session, "patient", text)
    if session.nudger:
        session.nudger.heard_patient()
    await session.worker.queue_frame(
        LLMMessagesAppendFrame(messages=[{"role": "user", "content": text}], run_llm=True)
    )


async def hangup(session: Session) -> None:
    if session.worker:
        await session.worker.cancel()
