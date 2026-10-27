import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

@Component({
  selector: 'app-sparkline',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<svg viewBox="0 0 100 30" role="img" [attr.aria-label]="label() + ': ' + values().join(', ')">
    <polyline [attr.points]="points()" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
  </svg>`,
  styles: `:host { display: inline-block; width: 76px; color: var(--blue); } svg { display: block; width: 100%; height: 30px; overflow: visible; }`,
})
export class SparklineComponent {
  readonly values = input<number[]>([]);
  readonly label = input('Trend');
  readonly points = computed(() => {
    const values = this.values();
    const min = Math.min(...values);
    const max = Math.max(...values);
    return values.map((value, index) =>
      `${values.length > 1 ? 4 + index * 92 / (values.length - 1) : 50},${max === min ? 15 : 26 - (value - min) / (max - min) * 22}`).join(' ');
  });
}
