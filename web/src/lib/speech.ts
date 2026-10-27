import { dispatch } from "./store";
import type { Role } from "./types";

/** Audio levels stay outside change detection; the canvas reads them each frame. */
export const levels = {
  agent: 0,
  patient: 0,
  /** last time a real (WebRTC) level arrived; synthetic envelopes are used otherwise */
  at: 0,
};

export function setLevel(role: Role, value: number) {
  const v = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  levels[role] = v;
  levels.at = performance.now();
}

let timer: number | undefined;

/** Mark who is speaking for roughly `ms`, then fall back to listening. */
export function markSpeaking(role: Role, ms: number) {
  dispatch({ type: "speaker", role });
  if (timer) window.clearTimeout(timer);
  timer = window.setTimeout(() => dispatch({ type: "speaker", role: null }), Math.max(300, ms));
}

/** Pipecat callbacks: hold the speaker until an explicit stop arrives. */
export function holdSpeaking(role: Role | null) {
  if (timer) window.clearTimeout(timer);
  timer = undefined;
  dispatch({ type: "speaker", role });
}

export function estimateSpeechMs(text: string) {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.min(9000, Math.max(1100, words * 330));
}
