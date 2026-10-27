# DaVita Missed-Treatment Voice Agent ("Village Line") — agent notes

Use case: **Patient Outreach → Missed Treatment recovery** (CWOW *Reschedule and Missed Treatment
Worklist*, `PATIENT_RESCHEDULE_WORKLIST`; ScheduleHub open chairs; Pt360 history). Agent "Maya" calls
a patient after a missed run, verifies identity, captures the MTX reason code, offers a specific
makeup chair, books it, fixes transport, escalates clinically, writes outcome back. Synthetic data only.

## Stack (all free / OSS except DaVita gateway)
- Pipecat 1.14.0 (`PipelineWorker`, `WorkerRunner`) · Silero VAD · MLX Whisper large-v3-turbo (STT)
  · Kokoro ONNX (TTS) · LLM = DaVita LLM API Service (OpenAI-compatible, dev:
  `https://llm-service-api-dev.ai-np.davita.com/v1/chat/completions`, headers `X-Use-Case-Id: uid-874fafa0`,
  `Authorization: Bearer $(gcloud auth print-identity-token)`). Model `claude-sonnet-4-6`.
  Gateway quirks: streaming drops tool_calls → use `stream:false`; Claude requires `temperature`;
  gemini-3.1-pro-preview streaming is broken.
- Phone: **Linphone app** on the user's cell, SIP/RTP over the LAN, no carrier. Our server is a tiny
  SIP registrar + UA (`server/app/sip/`). Linphone registers as `sip:<user>@<mac-lan-ip>:5060` (UDP).
- SIP gateway bridges RTP (PCMU 8 kHz) ⇄ a **Twilio-Media-Streams-compatible websocket** at
  `ws://127.0.0.1:7860/ws`, so the bot uses Pipecat's `TwilioFrameSerializer` (auto_hang_up=False)
  and can later move to Twilio/Telnyx/Genesys unchanged.

## Layout
- `server/app/main.py` FastAPI app (REST, SSE, `/ws`, WebRTC `/api/offer`), port 7860
- `server/app/bot.py` pipelines (phone ws, browser WebRTC, text chat)
- `server/app/llm_gateway.py` DaVita gateway LLM service (non-stream → fake stream)
- `server/app/bus.py` in-process event bus → SSE
- `server/app/clinic.py` + `server/data/clinic.json` synthetic CWOW/ScheduleHub store
- `server/app/sip/` registrar, UAC/UAS, RTP, ws bridge; `server/tools/fake_linphone.py` test phone
- `web/` Angular 22 + Angular Material dashboard (standalone, zoneless, signals, SCSS),
  port 8173 (proxy `/api`, `/ws` → `API_PORT`, default 7860 via `proxy.conf.cjs`).
  5173 is taken by another local project (VoiceAI-Sai-Demo), don't move back.
- UI entry: `web/src/app/app.component.ts`; `workspace.service.ts` exposes signal state and user actions.
  `web/src/lib/` retains typed REST/SSE/WebRTC adapters, event reducer and clinical normalizers.
  `web/src/styles.scss` defines the Material theme and shared design tokens.
- Frontend checks: `cd web && npm test -- --watch=false && npm run build && npm run test:e2e`.
  E2E uses **Firefox only** and a separate UI on 8174; install it with `npx playwright install firefox`.
  Offline replay: `http://localhost:8173/?mock=1` (`&speed=100` accelerates the script).
  Production output: `web/dist/village-line/browser`, also served by FastAPI when built before startup.
  Local `web-react/` is a git-ignored migration backup, not part of the application.
- `server/app/call_log.py` bus listener → one transcript file per session in `logs/calls/<YYYY-MM-DD_HH-MM-SS>_<patient>_<channel>.log`

## Bridge protocol (SIP gateway → `/ws`, Twilio Media Streams JSON)
gateway sends: `{"event":"connected"}`, then
`{"event":"start","streamSid":S,"start":{"streamSid":S,"callSid":C,"customParameters":{"patient_id":..,"session_id":..,"channel":"linphone","direction":"outbound|inbound"}}}`,
then `{"event":"media","streamSid":S,"media":{"payload":<b64 160-byte PCMU>}}` every 20 ms, `{"event":"stop"}` on BYE.
bot sends: `{"event":"media",...}` (PCMU b64, any chunk size → gateway re-packetizes to 20 ms RTP),
`{"event":"clear"}` (barge-in: flush queued audio), `{"event":"mark",...}` (ignore). WS close → gateway sends BYE.

## REST (all JSON)
- `GET /api/health`
- `GET /api/clinic` → `{facility, patients[]}` ; `GET /api/clinic/{id}` ; `POST /api/clinic/{id}/reset`
- `GET /api/sip/status` → `{registered, user, contact, server_ip, sip_port, domain}`
- `POST /api/call` `{patient_id}` → `{session_id}` (rings registered Linphone; 409 if not registered)
- `POST /api/hangup` `{session_id}`
- `POST /api/chat/start` `{patient_id}` → `{session_id}` (text-only session)
- `POST /api/sms/start` `{patient_id}` → `{session_id}` (Maya texts the registered Linphone user first; 409 if not registered)
- `POST /api/session/{session_id}/message` `{text}` → inject typed text into any live session
- `POST /api/offer` / `PATCH /api/offer` browser WebRTC (requestData `{patient_id, session_id}`)
- `GET /api/events` SSE stream; each `data:` is one JSON event below

## Events (SSE)
- `{type:"session", session_id, patient_id, channel:"linphone"|"browser"|"chat", direction, status:"ringing"|"live"|"ended", reason?}`
- `{type:"transcript", session_id, role:"agent"|"patient", text, final, ts}`
- `{type:"tool", session_id, name, args, result, ts}`
- `{type:"chart", session_id, patient}` (full public patient after a write)
- `{type:"latency", session_id, stt_ms, llm_ms, tts_ms, total_ms}`
- `{type:"summary", session_id, note}` (post-call CWOW note from gateway LLM)
- `{type:"sip", registered, user, contact}`

## Run
`./dev.sh` (needs `gcloud auth login`, uv, node). UI http://localhost:8173.
Env: `WEB_PORT` (8173), `API_PORT` (7860), `SIP_PORT` (5060), `SIP_HOST_IP` (override detected LAN IP),
`INBOUND_PATIENT`, `CALL_LOG_DIR` (default `logs/calls/`), `KOKORO_VOICE` (af_heart), `KOKORO_SPEED` (1.0),
`DEEPGRAM_API_KEY` (in git-ignored `server/.env`, loaded by `app/__init__.py`), `TTS_PROVIDER` (deepgram if key else kokoro),
`DEEPGRAM_VOICE` (aura-2-helena-en; also good: thalia, andromeda, asteria), `DEEPGRAM_SPEED` (1.0). `/api/health` shows the active TTS.
Deepgram websocket TTS TTFB ≈ 140 ms vs Kokoro 250–1100 ms. Gateway: only sonnet-4-6/opus/gemini-3.1-pro approved for
`uid-874fafa0` (haiku-4-5 → 403); sonnet-4-6 ≈ 3–5 s per non-streamed turn, the main latency.
`ECHO_GATE` (true: phone input is silenced while the agent speaks + `ECHO_TAIL_SECS` 0.6; no barge-in on phone).
Agent name lives in `prompts.AGENT_NAME` (+ hard-coded strings in `web/src`).

## Voice pipeline notes
- Silero VAD (`VADProcessor`, stop 0.5 s) + Smart Turn v3 (aggregator default stop strategy). stop 0.2 s was tried:
  Whisper got fragments ("10 works" → "10 words") and Smart Turn said INCOMPLETE more often (3 s fallback wait).
- Linphone **desktop on the same Mac** plays the agent through the speakers and its mic hears it → agent transcribes
  and answers itself. `EchoGate` in bot.py prevents this. Reproduce: fake phone `--echo 0.5` with `ECHO_GATE=false`.
- Gateway logs `inbound peak_rms=… voiced=…s` per call and warns after 20 s of silent RTP (muted softphone/wrong mic).

## SMS (Linphone Conversations tab = SIP MESSAGE)
- Gateway parses inbound MESSAGE (text/plain, or message/cpim unwrapped; is-composing/IMDN ignored; retransmits deduped
  by Call-ID+CSeq) → `main._on_sip_message` → one `channel:"sms"` session per Linphone user (`bot.run_chat`, no audio).
  Patient texting first = `direction:"inbound"` for `_last_patient`, no scripted opener. Replies: `Session.outbox` →
  `SipGateway.send_message` (From = the URI they texted, e.g. `sip:maya@<ip>`, so it lands in the same conversation).
- SMS prompt = `_TEXT_RULES` in prompts.py; outgoing text goes through `speech.clean_for_text` (no em/en dashes, no JSON).
- Thread closes after `SMS_IDLE_SECS` (1800) without texts; the next text opens a new one.
- Test: `uv run python tools/fake_linphone.py --server 127.0.0.1:5064 --text "Hey Maya" --text "yes it's me"`
  (`--await-text` for clinic-started threads, `--cpim`). `--selftest` covers SMS too (`SELFTEST_SIP_PORT/WS_PORT` if 5071/7871 busy).

## Linphone setup gotchas (see README "Call through Linphone")
- Use **Third-party SIP account**, never "Register/Create account" (linphone.org → "Bad URI").
- Domain = *this* Mac's LAN IP, UDP, proxy/registrar blank, any user/password (no auth).
- "Error during connection" = nothing bound on udp/5060 (API down, or another softphone holds it).
- Test without a phone: `uv run python tools/fake_linphone.py --server 127.0.0.1:5060 --record /tmp/maya.wav`
  then `POST /api/call`. Use spare ports (`API_PORT=7861 SIP_PORT=5062`) to avoid clobbering a running dev server.
