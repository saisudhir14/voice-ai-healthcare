import { ChangeDetectionStrategy, Component } from '@angular/core';
import { MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-about',
  standalone: true,
  imports: [MatDialogModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 mat-dialog-title>Care that follows through.</h2>
    <mat-dialog-content>
      <p>Maya is an automated outreach teammate. She checks in after a missed treatment,
        listens to what happened, and helps the patient find a way back.</p>
      <ol>
        <li>Confirm identity and understand the missed treatment.</li>
        <li>Check symptoms and route concerns to the care team.</li>
        <li>Offer a makeup chair and help arrange transportation.</li>
        <li>Document the outcome for the reschedule lead.</li>
      </ol>
      <p>This demonstration uses synthetic patients, schedules and charts.</p>
    </mat-dialog-content>
    <mat-dialog-actions align="end"><button mat-flat-button mat-dialog-close>Got it</button></mat-dialog-actions>`,
  styles: `p, li { line-height: 1.7; } li { margin-bottom: 10px; } ol { padding-left: 20px; }`,
})
export class AboutComponent {}
