import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { WorkspaceService } from './workspace.service';
import { CallPanelComponent } from './call-panel.component';
import { ConversationComponent } from './conversation.component';
import { ChartPanelComponent } from './chart-panel.component';
import { IconComponent } from './icon.component';
import { AboutComponent } from './about.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [MatButtonModule, MatButtonToggleModule, MatDialogModule, CallPanelComponent,
    ConversationComponent, ChartPanelComponent, IconComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  readonly workspace = inject(WorkspaceService);
  private readonly dialog = inject(MatDialog);
  readonly mobileView = signal('conversation');
  readonly linkLabel = computed(() => {
    const link = this.workspace.state().link;
    return { open: 'Connected', connecting: 'Connecting', down: 'Reconnecting', mock: 'Demo replay' }[link];
  });

  showAbout(): void {
    this.dialog.open(AboutComponent, { width: '480px', maxWidth: 'calc(100vw - 32px)' });
  }
}
