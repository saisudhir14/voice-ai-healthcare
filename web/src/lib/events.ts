import { estimateSpeechMs, holdSpeaking, markSpeaking } from "./speech";
import { dispatch, getState } from "./store";
import type { ServerEvent } from "./types";
import { hasBrowserTalk, stopBrowserTalk } from "./voice";

const TYPES = ["session", "transcript", "tool", "chart", "latency", "summary", "sip"] as const;

let source: EventSource | null = null;
let retry = 0;
let retryTimer: number | undefined;
let stopped = false;

/** Route one server event into the store (shared by SSE and the mock replay). */
export function ingest(ev: ServerEvent) {
  if (!ev || typeof ev !== "object" || !("type" in ev)) return;
  dispatch({ type: "server", event: ev });

  if (ev.type === "session" && ev.status === "ended") {
    const active = getState().session;
    if (active?.id === ev.session_id) {
      holdSpeaking(null);
      if (active.channel === "browser" && hasBrowserTalk()) void stopBrowserTalk();
    }
    return;
  }

  // Drive the orb from transcripts when we have no real audio levels (phone + chat + mock).
  if (ev.type === "transcript") {
    const active = getState().session;
    if (!active || active.id !== ev.session_id || active.status === "ended") return;
    if (active.channel === "browser" && !getState().mock) return; // Pipecat speaking callbacks drive it
    const final = ev.final !== false;
    if (ev.role === "agent") markSpeaking("agent", final ? estimateSpeechMs(ev.text) : 1400);
    else markSpeaking("patient", final ? 500 : 1200);
  }
}

function handle(raw: string) {
  if (!raw) return;
  try {
    const parsed = JSON.parse(raw) as ServerEvent | ServerEvent[];
    if (Array.isArray(parsed)) parsed.forEach(ingest);
    else ingest(parsed);
  } catch {
    /* keep-alive or malformed line */
  }
}

function open() {
  if (stopped) return;
  dispatch({ type: "link", link: retry === 0 ? "connecting" : "down" });
  const es = new EventSource("/api/events");
  source = es;
  es.onopen = () => {
    retry = 0;
    dispatch({ type: "link", link: "open" });
  };
  es.onmessage = (m) => handle(m.data);
  // Servers that use named SSE events (`event: transcript`) skip onmessage.
  TYPES.forEach((t) =>
    es.addEventListener(t, (m) => {
      const data = (m as MessageEvent<string>).data;
      try {
        const parsed = JSON.parse(data) as Record<string, unknown>;
        if (parsed && typeof parsed === "object" && !("type" in parsed)) parsed.type = t;
        ingest(parsed as unknown as ServerEvent);
      } catch {
        /* ignore */
      }
    }),
  );
  es.onerror = () => {
    dispatch({ type: "link", link: "down" });
    if (es.readyState === EventSource.CLOSED) {
      es.close();
      source = null;
      const wait = Math.min(10000, 800 * 2 ** retry++);
      retryTimer = window.setTimeout(open, wait);
    }
  };
}

export function connectEvents() {
  stopped = false;
  if (source) return;
  open();
}

export function disconnectEvents() {
  stopped = true;
  if (retryTimer) window.clearTimeout(retryTimer);
  source?.close();
  source = null;
}
