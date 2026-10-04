import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

const id = 'a'.repeat(43);
const base = 'https://api.sounddrop.test';
const url = 'https://youtu.be/BaW_jenozKc';
const job = {
  id, stage: 'queued', bitrate: 192, title: 'A quiet morning', duration: 125,
  progress: null, queue_position: 1, created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 3600000).toISOString(), error: null,
};

async function mockHealth(page: Page) {
  await page.route(`${base}/api/health`, route => route.fulfill({ json: { status: 'ready' } }));
}

test('layout, validation, keyboard controls, and FAQ', async ({ page }, info) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your video. Just the audio.' })).toBeVisible();
  await page.getByLabel('YouTube video link').fill('https://example.com');
  await page.getByRole('button', { name: 'Convert to MP3' }).click();
  await expect(page.getByRole('alert')).toContainText('Enter a YouTube');
  await page.getByLabel('YouTube video link').fill(url);
  const first = page.getByRole('radio').first();
  await first.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('radio').nth(1)).toBeChecked();
  await page.getByText('Which audio quality should I choose?').click();
  await expect(page.getByText(/192 kbps is a good balance/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Clear link' }).click();
  await page.screenshot({ path: info.outputPath('converter.png'), fullPage: true });
});

test('single submission, actual stages, direct download, and new conversion', async ({ page }) => {
  await mockHealth(page);
  let submissions = 0;
  let checks = 0;
  await page.route(`${base}/api/jobs`, async route => {
    submissions++;
    expect(route.request().postDataJSON()).toEqual({ url, bitrate: 320 });
    await route.fulfill({ status: 202, json: { ...job, bitrate: 320 } });
  });
  await page.route(`${base}/api/jobs/${id}`, route => {
    checks++;
    return route.fulfill({ json: { ...job, bitrate: 320,
      stage: checks === 1 ? 'downloading' : checks === 2 ? 'converting' : 'ready',
      progress: checks === 1 ? 42 : null,
    } });
  });
  await page.route(`${base}/api/jobs/${id}/download`, route => route.fulfill({
    body: 'ID3fixture', headers: { 'Content-Type': 'audio/mpeg', 'Content-Disposition': 'attachment; filename="morning.mp3"' },
  }));
  await page.goto('/');
  await page.getByLabel('YouTube video link').fill(url);
  await page.getByRole('radio').nth(2).check();
  await page.getByRole('button', { name: 'Convert to MP3' }).click();
  await expect(page.getByRole('progressbar')).toHaveAttribute('value', '42');
  await expect(page.getByRole('link', { name: 'Download MP3' })).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download MP3' }).click();
  expect((await download).suggestedFilename()).toBe('morning.mp3');
  expect(submissions).toBe(1);
  await page.getByRole('button', { name: 'Convert another video' }).click();
  await expect(page.getByRole('button', { name: 'Convert to MP3' })).toBeEnabled();
});

test('refresh restores the current job without submitting again', async ({ page }) => {
  await page.addInitScript(saved => sessionStorage.setItem('sounddrop.current-job', JSON.stringify(saved)), { id, url, bitrate: 192 });
  await page.route(`${base}/api/jobs/${id}`, route => route.fulfill({ json: { ...job, stage: 'ready' } }));
  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Download MP3' })).toBeVisible();
  await expect(page.getByText(/^Available until /)).toBeVisible();
  await page.reload();
  await expect(page.getByRole('link', { name: 'Download MP3' })).toBeVisible();
});

test('lost jobs explain a restart and permit starting over', async ({ page }) => {
  await page.addInitScript(saved => sessionStorage.setItem('sounddrop.current-job', JSON.stringify(saved)), { id, url, bitrate: 192 });
  await page.route(`${base}/api/jobs/${id}`, route => route.fulfill({ status: 404, json: { detail: { code: 'job_missing', message: 'This job expired or the server restarted. Start a new conversion.' } } }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('server restarted');
  await page.getByRole('button', { name: 'Start over' }).click();
  await expect(page.getByRole('button', { name: 'Convert to MP3' })).toBeEnabled();
});

test('ambiguous submission is never automatically repeated', async ({ page }) => {
  await mockHealth(page);
  let submissions = 0;
  await page.route(`${base}/api/jobs`, route => { submissions++; return route.abort('failed'); });
  await page.goto('/');
  await page.getByLabel('YouTube video link').fill(url);
  await page.getByRole('button', { name: 'Convert to MP3' }).click();
  await expect(page.getByRole('alert')).toContainText('could not confirm your submission');
  expect(submissions).toBe(1);
});

test('cold starts show a connecting state before sending a job', async ({ page }) => {
  let wake: (() => void) | undefined;
  const gate = new Promise<void>(resolve => { wake = resolve; });
  await page.route(`${base}/api/health`, async route => {
    await gate;
    await route.fulfill({ json: { status: 'ready' } });
  });
  await page.route(`${base}/api/jobs`, route => route.fulfill({ status: 429, json: { detail: { message: 'Hourly conversion limit reached.' } } }));
  await page.goto('/');
  await page.getByLabel('YouTube video link').fill(url);
  await page.getByRole('button', { name: 'Convert to MP3' }).click();
  await expect(page.getByRole('heading', { name: 'Connecting to conversion server' })).toBeVisible();
  wake?.();
  await expect(page.getByRole('alert')).toContainText('Hourly conversion limit');
});

test('copy uses server limits, and connecting can be cancelled without submitting', async ({ page }) => {
  let converting = false;
  await page.route(`${base}/api/health`, route => route.fulfill(converting
    ? { status: 503, json: { status: 'unavailable' } }
    : { json: { status: 'ready', limits: { max_duration_seconds: 1800, file_ttl_seconds: 7200 } } }));
  let submissions = 0;
  await page.route(`${base}/api/jobs`, route => { submissions++; return route.abort(); });
  await page.goto('/');
  await expect(page.locator('#url-hint')).toHaveText('YouTube videos and Shorts · up to 30 minutes');
  await page.getByText('How long is my download available?').click();
  await expect(page.getByText(/deleted after 2 hours/)).toBeVisible();
  await page.getByLabel('YouTube video link').fill(url);
  converting = true;
  await page.getByRole('button', { name: 'Convert to MP3' }).click();
  await expect(page.getByRole('heading', { name: 'Connecting to conversion server' })).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByRole('button', { name: 'Convert to MP3' })).toBeEnabled();
  await expect(page.getByLabel('YouTube video link')).toBeFocused();
  expect(submissions).toBe(0);
});

test('unconfigured builds never simulate conversion success', async ({ page }) => {
  await page.goto('http://127.0.0.1:5174');
  await expect(page.getByText('The converter is not connected yet. Please check back soon.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Convert to MP3' })).toBeDisabled();
});
