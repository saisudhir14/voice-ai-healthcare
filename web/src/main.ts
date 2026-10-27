import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { AppComponent } from './app/app.component';

bootstrapApplication(AppComponent, appConfig).catch(() => {
  const root = document.querySelector('app-root');
  if (root) {
    root.textContent = 'Village Line could not start. Reload the page to try again.';
  }
});
