import { afterRenderEffect, ChangeDetectionStrategy, Component, computed, ElementRef, inject, signal, viewChild } from '@angular/core';
import { DatePipe, JsonPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar } from '@angular/material/snack-bar';
import { WorkspaceService } from './workspace.service';
import { IconComponent } from './icon.component';
import { talkingPoints } from '../lib/chart';
import { displayName, firstName, formatMs, toolView } from '../lib/format';

@Component({
  selector: 'app-conversation',
  standalone: true,
  imports: [DatePipe, JsonPipe, FormsModule, MatButtonModule, MatInputModule, MatTooltipModule, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './conversation.component.html',
  styleUrl: './conversation.component.scss',
})
export class ConversationComponent {
  readonly workspace = inject(WorkspaceService);
  private readonly snackbar = inject(MatSnackBar);
  readonly list = viewChild<ElementRef<HTMLElement>>('list');
  readonly composer = viewChild<ElementRef<HTMLInputElement>>('composer');
  readonly draft = signal('');
  readonly isSending = signal(false);
  readonly isFollowing = signal(true);
  readonly displayName = displayName;
  readonly firstName = firstName;
  readonly formatMs = formatMs;
  readonly points = computed(() => {
    const patient = this.workspace.patient();
    const points = talkingPoints(patient);
    return points.length ? points : patient?.notes ? [patient.notes] : [];
  });
  readonly rows = computed(() => {
    const { items } = this.workspace.state();
    const patient = this.workspace.patient();
    return items.map((item) => ({
      item,
      tool: item.kind === 'tool' ? toolView(item.name, item.args, item.result, patient) : null,
    }));
  });
  readonly stats = computed(() => {
    const items = this.workspace.state().items;
    const totals = items.flatMap((item) => item.kind === 'turn' && item.latency?.total_ms !== undefined ? [item.latency.total_ms] : []);
    return { tools: items.filter((item) => item.kind === 'tool').length,
      average: totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : undefined };
  });
  readonly canSend = computed(() => this.workspace.session()?.status === 'live' && !this.isSending());
  private focusedSession = '';

  constructor() {
    // Scrolling and focus are DOM side effects, not derived application state.
    afterRenderEffect(() => {
      this.rows();
      this.workspace.state().summary;
      const list = this.list()?.nativeElement;
      if (list && this.isFollowing()) {
        list.scrollTop = list.scrollHeight;
      }
      const session = this.workspace.session();
      if (session?.channel === 'chat' && session.status === 'live' && session.id !== this.focusedSession) {
        this.composer()?.nativeElement.focus({ preventScroll: true });
        this.focusedSession = session.id;
      }
    });
  }

  onScroll(): void {
    const element = this.list()?.nativeElement;
    if (element) { this.isFollowing.set(element.scrollHeight - element.scrollTop - element.clientHeight < 80); }
  }

  follow(): void {
    this.isFollowing.set(true);
  }

  async send(): Promise<void> {
    const text = this.draft().trim();
    if (!text || !this.canSend()) { return; }
    this.isSending.set(true);
    this.draft.set('');
    this.follow();
    try { await this.workspace.send(text); } finally { this.isSending.set(false); }
  }

  async copyNote(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.workspace.state().summary || '');
      this.snackbar.open('Outreach note copied', 'Dismiss', { duration: 2500 });
    } catch {
      this.snackbar.open('Copy unavailable. Select the note to copy it manually.', 'Dismiss');
    }
  }
}
