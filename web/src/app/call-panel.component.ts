import { ChangeDetectionStrategy, Component, computed, DestroyRef, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatSnackBar } from '@angular/material/snack-bar';
import { WorkspaceService } from './workspace.service';
import { VoiceOrbComponent } from './voice-orb.component';
import { IconComponent } from './icon.component';
import { clock, displayName, firstName, initials, languageName } from '../lib/format';

@Component({
  selector: 'app-call-panel',
  standalone: true,
  imports: [MatButtonModule, MatTooltipModule, VoiceOrbComponent, IconComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './call-panel.component.html',
  styleUrl: './call-panel.component.scss',
})
export class CallPanelComponent {
  readonly workspace = inject(WorkspaceService);
  private readonly snackbar = inject(MatSnackBar);
  readonly displayName = displayName;
  readonly initials = initials;
  readonly languageName = languageName;
  readonly isEnding = signal(false);
  readonly now = signal(Date.now());
  readonly isSecure = window.isSecureContext;
  readonly channelNames: Record<string, string> = { browser: 'Browser voice', linphone: 'Phone call', sms: 'Text message', chat: 'Text chat' };
  readonly mode = computed(() => {
    const { session, speaker } = this.workspace.state();
    if (!session) { return 'idle'; }
    return session.status === 'live' ? speaker || 'listening' : session.status;
  });
  readonly caption = computed(() => {
    const mode = this.mode();
    const session = this.workspace.session();
    const text = session?.channel === 'chat' || session?.channel === 'sms';
    const name = firstName(this.workspace.patient());
    switch (mode) {
      case 'connecting': return 'Connecting…';
      case 'ringing': return session?.direction === 'inbound' ? 'Incoming call' : `Calling ${name}…`;
      case 'agent': return text ? 'Maya is replying' : 'Maya is speaking';
      case 'patient': return text ? `${name} replied` : `${name} is speaking`;
      case 'listening': return text ? 'Ready for your reply' : 'Maya is listening';
      case 'ended': return text ? 'Conversation complete' : 'Call complete';
      default: return 'Meet Maya.';
    }
  });
  readonly elapsed = computed(() => {
    const session = this.workspace.session();
    const now = this.now();
    return session?.liveAt ? clock(((session.endedAt ?? now) - session.liveAt) / 1000) : '00:00';
  });
  readonly domain = computed(() => {
    const sip = this.workspace.state().sip;
    return sip?.domain || (sip?.server_ip ? `${sip.server_ip}:${sip.sip_port || 5060}` : '');
  });

  constructor() {
    const timer = window.setInterval(() => {
      if (this.workspace.isActive()) { this.now.set(Date.now()); }
    }, 1000);
    inject(DestroyRef).onDestroy(() => window.clearInterval(timer));
  }

  async end(): Promise<void> {
    this.isEnding.set(true);
    try { await this.workspace.end(); } finally { this.isEnding.set(false); }
  }

  async copyDomain(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.domain());
      this.snackbar.open('Phone domain copied', 'Dismiss', { duration: 2500 });
    } catch {
      this.snackbar.open('Copy unavailable. Select and copy the domain above.', 'Dismiss');
    }
  }
}
