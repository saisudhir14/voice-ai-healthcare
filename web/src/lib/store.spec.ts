import { describe, expect, it } from 'vitest';
import { getState, reduce } from './store';
import { mockPatients } from './mockData';
import { chairList, labSeries, symptomScreen, worklistView } from './chart';
import type { Session } from './types';

const session: Session = {
  id: 'pending', patientId: 'james-cole', channel: 'chat',
  direction: 'outbound', status: 'connecting', createdAt: 100,
};
const initial = () => reduce(getState(), { type: 'loaded', facility: null, patients: mockPatients() });

describe('Outreach event ordering', () => {
  it('replays transcript and session events received before the REST session ID', () => {
    let state = reduce(initial(), { type: 'begin', session });
    state = reduce(state, { type: 'server', event: { type: 'session', session_id: 'chat-1', status: 'live' } });
    state = reduce(state, { type: 'server', event: { type: 'transcript', session_id: 'chat-1', role: 'agent', text: 'Hello James.', final: true } });
    expect(state.items).toHaveLength(0);
    state = reduce(state, { type: 'adopt', sessionId: 'chat-1' });
    expect(state.session?.status).toBe('live');
    expect(state.items.at(-1)).toMatchObject({ kind: 'turn', role: 'agent', text: 'Hello James.' });
    expect(state.buffer['chat-1']).toBeUndefined();
  });

  it('confirms an optimistic typed reply without duplicating it', () => {
    let state = reduce(initial(), { type: 'begin', session: { ...session, id: 'chat-1', status: 'live' } });
    state = reduce(state, { type: 'localTurn', text: 'Tomorrow works.' });
    state = reduce(state, { type: 'server', event: { type: 'transcript', session_id: 'chat-1', role: 'patient', text: 'Tomorrow works.', final: true } });
    expect(state.items).toHaveLength(1);
    expect(state.items[0]).toMatchObject({ text: 'Tomorrow works.', local: false, typed: true });
  });

  it('locks patient selection during outreach and clears old transcripts when switching after it ends', () => {
    let state = reduce(initial(), { type: 'begin', session });
    state = reduce(state, { type: 'localTurn', text: 'A reply for James' });
    expect(reduce(state, { type: 'select', id: 'rosa-alvarez' }).selectedId).toBe('james-cole');
    state = reduce(state, { type: 'localEnd' });
    state = reduce(state, { type: 'select', id: 'rosa-alvarez' });
    expect(state.selectedId).toBe('rosa-alvarez');
    expect(state.session).toBeNull();
    expect(state.items).toHaveLength(0);
  });

  it('adopts inbound SMS and preserves its previously buffered transcript', () => {
    let state = initial();
    state = reduce(state, { type: 'server', event: { type: 'transcript', session_id: 'sms-1', role: 'patient', text: 'Hi Maya' } });
    state = reduce(state, { type: 'server', event: { type: 'session', session_id: 'sms-1', patient_id: 'rosa-alvarez', direction: 'inbound', channel: 'sms', status: 'live' } });
    expect(state.selectedId).toBe('rosa-alvarez');
    expect(state.session).toMatchObject({ id: 'sms-1', channel: 'sms', direction: 'inbound' });
    expect(state.items.at(-1)).toMatchObject({ text: 'Hi Maya' });
  });

  it('applies chart writes for another patient without changing the active patient', () => {
    const patient = { ...mockPatients()[1], runtime: { emergency: true } };
    const state = reduce(initial(), { type: 'server', event: { type: 'chart', patient } });
    expect(state.selectedId).toBe('james-cole');
    expect(state.patients.find((item) => item.id === patient.id)?.runtime?.emergency).toBe(true);
  });
});

describe('Clinical data compatibility', () => {
  it('normalizes the three clinic lab payload shapes and retains abnormal flags', () => {
    const patients = mockPatients();
    expect(patients.map((patient) => labSeries(patient).find((lab) => lab.label === 'K+')?.values)).toEqual([
      [5.1, 5.8], [4.9, 4.6], [5.6, 6.1],
    ]);
    expect(labSeries(patients[2]).find((lab) => lab.label === 'K+')?.flag).toBe('high');
  });

  it('keeps a booked chair visible after it leaves open inventory', () => {
    const patient = mockPatients()[0];
    const booked = patient.open_chairs![0];
    patient.open_chairs = patient.open_chairs!.slice(1);
    patient.runtime = { ...patient.runtime, booked_slot: booked };
    expect(chairList(patient)).toContainEqual({ ...booked, booked: true });
  });

  it('preserves positive symptoms and explicit worklist completion', () => {
    const patient = mockPatients()[0];
    patient.runtime = { ...patient.runtime, symptom_screen: { swelling: true, chest_pain: false } };
    patient.worklist_row = { status: 'Completed', reason_code: 'transport' };
    expect(symptomScreen(patient)?.items).toEqual([
      { label: 'Swelling', value: 'Yes', positive: true },
      { label: 'Chest pain', value: 'No', positive: false },
    ]);
    expect(worklistView(patient, true)).toMatchObject({ status: 'Completed', reason: 'Transportation' });
  });
});
