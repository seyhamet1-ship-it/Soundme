// Phase 11F — Diagnostic: blob-URL revocation when the job leaves the UI.
//
// The completed-job page creates two blob URLs (dubbed video + source video)
// via the `job.status === 'completed'` effect. When the job stops being
// displayed, that effect's cleanup must revoke both URLs — otherwise the blobs
// stay alive for the whole lifetime of the document.
//
// The only in-app transition that moves a *completed* job out of the UI is
// picking a new file (`pick()` → `F(file)` + `J(null)`). Flipping a mocked
// job status does NOT work: the watcher stops as soon as the job reaches a
// terminal state, so the app never sees the mocked "failed" status.
//
// Usage: PLAYWRIGHT_JOB_ID=<jobId> npx playwright test e2e/phase11f_leak.spec.js

import path from 'node:path';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';

const JOB_ID = process.env.PLAYWRIGHT_JOB_ID || '';
const TOKEN = process.env.PLAYWRIGHT_TOKEN || 'soundme-test-token';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.resolve(HERE, '../../tests/fixtures/resumable_test.mp4');

test.describe('Phase 11F blob leak', () => {
  test.skip(!JOB_ID, 'PLAYWRIGHT_JOB_ID is required');
  test.skip(!existsSync(FIXTURE), `missing fixture ${FIXTURE}`);

  test('object URLs are revoked when the completed job is cleared', async ({ page }) => {
    let hits = 0;

    await page.addInitScript(
      ([jobId, token]) => {
        localStorage.setItem('soundme_token', token);
        localStorage.setItem('activeJob', jobId);

        window.__blob = { created: [], revoked: [] };
        const origCreate = URL.createObjectURL.bind(URL);
        const origRevoke = URL.revokeObjectURL.bind(URL);
        URL.createObjectURL = (obj) => {
          const u = origCreate(obj);
          window.__blob.created.push(u);
          return u;
        };
        URL.revokeObjectURL = (u) => {
          window.__blob.revoked.push(u);
          return origRevoke(u);
        };
      },
      [JOB_ID, TOKEN],
    );

    // Serve the job as "completed" so the players (and their blob URLs) are
    // rendered. The glob must cover the nested `/events` path as well,
    // otherwise the real (already-completed) job is served and the status
    // never reaches the app through SSE.
    await page.route('**/api/jobs/**', async (route) => {
      const url = route.request().url();
      if (url.includes('/download/')) return route.continue();
      hits += 1;
      const body = {
        id: JOB_ID, status: 'completed', stage: 'completed', progress: 100,
        filename: 'sample_input.mp4', outputs: {},
      };
      if (url.includes('/events')) {
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          headers: { 'Cache-Control': 'no-cache' },
          body: `data: ${JSON.stringify(body)}\n\n`,
        });
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      });
    });

    await page.goto('/');
    await expect(page.locator('video.dubbed-player')).toBeVisible({ timeout: 60_000 });
    await page.waitForTimeout(4000);

    const step1 = await page.evaluate(() => ({ ...window.__blob }));
    console.log('AFTER_COMPLETED=', JSON.stringify(step1));
    expect(step1.created.length).toBeGreaterThanOrEqual(2);

    // Snapshot the URLs created by the completed-job effect. The local-file
    // preview URL that `pick()` creates afterwards belongs to a different
    // effect and is revoked on unmount, so it is excluded from the assertion.
    const playerUrls = step1.created.slice();

    // ── The realistic transition: the user picks a new file ─────────────────
    await page.setInputFiles('input[type=file]', FIXTURE);
    await expect(page.locator('video.dubbed-player')).toHaveCount(0, { timeout: 30_000 });
    await page.waitForTimeout(2000);

    const step2 = await page.evaluate(() => ({ ...window.__blob }));
    console.log('AFTER_JOB_CLEARED=', JSON.stringify(step2));

    const leaked = playerUrls.filter((u) => !step2.revoked.includes(u));
    console.log('LEAKED=', JSON.stringify(leaked));
    console.log('REQUEST_HITS=', hits);

    expect(leaked).toEqual([]);
    // The completed-job effect must have created at least two URLs, and both
    // must be revoked once the job is cleared.
    expect(playerUrls.length).toBeGreaterThanOrEqual(2);
    expect(step2.revoked.length).toBeGreaterThanOrEqual(playerUrls.length);
  });
});
