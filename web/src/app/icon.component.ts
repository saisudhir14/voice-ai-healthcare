import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

const PATHS: Record<string, string> = {
  phone: 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z',
  mic: 'M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8',
  muted: 'm2 2 20 20M9 9v3a3 3 0 0 0 5 2M15 9V5a3 3 0 0 0-6-.5M5 10v2a7 7 0 0 0 12 5M19 10v2M12 19v3',
  message: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2ZM7 8h10M7 12h6',
  keyboard: 'M4 6h16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2ZM6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8',
  check: 'm5 12 4 4L19 6',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  chevron: 'm9 5 7 7-7 7',
  close: 'm6 6 12 12M6 18 18 6',
  send: 'm22 2-7 20-4-9-9-4 20-7ZM11 13 22 2',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-3-10 2 2 4-4',
  calendar: 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2ZM16 2v4M8 2v4M3 10h18',
  chair: 'M7 3h7a3 3 0 0 1 3 3v6H7ZM5 12h14v3H5ZM7 15v6M17 15v6',
  car: 'M5 17H3v-4l2-5a2 2 0 0 1 2-1h10a2 2 0 0 1 2 1l2 5v4h-2M3 13h18M9 17h6M5 17a2 2 0 1 0 4 0 2 2 0 1 0-4 0M15 17a2 2 0 1 0 4 0 2 2 0 1 0-4 0',
  bell: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M10 21h4',
  alert: 'm12 3 10 18H2L12 3ZM12 9v4M12 17h.01',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6ZM14 2v6h6M8 13h8M8 17h8',
  copy: 'M9 9h12v12H9ZM5 15H3V3h12v2',
  pulse: 'M2 12h5l3-9 4 18 3-9h5',
  search: 'M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0Zm-2 5 5 5',
  tag: 'M2 2h10l10 10-10 10L2 12V2ZM7 7h.01',
  bolt: 'm13 2-10 12h9l-1 8 10-12h-9l1-8Z',
  info: 'M12 8h.01M12 11v6M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
};

/** Local SVGs keep the interface usable offline, with no icon-font requests. */
@Component({
  selector: 'app-icon',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
    stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path [attr.d]="path()" />
  </svg>`,
  styles: `:host { display: inline-flex; width: 20px; height: 20px; flex-shrink: 0; vertical-align: middle; }
    svg { width: 100%; height: 100%; }`,
})
export class IconComponent {
  readonly name = input('pulse');
  readonly path = computed(() => PATHS[this.name()] ?? PATHS['pulse']);
}
