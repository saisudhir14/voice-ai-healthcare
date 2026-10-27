import { AfterViewInit, ChangeDetectionStrategy, Component, DestroyRef, ElementRef, inject, input, viewChild } from '@angular/core';
import { levels } from '../lib/speech';

@Component({
  selector: 'app-voice-orb',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<canvas #canvas width="440" height="440" aria-hidden="true"></canvas>`,
  styles: `:host { display: block; width: 190px; height: 190px; } canvas { width: 100%; height: 100%; display: block; }`,
})
export class VoiceOrbComponent implements AfterViewInit {
  readonly mode = input('idle');
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('canvas');
  private readonly destroyRef = inject(DestroyRef);

  ngAfterViewInit(): void {
    const ctx = this.canvas().nativeElement.getContext('2d');
    if (!ctx) { return; }
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    let previous = '';
    let amplitude = 0;
    const draw = (time: number): void => {
      const mode = this.mode();
      const reduced = motion.matches;
      if (reduced && previous === mode) {
        frame = requestAnimationFrame(draw);
        return;
      }
      previous = mode;
      const t = reduced ? 0 : time / 1000;
      const talking = mode === 'agent' || mode === 'patient';
      const fresh = performance.now() - levels.at < 600;
      const volume = mode === 'patient' ? levels.patient : levels.agent;
      const target = talking ? (fresh ? volume * 2 + .15 : .3 + .4 * Math.abs(Math.sin(t * 5))) : .04;
      amplitude += (target - amplitude) * .12;
      const patient = mode === 'patient';
      const ended = mode === 'ended';
      const palette = ended ? ['#8494a6', '#cad4e0', '#6a7991'] :
        patient ? ['#26796e', '#7fcea9', '#1c799d'] : ['#154f9b', '#53c8ec', '#3753b7'];
      ctx.clearRect(0, 0, 440, 440);
      const halo = ctx.createRadialGradient(220, 225, 90, 220, 225, 205);
      halo.addColorStop(0, ended ? '#dce3eb99' : '#7bb9e84d');
      halo.addColorStop(1, '#a3dcf000');
      ctx.fillStyle = halo;
      ctx.fillRect(0, 0, 440, 440);
      for (let layer = 0; layer < 3; layer++) {
        ctx.beginPath();
        for (let i = 0; i <= 128; i++) {
          const angle = i / 128 * Math.PI * 2;
          const wave = Math.sin(angle * 3 + t * .8 + layer) * .6 + Math.sin(angle * 5 - t * 1.2) * .3;
          const radius = (124 - layer * 7) * (1 + wave * (.025 + amplitude * .12));
          const x = 220 + Math.cos(angle) * radius;
          const y = 220 + Math.sin(angle) * radius;
          if (i === 0) { ctx.moveTo(x, y); } else { ctx.lineTo(x, y); }
        }
        ctx.closePath();
        const gradient = ctx.createLinearGradient(130, 120 + Math.sin(t * .5) * 40, 310, 320);
        gradient.addColorStop(0, palette[(layer + 1) % 3]);
        gradient.addColorStop(1, palette[layer]);
        ctx.globalAlpha = layer === 0 ? 1 : .54;
        ctx.fillStyle = gradient;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      const sheen = ctx.createRadialGradient(175, 152, 4, 205, 205, 125);
      sheen.addColorStop(0, '#ffffffaa');
      sheen.addColorStop(.55, '#ffffff16');
      sheen.addColorStop(1, '#ffffff00');
      ctx.fillStyle = sheen;
      ctx.beginPath(); ctx.arc(220, 220, 125, 0, Math.PI * 2); ctx.fill();
      if (mode === 'connecting' || mode === 'ringing') {
        ctx.strokeStyle = '#0069b180';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(220, 220, 148, t * 2, t * 2 + Math.PI * 1.4); ctx.stroke();
      }
      frame = requestAnimationFrame(draw);
    };
    frame = requestAnimationFrame(draw);
    this.destroyRef.onDestroy(() => cancelAnimationFrame(frame));
  }
}
