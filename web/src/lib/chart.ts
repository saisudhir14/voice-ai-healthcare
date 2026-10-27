/* Defensive normalizers for the CWOW / ScheduleHub chart. The clinic store is
 * being enriched while this UI is built, so each reader accepts several shapes
 * and returns a small, predictable view model (or nothing, and the section hides). */
import { humanize, isObj, num, outcomeLabel, reasonLabel, str } from "./format";
import type { Patient, Runtime, Slot, WorklistRow } from "./types";

/* ---------- worklist row ---------- */

export type WorkStatus = "Open" | "In Progress" | "Completed";

export interface WorklistView {
  id?: string;
  worklist: string;
  status: WorkStatus;
  reason: string;
  reasonDetail?: string;
  attempts: number;
  linkedRun: string;
  outcome: string;
  owner?: string;
  due?: string;
  nextCall?: string;
}

function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function statusFrom(raw?: string): WorkStatus | undefined {
  if (!raw) return undefined;
  const k = raw.toLowerCase().replace(/[^a-z]/g, "");
  if (k === "open" || k === "new" || k === "pending") return "Open";
  if (k === "inprogress" || k === "working" || k === "active" || k === "started") return "In Progress";
  if (k === "completed" || k === "complete" || k === "closed" || k === "done" || k === "resolved") return "Completed";
  return undefined;
}

function runLabel(run: unknown, p: Patient): string {
  if (typeof run === "string") return run;
  if (isObj(run)) {
    const parts = [str(run.run_id) || str(run.id), str(run.weekday) || str(run.date), str(run.time), str(run.chair)].filter(Boolean);
    if (parts.length) return parts.join(" · ");
  }
  const m = p.missed;
  if (!m) return "";
  return [m.weekday || m.date, m.time, m.chair].filter(Boolean).join(" · ");
}

export function worklistView(p: Patient, live: boolean): WorklistView {
  const row: WorklistRow = isObj(p.worklist_row) ? p.worklist_row : {};
  const rt: Runtime = p.runtime || {};
  const outcomeRaw = str(row.outcome) || rt.outcome || "OPEN";
  const derived: WorkStatus =
    rt.outcome_summary || (outcomeRaw !== "OPEN" && !live && (rt.outreach_attempts || 0) > 0)
      ? "Completed"
      : live || rt.identity_verified || rt.barrier || (rt.outreach_attempts || 0) > 0
        ? "In Progress"
        : "Open";
  const status = statusFrom(str(row.status)) || derived;
  const reasonCode = str(row.reason_code) || str(row.reason) || rt.barrier || p.missed?.reason_code;
  return {
    id: str(row.id) || str(row.worklist_id),
    worklist: str(row.worklist) || "Reschedule & Missed Treatment",
    status,
    reason: reasonLabel(reasonCode) || "Not yet captured",
    reasonDetail: rt.barrier_detail || undefined,
    attempts: num(row.attempts) ?? num(row.attempt_count) ?? rt.outreach_attempts ?? 0,
    linkedRun: runLabel(row.linked_run, p),
    outcome: outcomeLabel(outcomeRaw),
    owner: str(row.owner) || str(row.assigned_to) || p.facility?.reschedule_lead,
    due: str(row.due) || str(row.due_by),
    nextCall: rt.next_attempt
      ? `Maya calls back ${clock(rt.next_attempt.at)} · attempt ${rt.next_attempt.attempt} of ${rt.next_attempt.of}`
      : undefined,
  };
}

/* ---------- labs ---------- */

export interface LabSeries {
  key: string;
  label: string;
  unit: string;
  values: number[];
  dates: string[];
  flag?: "high" | "low" | string;
  range?: string;
  lo?: number;
  hi?: number;
}

const LAB_META: Record<string, { label: string; unit: string; lo?: number; hi?: number; order: number }> = {
  k: { label: "K+", unit: "mEq/L", lo: 3.5, hi: 5.5, order: 1 },
  potassium: { label: "K+", unit: "mEq/L", lo: 3.5, hi: 5.5, order: 1 },
  phos: { label: "Phos", unit: "mg/dL", lo: 3.5, hi: 5.5, order: 2 },
  phosphorus: { label: "Phos", unit: "mg/dL", lo: 3.5, hi: 5.5, order: 2 },
  ktv: { label: "Kt/V", unit: "", lo: 1.2, order: 3 },
  spktv: { label: "Kt/V", unit: "", lo: 1.2, order: 3 },
  albumin: { label: "Albumin", unit: "g/dL", lo: 4.0, order: 4 },
  alb: { label: "Albumin", unit: "g/dL", lo: 4.0, order: 4 },
  hgb: { label: "Hgb", unit: "g/dL", lo: 10, hi: 11.5, order: 5 },
  hemoglobin: { label: "Hgb", unit: "g/dL", lo: 10, hi: 11.5, order: 5 },
  hb: { label: "Hgb", unit: "g/dL", lo: 10, hi: 11.5, order: 5 },
  ca: { label: "Ca", unit: "mg/dL", lo: 8.4, hi: 10.2, order: 6 },
  calcium: { label: "Ca", unit: "mg/dL", lo: 8.4, hi: 10.2, order: 6 },
};

const labKey = (s: string) => s.toLowerCase().replace(/\+/g, "").replace(/[^a-z0-9]/g, "");
const NAME_KEYS = ["name", "test", "code", "label", "key", "analyte", "lab"];
const DATE_KEYS = ["date", "month", "drawn", "drawn_at", "collected", "collected_at", "at"];
const VALUE_KEYS = ["value", "v", "result", "latest", "current"];

function pick(o: Record<string, unknown>, keys: string[]): unknown {
  for (const k of keys) if (o[k] !== undefined && o[k] !== null) return o[k];
  return undefined;
}

function pointsFrom(v: unknown): { values: number[]; dates: string[] } {
  const values: number[] = [];
  const dates: string[] = [];
  if (Array.isArray(v)) {
    for (const el of v) {
      const n = num(el);
      if (n !== undefined) {
        values.push(n);
        dates.push("");
      } else if (isObj(el)) {
        const val = num(pick(el, VALUE_KEYS));
        if (val !== undefined) {
          values.push(val);
          dates.push(str(pick(el, DATE_KEYS)) || "");
        }
      }
    }
  }
  return { values, dates };
}

function seriesFromEntry(name: string, e: unknown): LabSeries | null {
  const key = labKey(name);
  const meta = LAB_META[key];
  let values: number[] = [];
  let dates: string[] = [];
  let unit = meta?.unit ?? "";
  let flag: string | undefined;
  let range: string | undefined;

  if (num(e) !== undefined) {
    values = [num(e)!];
    dates = [""];
  } else if (Array.isArray(e)) {
    ({ values, dates } = pointsFrom(e));
  } else if (isObj(e)) {
    const series = pick(e, ["values", "history", "trend", "results", "series", "points"]);
    if (Array.isArray(series)) ({ values, dates } = pointsFrom(series));
    if (!values.length) {
      const prior = num(pick(e, ["prior", "previous", "prev", "last_month"]));
      const cur = num(pick(e, VALUE_KEYS));
      if (prior !== undefined) {
        values.push(prior);
        dates.push(str(pick(e, ["prior_date", "previous_date"])) || "");
      }
      if (cur !== undefined) {
        values.push(cur);
        dates.push(str(pick(e, DATE_KEYS)) || "");
      }
    }
    unit = str(e.unit) ?? str(e.units) ?? unit;
    flag = str(e.flag) || str(e.status) || undefined;
    range = str(e.range) || str(e.target) || str(e.ref) || undefined;
  }
  if (!values.length) return null;
  const latest = values[values.length - 1];
  if (!flag && meta) {
    if (meta.hi !== undefined && latest > meta.hi) flag = "high";
    else if (meta.lo !== undefined && latest < meta.lo) flag = "low";
  }
  if (flag && /^(n|normal|ok|in range|wnl)$/i.test(flag)) flag = undefined;
  if (!range && meta) {
    range = meta.lo !== undefined && meta.hi !== undefined ? `${meta.lo}–${meta.hi}` : meta.lo !== undefined ? `≥ ${meta.lo}` : undefined;
  }
  return { key: meta ? meta.label : key, label: meta?.label || humanize(name), unit, values, dates, flag, range, lo: meta?.lo, hi: meta?.hi };
}

function pivotDraws(rows: Record<string, unknown>[]): LabSeries[] {
  const map = new Map<string, { values: number[]; dates: string[] }>();
  for (const row of rows) {
    const date = str(pick(row, DATE_KEYS)) || "";
    for (const [k, v] of Object.entries(row)) {
      if (DATE_KEYS.includes(k)) continue;
      const n = num(v);
      if (n === undefined) continue;
      const entry = map.get(k) || { values: [], dates: [] };
      entry.values.push(n);
      entry.dates.push(date);
      map.set(k, entry);
    }
  }
  const out: LabSeries[] = [];
  map.forEach((pts, k) => {
    const s = seriesFromEntry(k, pts.values.map((value, i) => ({ value, date: pts.dates[i] })));
    if (s) out.push(s);
  });
  return out;
}

export function labSeries(p: Patient): LabSeries[] {
  let raw: unknown = p.labs ?? (isObj(p.clinical) ? p.clinical.labs : undefined);
  if (!raw) return [];
  let out: LabSeries[] = [];
  if (isObj(raw)) {
    const nested = pick(raw, ["draws", "results", "history", "months", "monthly", "panels"]);
    if (Array.isArray(nested)) raw = nested;
  }
  if (Array.isArray(raw)) {
    const rows = raw.filter(isObj);
    const named = rows.filter((r) => str(pick(r, NAME_KEYS)));
    if (named.length) {
      // group by name (one row per draw, or one row per analyte)
      const groups = new Map<string, Record<string, unknown>[]>();
      for (const r of named) {
        const n = str(pick(r, NAME_KEYS))!;
        groups.set(n, [...(groups.get(n) || []), r]);
      }
      groups.forEach((list, n) => {
        const s =
          list.length === 1
            ? seriesFromEntry(n, list[0])
            : seriesFromEntry(n, { ...list[list.length - 1], values: list.map((r) => ({ value: pick(r, VALUE_KEYS), date: pick(r, DATE_KEYS) })) });
        if (s) out.push(s);
      });
    } else {
      out = pivotDraws(rows);
    }
  } else if (isObj(raw)) {
    for (const [k, v] of Object.entries(raw)) {
      if (["unit", "units", "as_of", "source"].includes(k)) continue;
      const s = seriesFromEntry(k, v);
      if (s) out.push(s);
    }
  }
  const order = (s: LabSeries) => LAB_META[labKey(s.label)]?.order ?? LAB_META[labKey(s.key)]?.order ?? 99;
  const seen = new Set<string>();
  return out
    .sort((a, b) => order(a) - order(b))
    .filter((s) => (seen.has(s.label) ? false : (seen.add(s.label), true)));
}

/* ---------- fluid / IDWG ---------- */

export function idwgTrend(p: Patient): { values: number[]; dates: string[] } {
  const c: Record<string, unknown> = isObj(p.clinical) ? p.clinical : {};
  const sources: unknown[] = [p.idwg, c.idwg_trend, c.idwg_history, c.idwg_series];
  for (const src of sources) {
    if (!src) continue;
    let arr: unknown = src;
    if (isObj(src)) arr = pick(src, ["trend", "history", "values", "series", "points", "last"]);
    if (Array.isArray(arr)) {
      const values: number[] = [];
      const dates: string[] = [];
      for (const el of arr) {
        const n = num(el);
        if (n !== undefined) {
          values.push(n);
          dates.push("");
        } else if (isObj(el)) {
          const v = num(pick(el, ["kg", "idwg_kg", "idwg", "value", "gain_kg"]));
          if (v !== undefined) {
            values.push(v);
            dates.push(str(pick(el, DATE_KEYS)) || "");
          }
        }
      }
      if (values.length) return { values, dates };
    }
  }
  return { values: [], dates: [] };
}

/* ---------- meds ---------- */

export interface MedView {
  name: string;
  dose?: string;
  sig?: string;
  kind?: string;
  note?: string;
  binder: boolean;
}

function medFrom(v: unknown, kind?: string): MedView | null {
  if (typeof v === "string") return { name: v, kind, binder: /binder|sevelamer|calcium acetate|lanthanum|velphoro|auryxia|phoslo|renvela/i.test(`${kind} ${v}`) };
  if (!isObj(v)) return null;
  const name = str(pick(v, ["name", "drug", "med", "medication"]));
  if (!name) return null;
  const k = str(pick(v, ["kind", "class", "category", "type"])) || kind;
  return {
    name,
    dose: str(pick(v, ["dose", "strength"])),
    sig: str(pick(v, ["sig", "frequency", "freq", "schedule", "directions"])),
    kind: k,
    note: str(pick(v, ["note", "adherence", "status", "comment"])),
    binder: v.binder === true || /binder|sevelamer|calcium acetate|lanthanum|velphoro|auryxia|phoslo|renvela/i.test(`${k} ${name}`),
  };
}

export function medsView(p: Patient): MedView[] {
  const raw = p.meds ?? p.medications;
  const out: MedView[] = [];
  if (Array.isArray(raw)) raw.forEach((m) => {
    const v = medFrom(m);
    if (v) out.push(v);
  });
  else if (isObj(raw)) {
    for (const [k, v] of Object.entries(raw)) {
      if (Array.isArray(v)) v.forEach((m) => {
        const x = medFrom(m, k);
        if (x) out.push(x);
      });
      else {
        const x = medFrom(isObj(v) && !v.name ? { ...v, name: humanize(k) } : v, k);
        if (x) out.push(x);
      }
    }
  }
  return out.sort((a, b) => Number(b.binder) - Number(a.binder));
}

/* ---------- hospitalizations ---------- */

export function hospitalizations(p: Patient): { when?: string; where?: string; why?: string; text?: string }[] {
  const raw = p.hospitalizations;
  const out: { when?: string; where?: string; why?: string; text?: string }[] = [];
  if (Array.isArray(raw)) {
    for (const h of raw) {
      if (typeof h === "string") out.push({ text: h });
      else if (isObj(h))
        out.push({
          when: str(pick(h, ["date", "admit_date", "admitted", "discharge_date", "discharged"])),
          where: str(pick(h, ["facility", "hospital", "where"])),
          why: str(pick(h, ["reason", "dx", "diagnosis", "cause"])),
        });
    }
  }
  if (!out.length && typeof p.clinical?.hospitalization === "string") out.push({ text: p.clinical.hospitalization });
  return out;
}

/* ---------- symptom screen ---------- */

export interface ScreenItem {
  label: string;
  value: string;
  positive: boolean;
}

const POS = (v: unknown) => v === true || (typeof v === "string" && /^(yes|y|true|present|positive|reported)$/i.test(v.trim()));
const NEG = (v: unknown) => v === false || (typeof v === "string" && /^(no|n|false|absent|negative|denied|none)$/i.test(v.trim()));

export function symptomScreen(p: Patient): { items: ScreenItem[]; summary?: string; emergent?: boolean } | null {
  const raw = p.runtime?.symptom_screen;
  if (!raw) return null;
  const items: ScreenItem[] = [];
  let summary: string | undefined;
  let emergent: boolean | undefined;
  const add = (label: string, v: unknown) => {
    if (v === null || v === undefined) return;
    const inner = isObj(v) ? pick(v, ["present", "answer", "value", "result"]) : v;
    const positive = POS(inner);
    const value = positive ? "Yes" : NEG(inner) ? "No" : str(inner) || "";
    if (!value) return;
    items.push({ label: humanize(label), value, positive });
  };
  if (Array.isArray(raw)) {
    for (const r of raw) {
      if (isObj(r)) {
        const label = str(pick(r, ["symptom", "name", "question", "label", "item"]));
        if (label) add(label, pick(r, ["present", "answer", "value", "result", "response"]));
      } else if (typeof r === "string") items.push({ label: humanize(r), value: "Reported", positive: true });
    }
  } else if (isObj(raw)) {
    summary = str(pick(raw, ["summary", "result", "outcome", "note"]));
    const em = pick(raw, ["emergent", "emergency", "red_flag"]);
    emergent = typeof em === "boolean" ? em : undefined;
    const nested = pick(raw, ["items", "answers", "symptoms", "responses"]);
    const src = Array.isArray(nested) ? null : isObj(nested) ? nested : raw;
    if (Array.isArray(nested)) {
      const sub = symptomScreen({ ...p, runtime: { ...p.runtime, symptom_screen: nested } });
      if (sub) items.push(...sub.items);
    } else if (src) {
      for (const [k, v] of Object.entries(src)) {
        if (["summary", "result", "outcome", "note", "emergent", "emergency", "red_flag", "at", "completed", "completed_at", "by"].includes(k)) continue;
        if (typeof v === "boolean" || typeof v === "string" || typeof v === "number" || isObj(v)) add(k, v);
      }
    }
  } else if (typeof raw === "string") {
    summary = raw;
  }
  if (!items.length && !summary) return null;
  return { items, summary, emergent };
}

/* ---------- chairs ---------- */

export function chairList(p: Patient): (Slot & { booked: boolean })[] {
  const booked = p.runtime?.booked_slot || null;
  const list: (Slot & { booked: boolean })[] = (p.open_chairs || []).map((s) => ({ ...s, booked: !!booked && booked.id === s.id }));
  if (booked && !list.some((s) => s.id === booked.id)) list.unshift({ ...booked, booked: true });
  return list;
}

/* ---------- talking points ---------- */

export function talkingPoints(p?: Patient | null): string[] {
  const raw = p?.talking_points;
  if (Array.isArray(raw)) {
    return raw
      .map((t) => (typeof t === "string" ? t : isObj(t) ? str(pick(t, ["text", "point", "say", "summary"])) : undefined))
      .filter((t): t is string => !!t);
  }
  if (typeof raw === "string") return [raw];
  return [];
}

export function confirmationSent(p: Patient): string | null {
  const v = p.runtime?.confirmation_sent;
  if (!v) return null;
  if (typeof v === "string") return v;
  return "Text confirmation sent";
}
