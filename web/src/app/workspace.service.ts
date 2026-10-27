import { computed, DestroyRef, inject, Injectable } from '@angular/core';
import { appState, dispatch, selectedPatient } from '../lib/store';
import { boot, _teardown, endCall, newCall, selectPatient, sendText, startCall, toggleMic } from '../lib/session';
import type { Channel } from '../lib/types';

/** Angular facade over the shared transport/reducer layer. All view state is
 * read-only; the same actions serve REST, SSE, WebRTC and the offline replay. */
@Injectable({ providedIn: 'root' })
export class WorkspaceService {
  readonly state = appState;
  readonly patient = computed(() => selectedPatient(this.state()));
  readonly session = computed(() => this.state().session);
  readonly isActive = computed(() => {
    const session = this.session();
    return !!session && session.status !== 'ended';
  });
  readonly canStart = computed(() => this.state().loaded && !!this.patient() && !this.isActive());

  constructor() {
    boot();
    inject(DestroyRef).onDestroy(_teardown);
  }

  start(channel: Channel): void { void startCall(channel); }
  async end(): Promise<void> { await endCall(); }
  restart(): void { newCall(); }
  select(id: string): void { selectPatient(id); }
  async send(text: string): Promise<void> { await sendText(text); }
  mute(): void { toggleMic(); }
  dismissError(): void { dispatch({ type: 'error', message: null }); }
}
