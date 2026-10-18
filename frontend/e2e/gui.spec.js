// Browser evidence: renders, live preview, TTS_UNAVAILABLE error, full upload run.
// Runs against real backend on :8000 and captures screenshots at 1280px and 375px.
import fs from 'fs';
import path from 'path';
import { test, expect } from '@playwright/test';

const strings = JSON.parse(
  fs.readFileSync(path.resolve(process.cwd(), 'src', 'i18n', 'strings.json'), 'utf-8')
);

const EVIDENCE = path.resolve(process.cwd(), '..', 'docs', 'evidence');
const SCREENSHOTS = path.resolve(process.cwd(), '..', 'docs', 'screenshots');
const SAMPLE = path.resolve(process.cwd(), '..', 'sample_input.mp4');

async function captureResponsive(page, name) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({ path: path.join(SCREENSHOTS, `${name}-1280.png`), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: path.join(SCREENSHOTS, `${name}-375.png`), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });
}

test.describe('Soundme GUI evidence — Real Mode', () => {
  test('1. renders the Khmer studio landing state (upload state)', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('h1')).toContainText(strings.app_title.km);
    await expect(page.getByText(strings.source_lang.km)).toBeVisible();
    await page.screenshot({ path: path.join(EVIDENCE, 'gui-01-upload.png'), fullPage: true });
    await captureResponsive(page, 'upload');
  });

  test('2. voice preview returns live synthesized audio', async ({ page }) => {
    await page.goto('/');
    const previewResponse = page.waitForResponse(
      (r) => r.url().includes('/api/tts/preview') && r.request().method() === 'POST',
    );
    const previewBtn = page.getByRole('button', { name: /Preview voice|សាកល្បងសំឡេង/ });
    await previewBtn.click();
    const response = await previewResponse;
    expect(response.status()).toBe(200);
    expect(response.headers()['x-tts-provider']).toBeTruthy();

    const contentLength = parseInt(response.headers()['content-length'] || '0', 10);
    expect(contentLength).toBeGreaterThan(1000);

    // Verify raw WAV bytes have RIFF magic header
    const directRes = await page.request.post('/api/tts/preview', {
      data: { voice: 'km-KH-SreymomNeural' },
    });
    expect(directRes.status()).toBe(200);
    const body = await directRes.body();
    expect(body.length).toBeGreaterThan(1000);
    expect(body.subarray(0, 4).toString()).toBe('RIFF');

    await page.screenshot({ path: path.join(EVIDENCE, 'gui-02-preview.png'), fullPage: true });
  });

  test('3. TTS_UNAVAILABLE surfaces as an accessible bilingual error', async ({ page }) => {
    await page.route('**/api/tts/preview', (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          detail:
            'TTS_UNAVAILABLE: Edge TTS -> Azure Speech (if configured) -> MMS failed (edge:Error:down; mms:Error:down).',
        }),
      }),
    );
    await page.goto('/');
    const previewBtn = page.getByRole('button', { name: /Preview voice|សាកល្បងសំឡេង/ });
    await previewBtn.click();
    const alert = page.getByRole('alert');
    await expect(alert).toContainText('TTS_UNAVAILABLE');
    await expect(alert).toContainText('Edge TTS -> Azure Speech (if configured) -> MMS failed');
    await page.screenshot({ path: path.join(EVIDENCE, 'gui-03-tts-unavailable.png'), fullPage: true });
  });

  test('4. failed job cards expose the TTS_UNAVAILABLE code and Khmer message (error state)', async ({ page }) => {
    await page.route('**/api/jobs', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }));
    await page.route('**/api/jobs/job-fail', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'job-fail',
          status: 'failed',
          stage: 'failed',
          progress: 100,
          filename: 'demo.mp4',
          error: {
            code: 'TTS_UNAVAILABLE',
            message_en: 'TTS_UNAVAILABLE: Edge TTS -> Azure Speech (if configured) -> MMS failed (edge:down; mms:down).',
            message_km: strings.tts_unavailable.km,
          },
        }),
      }),
    );
    await page.addInitScript(() => localStorage.setItem('activeJob', 'job-fail'));
    await page.goto('/');
    const alert = page.getByRole('alert');
    await expect(alert).toContainText('[TTS_UNAVAILABLE]');
    // Correct spelling: "មិនអាចបង្កើតសំឡេងខ្មែរបាន"
    await expect(alert).toContainText('មិនអាចបង្កើតសំឡេងខ្មែរបាន');
    await page.screenshot({ path: path.join(EVIDENCE, 'gui-04-tts-unavailable-job.png'), fullPage: true });
    await captureResponsive(page, 'error');
  });

  test('5. full upload run reaches completed downloadable Khmer dub (real mode)', async ({ page }) => {
    // 1. Capture review state screenshot using route
    await page.route('**/api/jobs/job-review-demo', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'job-review-demo',
          status: 'awaiting_review',
          stage: 'translate',
          progress: 50,
          filename: 'sample_input.mp4',
          segments: [
            { id: 's1', start: 0.0, end: 2.1, source_text: 'Hello, welcome to SoundMe.', text_km: 'សួស្តី សូមស្វាគមន៍មកកាន់ SoundMe។' },
            { id: 's2', start: 2.6, end: 5.6, source_text: 'This is a short English test sentence.', text_km: 'នេះជាប្រយោគសាកល្បងភាសាអង់គ្លេសខ្លី។' },
          ],
        }),
      }),
    );

    await page.goto('/');
    await page.evaluate(() => localStorage.setItem('activeJob', 'job-review-demo'));
    await page.reload();
    await expect(page.getByText('សួស្តី សូមស្វាគមន៍មកកាន់ SoundMe។')).toBeVisible();
    await captureResponsive(page, 'review');

    // 2. Clear review fixture and execute real upload
    await page.evaluate(() => localStorage.removeItem('activeJob'));
    await page.reload();
    await page.setInputFiles('input[type="file"]', SAMPLE);
    await expect(page.locator('strong').getByText('sample_input.mp4')).toBeVisible();
    await page.getByRole('button', { name: 'EN' }).click();
    await page.locator('select').first().selectOption('en');

    const uploadResponse = page.waitForResponse(
      (r) => r.url().includes('/api/jobs') && r.request().method() === 'POST',
    );
    await page.getByRole('button', { name: 'Start dubbing' }).click();
    const res = await uploadResponse;
    expect(res.status()).toBe(202);
    const { id: jobId } = await res.json();
    expect(jobId).toBeTruthy();

    // 3. Capture progress state screenshot
    await page.waitForTimeout(1000);
    await captureResponsive(page, 'progress');

    // 4. Wait for completion in real mode (ASR -> NLLB -> Edge/MMS TTS -> ffmpeg)
    const startPoll = Date.now();
    let job = null;
    while (Date.now() - startPoll < 840_000) {
      const resp = await page.request.get(`/api/jobs/${jobId}`);
      if (resp.ok()) {
        job = await resp.json();
        if (job.status === 'completed' || job.status === 'failed') break;
      }
      await page.waitForTimeout(1500);
    }
    expect(job?.status, JSON.stringify(job?.error || null)).toBe('completed');

    // Assert both original and dubbed players are visible
    await expect(page.locator('.original-player')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.dubbed-player')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('Download VIDEO')).toBeVisible({ timeout: 30_000 });

    // 5. Capture completed and history screenshots
    await page.screenshot({ path: path.join(EVIDENCE, 'gui-05-completed.png'), fullPage: true });
    await captureResponsive(page, 'completed');
    await captureResponsive(page, 'history');
  });
});
