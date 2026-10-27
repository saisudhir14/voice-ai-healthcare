/* ?mock=1 — scripted replay through the exact same ingest() path as SSE.
 * Doubles as a no-backend fallback demo. ?speed=2 plays twice as fast. */
import { ingest } from "./events";
import { freshRuntime, mockPatients } from "./mockData";
import { dispatch, getState } from "./store";
import type { Facility, Patient, Runtime, Slot } from "./types";

type Beat =
  | { k: "agent"; text: string }
  | { k: "patient"; text: string }
  | { k: "tool"; name: string; args: Record<string, unknown> }
  | { k: "latency"; total: number }
  | { k: "pause"; ms: number };

const speed = (() => {
  const v = Number(new URLSearchParams(window.location.search).get("speed"));
  return Number.isFinite(v) && v > 0 ? v : 1;
})();

let timers: number[] = [];
let activeSid: string | null = null;
let working: Patient | null = null;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
const words = (t: string) => t.split(/\s+/).filter(Boolean);
const nowIso = () => new Date().toISOString();

function scriptFor(p: Patient): Beat[] {
  const name = p.preferred_name || p.first_name || "there";
  const full = [p.first_name, p.last_name].filter(Boolean).join(" ");
  const chair = p.missed?.chair || "your chair";
  const fac = p.facility?.name || "Riverside Dialysis";
  const sw = (p.facility?.social_worker || "our social worker").split(",")[0];

  if (p.id === "rosa-alvarez") {
    return [
      { k: "agent", text: `Hi, this is Maya calling from ${fac}. Am I speaking with ${full}?` },
      { k: "patient", text: "Yes, this is Rosa." },
      { k: "latency", total: 1180 },
      { k: "tool", name: "verify_identity", args: { verified: true } },
      { k: "agent", text: `Thanks, Rosa. We missed you in ${chair} this morning. How are you feeling?` },
      { k: "patient", text: "I felt dizzy when I stood up, so I stayed in bed." },
      { k: "latency", total: 1420 },
      { k: "tool", name: "log_barrier", args: { barrier: "illness", detail: "Lightheaded on standing this morning; stayed home" } },
      { k: "agent", text: "I'm sorry. A few quick questions. Any chest pain, trouble breathing, or confusion right now?" },
      { k: "patient", text: "No, nothing like that. Just a little dizzy." },
      { k: "latency", total: 1610 },
      { k: "tool", name: "record_symptom_screen", args: { chest_pain: false, shortness_of_breath: false, confusion: false, lightheaded: true } },
      { k: "tool", name: "escalate_care_team", args: { owner: "RN", reason: "Orthostatic dizziness on missed-treatment call; check BP before makeup run", urgency: "urgent" } },
      { k: "tool", name: "check_open_chairs", args: {} },
      { k: "agent", text: "Your nurse will check your blood pressure when you arrive. I can hold Chair 4 today at 2:00. Can your daughter bring you?" },
      { k: "patient", text: "Yes, she gets off at one. Two o'clock is fine." },
      { k: "latency", total: 1350 },
      { k: "tool", name: "book_makeup_treatment", args: { slot_id: "slot-today-1400" } },
      { k: "tool", name: "confirm_transport", args: { status: "daughter" } },
      { k: "agent", text: "Done. Chair 4 today at 2:00. If the dizziness gets worse, call 911. See you this afternoon, Rosa." },
      { k: "tool", name: "send_confirmation", args: { channel: "sms" } },
      { k: "tool", name: "write_call_outcome", args: { outcome: "RESCHEDULED", summary: "Lightheaded, no emergent symptoms. RN tasked for BP check. Makeup Chair 4 today 2:00 PM, daughter driving." } },
      { k: "pause", ms: 900 },
    ];
  }

  if (p.id === "deshawn-reed") {
    return [
      { k: "agent", text: `Hi, this is Maya from ${fac}. Is this ${full}?` },
      { k: "patient", text: "Yeah, it's Shawn." },
      { k: "latency", total: 1090 },
      { k: "tool", name: "verify_identity", args: { verified: true } },
      { k: "agent", text: "Thanks, Shawn. We didn't see you for your 4:30 run. No judgment, I just want to check in." },
      { k: "patient", text: "I just needed a day off from the chair, honestly." },
      { k: "latency", total: 1520 },
      { k: "tool", name: "log_barrier", args: { barrier: "declined", detail: "Treatment fatigue; chose to skip" } },
      { k: "agent", text: "That makes sense. I can hold Chair 9 tomorrow at 10:00 so you don't go too long. Want it?" },
      { k: "patient", text: "Not tomorrow. I'll just come Wednesday." },
      { k: "latency", total: 1730 },
      { k: "tool", name: "escalate_care_team", args: { owner: "RN", reason: "Third miss in two weeks; declined makeup. IDWG 3.8 kg, K+ 6.1", urgency: "urgent" } },
      { k: "tool", name: "escalate_care_team", args: { owner: "NEPH", reason: "Repeat declined treatments; IDT follow-up", urgency: "routine" } },
      { k: "agent", text: "Okay, I respect that. Please go easy on fluids and potassium until Wednesday, and call us if you feel short of breath." },
      { k: "tool", name: "write_call_outcome", args: { outcome: "FORFEITED", summary: "Declined makeup after one offer. RN and nephrologist notified." } },
      { k: "pause", ms: 900 },
    ];
  }

  // James Cole — the hero script.
  return [
    { k: "agent", text: `Hi, this is Maya calling from ${fac}. Am I speaking with ${full}?` },
    { k: "patient", text: `Yeah, this is ${name}.` },
    { k: "latency", total: 1240 },
    { k: "tool", name: "verify_identity", args: { verified: true } },
    { k: "agent", text: `Thanks, ${name}. We missed you in ${chair} this morning. Is everything okay?` },
    { k: "patient", text: "I'm okay. The van never came. I waited outside till almost six." },
    { k: "latency", total: 1460 },
    { k: "tool", name: "log_barrier", args: { barrier: "transport", detail: "Broker van no-show, 5:10–5:25 AM window. Patient waited outside." } },
    { k: "agent", text: "I'm sorry, that's frustrating. Before we reschedule: any chest pain, shortness of breath, or swelling since Friday?" },
    { k: "patient", text: "No chest pain. My ankles are a little puffy, that's it." },
    { k: "latency", total: 1880 },
    { k: "tool", name: "record_symptom_screen", args: { chest_pain: false, shortness_of_breath: false, confusion: false, swelling: true } },
    { k: "agent", text: "Thanks for telling me. I'll flag that for your nurse. Let me look at open chairs." },
    { k: "tool", name: "check_open_chairs", args: {} },
    { k: "agent", text: "I can hold Chair 9 tomorrow at 10:00 in the morning, or Chair 4 today at 2:00. Which works better?" },
    { k: "patient", text: "Tomorrow at ten. My daughter can't take me before eight anyway." },
    { k: "latency", total: 2240 },
    { k: "tool", name: "book_makeup_treatment", args: { slot_id: "slot-wed-1000" } },
    { k: "agent", text: `Done. Chair 9 tomorrow at 10:00 is yours. I'll have ${sw}, our social worker, set up a ride so you're not counting on the broker again. Okay?` },
    { k: "patient", text: "Yes, please. That would help a lot." },
    { k: "latency", total: 1390 },
    { k: "tool", name: "request_ride_assist", args: { reason: "Broker no-show, third this month. Arrange ride for makeup tomorrow 10:00 AM." } },
    { k: "tool", name: "escalate_care_team", args: { owner: "RN", reason: "Ankle swelling, IDWG 3.2 kg, K+ 5.8 after missed run. Review fluid before makeup.", urgency: "urgent" } },
    { k: "agent", text: "You're all set. Keep fluids light today, about a liter, and call us if you get short of breath. I'm texting you a confirmation now." },
    { k: "patient", text: "Thanks, Maya. Bye." },
    { k: "latency", total: 1170 },
    { k: "tool", name: "send_confirmation", args: { channel: "sms", to: p.phone } },
    { k: "tool", name: "write_call_outcome", args: { outcome: "RESCHEDULED", summary: "Broker no-show. Makeup Chair 9 tomorrow 10:00 AM. MSW tasked for ride, RN tasked for swelling and IDWG." } },
    { k: "pause", ms: 900 },
  ];
}

function slot(id: unknown): Slot | undefined {
  return working?.open_chairs?.find((s) => s.id === id);
}

function applyTool(name: string, args: Record<string, unknown>): unknown {
  const p = working;
  if (!p) return { ok: false };
  const rt = (p.runtime ||= freshRuntime(p));
  const fac: Facility = p.facility || {};
  const row = (p.worklist_row ||= { status: "Open", attempts: 0 });
  rt.last_event_at = nowIso();
  if (row.status === "Open") row.status = "In Progress";
  switch (name) {
    case "verify_identity":
      rt.identity_verified = args.verified !== false;
      return { ok: true, verified: rt.identity_verified };
    case "check_open_chairs": {
      const open = (p.open_chairs || []).filter((s) => s.available !== false);
      rt.offered_slots = clone(open);
      return { chairs: open.map((c) => ({ slot_id: c.id, say: `${c.chair} ${c.spoken}`, time: c.time, chair: c.chair })) };
    }
    case "log_barrier":
      rt.barrier = String(args.barrier || "other");
      rt.barrier_detail = String(args.detail || "");
      row.reason_code = rt.barrier;
      row.attempts = (row.attempts || 0) + 1;
      rt.outreach_attempts = (rt.outreach_attempts || 0) + 1;
      if (p.missed) p.missed.reason_code = rt.barrier;
      rt.outreach_notes = [...(rt.outreach_notes || []), { at: nowIso(), text: rt.barrier_detail }];
      return { ok: true, barrier: rt.barrier };
    case "record_symptom_screen":
      rt.symptom_screen = { ...args };
      return { ok: true };
    case "book_makeup_treatment": {
      const s = slot(args.slot_id);
      if (!s) return { ok: false, error: "unknown_slot" };
      rt.booked_slot = clone(s);
      rt.outcome = "RESCHEDULED";
      if (p.missed) p.missed.status = "rescheduled";
      p.open_chairs = (p.open_chairs || []).filter((c) => c.id !== s.id);
      return { ok: true, booked: s, say: `${s.chair} ${s.spoken}` };
    }
    case "confirm_transport":
      rt.transport_status = String(args.status || "confirmed");
      if (p.transportation) p.transportation.status = rt.transport_status;
      return { ok: true, status: rt.transport_status };
    case "request_ride_assist":
      rt.transport_status = "msw_tasked";
      if (p.transportation) p.transportation.status = "msw_tasked";
      rt.escalations = [...(rt.escalations || []), { owner: "MSW", name: fac.social_worker, reason: String(args.reason || "Arrange makeup-day ride"), urgency: "urgent", at: nowIso() }];
      return { ok: true, tasked: "MSW" };
    case "escalate_care_team": {
      const owner = String(args.owner || "RN");
      const names: Record<string, string | undefined> = { RN: fac.charge_nurse, NEPH: fac.nephrologist, MSW: fac.social_worker, FA: fac.facility_admin, AA: fac.reschedule_lead };
      rt.escalations = [...(rt.escalations || []), { owner, name: names[owner] || owner, reason: String(args.reason || ""), urgency: String(args.urgency || "routine"), at: nowIso() }];
      return { ok: true };
    }
    case "send_confirmation":
      rt.confirmation_sent = `Text sent to ${p.phone || "patient"} · ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
      return { ok: true };
    case "write_call_outcome":
      rt.outcome = String(args.outcome || rt.outcome);
      rt.outcome_summary = String(args.summary || "");
      rt.outreach_attempts = (rt.outreach_attempts || 0) + 1;
      row.attempts = (row.attempts || 0) + (row.reason_code ? 0 : 1);
      row.outcome = rt.outcome;
      row.status = "Completed";
      rt.outreach_notes = [...(rt.outreach_notes || []), { at: nowIso(), text: rt.outcome_summary }];
      return { ok: true };
    default:
      return { ok: true };
  }
}

function summaryNote(p: Patient): string {
  const rt: Runtime = p.runtime || {};
  const booked = rt.booked_slot;
  const lines = [
    `MISSED TREATMENT OUTREACH · ${p.first_name} ${p.last_name} (${p.mpi})`,
    `Missed run: ${p.missed?.weekday} ${p.missed?.time}, ${p.missed?.chair}. Outreach by Maya (AI) via phone; identity verified by name.`,
    `Reason: ${rt.barrier_detail || rt.barrier || "not captured"}.`,
  ];
  const screen = rt.symptom_screen as Record<string, unknown> | null | undefined;
  if (screen) {
    const pos = Object.entries(screen).filter(([, v]) => v === true).map(([k]) => k.replaceAll("_", " "));
    lines.push(`Symptom screen: ${pos.length ? `reports ${pos.join(", ")}` : "negative"}; denies chest pain, SOB, confusion.`);
  }
  if (booked) lines.push(`Makeup: ${booked.chair}, ${booked.weekday} ${booked.time} (${booked.shift}). Patient accepted.`);
  (rt.escalations || []).forEach((e) => lines.push(`Task: ${e.owner} (${e.name}): ${e.reason} [${e.urgency}]`));
  lines.push(`Outcome: ${rt.outcome}. Worklist row closed.`);
  return lines.join("\n");
}

function at(ms: number, fn: () => void) {
  timers.push(window.setTimeout(fn, ms / speed));
}

export function stopMock() {
  timers.forEach((t) => window.clearTimeout(t));
  timers = [];
}

/** Starts the scripted call; returns the mock session id. */
export function startMockCall(patientId: string, channel: "linphone" | "browser" | "chat" | "sms"): string {
  stopMock();
  const src = mockPatients().find((p) => p.id === patientId) || getState().patients.find((p) => p.id === patientId);
  if (!src) throw new Error("Unknown mock patient");
  working = clone(src);
  working.runtime = freshRuntime(working);
  dispatch({ type: "patient", patient: clone(working) });

  const sid = `mock-${Date.now().toString(36)}`;
  activeSid = sid;
  const send = (ev: Parameters<typeof ingest>[0]) => {
    if (activeSid === sid) ingest(ev);
  };

  let t = 0;
  if (channel === "linphone") {
    at(250, () => send({ type: "session", session_id: sid, patient_id: patientId, channel, direction: "outbound", status: "ringing" }));
    t = 2600;
  } else {
    t = channel === "browser" ? 900 : 300;
  }
  at(t, () => send({ type: "session", session_id: sid, patient_id: patientId, channel, direction: "outbound", status: "live" }));
  t += 700;

  for (const beat of scriptFor(working)) {
    if (beat.k === "agent") {
      const ws = words(beat.text);
      const dur = Math.max(1400, ws.length * 300);
      // Agent text arrives sentence-by-sentence as TTS speaks.
      const sentences = beat.text.match(/[^.!?]+[.!?]+/g) || [beat.text];
      let off = 0;
      for (const s of sentences) {
        const text = s.trim();
        at(t + off, () => send({ type: "transcript", session_id: sid, role: "agent", text, final: true, ts: Date.now() }));
        off += Math.max(700, words(text).length * 300);
      }
      t += Math.max(dur, off) + 500;
    } else if (beat.k === "patient") {
      const ws = words(beat.text);
      ws.forEach((_, i) => {
        if (i === ws.length - 1) return;
        at(t + i * 260, () => send({ type: "transcript", session_id: sid, role: "patient", text: ws.slice(0, i + 1).join(" "), final: false, ts: Date.now() }));
      });
      t += ws.length * 260 + 200;
      at(t, () => send({ type: "transcript", session_id: sid, role: "patient", text: beat.text, final: true, ts: Date.now() }));
      t += 350;
    } else if (beat.k === "latency") {
      const total = beat.total;
      const stt = Math.round(total * 0.22);
      const tts = Math.round(total * 0.16);
      at(t + 200, () => send({ type: "latency", session_id: sid, stt_ms: stt, llm_ms: total - stt - tts, tts_ms: tts, total_ms: total }));
    } else if (beat.k === "tool") {
      const { name, args } = beat;
      at(t, () => {
        if (activeSid !== sid) return;
        const result = applyTool(name, args);
        send({ type: "tool", session_id: sid, name, args, result, ts: Date.now() });
        if (working) send({ type: "chart", session_id: sid, patient: clone(working) });
      });
      t += 650;
    } else if (beat.k === "pause") {
      t += beat.ms;
    }
  }
  at(t, () => send({ type: "session", session_id: sid, patient_id: patientId, channel, direction: "outbound", status: "ended", reason: "completed" }));
  at(t + 1800, () => {
    if (working) send({ type: "summary", session_id: sid, note: summaryNote(working) });
  });
  return sid;
}

/** Typed text during a mock session: echo it, then Maya acknowledges. */
export function mockMessage(text: string) {
  const sid = activeSid;
  if (!sid) return;
  window.setTimeout(() => ingest({ type: "transcript", session_id: sid, role: "patient", text, final: true, ts: Date.now() }), 150);
  window.setTimeout(() => {
    ingest({ type: "latency", session_id: sid, stt_ms: 0, llm_ms: 980, tts_ms: 210, total_ms: 1190 });
    ingest({ type: "transcript", session_id: sid, role: "agent", text: "Got it, I've added that to your note for the care team.", final: true, ts: Date.now() });
  }, 1400);
}

export function endMockCall() {
  const sid = activeSid;
  stopMock();
  if (!sid) return;
  ingest({ type: "session", session_id: sid, status: "ended", reason: "hung_up" });
  window.setTimeout(() => {
    if (working && activeSid === sid) ingest({ type: "summary", session_id: sid, note: summaryNote(working) });
  }, 1200);
}
