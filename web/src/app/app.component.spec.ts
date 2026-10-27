import { computed, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppComponent } from './app.component';
import { WorkspaceService } from './workspace.service';
import { getState, selectedPatient } from '../lib/store';
import { mockFacility, mockPatients, mockSip } from '../lib/mockData';

describe('Angular workspace', () => {
  const state = signal(getState());
  const session = computed(() => state().session);
  const workspace = {
    state: state.asReadonly(),
    patient: computed(() => selectedPatient(state())),
    session,
    isActive: computed(() => !!session() && session()?.status !== 'ended'),
    canStart: computed(() => !session() || session()?.status === 'ended'),
    start: vi.fn(),
    select: vi.fn(),
    send: vi.fn().mockResolvedValue(undefined),
    end: vi.fn().mockResolvedValue(undefined),
    restart: vi.fn(),
    mute: vi.fn(),
    dismissError: vi.fn(),
  };

  beforeEach(() => {
    state.set({ ...getState(), loaded: true, mock: true, link: 'mock', facility: mockFacility,
      patients: mockPatients(), selectedId: 'james-cole', sip: mockSip, session: null, items: [], summary: null });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    // jsdom has no Web Animations implementation. We test the resulting chart
    // and controls here; motion/layout are covered by the Firefox suite.
    vi.stubGlobal('matchMedia', () => ({
      matches: true, addListener: vi.fn(), removeListener: vi.fn(),
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
    }));
    TestBed.configureTestingModule({
      imports: [AppComponent],
      providers: [{ provide: WorkspaceService, useValue: workspace }],
    });
  });
  afterEach(() => {
    TestBed.resetTestingModule();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it('renders patients, Material actions and chart, and delegates outreach commands', async () => {
    const fixture = TestBed.createComponent(AppComponent);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('Meet Maya.');
    expect(root.textContent).toContain('James Cole');
    expect(root.textContent).toContain('Missed treatment worklist');
    const buttons = Array.from(root.querySelectorAll('button'));
    const chat = buttons.find((button) => button.textContent?.trim() === 'Chat');
    expect(chat?.disabled).toBe(false);
    chat?.click();
    expect(workspace.start).toHaveBeenCalledWith('chat');
    buttons.find((button) => button.textContent?.includes('Rosa Alvarez'))?.click();
    expect(workspace.select).toHaveBeenCalledWith('rosa-alvarez');
  });

  it('reacts to live transcript signals and submits the typed reply through the service', async () => {
    state.update((current) => ({
      ...current,
      session: { id: 'chat-1', patientId: 'james-cole', channel: 'chat', direction: 'outbound', status: 'live', createdAt: Date.now() },
      items: [{ kind: 'turn', id: 'turn-1', role: 'agent', text: 'Hello James.', interim: '', ts: Date.now() }],
    }));
    const fixture = TestBed.createComponent(AppComponent);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('Hello James.');
    expect(root.textContent).not.toContain('Who are we reaching?');
    const input = root.querySelector<HTMLInputElement>('#reply')!;
    expect(input.disabled).toBe(false);
    input.value = 'Tomorrow at ten works.';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await fixture.whenStable();
    root.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await fixture.whenStable();
    expect(workspace.send).toHaveBeenCalledWith('Tomorrow at ten works.');
    expect(input.value).toBe('');
  });
});
