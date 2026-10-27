import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { MatTabsModule } from '@angular/material/tabs';
import { WorkspaceService } from './workspace.service';
import { IconComponent } from './icon.component';
import { ClinicalChartComponent } from './clinical-chart.component';
import { FlashDirective } from './flash.directive';
import { chairList, confirmationSent, symptomScreen, worklistView } from '../lib/chart';
import { displayName, humanize, initials, languageName, ownerLabel, slotLabel, urgencyTone } from '../lib/format';

@Component({
  selector: 'app-chart-panel',
  standalone: true,
  imports: [MatTabsModule, IconComponent, ClinicalChartComponent, FlashDirective],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chart-panel.component.html',
  styleUrl: './chart-panel.component.scss',
})
export class ChartPanelComponent {
  readonly workspace = inject(WorkspaceService);
  readonly displayName = displayName;
  readonly initials = initials;
  readonly languageName = languageName;
  readonly humanize = humanize;
  readonly ownerLabel = ownerLabel;
  readonly urgencyTone = urgencyTone;
  readonly slotLabel = slotLabel;
  readonly steps = ['Open', 'In Progress', 'Completed'];
  readonly chart = computed(() => {
    const patient = this.workspace.patient();
    const isActive = this.workspace.isActive();
    if (!patient) { return null; }
    return {
      patient,
      work: worklistView(patient, isActive),
      chairs: chairList(patient),
      confirmation: confirmationSent(patient),
      screen: symptomScreen(patient),
      transport: patient.runtime?.transport_status || patient.transportation?.status,
    };
  });
}
