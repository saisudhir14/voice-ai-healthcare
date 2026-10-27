import type { Patient, Slot } from "./types";

export type Tone = "ok" | "info" | "warn" | "alert";

export const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
export const str = (v: unknown): string | undefined =>
  typeof v === "string" ? v : typeof v === "number" ? String(v) : undefined;
export const num = (v: unknown): number | undefined => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return undefined;
};

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  const t = String(s).replace(/[_-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
}

export function displayName(p?: Patient | null): string {
  if (!p) return "Patient";
  const first = p.preferred_name || p.first_name || "";
  return [first, p.last_name].filter(Boolean).join(" ") || p.id;
}

export function firstName(p?: Patient | null): string {
  return p?.preferred_name || p?.first_name || "the patient";
}

export function initials(p?: Patient | null): string {
  const a = (p?.preferred_name || p?.first_name || "?").charAt(0);
  const b = (p?.last_name || "").charAt(0);
  return `${a}${b}`.toUpperCase();
}

const LANGS: Record<string, string> = {
  en: "English",
  es: "Spanish",
  "es-us": "Spanish",
  vi: "Vietnamese",
  zh: "Chinese",
  tl: "Tagalog",
  ar: "Arabic",
  fr: "French",
  ht: "Haitian Creole",
  ko: "Korean",
  ru: "Russian",
  pt: "Portuguese",
};

export function languageName(code?: string | null): string {
  if (!code) return "";
  const key = code.toLowerCase();
  return LANGS[key] || LANGS[key.split(/[-_]/)[0]] || humanize(code);
}

const REASONS: Record<string, string> = {
  transport: "Transportation",
  transportation: "Transportation",
  ride: "Transportation",
  illness: "Illness",
  sick: "Illness",
  life: "Personal / life event",
  personal: "Personal / life event",
  declined: "Patient declined",
  refused: "Patient declined",
  hospital: "Hospitalized",
  hospitalized: "Hospitalized",
  weather: "Weather",
  work: "Work conflict",
  other: "Other",
};

export function reasonLabel(code?: string | null): string {
  if (!code || code === "unknown") return "";
  const key = code.toLowerCase().replace(/^mtx[_-]?/, "");
  return REASONS[key] || humanize(code);
}

const OWNERS: Record<string, string> = {
  RN: "Charge nurse",
  NEPH: "Nephrologist",
  MD: "Nephrologist",
  MSW: "Social worker",
  SW: "Social worker",
  FA: "Facility admin",
  AA: "Reschedule lead",
  RD: "Dietitian",
};

export function ownerLabel(owner?: string | null): string {
  if (!owner) return "Care team";
  return OWNERS[owner.toUpperCase()] || owner;
}

const OUTCOMES: Record<string, string> = {
  OPEN: "Open",
  RESCHEDULED: "Rescheduled",
  FORFEITED: "Patient declined",
  UNREACHED: "No answer",
  DIALYZED_ELSEWHERE: "Treated elsewhere",
  CLINICAL_ESCALATION: "Clinical escalation",
};

export function outcomeLabel(o?: string | null): string {
  if (!o) return "";
  return OUTCOMES[o.toUpperCase()] || humanize(o);
}

export function urgencyTone(u?: string | null): Tone {
  const k = (u || "").toLowerCase();
  if (k === "stat" || k === "emergent" || k === "critical") return "alert";
  if (k === "urgent" || k === "high") return "warn";
  return "info";
}

export function slotLabel(slot?: Partial<Slot> | null): string {
  if (!slot) return "";
  const when = [slot.date_label || slot.weekday, slot.time].filter(Boolean).join(" ");
  return [slot.chair, when].filter(Boolean).join(" · ");
}

export function latencyTone(ms?: number): Tone {
  if (ms === undefined) return "info";
  if (ms < 1500) return "ok";
  if (ms < 3000) return "warn";
  return "alert";
}

export function formatMs(ms?: number): string {
  if (ms === undefined || !Number.isFinite(ms)) return "–";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

export function clock(totalSec: number): string {
  const s = Math.max(0, Math.floor(totalSec));
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export interface ToolView {
  label: string;
  detail?: string;
  tone: Tone;
  icon: "check" | "search" | "tag" | "chair" | "car" | "bell" | "alert" | "file" | "message" | "pulse" | "bolt";
}

function findSlot(id: unknown, patient?: Patient | null): Slot | undefined {
  const sid = str(id);
  if (!sid) return undefined;
  const pools: (Slot | null | undefined)[] = [
    patient?.runtime?.booked_slot,
    ...(patient?.open_chairs || []),
    ...(patient?.runtime?.offered_slots || []),
  ];
  return pools.find((s): s is Slot => !!s && s.id === sid);
}

export function toolView(name: string, args: Record<string, unknown>, result: unknown, patient?: Patient | null): ToolView {
  const res: Record<string, unknown> = isObj(result) ? result : {};
  const failed = res.ok === false || !!res.error;
  switch (name) {
    case "verify_identity": {
      const ok = args.verified !== false && res.verified !== false;
      return ok
        ? { label: "Identity verified", detail: "Name and date of birth", tone: "ok", icon: "check" }
        : { label: "Identity not confirmed", detail: "No details shared", tone: "warn", icon: "alert" };
    }
    case "check_open_chairs": {
      const chairs = Array.isArray(res.chairs) ? res.chairs.length : Array.isArray(result) ? result.length : undefined;
      return {
        label: "Checked ScheduleHub",
        detail: chairs !== undefined ? `${chairs} open chair${chairs === 1 ? "" : "s"}` : undefined,
        tone: "info",
        icon: "search",
      };
    }
    case "log_barrier": {
      const code = str(args.barrier) || str(args.reason_code) || str(res.barrier);
      return {
        label: `Reason code: ${reasonLabel(code) || "Captured"}`,
        detail: str(args.detail),
        tone: "info",
        icon: "tag",
      };
    }
    case "book_makeup_treatment": {
      if (failed) {
        return { label: "Chair unavailable", detail: humanize(str(res.error) || "Trying another time"), tone: "warn", icon: "alert" };
      }
      const booked = (isObj(res.booked) ? (res.booked as unknown as Slot) : undefined) || findSlot(args.slot_id, patient);
      const when = booked ? [booked.date_label || booked.weekday, booked.time].filter(Boolean).join(" ") : "";
      return {
        label: booked ? `Held ${booked.chair || "chair"}${when ? ` · ${when}` : ""}` : "Makeup chair held",
        detail: booked?.pod || booked?.shift,
        tone: "ok",
        icon: "chair",
      };
    }
    case "confirm_transport": {
      const s = str(args.status) || "confirmed";
      return { label: "Ride confirmed", detail: s === "confirmed" ? undefined : humanize(s), tone: "ok", icon: "car" };
    }
    case "request_ride_assist":
      return { label: "Ride task to social work", detail: str(args.reason), tone: "info", icon: "car" };
    case "escalate_care_team": {
      const urgency = str(args.urgency) || "routine";
      return {
        label: `Task to ${ownerLabel(str(args.owner))} · ${humanize(urgency)}`,
        detail: str(args.reason),
        tone: urgencyTone(urgency) === "alert" ? "alert" : urgencyTone(urgency) === "warn" ? "warn" : "info",
        icon: "bell",
      };
    }
    case "flag_emergency":
      return { label: "Emergency flagged · booking stopped", detail: str(args.reason), tone: "alert", icon: "alert" };
    case "write_call_outcome":
      return {
        label: `Worklist closed · ${outcomeLabel(str(args.outcome)) || "Outcome written"}`,
        detail: undefined,
        tone: "ok",
        icon: "file",
      };
    case "record_contact_attempt": {
      const attempt = `Attempt ${num(res.attempt) ?? "?"} of ${num(res.of) ?? "?"} · ${humanize(str(res.result) || "no response")}`;
      return {
        label: attempt,
        detail: str(res.callback_at)
          ? `Calling back at ${str(res.callback_at)}`
          : res.handed_to_aa
            ? "Out of attempts · task to reschedule lead"
            : undefined,
        tone: "warn",
        icon: "bell",
      };
    }
    default:
      break;
  }
  if (/symptom|screen/.test(name)) {
    const flagged = Object.entries(args).filter(([, v]) => v === true || v === "yes").length;
    return {
      label: "Symptom screen recorded",
      detail: flagged ? `${flagged} positive` : "No red flags",
      tone: flagged ? "warn" : "ok",
      icon: "pulse",
    };
  }
  if (/confirm|sms|text|message|send/.test(name)) {
    return { label: "Confirmation text sent", detail: str(args.channel) || str(args.to), tone: "ok", icon: "message" };
  }
  if (/language/.test(name)) {
    return { label: `Language set · ${languageName(str(args.language) || str(args.code))}`, tone: "info", icon: "message" };
  }
  return { label: humanize(name), detail: undefined, tone: failed ? "warn" : "info", icon: "bolt" };
}
