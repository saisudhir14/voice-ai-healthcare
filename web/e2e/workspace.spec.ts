import { expect, test } from '@playwright/test';
import { mockFacility, mockPatients, mockSip } from '../src/lib/mockData';

test('patient selection, clinical context and dialog keyboard navigation', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/?mock=1');
  await expect(page.getByRole('heading', { name: 'Meet Maya.' })).toBeVisible();
  await page.getByRole('button', { name: /Rosa Alvarez/ }).click();
  await expect(page.getByRole('heading', { name: 'Rosa Alvarez', exact: true })).toBeVisible();
  await page.getByRole('tab', { name: 'Clinical context' }).click();
  await expect(page.getByRole('cell', { name: '4.6 mEq/L' })).toBeVisible();
  await page.getByRole('button', { name: 'About Village Line' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.getByRole('button', { name: 'About Village Line' })).toBeFocused();
  expect(errors).toEqual([]);
});

test('complete replay writes the chart, chair, tasks and outreach note', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/?mock=1&speed=100');
  await page.getByRole('button', { name: 'Call my phone' }).click();
  await expect(page.getByRole('heading', { name: 'CWOW outreach note' })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole('heading', { name: 'Call complete' })).toBeVisible();
  const chart = page.getByRole('complementary', { name: 'Patient chart' });
  await expect(chart.getByText('Transportation', { exact: true }).first()).toBeVisible();
  await expect(chart.getByText('Makeup treatment held', { exact: true })).toBeVisible();
  await expect(chart.getByText('Social work arranging', { exact: true })).toBeVisible();
  await expect(chart.getByText('Charge nurse', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Start another outreach' }).click();
  await expect(page.getByRole('heading', { name: 'Meet Maya.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'CWOW outreach note' })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('chat accepts typed replies and browser mode supports mute and hangup', async ({ page }) => {
  await page.goto('/?mock=1');
  await page.getByRole('button', { name: 'Chat', exact: true }).click();
  const input = page.getByRole('textbox', { name: 'Reply as James' });
  await expect(input).toBeEnabled();
  await input.fill('My daughter can drive me tomorrow.');
  await page.getByRole('button', { name: 'Send reply' }).click();
  await expect(page.getByText('My daughter can drive me tomorrow.', { exact: true })).toHaveCount(1);
  await page.getByRole('button', { name: 'End conversation', exact: true }).click();
  await page.getByRole('button', { name: 'Start another outreach' }).click();
  await page.getByRole('button', { name: 'Browser voice', exact: true }).click();
  await page.getByRole('button', { name: 'Mute microphone', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Unmute microphone' })).toHaveAttribute('aria-pressed', 'true');
  await page.getByRole('button', { name: 'End call', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Call complete' })).toBeVisible();
});

test('mobile workspace has no horizontal overflow and both views are accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/?mock=1');
  await page.getByRole('radio', { name: 'Patient chart', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Patient chart' })).toBeVisible();
  await page.getByRole('tab', { name: 'Clinical context' }).click();
  await expect(page.getByRole('heading', { name: 'Lab trends' })).toBeVisible();
  await page.getByRole('radio', { name: 'Conversation', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Conversation', exact: true })).toBeVisible();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test('REST start, SSE event replay, message and hangup preserve the API contract', async ({ page }) => {
  const patients = mockPatients();
  await page.route('**/api/clinic', (route) => route.fulfill({ json: { facility: mockFacility, patients } }));
  await page.route('**/api/sip/status', (route) => route.fulfill({ json: { ...mockSip, registered: false } }));
  await page.route('**/api/clinic/*/reset', (route) => route.fulfill({ json: patients[0] }));
  // Real EventSource, with events intentionally arriving before the REST response.
  await page.route('**/api/events', (route) => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      { type: 'session', session_id: 'test-chat', patient_id: patients[0].id, channel: 'chat', status: 'live' },
      { type: 'transcript', session_id: 'test-chat', role: 'agent', text: 'Hello from the clinic.', final: true },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
  }));
  await page.route('**/api/chat/start', async (route) => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().postDataJSON()).toEqual({ patient_id: 'james-cole' });
    await route.fulfill({ json: { session_id: 'test-chat' } });
  });
  await page.route('**/api/session/test-chat/message', (route) => route.fulfill({ json: { ok: true } }));
  await page.route('**/api/hangup', (route) => route.fulfill({ json: { ok: true } }));
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Call my phone' })).toBeDisabled();
  await page.getByRole('button', { name: 'Chat', exact: true }).click();
  await expect(page.getByText('Hello from the clinic.', { exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Reply as James' }).fill('Tomorrow works');
  const sent = page.waitForRequest('**/api/session/test-chat/message');
  await page.getByRole('button', { name: 'Send reply' }).click();
  expect((await sent).postDataJSON()).toEqual({ text: 'Tomorrow works' });
  const hungUp = page.waitForRequest('**/api/hangup');
  await page.getByRole('button', { name: 'End conversation', exact: true }).click();
  expect((await hungUp).postDataJSON()).toEqual({ session_id: 'test-chat' });
});

test('phone conflict is actionable and leaves controls recoverable', async ({ page }) => {
  await page.route('**/api/clinic', (route) => route.fulfill({ json: { facility: mockFacility, patients: mockPatients() } }));
  await page.route('**/api/clinic/*/reset', (route) => route.fulfill({ json: mockPatients()[0] }));
  await page.route('**/api/sip/status', (route) => route.fulfill({ json: mockSip }));
  await page.route('**/api/events', (route) => route.fulfill({ contentType: 'text/event-stream', body: ': connected\n\n' }));
  await page.route('**/api/call', (route) => route.fulfill({ status: 409, json: { detail: 'Not registered' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Call my phone' }).click();
  await expect(page.getByRole('alert')).toContainText("Linphone isn't registered yet");
  await page.getByRole('button', { name: 'Start another outreach' }).click();
  await expect(page.getByRole('button', { name: 'Chat', exact: true })).toBeEnabled();
});
