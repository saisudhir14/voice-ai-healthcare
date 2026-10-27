import { DestroyRef, Directive, ElementRef, inject, input, OnChanges, SimpleChanges } from '@angular/core';

/** Highlight chart writes without animating initial load. */
@Directive({ selector: '[appFlash]', standalone: true })
export class FlashDirective implements OnChanges {
  readonly appFlash = input<unknown>();
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  private animation?: Animation;
  constructor() {
    inject(DestroyRef).onDestroy(() => this.animation?.cancel());
  }
  ngOnChanges(changes: SimpleChanges): void {
    const change = changes['appFlash'];
    if (!change || change.firstChange || change.previousValue === change.currentValue) { return; }
    this.animation?.cancel();
    this.animation = this.element.nativeElement.animate([
      { backgroundColor: '#d9effb', boxShadow: '0 0 0 3px #d9effb' },
      { backgroundColor: 'transparent', boxShadow: '0 0 0 3px transparent' },
    ], { duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1800, easing: 'ease-out' });
  }
}
