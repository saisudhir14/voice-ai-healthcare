import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Patient } from '../lib/types';
import { hospitalizations, idwgTrend, labSeries, medsView } from '../lib/chart';
import { humanize, reasonLabel } from '../lib/format';
import { SparklineComponent } from './sparkline.component';

@Component({
  selector: 'app-clinical-chart',
  standalone: true,
  imports: [DatePipe, SparklineComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './clinical-chart.component.html',
  styles: `
    :host { display: block; }
    .fluid-values { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .fluid-values dt { font-size: 10px; color: var(--muted); }
    .fluid-values dd { margin: 6px 0 0; font-size: 20px; font-weight: 600; letter-spacing: -.6px; }
    .fluid-values small { font-size: 10px; font-weight: 400; color: var(--muted); letter-spacing: 0; }
    .fluid-trend { display: flex; align-items: center; gap: 16px; padding-top: 10px; font-size: 10px; color: var(--muted); }
    .fluid-trend app-sparkline { flex: 1; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { padding: 10px 4px 10px 0; text-align: right; border-bottom: 1px solid var(--line); }
    th:first-child { text-align: left; }
    thead th { font-size: 10px; font-weight: 400; color: var(--muted); }
    tbody th { font-weight: 600; }
    td small, th small { display: block; color: var(--muted); font-size: 9px; font-weight: 400; margin-top: 3px; }
    td app-sparkline { width: 42px; }
    .abnormal { color: var(--danger); font-weight: 650; }
    .meds, .history { padding: 0; margin: 0; list-style: none; }
    .meds li, .history li { padding: 10px 0; font-size: 11px; line-height: 1.7; border-bottom: 1px solid var(--line); }
    .meds p { margin: 4px 0 0; color: var(--muted); }
    .meds small { display: block; color: var(--warning); margin-top: 4px; }
    h4 { font-size: 11px; margin: 14px 0 8px; }
    .access { font-size: 12px; line-height: 1.7; margin-bottom: 0; }
    .history time { display: block; font-size: 10px; color: var(--muted); }
  `,
})
export class ClinicalChartComponent {
  readonly patient = input.required<Patient>();
  readonly humanize = humanize;
  readonly data = computed(() => {
    const patient = this.patient();
    return {
      labs: labSeries(patient),
      fluid: idwgTrend(patient),
      meds: medsView(patient),
      hospitalizations: hospitalizations(patient),
      consent: typeof patient.consent_status === 'object' ?
        [patient.consent_status.status, patient.consent_status.voice_outreach ? 'Voice outreach OK' : '',
          patient.consent_status.sms_outreach ? 'SMS outreach OK' : ''].filter(Boolean).join(' · ') : patient.consent_status,
      misses: (patient.miss_history || []).map((miss) => typeof miss === 'string' ? miss :
        [miss.date, miss.type === 'shortened' ? `Shortened ${miss.minutes_short || 0} min` : '',
          miss.reason_label || reasonLabel(miss.reason_code), miss.note].filter(Boolean).join(' · ')),
    };
  });
}
