import type { Facility, Patient, SipStatus } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Init = RequestInit & { json?: unknown };

async function req<T>(path: string, init?: Init): Promise<T> {
  const opts: Init = init ?? {};
  const { json, ...rest } = opts;
  const res = await fetch(path, {
    ...rest,
    headers: { Accept: "application/json", ...(json !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { detail?: unknown; error?: unknown; message?: unknown };
      const d = body.detail ?? body.error ?? body.message;
      detail = typeof d === "string" ? d : d ? JSON.stringify(d) : "";
    } catch {
      /* not json */
    }
    throw new ApiError(res.status, detail || `${res.status} ${res.statusText}`);
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  health: () => req<{ ok: boolean }>("/api/health"),
  clinic: () => req<{ facility?: Facility; patients?: Patient[] }>("/api/clinic"),
  patient: (id: string) => req<Patient>(`/api/clinic/${encodeURIComponent(id)}`),
  reset: (id: string) => req<Patient>(`/api/clinic/${encodeURIComponent(id)}/reset`, { method: "POST" }),
  sipStatus: () => req<SipStatus>("/api/sip/status"),
  call: (patient_id: string) => req<{ session_id: string }>("/api/call", { method: "POST", json: { patient_id } }),
  hangup: (session_id: string) => req<unknown>("/api/hangup", { method: "POST", json: { session_id } }),
  chatStart: (patient_id: string) =>
    req<{ session_id: string }>("/api/chat/start", { method: "POST", json: { patient_id } }),
  smsStart: (patient_id: string) =>
    req<{ session_id: string }>("/api/sms/start", { method: "POST", json: { patient_id } }),
  message: (session_id: string, text: string) =>
    req<unknown>(`/api/session/${encodeURIComponent(session_id)}/message`, { method: "POST", json: { text } }),
};
