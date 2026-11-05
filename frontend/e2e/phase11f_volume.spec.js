// Phase 11F — Diagnostic: request volume while a COMPLETED job is on screen.
//
// The polling effect now stops once the job reaches a terminal status, so the
// browser must stop hitting /api/jobs/{id} (and re-fetching the full /api/jobs
// history list) every ~1.3 s forever. The backend enforces 120 rpm/user, so
// unbounded polling caused HTTP 429 during long-lived sessions.
//
// Usage: PLAYWRIGHT_JOB_ID=<jobId> npx playwright test e2e/phase11f_volume.spec.js

import { test, expect } from '@playwright/test';

const JOB_ID = process.env.PLAYWRIGHT_JOB_ID || '';
const TOKEN = process.env.PLAYWRIGHT_TOKEN || 'soundme-test-token';

test.describe('Phase 11F request volume', () => {
  test.skip(!JOB_ID, 'PLAYWRIGHT_JOB_ID is required');

  test('counts requests over a 30 s window with a completed job', async ({ page }) => {
    const counts = {};
    const t0 = Date.now();
    const codes = {};

    await page.addInitScript(
      ([jobId, token]) => {
        localStorage.setItem('soundme_token', token);
        localStorage.setItem('activeJob', jobId);
      },
      [JOB_ID, TOKEN],
    );

    page.on('request', (r) => {
      const u = new URL(r.url());
      if (!u.pathname.startsWith('/api/')) return;
      const key = u.pathname.replace(/[a-f0-9]{32}/g, '{id}');
      counts[key] = (counts[key] || 0) + 1;
    });
    page.on('response', async (res) => {
      const u = new URL(res.url());
      if (!u.pathname.startsWith('/api/')) return;
      const key = `${res.status()} ${u.pathname.replace(/[a-f0-9]{32}/g, '{id}')}`;
      codes[key] = (codes[key] || 0) + 1;
    });

    await page.goto('/');
    await page.waitForTimeout(30_000);

    const elapsed = (Date.now() - t0) / 1000;
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const perMin = (total / elapsed) * 60;
    console.log('ELAPSED_S=', elapsed.toFixed(1));
    console.log('COUNTS=', JSON.stringify(counts));
    console.log('CODES=', JSON.stringify(codes));
    console.log('REQUESTS_PER_MIN=', perMin.toFixed(1));
    expect(Object.keys(counts).length).toBeGreaterThan(0);

    // The completed job must not keep the 1.3 s poll loop alive.
    const polls = counts['/api/jobs/{id}'] || 0;
    console.log('POLL_COUNT=', polls);
    expect(polls).toBeLessThanOrEqual(1);

    // The SSE endpoint must be authenticated (was 401 before the fix).
    expect(codes['200 /api/jobs/{id}/events'] || 0).toBeGreaterThan(0);

    // No rate limiting (429) may occur, and the overall rate must stay far
    // below the backend's 120 rpm/user budget.
    const rateLimited = Object.keys(codes).filter((k) => k.startsWith('429'));
    console.log('RATE_LIMITED=', JSON.stringify(rateLimited));
    expect(rateLimited).toEqual([]);
    expect(perMin).toBeLessThan(60);
  });
});
