#!/usr/bin/env bash
# Install Village Line dependencies and start the voice API plus the demo UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

API_PORT="${API_PORT:-7860}"
WEB_PORT="${WEB_PORT:-8173}"
SIP_PORT="${SIP_PORT:-5060}"
OLLAMA_MODEL="${OLLAMA_MODEL:-llama3.1:8b}"
LOG_DIR="$ROOT/.dev"

API_PID=""
WEB_PID=""
OLLAMA_PID=""
export OLLAMA_MODEL API_PORT WEB_PORT SIP_PORT

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing '$1'. Install it, then run ./dev.sh again."
    exit 1
  fi
}

free_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "Stopping whatever is already on port $port"
    kill $pids 2>/dev/null || true
    sleep 0.4
  fi
}

cleanup() {
  echo
  echo "Stopping Village Line"
  if [[ -n "$API_PID" ]]; then kill "$API_PID" 2>/dev/null || true; fi
  if [[ -n "$WEB_PID" ]]; then kill "$WEB_PID" 2>/dev/null || true; fi
  if [[ -n "$OLLAMA_PID" ]]; then kill "$OLLAMA_PID" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

need uv
need node
need npm
need curl

mkdir -p "$LOG_DIR"

echo "Installing Python libraries"
(cd "$ROOT/server" && uv sync)

echo "Installing frontend libraries"
(cd "$ROOT/web" && npm ci)

GCP_PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
if [[ -z "$GCP_PROJECT" ]] && command -v gcloud >/dev/null 2>&1; then
  GCP_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -n "$GCP_PROJECT" && "$GCP_PROJECT" != "(unset)" ]]; then
  export GOOGLE_CLOUD_PROJECT="$GCP_PROJECT"
  export GOOGLE_CLOUD_QUOTA_PROJECT="$GCP_PROJECT"
fi

if command -v gcloud >/dev/null 2>&1 && gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "Voice engine: Google Cloud (${GOOGLE_CLOUD_PROJECT:-unset project})"
else
  echo "Google login is expired. This start uses local Whisper, Ollama, and Kokoro."
  echo "For the fast path, sign in once: gcloud auth login --update-adc"
  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama is not installed, and Google is not signed in. Voice will not start."
  elif ! curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null; then
    echo "Starting Ollama"
    ollama serve >"$LOG_DIR/ollama.log" 2>&1 &
    OLLAMA_PID=$!
    for _ in $(seq 1 30); do
      if curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null; then
        break
      fi
      sleep 1
    done
  fi
  if command -v ollama >/dev/null 2>&1 && ! ollama list | awk 'NR>1 {print $1}' | grep -qx "$OLLAMA_MODEL"; then
    echo "Pulling $OLLAMA_MODEL"
    ollama pull "$OLLAMA_MODEL"
  fi
fi

free_port "$API_PORT"
free_port "$WEB_PORT"

SIP_HOLDER="$(lsof -nP -iUDP:"$SIP_PORT" 2>/dev/null | awk -v p=":$SIP_PORT" 'NR>1 {split($9, a, "->"); if (substr(a[1], length(a[1]) - length(p) + 1) == p) print $1" (pid "$2")"}' | sort -u | paste -sd, - || true)"
if [[ -n "$SIP_HOLDER" ]]; then
  echo "UDP $SIP_PORT is taken by $SIP_HOLDER. Quit that app (Zoom/Webex/Bria/another softphone) or Linphone calls won't work."
fi

echo "Starting voice API on :$API_PORT"
(
  cd "$ROOT/server"
  exec uv run uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT"
) >"$LOG_DIR/api.log" 2>&1 &
API_PID=$!

echo "Starting Angular UI on :$WEB_PORT"
(
  cd "$ROOT/web"
  exec npm run dev -- --host --port "$WEB_PORT" --strictPort
) >"$LOG_DIR/web.log" 2>&1 &
WEB_PID=$!

for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then
    break
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    echo "Voice API exited. Last lines of $LOG_DIR/api.log:"
    tail -n 40 "$LOG_DIR/api.log" || true
    exit 1
  fi
  sleep 0.5
done

if ! curl -sf "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then
  echo "Voice API did not become ready. See $LOG_DIR/api.log"
  exit 1
fi

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")"

echo
echo "Village Line is running"
echo "  Laptop   http://localhost:$WEB_PORT"
echo "  Phone    http://$LAN_IP:$WEB_PORT"
echo "  API logs $LOG_DIR/api.log"
echo "  UI logs  $LOG_DIR/web.log"
echo
SIP_DOMAIN="$(curl -sf "http://127.0.0.1:$API_PORT/api/sip/status" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("domain",""))' 2>/dev/null || true)"
if [[ -n "$SIP_DOMAIN" ]]; then
  echo "Linphone: Use a SIP account / Third-party SIP account (do NOT create a linphone.org account)"
  echo "  Username  anything, e.g. $(whoami)"
  echo "  Password  anything (not checked)"
  echo "  Domain    ${SIP_DOMAIN%:5060}"
  echo "  Transport UDP    Proxy/Registrar: leave blank"
else
  echo "SIP gateway is not running. Check $LOG_DIR/api.log for 'could not bind udp/$SIP_PORT'."
fi
echo
echo "Press Ctrl-C to stop."

wait "$API_PID" "$WEB_PID"
