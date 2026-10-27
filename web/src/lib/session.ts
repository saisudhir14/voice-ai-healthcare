/* User intents: boot, place/end calls, type into a live session.
 * Real mode talks to the REST contract in AGENTS.md; mock mode replays a script. */
import { api, ApiError } from "./api";
import { connectEvents, disconnectEvents } from "./events";
import { endMockCall, mockMessage, startMockCall, stopMock } from "./mock";
import { mockFacility, mockPatients, mockSip } from "./mockData";
import { holdSpeaking } from "./speech";
import { dispatch, getState, newId } from "./store";
import type { Channel, Session } from "./types";
import { setMic, startBrowserTalk, stopBrowserTalk } from "./voice";

export const isMock = (() => {
  const q = new URLSearchParams(window.location.search);
  return q.has("mock") && q.get("mock") !== "0";
})();

let sipPoll: number | undefined;
let clinicRetry: number | undefined;
let autoplayTimer: number | undefined;

async function refreshSip() {
  try {
    const sip = await api.sipStatus();
    const prev = getState().sip;
    dispatch({ type: "sipStatus", sip: { ...prev, ...sip } });
  } catch {
    if (!getState().sip) dispatch({ type: "sipStatus", sip: null });
  }
}

async function loadClinic() {
  try {
    const data = await api.clinic();
    dispatch({ type: "loaded", facility: data.facility || null, patients: data.patients || [] });
  } catch (e) {
    dispatch({
      type: "loadError",
      message: e instanceof ApiError ? `Clinic API error: ${e.message}` : "Can't reach the Village Line server on port 7860.",
    });
    if (booted) clinicRetry = window.setTimeout(loadClinic, 4000);
  }
}

let booted = false;
export function boot() {
  if (booted) return;
  booted = true;
  if (isMock) {
    dispatch({ type: "link", link: "mock" });
    dispatch({ type: "loaded", facility: mockFacility, patients: mockPatients() });
    dispatch({ type: "sipStatus", sip: mockSip });
    if (new URLSearchParams(window.location.search).has("autoplay")) {
      autoplayTimer = window.setTimeout(() => void startCall("linphone"), 600);
    }
    return;
  }
  connectEvents();
  void loadClinic();
  void refreshSip();
  sipPoll = window.setInterval(() => {
    // SSE `sip` events are primary; poll slowly as a safety net.
    void refreshSip();
  }, 8000);
}

function begin(channel: Channel, id = "pending"): Session {
  const s = getState();
  const session: Session = {
    id,
    patientId: s.selectedId,
    channel,
    direction: "outbound",
    status: "connecting",
    createdAt: Date.now(),
  };
  dispatch({ type: "begin", session });
  return session;
}

function fail(message: string) {
  dispatch({ type: "error", message });
  dispatch({ type: "localEnd", reason: "failed" });
}

let starting = false;

export async function startCall(channel: Channel) {
  if (starting) return;
  starting = true;
  try {
    await placeCall(channel);
  } finally {
    starting = false;
  }
}

async function placeCall(channel: Channel) {
  const s = getState();
  if (s.session && s.session.status !== "ended") return;
  const patientId = s.selectedId;

  if (s.mock) {
    begin(channel, "mock-pending");
    const sid = startMockCall(patientId, channel);
    dispatch({ type: "adopt", sessionId: sid });
    return;
  }

  const browserSid = channel === "browser" ? crypto.randomUUID?.() || newId("web") : "pending";
  begin(channel, browserSid);

  // Fresh chart for a clean demo run; never block the call on it.
  try {
    const fresh = await api.reset(patientId);
    if (fresh?.id) dispatch({ type: "patient", patient: fresh });
  } catch {
    /* older server or reset not needed */
  }
  if (getState().session?.status !== "connecting") return; // ended while resetting

  if (channel === "browser") {
    const sid = browserSid;
    try {
      await startBrowserTalk(patientId, sid);
    } catch (e) {
      await stopBrowserTalk();
      fail(e instanceof Error ? `Couldn't start browser audio: ${e.message}` : "Couldn't start browser audio.");
    }
    return;
  }

  try {
    const res =
      channel === "chat"
        ? await api.chatStart(patientId)
        : channel === "sms"
          ? await api.smsStart(patientId)
          : await api.call(patientId);
    if (!res?.session_id) throw new Error("Server did not return a session id");
    if (getState().session?.status === "ended") {
      // Presenter hung up before the server answered; don't leave the phone ringing.
      void api.hangup(res.session_id).catch(() => undefined);
      return;
    }
    dispatch({ type: "adopt", sessionId: res.session_id });
    if (channel === "chat" || channel === "sms") {
      const cur = getState().session;
      if (cur && cur.status === "connecting") {
        dispatch({
          type: "server",
          event: { type: "session", session_id: res.session_id, patient_id: patientId, channel, direction: "outbound", status: "live" },
        });
      }
    }
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      fail("Linphone isn't registered yet. Pair the phone, then try again.");
      void refreshSip();
    } else {
      fail(e instanceof Error ? e.message : "Couldn't start the call.");
    }
  }
}

export async function endCall() {
  const s = getState().session;
  if (!s) return;
  holdSpeaking(null);
  if (getState().mock) {
    endMockCall();
    return;
  }
  if (s.channel === "browser") {
    dispatch({ type: "localEnd", reason: "ended_by_you" });
    await stopBrowserTalk();
  }
  if (s.id && s.id !== "pending") {
    try {
      await api.hangup(s.id);
    } catch {
      /* browser/chat sessions may not need it */
    }
  }
  dispatch({ type: "localEnd", reason: "ended_by_you" });
}

export async function sendText(text: string) {
  const t = text.trim();
  const s = getState().session;
  if (!t || !s || s.status === "ended") return;
  dispatch({ type: "localTurn", text: t });
  if (getState().mock) {
    mockMessage(t);
    return;
  }
  try {
    await api.message(s.id, t);
  } catch (e) {
    dispatch({ type: "error", message: e instanceof Error ? `Message not delivered: ${e.message}` : "Message not delivered." });
  }
}

export function newCall() {
  stopMock();
  dispatch({ type: "clearSession" });
}

export function toggleMic() {
  const muted = !getState().micMuted;
  setMic(!muted);
  dispatch({ type: "mic", muted });
}

export function selectPatient(id: string) {
  dispatch({ type: "select", id });
}

export function _teardown() {
  booted = false;
  if (sipPoll) window.clearInterval(sipPoll);
  window.clearTimeout(clinicRetry);
  window.clearTimeout(autoplayTimer);
  disconnectEvents();
  stopMock();
  holdSpeaking(null);
  void stopBrowserTalk();
}
