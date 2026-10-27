/* Synthetic clinic used by ?mock=1. Mirrors server/data/clinic.json plus the
 * enrichment fields (worklist_row, labs, idwg, meds, hospitalizations,
 * talking_points, runtime.symptom_screen/language/confirmation_sent).
 * Each patient uses a different labs shape on purpose to exercise the normalizers. */
import type { Facility, Patient, Runtime, SipStatus, Slot } from "./types";

export const mockFacility: Facility = {
  name: "Riverside Dialysis",
  code: "FAC-03436",
  address: "2100 Riverside Drive, Nashville, TN 37206",
  phone: "(615) 555-0140",
  nephrologist: "Dr. Priya Shah",
  charge_nurse: "Keisha Brooks, RN",
  social_worker: "Marcus Vance, MSW",
  facility_admin: "Elena Ortiz, FA",
  reschedule_lead: "Tanya Cole, AA",
};

export const mockChairs: Slot[] = [
  { id: "slot-today-1400", date_label: "today", spoken: "today at 2:00 in the afternoon", weekday: "Monday", time: "2:00 PM", shift: "2nd Shift", chair: "Chair 4", pod: "Pod A", available: true },
  { id: "slot-wed-1000", date_label: "tomorrow", spoken: "tomorrow at 10:00 in the morning", weekday: "Tuesday", time: "10:00 AM", shift: "1st Shift", chair: "Chair 9", pod: "Pod B", available: true },
  { id: "slot-thu-0600", date_label: "Wednesday", spoken: "Wednesday at 6:00 in the morning", weekday: "Wednesday", time: "6:00 AM", shift: "1st Shift", chair: "Chair 2", pod: "Pod A", available: true },
];

export const mockSip: SipStatus = {
  registered: true,
  user: "sai",
  contact: "sip:sai@192.168.1.42:51234;transport=udp",
  server_ip: "192.168.1.20",
  sip_port: 5060,
  domain: "192.168.1.20:5060",
};

export function freshRuntime(p: Patient): Runtime {
  return {
    identity_verified: false,
    barrier: null,
    barrier_detail: null,
    offered_slots: [],
    booked_slot: null,
    transport_status: p.transportation?.status,
    outcome: "OPEN",
    outcome_summary: null,
    escalations: [],
    emergency: false,
    outreach_attempts: 0,
    outreach_notes: [],
    last_event_at: null,
    symptom_screen: null,
    language: p.preferred_language || "en",
    confirmation_sent: false,
  };
}

const base: Patient[] = [
  {
    id: "james-cole",
    mpi: "DV-2206385",
    first_name: "James",
    last_name: "Cole",
    preferred_name: "James",
    age: 58,
    preferred_language: "en",
    phone: "+16155550112",
    persona: "Ride never showed",
    persona_blurb: "Missed this morning after the broker van never arrived. Offer a specific chair and fix the ride.",
    modality: "ICHD",
    days_on_dialysis: 412,
    admission_date: "2025-09-10",
    schedule: { pattern: "Mon · Wed · Fri", spoken_days: "Monday, Wednesday, and Friday", shift: "1st Shift", time: "6:00 AM", chair: "Chair 7", pod: "Pod B", duration_hours: 4 },
    missed: { date: "today", weekday: "Monday", time: "6:00 AM", chair: "Chair 7", minutes_since: 47, status: "potential_miss", reason_code: "unknown" },
    transportation: { type: "Medical broker van", status: "no_show", detail: "Broker pickup window 5:10–5:25 AM. Van never arrived. Two prior ride failures this month." },
    insurance_status: "Medicare Part B · secondary Medicaid",
    consent_status: "signed",
    access: { type: "AV fistula", location: "Left forearm", note: "Mature fistula, last cannulation uneventful Friday." },
    clinical: { target_dry_weight_kg: 82.4, last_post_weight_kg: 84.1, idwg_kg: 3.2, idwg_flag: "high", hospitalization: null },
    miss_history: ["2026-10-13 transport", "2026-10-06 weather"],
    notes: "Usually reliable. Daughter cannot leave work before 8. Needs a later chair or a confirmed ride.",
    worklist_row: {
      id: "WL-88412",
      worklist: "Reschedule & Missed Treatment",
      status: "Open",
      reason_code: null,
      attempts: 0,
      linked_run: { run_id: "TX-5512093", weekday: "Mon", time: "6:00 AM", chair: "Chair 7" },
      owner: "Tanya Cole, AA",
      due: "Today 12:00 PM",
    },
    labs: [
      { name: "K", unit: "mEq/L", values: [{ date: "Sep 15", value: 5.1 }, { date: "Oct 13", value: 5.8 }] },
      { name: "Phos", unit: "mg/dL", values: [{ date: "Sep 15", value: 5.9 }, { date: "Oct 13", value: 6.4 }] },
      { name: "Kt/V", unit: "", values: [{ date: "Sep 15", value: 1.38 }, { date: "Oct 13", value: 1.29 }] },
      { name: "Albumin", unit: "g/dL", values: [{ date: "Sep 15", value: 3.9 }, { date: "Oct 13", value: 3.8 }] },
      { name: "Hgb", unit: "g/dL", values: [{ date: "Sep 15", value: 10.6 }, { date: "Oct 13", value: 10.2 }] },
      { name: "Ca", unit: "mg/dL", values: [{ date: "Sep 15", value: 9.1 }, { date: "Oct 13", value: 8.9 }] },
    ],
    idwg: [
      { date: "Oct 3", kg: 2.1 },
      { date: "Oct 6", kg: 2.6 },
      { date: "Oct 8", kg: 2.3 },
      { date: "Oct 10", kg: 2.8 },
      { date: "Oct 13", kg: 3.4 },
      { date: "Oct 15", kg: 2.9 },
      { date: "Oct 17", kg: 3.2 },
    ],
    meds: [
      { name: "Sevelamer carbonate", dose: "1600 mg", sig: "TID with meals", kind: "Phosphate binder", note: "Skips lunch dose at work" },
      { name: "Cinacalcet", dose: "30 mg", sig: "Daily" },
      { name: "Epoetin alfa", dose: "4,000 units", sig: "IV each treatment", kind: "ESA" },
      { name: "Amlodipine", dose: "10 mg", sig: "Daily" },
    ],
    hospitalizations: [],
    talking_points: [
      "Broker van no-showed 5:10–5:25 AM; third ride failure this month",
      "Daughter can't drive before 8 AM, so a later chair helps",
      "IDWG 3.2 kg and K+ 5.8 trending up: screen for swelling and shortness of breath",
      "Chair 9 tomorrow at 10:00 AM fits his ride window",
    ],
  },
  {
    id: "rosa-alvarez",
    mpi: "DV-4419027",
    first_name: "Rosa",
    last_name: "Alvarez",
    preferred_name: "Rosa",
    age: 64,
    preferred_language: "es",
    phone: "+16155550188",
    persona: "Felt dizzy, stayed home",
    persona_blurb: "Missed today because she felt lightheaded. Check symptoms, then offer a chair.",
    modality: "ICHD",
    days_on_dialysis: 86,
    admission_date: "2026-08-02",
    schedule: { pattern: "Tue · Thu · Sat", spoken_days: "Tuesday, Thursday, and Saturday", shift: "2nd Shift", time: "10:30 AM", chair: "Chair 11", pod: "Pod C", duration_hours: 3.5 },
    missed: { date: "today", weekday: "Tuesday", time: "10:30 AM", chair: "Chair 11", minutes_since: 22, status: "potential_miss", reason_code: "unknown" },
    transportation: { type: "Daughter drives", status: "available", detail: "Daughter can bring her after 1:00 PM today." },
    insurance_status: "Medicare Advantage",
    consent_status: "signed",
    access: { type: "Tunneled CVC", location: "Right IJ", note: "Vein mapping scheduled Nov 4. Hesitant about fistula surgery." },
    clinical: { target_dry_weight_kg: 61.0, last_post_weight_kg: 62.8, idwg_kg: 2.1, idwg_flag: "watch", hospitalization: "Discharged St. Thomas Midtown 2026-09-28, volume overload" },
    miss_history: [],
    notes: "New to dialysis. If she reports chest pain, trouble breathing, or confusion, stop booking and escalate.",
    worklist_row: { id: "WL-88415", status: "Open", attempts: 0, owner: "Tanya Cole, AA" },
    labs: {
      potassium: { value: 4.6, prior: 4.9, unit: "mEq/L" },
      phosphorus: { value: 4.8, prior: 5.2, unit: "mg/dL" },
      ktv: { value: 1.18, prior: 1.22 },
      albumin: { value: 3.4, prior: 3.5, unit: "g/dL" },
      hemoglobin: { value: 9.4, prior: 9.9, unit: "g/dL" },
      calcium: { value: 8.6, prior: 8.8, unit: "mg/dL" },
    },
    idwg: [1.8, 2.4, 2.0, 2.2, 2.1],
    meds: { binder: { name: "Calcium acetate", dose: "667 mg", sig: "2 caps with meals" }, other: ["Midodrine 5 mg pre-treatment", "Iron sucrose 100 mg IV weekly"] },
    hospitalizations: [{ date: "2026-09-24", facility: "St. Thomas Midtown", reason: "Volume overload" }],
    talking_points: ["Prefers Spanish", "Lightheaded this morning: screen for emergent symptoms before booking", "Daughter can drive after 1 PM"],
  },
  {
    id: "deshawn-reed",
    mpi: "DV-7730144",
    first_name: "DeShawn",
    last_name: "Reed",
    preferred_name: "Shawn",
    age: 47,
    preferred_language: "en",
    phone: "+16155550140",
    persona: "Needed a day",
    persona_blurb: "Third miss in two weeks. Tired of the chair. Respect the choice, still document, still offer one time.",
    modality: "ICHD",
    days_on_dialysis: 1205,
    admission_date: "2023-07-11",
    schedule: { pattern: "Mon · Wed · Fri", spoken_days: "Monday, Wednesday, and Friday", shift: "3rd Shift", time: "4:30 PM", chair: "Chair 3", pod: "Pod A", duration_hours: 4 },
    missed: { date: "today", weekday: "Monday", time: "4:30 PM", chair: "Chair 3", minutes_since: 18, status: "confirmed_miss", reason_code: "unknown" },
    transportation: { type: "Self / city bus", status: "available", detail: "Takes the 4:05 bus. No broker." },
    insurance_status: "Medicaid",
    consent_status: "signed",
    access: { type: "AV graft", location: "Right upper arm", note: "Graft thrill present last Friday." },
    clinical: { target_dry_weight_kg: 94.2, last_post_weight_kg: 96.0, idwg_kg: 3.8, idwg_flag: "high", hospitalization: null },
    miss_history: ["2026-10-20 patient declined", "2026-10-13 patient declined"],
    notes: "Frequent misser. IDT already involved. If he declines twice on this call, notify RN and nephrologist. Do not argue.",
    worklist_row: { id: "WL-88419", status: "Open", attempts: 1, owner: "Tanya Cole, AA" },
    labs: [
      { date: "Sep 15", k: 5.6, phos: 7.1, ktv: 1.31, albumin: 3.7, hgb: 11.0, ca: 9.4 },
      { date: "Oct 13", k: 6.1, phos: 7.6, ktv: 1.24, albumin: 3.6, hgb: 10.8, ca: 9.6 },
    ],
    idwg: { trend: [3.1, 3.6, 4.0, 3.5, 3.8] },
    meds: ["Sevelamer 2400 mg TID", "Lisinopril 20 mg daily"],
    hospitalizations: [],
    talking_points: ["Three misses in two weeks", "Do not argue; offer once, document the choice"],
  },
];

export function mockPatients(): Patient[] {
  return base.map((p) => {
    const patient = JSON.parse(JSON.stringify(p)) as Patient;
    patient.facility = { ...mockFacility };
    patient.open_chairs = mockChairs.map((c) => ({ ...c }));
    patient.runtime = freshRuntime(patient);
    return patient;
  });
}
