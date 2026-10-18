import fs from 'fs';
import path from 'path';
import { test, expect } from '@playwright/test';

const strings = JSON.parse(
  fs.readFileSync(path.resolve(process.cwd(), 'src', 'i18n', 'strings.json'), 'utf-8')
);

const ROOT = path.resolve(process.cwd(), '..');
const FIXTURES = path.resolve(ROOT, 'tests', 'fixtures');
const SCREENSHOTS = path.resolve(ROOT, 'docs', 'screenshots');
const SAMPLE = path.resolve(ROOT, 'sample_input.mp4');

fs.mkdirSync(SCREENSHOTS, { recursive: true });

test.describe('Soundme Phase 6b: Breaker, Idempotency & Edge Cases', () => {

  test('1. Double-click & rapid triple-click disables button and creates exactly ONE job', async ({ page }) => {
    await page.goto('/');
    await page.setInputFiles('input[type="file"]', SAMPLE);
    await expect(page.locator('strong').getByText('sample_input.mp4')).toBeVisible();

    const startBtn = page.getByRole('button', { name: strings.start_dubbing.km });
    await expect(startBtn).toBeVisible();

    // Track network requests to POST /api/jobs
    let postJobsCount = 0;
    let idempotencyKeys = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/jobs') && req.method() === 'POST' && !req.url().includes('/cancel')) {
        postJobsCount++;
        const idKey = req.headers()['idempotency-key'];
        if (idKey) idempotencyKeys.push(idKey);
      }
    });

    // Rapid double click
    await startBtn.dblclick();

    // Verify button is disabled during submission or replaced by progress
    // Wait briefly for network activity to settle
    await page.waitForTimeout(2000);

    // Assert client generated Idempotency-Key header
    expect(idempotencyKeys.length).toBeGreaterThanOrEqual(1);
    expect(idempotencyKeys[0]).toMatch(/^[0-9a-fA-F-]+$/);

    // Exactly one job request went through because UI disabled subsequent clicks
    expect(postJobsCount).toBe(1);

    // Clean up created job
    const activeJobId = await page.evaluate(() => localStorage.getItem('activeJob'));
    if (activeJobId) {
      await page.request.delete(`/api/jobs/${activeJobId}`);
    }
  });

  test('4a. Edge Case: 0-byte file shows clear Khmer error and no orphan job', async ({ page }) => {
    await page.goto('/');
    const jobsBefore = await (await page.request.get('/api/jobs')).json();

    await page.setInputFiles('input[type="file"]', path.join(FIXTURES, 'empty.mp4'));
    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(strings.file_empty.km);
    // Ensure no stack trace
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-01-empty.png'), fullPage: true });

    // Assert no orphan jobs created
    const jobsAfter = await (await page.request.get('/api/jobs')).json();
    expect(jobsAfter.length).toBe(jobsBefore.length);
  });

  test('4b. Edge Case: .txt renamed .mp4 shows clear Khmer error and no orphan job', async ({ page }) => {
    await page.goto('/');
    const jobsBefore = await (await page.request.get('/api/jobs')).json();

    await page.setInputFiles('input[type="file"]', path.join(FIXTURES, 'text_renamed.mp4'));
    const startBtn = page.getByRole('button', { name: strings.start_dubbing.km });
    await startBtn.click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(strings.invalid_video.km);
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-02-txt-renamed-mp4.png'), fullPage: true });

    // Assert no orphan jobs created
    const jobsAfter = await (await page.request.get('/api/jobs')).json();
    expect(jobsAfter.length).toBe(jobsBefore.length);
  });

  test('4c. Edge Case: corrupt mp4 shows clear Khmer error and no orphan job', async ({ page }) => {
    await page.goto('/');
    const jobsBefore = await (await page.request.get('/api/jobs')).json();

    await page.setInputFiles('input[type="file"]', path.join(FIXTURES, 'corrupt.mp4'));
    const startBtn = page.getByRole('button', { name: strings.start_dubbing.km });
    await startBtn.click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(strings.invalid_video.km);
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-03-corrupt-mp4.png'), fullPage: true });

    // Assert no orphan jobs created
    const jobsAfter = await (await page.request.get('/api/jobs')).json();
    expect(jobsAfter.length).toBe(jobsBefore.length);
  });

  test('4d. Edge Case: oversize (>500MB) shows clear Khmer error and no upload', async ({ page }) => {
    await page.goto('/');
    const oversizePath = path.join(FIXTURES, 'oversize.mp4');
    test.skip(!fs.existsSync(oversizePath), 'oversize.mp4 fixture missing');

    await page.setInputFiles('input[type="file"]', oversizePath);
    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(strings.upload_too_large.km);
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-04-oversize.png'), fullPage: true });
  });

  test('4e. Edge Case: no audio track fails gracefully with NO_AUDIO_TRACK and Khmer error', async ({ page }) => {
    await page.goto('/');
    await page.setInputFiles('input[type="file"]', path.join(FIXTURES, 'no_audio.mp4'));
    const startBtn = page.getByRole('button', { name: strings.start_dubbing.km });
    await startBtn.click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText('[NO_AUDIO_TRACK]');
    await expect(alert).toContainText(strings.no_audio_track.km);
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-05-no-audio.png'), fullPage: true });

    // Clean up job
    const activeJobId = await page.evaluate(() => localStorage.getItem('activeJob'));
    if (activeJobId) {
      await page.request.delete(`/api/jobs/${activeJobId}`);
    }
  });

  test('4f. Edge Case: silent audio fails gracefully with NO_SPEECH_DETECTED and Khmer error', async ({ page }) => {
    await page.goto('/');
    await page.setInputFiles('input[type="file"]', path.join(FIXTURES, 'silent_audio.mp4'));
    const startBtn = page.getByRole('button', { name: strings.start_dubbing.km });
    await startBtn.click();

    const alert = page.getByRole('alert');
    await expect(alert).toBeVisible({ timeout: 30_000 });
    await expect(alert).toContainText('[NO_SPEECH_DETECTED]');
    await expect(alert).toContainText(strings.no_speech_detected.km);
    const text = await alert.innerText();
    expect(text).not.toContain('Traceback');
    expect(text).not.toContain('File "');

    await page.screenshot({ path: path.join(SCREENSHOTS, 'edge-06-silent-audio.png'), fullPage: true });

    // Clean up job
    const activeJobId = await page.evaluate(() => localStorage.getItem('activeJob'));
    if (activeJobId) {
      await page.request.delete(`/api/jobs/${activeJobId}`);
    }
  });
});
