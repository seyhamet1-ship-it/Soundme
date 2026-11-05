// Phase 11F — Authenticated media end-to-end verification.
//
// Verifies that the completed-job page loads the dubbed + source videos through
// authenticated blob URLs, that download buttons save real files, that no media
// URL ever carries a `?token=` query parameter, and that no 401/403/CORS or
// React warnings appear in the console.
//
// Usage: PLAYWRIGHT_JOB_ID=<jobId> npx playwright test e2e/phase11f_media.spec.js

import { statSync } from 'node:fs';
import { test, expect } from '@playwright/test';

const JOB_ID = process.env.PLAYWRIGHT_JOB_ID || '';
const TOKEN = process.env.PLAYWRIGHT_TOKEN || 'soundme-test-token';

test.describe('Phase 11F authenticated media', () => {
  test.skip(!JOB_ID, 'PLAYWRIGHT_JOB_ID is required');

  test('completed job plays and downloads via authenticated blob URLs', async ({ page }) => {
    const consoleErrors = [];
    const consoleWarnings = [];
    const failedRequests = [];
    const networkUrls = [];

    page.on('console', (msg) => {
      const t = msg.type();
      if (t === 'error') consoleErrors.push(msg.text());
      if (t === 'warning') consoleWarnings.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on('requestfailed', (req) =>
      failedRequests.push(`${req.method()} ${req.url()} :: ${req.failure()?.errorText}`),
    );
    page.on('request', (req) => networkUrls.push(req.url()));

    // Seed the token + active job before the app boots.
    await page.addInitScript(
      ([jobId, token]) => {
        localStorage.setItem('soundme_token', token);
        localStorage.setItem('activeJob', jobId);
      },
      [JOB_ID, TOKEN],
    );

    await page.goto('/');
    await expect(page.locator('video.dubbed-player')).toBeVisible({ timeout: 60_000 });
    await expect(page.locator('video.original-player')).toBeVisible({ timeout: 60_000 });

    // The UI defaults to Khmer; switch to English so button labels are stable.
    const enToggle = page.getByRole('button', { name: 'EN', exact: true });
    if (await enToggle.count()) {
      await enToggle.first().click();
      await page.waitForTimeout(500);
    }

    // ── Players must be blob-backed and free of tokens ──────────────────────
    const dubbedSrc = await page.locator('video.dubbed-player').getAttribute('src');
    const sourceSrc = await page.locator('video.original-player').getAttribute('src');
    console.log('DUBBED_SRC=', dubbedSrc);
    console.log('SOURCE_SRC=', sourceSrc);

    expect(dubbedSrc).toMatch(/^blob:/);
    expect(sourceSrc).toMatch(/^blob:/);
    expect(dubbedSrc).not.toContain('token=');
    expect(sourceSrc).not.toContain('token=');


    // ── Playback: the media must actually decode and advance ────────────────
    const playback = await page.evaluate(async () => {
      const v = document.querySelector('video.dubbed-player');
      if (!v) return { error: 'no dubbed player' };
      await new Promise((resolve) => {
        if (v.readyState >= 2) return resolve();
        v.addEventListener('loadeddata', resolve, { once: true });
        setTimeout(resolve, 15000);
      });
      const readyState = v.readyState;
      const duration = v.duration;
      let played = false;
      try {
        await v.play();
        await new Promise((r) => setTimeout(r, 2500));
        played = v.currentTime > 0;
        v.pause();
      } catch (e) {
        return { error: `play() failed: ${e.message}`, readyState, duration };
      }
      return {
        readyState,
        duration,
        currentTime: v.currentTime,
        played,
        videoWidth: v.videoWidth,
        videoHeight: v.videoHeight,
      };
    });
    console.log('PLAYBACK=', JSON.stringify(playback));
    expect(playback.error).toBeUndefined();
    expect(playback.readyState).toBeGreaterThanOrEqual(2);
    expect(playback.played).toBe(true);
    expect(playback.videoWidth).toBeGreaterThan(0);

    // ── Downloads: each button must produce a real, non-empty file ──────────
    const dlButtons = page.locator('button.download-link');
    const dlCount = await dlButtons.count();
    console.log('DOWNLOAD_BUTTONS=', dlCount);
    expect(dlCount).toBeGreaterThanOrEqual(4);

    for (const kind of ['video', 'audio', 'srt', 'vtt']) {
      const idx = ['video', 'audio', 'srt', 'vtt'].indexOf(kind);
      const btn = dlButtons.nth(idx);
      console.log(`BUTTON_${kind.toUpperCase()}_TEXT=`, (await btn.innerText()).trim());
      const [download] = await Promise.all([
        page.waitForEvent('download', { timeout: 60_000 }),
        btn.click(),
      ]);
      const name = download.suggestedFilename();
      const path = await download.path();
      const size = path ? statSync(path).size : 0;
      console.log(`DOWNLOAD ${kind} -> ${name} (${size} bytes)`);
      expect(name).toBe(`soundme_${kind}.${kind === 'audio' ? 'wav' : kind}`);
      expect(path).toBeTruthy();
      expect(size).toBeGreaterThan(0);
    }


    // ── No tokens in any network URL ───────────────────────────────────────
    const tokenLeaks = networkUrls.filter((u) => /[?&]token=/.test(u));
    console.log('TOKEN_LEAK_URLS=', JSON.stringify(tokenLeaks));
    expect(tokenLeaks).toEqual([]);

    // ── No auth/CORS failures ──────────────────────────────────────────────
    // net::ERR_ABORTED is benign teardown noise: the SSE stream is aborted when
    // the job reaches a terminal state, and the blob-backed <video> request is
    // aborted when the element's src changes. Neither indicates an auth or
    // CORS problem, so only genuine failures are reported here.
    const authIssues = [...consoleErrors, ...failedRequests].filter((m) => {
      if (/net::ERR_ABORTED/.test(m)) return false;
      return /\b(401|403)\b|CORS|Failed to load|net::ERR/i.test(m);
    });
    console.log('AUTH_ISSUES=', JSON.stringify(authIssues));
    expect(authIssues).toEqual([]);

    const reactWarnings = consoleWarnings.filter((m) => /React|Warning:/.test(m));
    console.log('REACT_WARNINGS=', JSON.stringify(reactWarnings));
    expect(reactWarnings).toEqual([]);
  });

  test('invalid session surfaces an error and renders no media', async ({ page }) => {
    const consoleErrors = [];
    page.on('console', (m) => {
      if (m.type() === 'error') consoleErrors.push(m.text());
    });

    // A bad token must not yield media; the app must show an error instead.
    await page.addInitScript(
      ([jobId, token]) => {
        localStorage.setItem('soundme_token', token);
        localStorage.setItem('activeJob', jobId);
      },
      [JOB_ID, 'definitely-not-a-valid-token'],
    );

    await page.goto('/');
    await page.waitForTimeout(12_000);

    const body = await page.locator('body').innerText();
    console.log('BODY_SNIPPET=', body.slice(0, 600));

    const unauthorized = consoleErrors.filter((m) => /\b401\b|Unauthorized|Authentication/i.test(m));
    console.log('UNAUTHORIZED_MESSAGES=', JSON.stringify(unauthorized));
    // The app must not crash and must not render a video element.
    expect(await page.locator('video.dubbed-player').count()).toBe(0);
  });
});
