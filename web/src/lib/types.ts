/* Shapes follow AGENTS.md + server/app/clinic.py `public_patient`.
 * Everything beyond the core contract is optional: the clinic store is being
 * enriched, so every renderer must read defensively. */

export type Role = "agent" | "patient";
export type SessionStatus = "connecting" | "ringing" | "live" | "ended";
export type Channel = "linphone" | "browser" | "chat" | "sms";

export interface Slot {
  id: string;
  date_label?: string;
  spoken?: string;
  weekday?: string;
  time?: string;
  shift?: string;
  chair?: string;
  pod?: string;
  available?: boolean;
}

export interface Escalation {
  owner?: string;
  name?: string;
  reason?: string;
  urgency?: string;
  at?: string;
}

export interface Facility {
  name?: string;
  code?: string;
  address?: string;
  phone?: string;
  nephrologist?: string;
  charge_nurse?: string;
  social_worker?: string;
  facility_admin?: string;
  reschedule_lead?: string;
}

export interface Runtime {
  identity_verified?: boolean;
  barrier?: string | null;
  barrier_detail?: string | null;
  offered_slots?: Slot[];
  booked_slot?: Slot | null;
  transport_status?: string;
  outcome?: string;
  outcome_summary?: string | null;
  escalations?: Escalation[];
  emergency?: boolean;
  outreach_attempts?: number;
  outreach_notes?: { at?: string; text?: string | null }[];
  last_event_at?: string | null;
  symptom_screen?: unknown;
  language?: string | null;
  confirmation_sent?: boolean | string | null;
  contact_log?: { attempt: number; at: string; result: string; label?: string }[];
  next_attempt?: { at: string; attempt: number; of: number } | null;
  [key: string]: unknown;
}

export interface WorklistRow {
  id?: string;
  worklist?: string;
  status?: string;
  reason_code?: string | null;
  reason?: string | null;
  attempts?: number;
  attempt_count?: number;
  linked_run?: unknown;
  outcome?: string | null;
  owner?: string;
  assigned_to?: string;
  due?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface MissEvent {
  date?: string;
  type?: string;
  minutes_short?: number;
  reason_code?: string;
  reason_label?: string;
  note?: string;
}

export interface Patient {
  id: string;
  mpi?: string;
  first_name?: string;
  last_name?: string;
  preferred_name?: string;
  age?: number;
  preferred_language?: string;
  phone?: string;
  persona?: string;
  persona_blurb?: string;
  modality?: string;
  days_on_dialysis?: number;
  admission_date?: string;
  schedule?: {
    pattern?: string;
    spoken_days?: string;
    shift?: string;
    time?: string;
    chair?: string;
    pod?: string;
    duration_hours?: number;
  };
  missed?: {
    date?: string;
    weekday?: string;
    time?: string;
    chair?: string;
    minutes_since?: number;
    status?: string;
    reason_code?: string;
  };
  transportation?: { type?: string; status?: string; detail?: string };
  insurance_status?: string;
  consent_status?: string | { status?: string; voice_outreach?: boolean; sms_outreach?: boolean };
  access?: { type?: string; type_label?: string; location?: string; note?: string };
  clinical?: {
    target_dry_weight_kg?: number;
    last_post_weight_kg?: number;
    idwg_kg?: number;
    idwg_flag?: string;
    hospitalization?: unknown;
    [key: string]: unknown;
  };
  miss_history?: (string | MissEvent)[];
  notes?: string;
  runtime?: Runtime;
  facility?: Facility;
  open_chairs?: Slot[];
  worklist_row?: WorklistRow | null;
  labs?: unknown;
  idwg?: unknown;
  meds?: unknown;
  medications?: unknown;
  hospitalizations?: unknown;
  talking_points?: unknown;
  [key: string]: unknown;
}

export interface SipStatus {
  registered: boolean;
  user?: string | null;
  contact?: string | null;
  server_ip?: string;
  sip_port?: number | string;
  domain?: string;
}

export interface Latency {
  stt_ms?: number;
  llm_ms?: number;
  tts_ms?: number;
  total_ms?: number;
}

export type ServerEvent =
  | {
      type: "session";
      session_id: string;
      patient_id?: string;
      channel?: string;
      direction?: string;
      status: string;
      reason?: string;
      callback?: boolean;
      attempt?: number;
      of?: number;
    }
  | {
      type: "transcript";
      session_id: string;
      role: Role;
      text: string;
      final?: boolean;
      ts?: number | string;
    }
  | {
      type: "tool";
      session_id: string;
      name: string;
      args?: Record<string, unknown>;
      result?: unknown;
      ts?: number | string;
    }
  | { type: "chart"; session_id?: string; patient: Patient }
  | ({ type: "latency"; session_id: string } & Latency)
  | { type: "summary"; session_id: string; note: string }
  | { type: "sip"; registered: boolean; user?: string | null; contact?: string | null };

export interface Session {
  id: string;
  patientId: string;
  channel: string;
  direction: string;
  status: SessionStatus;
  reason?: string;
  createdAt: number;
  liveAt?: number;
  endedAt?: number;
}

export type Item =
  | {
      kind: "turn";
      id: string;
      role: Role;
      text: string;
      interim: string;
      ts: number;
      latency?: Latency;
      local?: boolean;
      typed?: boolean;
    }
  | {
      kind: "tool";
      id: string;
      name: string;
      args: Record<string, unknown>;
      result: unknown;
      ts: number;
    }
  | { kind: "note"; id: string; text: string; tone: "info" | "ok" | "warn" | "alert"; ts: number };
