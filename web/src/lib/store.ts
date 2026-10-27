import { signal } from "@angular/core";
import type { Facility, Item, Latency, Patient, Role, ServerEvent, Session, SessionStatus, SipStatus } from "./types";

export type LinkState = "connecting" | "open" | "down" | "mock";

export interface State {
  link: LinkState;
  mock: boolean;
  loaded: boolean;
  loadError: string | null;
  facility: Facility | null;
  patients: Patient[];
  selectedId: string;
  sip: SipStatus | null;
  session: Session | null;
  items: Item[];
  summary: string | null;
  speaker: Role | null;
  pendingLatency: Latency | null;
  buffer: Record<string, ServerEvent[]>;
  error: string | null;
  micMuted: boolean;
  chartTick: number;
}

export type Action =
  | { type: "server"; event: ServerEvent }
  | { type: "link"; link: LinkState }
  | { type: "loaded"; facility: Facility | null; patients: Patient[] }
  | { type: "loadError"; message: string }
  | { type: "select"; id: string }
  | { type: "sipStatus"; sip: SipStatus | null }
  | { type: "begin"; session: Session }
  | { type: "adopt"; sessionId: string }
  | { type: "localEnd"; reason?: string }
  | { type: "clearSession" }
  | { type: "localTurn"; text: string }
  | { type: "speaker"; role: Role | null }
  | { type: "error"; message: string | null }
  | { type: "mic"; muted: boolean }
  | { type: "patient"; patient: Patient };

const initial: State = {
  link: "connecting",
  mock: false,
  loaded: false,
  loadError: null,
  facility: null,
  patients: [],
  selectedId: "james-cole",
  sip: null,
  session: null,
  items: [],
  summary: null,
  speaker: null,
  pendingLatency: null,
  buffer: {},
  error: null,
  micMuted: false,
  chartTick: 0,
};

let seq = 0;
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

const toMs = (ts: unknown): number => {
  if (typeof ts === "number") return ts < 1e12 ? ts * 1000 : ts;
  if (typeof ts === "string") {
    const n = Date.parse(ts);
    if (!Number.isNaN(n)) return n;
  }
  return Date.now();
};

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

function upsertPatient(list: Patient[], patient: Patient): Patient[] {
  if (!patient?.id) return list;
  const idx = list.findIndex((p) => p.id === patient.id);
  if (idx === -1) return [...list, patient];
  const next = list.slice();
  next[idx] = { ...list[idx], ...patient };
  return next;
}

function statusOf(raw: string): SessionStatus {
  if (raw === "ringing" || raw === "live" || raw === "ended" || raw === "connecting") return raw;
  if (raw === "connected" || raw === "answered" || raw === "active") return "live";
  if (raw === "hangup" || raw === "completed" || raw === "failed" || raw === "closed") return "ended";
  return "connecting";
}

function channelLabel(channel: string) {
  if (channel === "linphone") return "phone";
  if (channel === "browser") return "browser";
  if (channel === "chat") return "chat";
  if (channel === "sms") return "SMS";
  return channel;
}

/** Apply an event that belongs to the active session. */
function applySessionEvent(state: State, ev: ServerEvent): State {
  switch (ev.type) {
    case "session": {
      const prev = state.session;
      if (!prev) return state;
      const status = statusOf(ev.status);
      const now = Date.now();
      const session: Session = {
        ...prev,
        patientId: ev.patient_id || prev.patientId,
        channel: ev.channel || prev.channel,
        direction: ev.direction || prev.direction,
        status,
        reason: ev.reason ?? prev.reason,
        liveAt: prev.liveAt ?? (status === "live" ? now : undefined),
        endedAt: status === "ended" ? prev.endedAt ?? now : prev.endedAt,
      };
      let items = state.items;
      if (status !== prev.status) {
        const text =
          status === "ringing"
            ? session.direction === "inbound"
              ? "Incoming call"
              : "Ringing the patient's phone"
            : status === "live"
              ? `Connected over ${channelLabel(session.channel)}`
              : status === "ended"
                ? `${session.channel === "sms" ? "Conversation" : "Call"} ended${ev.reason ? ` · ${String(ev.reason).replaceAll("_", " ")}` : ""}`
                : "";
        if (text) {
          items = [...items, { kind: "note", id: uid("n"), text, tone: status === "ended" ? "info" : "ok", ts: now }];
        }
      }
      return {
        ...state,
        session,
        items,
        selectedId: session.patientId || state.selectedId,
        speaker: status === "ended" ? null : state.speaker,
      };
    }
    case "transcript": {
      const text = (ev.text || "").trim();
      if (!text) return state;
      const final = ev.final !== false;
      const items = state.items.slice();
      const ts = toMs(ev.ts);

      // Typed text echoed back by the server: confirm the optimistic bubble instead of duplicating it.
      if (ev.role === "patient" && final) {
        for (let i = items.length - 1; i >= Math.max(0, items.length - 4); i--) {
          const it = items[i];
          if (it.kind === "turn" && it.role === "patient" && it.local && norm(it.text) === norm(text)) {
            items[i] = { ...it, local: false };
            return { ...state, items };
          }
        }
      }

      const last = items[items.length - 1];
      if (last && last.kind === "turn" && last.role === ev.role && !last.local) {
        let nextText = last.text;
        let interim = last.interim;
        if (final) {
          if (nextText && norm(nextText).endsWith(norm(text))) {
            // duplicate final; ignore
          } else if (nextText && norm(text).startsWith(norm(nextText))) {
            nextText = text;
          } else {
            nextText = nextText ? `${nextText} ${text}` : text;
          }
          interim = "";
        } else {
          interim = text;
        }
        items[items.length - 1] = { ...last, text: nextText, interim };
        return { ...state, items };
      }

      let latency: Latency | undefined;
      let pendingLatency = state.pendingLatency;
      if (ev.role === "agent" && pendingLatency) {
        latency = pendingLatency;
        pendingLatency = null;
      }
      items.push({
        kind: "turn",
        id: uid("t"),
        role: ev.role,
        text: final ? text : "",
        interim: final ? "" : text,
        ts,
        latency,
      });
      return { ...state, items, pendingLatency };
    }
    case "tool": {
      const item: Item = {
        kind: "tool",
        id: uid("x"),
        name: ev.name,
        args: (ev.args && typeof ev.args === "object" ? ev.args : {}) as Record<string, unknown>,
        result: ev.result,
        ts: toMs(ev.ts),
      };
      return { ...state, items: [...state.items, item] };
    }
    case "latency": {
      const lat: Latency = {
        stt_ms: ev.stt_ms,
        llm_ms: ev.llm_ms,
        tts_ms: ev.tts_ms,
        total_ms: ev.total_ms,
      };
      const items = state.items.slice();
      for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i];
        if (it.kind !== "turn") continue;
        if (it.role === "patient") break;
        if (!it.latency) {
          items[i] = { ...it, latency: lat };
          return { ...state, items };
        }
        break;
      }
      return { ...state, pendingLatency: lat };
    }
    case "summary":
      return { ...state, summary: ev.note || state.summary };
    default:
      return state;
  }
}

function sessionFromEvent(ev: Extract<ServerEvent, { type: "session" }>, fallbackPatient: string): Session {
  const status = statusOf(ev.status);
  const now = Date.now();
  return {
    id: ev.session_id,
    patientId: ev.patient_id || fallbackPatient,
    channel: ev.channel || "linphone",
    direction: ev.direction || "outbound",
    status,
    reason: ev.reason,
    createdAt: now,
    liveAt: status === "live" ? now : undefined,
  };
}

function replayBuffer(state: State, sessionId: string): State {
  const queued = state.buffer[sessionId] || [];
  const { [sessionId]: _drop, ...rest } = state.buffer;
  let next: State = { ...state, buffer: rest };
  for (const ev of queued) next = applySessionEvent(next, ev);
  return next;
}

function bufferEvent(state: State, ev: ServerEvent): State {
  const id = "session_id" in ev ? ev.session_id : undefined;
  if (!id) return state;
  const list = [...(state.buffer[id] || []), ev].slice(-300);
  const buffer = { ...state.buffer, [id]: list };
  const keys = Object.keys(buffer);
  if (keys.length > 12) delete buffer[keys[0]];
  return { ...state, buffer };
}

function onServer(state: State, ev: ServerEvent): State {
  if (!ev || typeof ev !== "object") return state;
  if (ev.type === "sip") {
    return {
      ...state,
      sip: { ...(state.sip || {}), registered: !!ev.registered, user: ev.user ?? null, contact: ev.contact ?? null },
    };
  }
  if (ev.type === "chart") {
    if (!ev.patient?.id) return state;
    const patients = upsertPatient(state.patients, ev.patient);
    return { ...state, patients, chartTick: state.chartTick + 1 };
  }

  const sid = "session_id" in ev ? ev.session_id : undefined;
  if (!sid) return state;
  const active = state.session;

  if (active && active.id === sid) return applySessionEvent(state, ev);

  // Auto-adopt a new inbound call (patient calling the clinic) or Maya's automatic callback when we're not busy.
  if (ev.type === "session") {
    const status = statusOf(ev.status);
    const idle = !active || active.status === "ended";
    if (idle && (ev.direction === "inbound" || ev.callback) && (status === "ringing" || status === "live")) {
      const session = sessionFromEvent(ev, state.selectedId);
      const note: Item = {
        kind: "note",
        id: uid("n"),
        text: ev.callback
          ? `Calling the patient back · attempt ${ev.attempt ?? "?"} of ${ev.of ?? "?"}`
          : ev.channel === "sms"
            ? "Patient texted the clinic"
            : status === "ringing"
              ? "Incoming call"
              : "Patient called in",
        tone: "ok",
        ts: Date.now(),
      };
      const adopted: State = {
        ...state,
        session,
        selectedId: session.patientId,
        items: [note],
        summary: null,
        speaker: null,
        pendingLatency: null,
      };
      return replayBuffer(bufferWithout(adopted, sid), sid);
    }
  }
  return bufferEvent(state, ev);
}

function bufferWithout(state: State, sid: string): State {
  // drop the triggering session event from buffer replay (already applied)
  const list = (state.buffer[sid] || []).filter((e) => e.type !== "session");
  return { ...state, buffer: { ...state.buffer, [sid]: list } };
}

export function reduce(state: State, action: Action): State {
  switch (action.type) {
    case "server":
      return onServer(state, action.event);
    case "link":
      return { ...state, link: action.link, mock: action.link === "mock" ? true : state.mock };
    case "loaded": {
      const patients = action.patients;
      const selectedId = patients.some((p) => p.id === state.selectedId) ? state.selectedId : patients[0]?.id || state.selectedId;
      return { ...state, loaded: true, loadError: null, facility: action.facility, patients, selectedId };
    }
    case "loadError":
      return { ...state, loaded: true, loadError: action.message };
    case "select":
      if (state.session && state.session.status !== "ended") return state;
      return { ...state, selectedId: action.id, session: null, items: [], summary: null, speaker: null, error: null };
    case "sipStatus":
      return { ...state, sip: action.sip };
    case "begin":
      return {
        ...state,
        session: action.session,
        selectedId: action.session.patientId,
        items: [],
        summary: null,
        speaker: null,
        pendingLatency: null,
        error: null,
        micMuted: false,
      };
    case "adopt": {
      if (!state.session) return state;
      const session = { ...state.session, id: action.sessionId };
      return replayBuffer({ ...state, session }, action.sessionId);
    }
    case "localEnd": {
      const s = state.session;
      if (!s || s.status === "ended") return state;
      return applySessionEvent(state, {
        type: "session",
        session_id: s.id,
        status: "ended",
        reason: action.reason,
      });
    }
    case "clearSession":
      return { ...state, session: null, items: [], summary: null, speaker: null, pendingLatency: null, error: null };
    case "localTurn":
      return {
        ...state,
        items: [
          ...state.items,
          { kind: "turn", id: uid("t"), role: "patient", text: action.text, interim: "", ts: Date.now(), local: true, typed: true },
        ],
      };
    case "speaker":
      if (state.speaker === action.role) return state;
      return { ...state, speaker: action.role };
    case "error":
      return { ...state, error: action.message };
    case "mic":
      return { ...state, micMuted: action.muted };
    case "patient":
      return { ...state, patients: upsertPatient(state.patients, action.patient), chartTick: state.chartTick + 1 };
    default:
      return state;
  }
}

const state = signal<State>(initial);
export const appState = state.asReadonly();

export function getState(): State {
  return state();
}

export function dispatch(action: Action): void {
  state.update((current) => reduce(current, action));
}

export function selectedPatient(s: State): Patient | undefined {
  return s.patients.find((p) => p.id === s.selectedId) || s.patients[0];
}

export const newId = uid;
