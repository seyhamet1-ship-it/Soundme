// Screenshots for long-job states: ETA, resume after refresh, and queued at 1280px and 375px.
import path from 'path';
import { test, expect } from '@playwright/test';

const SCREENSHOTS = path.resolve(process.cwd(), '..', 'docs', 'screenshots');

async function captureResponsive(page, name) {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.screenshot({ path: path.join(SCREENSHOTS, `${name}-1280.png`), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: path.join(SCREENSHOTS, `${name}-375.png`), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });
}

test.describe('Soundme Long-Job GUI States Evidence', () => {
  test('1. renders ETA and long-job warning (long-eta)', async ({ page }) => {
    // Mock an active running job with ETA
    await page.route('**/api/jobs', (route) => {
      if (route.request().method() === 'GET') {
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify([{ id: 'mock-long-job-01', filename: 'lecture_60min.mp4', status: 'running' }]),
        });
      }
      return route.continue();
    });

    await page.route('**/api/jobs/mock-long-job-01', (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'mock-long-job-01',
          filename: 'lecture_60min.mp4',
          status: 'running',
          stage: 'translate',
          progress: 42,
          eta_seconds: 1850,
          options: { source_lang: 'en', voice: 'km-KH-SreymomNeural' },
        }),
      });
    });

    await page.addInitScript(() => {
      localStorage.setItem('activeJob', 'mock-long-job-01');
    });

    await page.goto('/');
    await expect(page.locator('.eta-badge')).toBeVisible();
    await expect(page.locator('.eta-badge')).toContainText('~31');
    await captureResponsive(page, 'long-eta');
  });

  test('2. renders queued state (long-queued)', async ({ page }) => {
    await page.route('**/api/jobs', (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ id: 'mock-queued-02', filename: 'documentary_part2.mp4', status: 'queued' }]),
      });
    });

    await page.route('**/api/jobs/mock-queued-02', (route) => {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'mock-queued-02',
          filename: 'documentary_part2.mp4',
          status: 'queued',
          stage: 'queued',
          progress: 0,
        }),
      });
    });

    await page.addInitScript(() => {
      localStorage.setItem('activeJob', 'mock-queued-02');
    });

    await page.goto('/');
    await expect(page.getByText(/កំពុងរង់ចាំក្នុងជួរ|Queued/)).toBeVisible();
    await captureResponsive(page, 'long-queued');
  });

  test('3. renders resume after refresh state (long-resume)', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem('activeJob');
      localStorage.setItem('pendingUploadId', 'upload_session_abc123');
    });

    await page.goto('/');
    await expect(page.locator('.resume-notice')).toBeVisible();
    await captureResponsive(page, 'long-resume');
  });
});
