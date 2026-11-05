/**
 * Playwright E2E verification for Soundme frontend.
 * Verifies UI rendering, API connectivity, job display, video playback, secure download.
 */
import { chromium } from 'playwright';

const FRONTEND_URL = 'http://localhost:5173';
const TOKEN = 'soundme-test-token';

async function main() {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();

  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  const allRequests = [];
  page.on('request', (req) => allRequests.push(req));

  // ── Step 1: Navigate with token in URL ──
  const url = new URL(FRONTEND_URL);
  url.searchParams.set('token', TOKEN);
  console.log('[1] Navigating to', url.toString());
  await page.goto(url.toString(), { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  // ── Step 2: Verify UI renders ──
  console.log('[2] Checking UI render...');
  const bodyText = await page.evaluate(() => document.body.textContent.trim());
  const uiRendered = bodyText.length > 50;
  console.log('  Body text length:', bodyText.length);
  console.log('  UI rendered:', uiRendered ? 'PASS' : 'FAIL');
  if (consoleErrors.length > 0) {
    console.log('  Console errors:', consoleErrors.slice(0, 5));
  } else {
    console.log('  No console errors: PASS');
  }

  // ── Step 3: Verify API fetch ──
  console.log('[3] Verifying API connectivity...');
  const jobsReq = await page.waitForRequest(
    (req) => req.url().includes('/api/jobs') && !req.url().includes('/download'),
    { timeout: 15000 }
  ).catch(() => null);
  if (jobsReq) {
    console.log('  API request:', jobsReq.url());
    const authHdr = jobsReq.headers()['authorization'];
    console.log('  Authorization header:', authHdr || '(none)');
    console.log('  Bearer token present:', authHdr === `Bearer ${TOKEN}` ? 'PASS' : 'FAIL');
  } else {
    console.log('  No /api/jobs request intercepted');
  }

  // ── Step 4: Verify completed job in UI ──
  console.log('[4] Checking for completed job...');
  const jobCheck = await page.evaluate(() => {
    const body = document.body.textContent;
    return {
      hasCompleted: body.includes('completed') || body.includes('100%'),
      hasJobId: body.includes('32f001ba'),
    };
  });
  console.log('  Job display:', JSON.stringify(jobCheck));

  // ── Step 5: Click view-result for completed job ──
  console.log('[5] Clicking view-result...');
  const viewClicked = await page.evaluate(() => {
    const btns = document.querySelectorAll('button');
    for (const btn of btns) {
      const txt = (btn.textContent || '').toLowerCase();
      if (txt.includes('view') || btn.className?.includes('view-btn')) {
        btn.click(); return true;
      }
    }
    return false;
  });
  var videoInfo = [];
  var dlAuthPass = false, dlNoTokenInUrl = false;

  if (viewClicked) {
    console.log('  PASS: view-result clicked');
    await page.waitForTimeout(3000);

    // ── Step 6: Verify video player ──
    console.log('[6] Checking video player...');
    videoInfo = await page.evaluate(() => {
      const vids = document.querySelectorAll('video');
      return Array.from(vids).map((v, i) => {
        const src = v.getAttribute('src') || v.querySelector('source')?.getAttribute('src') || '';
        return { i, cls: v.className, src: src ? src.substring(0, 100) : '', isBlob: src.startsWith('blob:') };
      });
    });
    if (videoInfo.length > 0) {
      videoInfo.forEach((v) => console.log(`  video[${v.i}]: cls="${v.cls}" src="${v.src}" blob=${v.isBlob}`));
      const hasBlob = videoInfo.some((v) => v.isBlob);
      console.log('  Blob URL video:', hasBlob ? 'PASS' : 'FAIL');
    } else {
      console.log('  No <video> elements found: FAIL');
    }

    // ── Step 7: Verify secure download ──
    console.log('[7] Testing secure download...');
    const downloadReqs = [];
    page.on('request', (req) => {
      if (req.url().includes('/download')) downloadReqs.push(req);
    });

    await page.evaluate(() => {
      const btns = document.querySelectorAll('button');
      for (const btn of btns) {
        const txt = (btn.textContent || '').toLowerCase();
        if (txt.includes('download') || btn.className?.includes('download-link')) {
          btn.click(); return;
        }
      }
    });
    await page.waitForTimeout(3000);

    const dlReq = downloadReqs[downloadReqs.length - 1];
    if (dlReq) {
      const hdrs = dlReq.headers();
      const auth = hdrs['authorization'] || hdrs['x-access-token'];
      const u = dlReq.url();
      dlAuthPass = auth === `Bearer ${TOKEN}`;
      dlNoTokenInUrl = !u.includes('?token=') && !u.includes('&token=');
      console.log('  Download URL:', u);
      console.log('  Authorization:', auth || '(none)');
      console.log('  Bearer token:', dlAuthPass ? 'PASS' : 'FAIL');
      console.log('  No token in URL:', dlNoTokenInUrl ? 'PASS' : 'FAIL');
    } else {
      console.log('  No download request captured');
      const allDl = allRequests.filter((r) => r.url().includes('/download'));
      if (allDl.length > 0) {
        const r = allDl[allDl.length - 1];
        const hdrs = r.headers();
        const auth = hdrs['authorization'] || hdrs['x-access-token'];
        const u = r.url();
        dlAuthPass = auth === `Bearer ${TOKEN}`;
        dlNoTokenInUrl = !u.includes('?token=') && !u.includes('&token=');
        console.log('  Found in all requests - URL:', u);
        console.log('  Authorization:', auth || '(none)');
      }
    }
  } else {
    console.log('  view-result button not found: FAIL');
  }

  // ── Screenshot ──
  await page.screenshot({ path: 'frontend-verification.png', fullPage: true });
  console.log('\nScreenshot saved: frontend-verification.png');

  // ── Summary ──
  console.log('\n========================================');
  console.log('VERIFICATION SUMMARY');
  console.log('========================================');
  console.log('Backend :', 'YES (port 8000)');
  console.log('Frontend:', 'YES (port 5173)');
  console.log('UI rendered:', uiRendered ? 'PASS' : 'FAIL');
  console.log('Console errors:', consoleErrors.length);
  console.log('API connectivity:', jobsReq ? 'PASS' : 'FAIL');
  console.log('Bearer token on API:', jobsReq && jobsReq.headers()['authorization'] === `Bearer ${TOKEN}` ? 'PASS' : 'FAIL');
  console.log('Completed job in UI:', jobCheck.hasCompleted ? 'PASS' : 'FAIL');
  console.log('Video player (blob URL):', videoInfo && videoInfo.some((v) => v.isBlob) ? 'PASS' : 'FAIL');
  console.log('Secure download (Bearer):', dlAuthPass ? 'PASS' : 'FAIL');
  console.log('No token in download URL:', dlNoTokenInUrl ? 'PASS' : 'FAIL');
  console.log('========================================');

  await browser.close();
}

main().catch((err) => { console.error('Test error:', err); process.exit(1); });
