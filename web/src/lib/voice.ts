/* Browser talk mode: Pipecat client + SmallWebRTC transport against /api/offer.
 * Transcripts/tools/chart still arrive over SSE (keyed by session_id); this module
 * only owns audio: mic, Maya's speaker, speaking callbacks and audio levels. */
import type { PipecatClient, PipecatClientOptions } from "@pipecat-ai/client-js";
import { holdSpeaking, setLevel } from "./speech";
import { dispatch, getState } from "./store";

let client: PipecatClient | null = null;
let speaker: HTMLAudioElement | null = null;
let speakerPoll: number | undefined;

function speakerEl(): HTMLAudioElement {
  if (speaker) return speaker;
  const el = document.createElement("audio");
  el.autoplay = true;
  el.setAttribute("playsinline", "");
  el.style.display = "none";
  document.body.appendChild(el);
  speaker = el;
  return el;
}

function attachTrack(track: MediaStreamTrack) {
  const el = speakerEl();
  const current = (el.srcObject as MediaStream | null)?.getAudioTracks()[0];
  if (current?.id === track.id && !el.paused) return;
  el.srcObject = new MediaStream([track]);
  el.muted = false;
  void el.play().catch(() => {
    dispatch({ type: "error", message: "The browser blocked Maya's audio. Tap anywhere on the page to unmute." });
  });
}

/** Some browsers never fire onTrackStarted for the remote track; read it off the peer connection. */
function playRemote() {
  const c = client;
  if (!c) return;
  const pc = (c as unknown as { transport?: { pc?: RTCPeerConnection | null } }).transport?.pc;
  const track = pc
    ?.getReceivers()
    .map((r) => r.track)
    .find((t) => t?.kind === "audio");
  if (track) attachTrack(track);
}

export async function startBrowserTalk(patientId: string, sessionId: string): Promise<void> {
  await stopBrowserTalk();
  // Unlock autoplay inside the click gesture.
  const el = speakerEl();
  el.muted = false;
  void el.play().catch(() => undefined);

  const [{ PipecatClient }, { SmallWebRTCTransport }] = await Promise.all([
    import("@pipecat-ai/client-js"),
    import("@pipecat-ai/small-webrtc-transport"),
  ]);
  // The user can end the call while the voice SDK is loading.
  if (getState().session?.id !== sessionId || getState().session?.status === "ended") { return; }

  // If the server's `session` event is late or missing, Pipecat's ready signal flips us to live.
  const markLive = () => {
    const s = getState().session;
    if (s && s.id === sessionId && s.status === "connecting") {
      dispatch({
        type: "server",
        event: { type: "session", session_id: sessionId, patient_id: patientId, channel: "browser", direction: "outbound", status: "live" },
      });
    }
  };

  const callbacks: Record<string, unknown> = {
    onConnected: () => playRemote(),
    onBotReady: () => {
      markLive();
      playRemote();
    },
    onDisconnected: () => {
      holdSpeaking(null);
      const s = getState().session;
      if (s && s.id === sessionId && s.status !== "ended") dispatch({ type: "localEnd", reason: "disconnected" });
    },
    onUserStartedSpeaking: () => holdSpeaking("patient"),
    onUserStoppedSpeaking: () => holdSpeaking(null),
    onBotStartedSpeaking: () => {
      holdSpeaking("agent");
      playRemote();
    },
    onBotStoppedSpeaking: () => holdSpeaking(null),
    onLocalAudioLevel: (level: number) => setLevel("patient", level),
    onRemoteAudioLevel: (level: number) => setLevel("agent", level),
    onTrackStarted: (track: MediaStreamTrack, participant?: { local?: boolean }) => {
      if (track.kind !== "audio" || participant?.local) return;
      attachTrack(track);
    },
    onError: (err: unknown) => {
      const e = err as { message?: string; data?: { message?: string } } | string;
      const message = typeof e === "string" ? e : e?.data?.message || e?.message || "Voice connection failed";
      dispatch({ type: "error", message });
    },
  };

  const options = {
    transport: new SmallWebRTCTransport({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] }),
    enableMic: true,
    enableCam: false,
    callbacks,
  } as unknown as PipecatClientOptions;

  const c = new PipecatClient(options);
  client = c;
  const connect = c.connect.bind(c) as unknown as (params: unknown) => Promise<unknown>;
  await connect({
    webrtcRequestParams: {
      endpoint: "/api/offer",
      requestData: { patient_id: patientId, session_id: sessionId },
    },
  });
  if (speakerPoll) window.clearInterval(speakerPoll);
  speakerPoll = window.setInterval(playRemote, 400);
  window.setTimeout(() => {
    if (speakerPoll) window.clearInterval(speakerPoll);
    speakerPoll = undefined;
  }, 12000);
}

export async function stopBrowserTalk(): Promise<void> {
  if (speakerPoll) window.clearInterval(speakerPoll);
  speakerPoll = undefined;
  const c = client;
  client = null;
  if (c) {
    try {
      await c.disconnect();
    } catch {
      /* already closed */
    }
  }
  if (speaker) {
    speaker.pause();
    speaker.srcObject = null;
  }
  setLevel("agent", 0);
  setLevel("patient", 0);
}

export function setMic(enabled: boolean) {
  const c = client as unknown as { enableMic?: (on: boolean) => void } | null;
  try {
    c?.enableMic?.(enabled);
  } catch {
    /* ignore */
  }
}

export function hasBrowserTalk() {
  return !!client;
}
