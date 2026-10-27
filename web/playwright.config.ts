import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  use: {
    baseURL: 'http://127.0.0.1:8174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'firefox', use: { ...devices['Desktop Firefox'], viewport: { width: 1440, height: 1000 } } }],
  webServer: {
    command: 'npm run dev -- --port 8174',
    url: 'http://127.0.0.1:8174',
    reuseExistingServer: !process.env['CI'],
    timeout: 60000,
  },
});
