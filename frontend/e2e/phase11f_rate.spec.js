// Phase 11F — Diagnostic: measure the effective job-poll request rate.
//
// The SSE feed is now consumed with fetch() (so the Authorization header can
// be attached), so a healthy session should be driven by the event stream
// rather than by the 1.3 s polling interval.
//
// This spec asserts the FIXED behaviour:
//   1. The SSE endpoint is reachable with the bearer token (HTTP 200).
//   2. At most one polling interval is active (no doubled request rate).
//   3. The number of /api/jobs/{id} polls stays within the server's
//      120 rpm/user budget.
//
// Usage: PLAYWRIGHT_JOB_ID=<jobId> npx playwright test e2e/phase11f_rate.spec.js

import { test, expect } from '@playwright/test';

const JOB_ID = process.env.PLAYWRIGHT_JOB_ID || '';
const TOKEN = process.env.PLAYWRIGHT_TOKEN || 'soundme-test-token';

test.describe('Phase 11F poll rate', () => {
  test.skip(!JOB_ID, 'PLAYWRIGHT_JOB_ID is required');

  test('measures poll rate over a 12 s window', async ({ page }) => {
    const stamps = [];
    const t0 = Date.now();
    const sseCodes = [];

    await page.addInitScript(
      ([jobId, token]) => {
        localStorage.setItem('soundme_token', token);
        localStorage.setItem('activeJob', jobId);
      },
      [JOB_ID, TOKEN],
    );

    page.on('request', (r) => {
      const u = r.url();
      if (/\/api\/jobs\/[a-f0-9]{32}$/.test(u) || /\/api\/jobs\/[a-f0-9]{32}\?/.test(u)) {
        stamps.push(Date.now() - t0);
      }
    });
    page.on('response', (res) => {
      if (res.url().includes('/events')) sseCodes.push(res.status());
    });

    await page.goto('/');
    await page.waitForTimeout(12_000);

    console.log('POLL_TIMES_MS=', JSON.stringify(stamps));
    console.log('POLL_COUNT=', stamps.length);
    console.log('SSE_CODES=', JSON.stringify(sseCodes));
    const gaps = stamps.slice(1).map((s, i) => s - stamps[i]);
    console.log('POLL_GAPS_MS=', JSON.stringify(gaps));
    // A single 1300 ms interval yields gaps of ~1300 ms; two concurrent
    // intervals yield ~650 ms.
    const median = gaps.length
      ? gaps.slice().sort((a, b) => a - b)[Math.floor(gaps.length / 2)]
      : null;
    console.log('MEDIAN_GAP_MS=', median);
    console.log('POLLS_PER_MIN=', ((stamps.length / 12) * 60).toFixed(1));

    // 1. The authenticated SSE endpoint must be reachable (was 401 before).
    expect(sseCodes.length).toBeGreaterThan(0);
    expect(sseCodes[0]).toBe(200);

    // 2. Never more than one polling interval: gaps must not be ~650 ms.
    if (median !== null) {
      expect(median).toBeGreaterThan(900);
    }

    // 3. Stay well inside the backend's 120 rpm/user limit.
    expect(stamps.length / 12 * 60).toBeLessThan(60);
  });
});
