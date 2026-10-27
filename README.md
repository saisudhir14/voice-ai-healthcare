# Village Line

Live missed-treatment recovery voice agent for DaVita.

Maya calls as an automated teammate from Riverside Dialysis. She already knows the chair the patient missed, asks why, offers a specific makeup slot from ScheduleHub, and writes the outreach back to a CWOW-shaped chart. All patients are synthetic.

This is the **Patient Outreach** use case — the DaVita name for it is **Missed Tx / Awaiting Reschedule**.

## Talk from your phone

1. Start both servers below on this Mac.
2. Put your phone on the same Wi-Fi.
3. Open the LAN URL printed on the page (also from `GET /api/lan`).
4. Tap **Talk to Maya**. Allow the microphone.

The voice path is peer-to-peer WebRTC in the phone browser. No Twilio bill.

## Call through Linphone (SIP, no carrier)

The API runs its own tiny SIP registrar on UDP 5060. Linphone (desktop or phone app) registers straight to this Mac; nothing goes to linphone.org.

1. `./dev.sh`. It prints the exact Linphone settings at the end (or `curl localhost:7860/api/sip/status`).
2. In Linphone, choose **Use a SIP account → Third-party SIP account**. Do **not** use "Register / Create account" (that's linphone.org and fails with "Bad URI").
   | Field | Value |
   |---|---|
   | Username | anything, e.g. `voice-ai-test` |
   | Password | anything (not checked on the LAN) |
   | Domain | this Mac's IP, `ipconfig getifaddr en0` (not someone else's) |
   | Transport | UDP |
   | Outbound proxy / Registrar | blank |
3. Click **Connection**. `GET /api/sip/status` should show `"registered": true`.
4. In the dashboard pick a patient and place the call; Linphone rings. Or dial `maya` from Linphone (inbound uses the last selected patient, `INBOUND_PATIENT` overrides).

If it says "Error during connection":
- The API isn't running, or something else holds UDP 5060 (`lsof -nP -iUDP:5060`; quit Zoom/Webex/Bria). `SIP_PORT=5070 ./dev.sh` moves it; then use domain `IP:5070`.
- macOS asked to allow incoming connections for Python: click **Allow** (System Settings → Network → Firewall).
- Phone app only: phone and Mac must be on the same Wi-Fi. Corporate/guest Wi-Fi usually blocks device-to-device traffic; use home Wi-Fi or a hotspot. VPN can change which IP is right; set `SIP_HOST_IP=<ip>` if the detected one is wrong.
- The Mac's IP changes per network; update the Linphone domain when it does.

Test without a phone: `cd server && uv run python tools/fake_linphone.py --server 127.0.0.1:5060 --record /tmp/maya.wav`, then `POST /api/call {"patient_id":"james-cole"}`.

## Stack

| Layer | What |
|---|---|
| Transport | Pipecat SmallWebRTC |
| VAD | Silero |
| STT | Google Cloud Speech-to-Text streaming |
| LLM | Vertex Gemini 2.5 Flash |
| TTS | Google Chirp 3 HD (`en-US-Chirp3-HD-Aoede`, English only) |
| Clinic | In-memory ScheduleHub + CWOW stand-in |
| Dashboard | Angular 22, Angular Material, signals, SCSS |

If `gcloud` is not signed in, the call falls back to local Whisper, Ollama, and Kokoro. Sign in for the fast path:

```bash
gcloud auth login --update-adc
```

## Run

```bash
./dev.sh
```

That installs Python and frontend libraries, then starts the voice API and the demo UI. With a current Google login it does not start Ollama.

Open [http://localhost:8173](http://localhost:8173). Ctrl-C stops both servers. Refresh the page and tap **Talk to Maya** again after a restart. Ports: UI `WEB_PORT` (8173), API `API_PORT` (7860), SIP `SIP_PORT` (5060).

## Angular dashboard

The UI in `web/` is a standalone, zoneless Angular application with a custom Angular
Material theme. It supports phone calls, browser voice, typed chat and Linphone SMS.
Transcripts, care actions, chart updates and outreach notes arrive over the existing
SSE connection. Browser voice loads the Pipecat SDK on demand.

```bash
cd web
npm ci
npm run dev
```

Open [http://localhost:8173/?mock=1](http://localhost:8173/?mock=1) to explore the
scripted offline demo without a running API, microphone or phone. Without `?mock=1`,
the dashboard connects to the real local API. `API_PORT` controls the API proxy target.

The dashboard has three areas: outreach controls, a live conversation with a reply
box, and the patient chart. The chart separates outreach progress from clinical
context. On smaller screens, use the Conversation / Patient chart switch.

```bash
npm run typecheck
npm test -- --watch=false
npm run build
npx playwright install firefox
npm run test:e2e
```

E2E tests run in Firefox on a separate server at port 8174. They cover patient
selection, a complete replay, typed replies, microphone controls, mobile layout,
REST/SSE ordering and call failures. The build writes to
`web/dist/village-line/browser`; FastAPI serves that directory if it exists at startup.
The former React source is preserved locally in git-ignored `web-react/`.

## Call logs

Every call (Linphone, browser, or typed chat) gets its own transcript file in `logs/calls/`, named by start time, patient and channel, e.g. `2026-10-27_10-59-29_james-cole_linphone.log`. It holds a header (session, patient, channel), each timestamped Maya/Patient line, the chart writes (tool calls), how and when the call ended, and the post-call note. `CALL_LOG_DIR` moves it. The folder is git-ignored.

## What to say

- **James Cole** — “The van never showed.” Maya should hold tomorrow 10:00 Chair 9 and task social work for a ride.
- **Rosa Alvarez** — “I felt dizzy.” She offers today’s 2:00 chair. If you say chest pain, she stops booking.
- **DeShawn Reed** — “I just needed a day.” She respects the choice and still notifies the nurse.

## Why this use case

Missing one dialysis treatment raises 30-day hospitalization risk about 41%. Clinics are expected to outreach within 30 minutes and resolve the same day. Village Line is that call, with the chart moving while you listen.
