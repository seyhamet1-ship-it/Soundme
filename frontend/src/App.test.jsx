// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, fireEvent } from '@testing-library/react';
import App from './App';

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

describe('Khmer dubbing studio', () => {
  it('renders the Khmer upload workflow and recent jobs', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    expect(screen.getByText('កម្មវិធីបកសំឡេងវីដេអូជាភាសាខ្មែរ')).toBeTruthy();
    expect(screen.getByText('ភាសាដើម')).toBeTruthy();
    expect(screen.getByText('ប្រវត្តិការងារ')).toBeTruthy();
  });
  it('switches to English', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    expect(screen.getByText('Khmer Video Dubber')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Choose or drop a video' })).toBeTruthy();
  });
  it('rejects a non-video file with an accessible error', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] } });
    expect(screen.getByRole('alert').textContent).toContain('Choose an MP4');
  });
  it('surfaces TTS_UNAVAILABLE job failures with an accessible error', async () => {
    localStorage.setItem('activeJob', 'job1');
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url).includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'failed', stage: 'failed', progress: 100,
          error: { code: 'TTS_UNAVAILABLE', message_en: 'TTS_UNAVAILABLE: Edge -> Azure -> MMS failed', message_km: 'មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។' },
        }) });
      }
      return Promise.resolve({ json: async () => [] });
    }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('[TTS_UNAVAILABLE]');
    expect(alert.textContent).toContain('Edge -> Azure -> MMS failed');
  });
  it('switches the job failure message to Khmer in Khmer mode', async () => {
    localStorage.setItem('activeJob', 'job1');
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (String(url).includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'failed', stage: 'failed', progress: 100,
          error: { code: 'TTS_UNAVAILABLE', message_en: 'TTS_UNAVAILABLE: failed', message_km: 'មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។' },
        }) });
      }
      return Promise.resolve({ json: async () => [] });
    }));
    render(<App />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('មិនអាចបង្កើតសំឡេងខ្មែរបានទេ។');
  });
});

// ── Authenticated downloads (no ?token= in URLs) ────────────────────────────
describe('authenticated downloads', () => {
  const blobResponse = () => ({
    ok: true,
    status: 200,
    blob: async () => new Blob(['binary-data'], { type: 'video/mp4' }),
  });

  beforeEach(() => {
    // jsdom lacks URL.createObjectURL / revokeObjectURL
    if (!URL.createObjectURL) {
      let counter = 0;
      URL.createObjectURL = vi.fn(() => `blob:mock/${++counter}`);
      URL.revokeObjectURL = vi.fn();
    }
    // jsdom cannot navigate to blob: URLs, so the anchor activation in
    // downloadWithAuth() would log "Not implemented: navigation". Stub the
    // click so the test exercises the fetch + blob-URL path without it.
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  });

  it('never puts a token in the download URL and sends auth headers instead', async () => {
    localStorage.setItem('activeJob', 'job1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/jobs/job1/download/')) return Promise.resolve(blobResponse());
      if (u.includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'completed', stage: 'completed', progress: 100,
        }) });
      }
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: false }) });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    const downloadBtn = await screen.findByRole('button', { name: 'Download Video' });
    await fireEvent.click(downloadBtn);

    const dlCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/download/'));
    expect(dlCall).toBeTruthy();
    const [calledUrl, calledOpts] = dlCall;

    // URL must NOT contain a token
    expect(calledUrl).not.toContain('token=');
    expect(calledUrl).not.toContain('soundme-test-token');
    expect(calledUrl).toContain('/api/jobs/job1/download/video');

    // Auth must be sent via headers
    const headers = calledOpts.headers || {};
    expect(headers.Authorization).toBe('Bearer soundme-test-token');
    expect(headers['X-Access-Token']).toBe('soundme-test-token');
  });

  it('renders the video player with a blob URL instead of a tokenised URL', async () => {
    localStorage.setItem('activeJob', 'job1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/jobs/job1/download/')) return Promise.resolve(blobResponse());
      if (u.includes('/jobs/job1')) {
        return Promise.resolve({ json: async () => ({
          id: 'job1', status: 'completed', stage: 'completed', progress: 100,
        }) });
      }
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: false }) });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    const dubbed = await screen.findByLabelText('Dubbed video');
    await vi.waitFor(() => {
      expect(dubbed.getAttribute('src')).toMatch(/^blob:/);
    });
    expect(dubbed.getAttribute('src')).not.toContain('token=');
  });
});

// ── Phase 11F: authenticated SSE + bounded polling ──────────────────────────
describe('job status streaming', () => {
  const sseFrame = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

  // Builds a fake streaming response for the SSE endpoint.
  const sseResponse = (frames, { keepOpen = true } = {}) => {
    const encoder = new TextEncoder();
    let i = 0;
    const body = {
      getReader() {
        return {
          read() {
            if (i < frames.length) {
              return Promise.resolve({ done: false, value: encoder.encode(frames[i++]) });
            }
            if (keepOpen) {
              // Never resolves — the stream stays open like the real backend.
              return new Promise(() => {});
            }
            return Promise.resolve({ done: true, value: undefined });
          },
        };
      },
    };
    return { ok: true, status: 200, body };
  };

  const jobJson = (status, extra = {}) => ({
    json: async () => ({ id: 'job1', status, stage: status, progress: 50, ...extra }),
  });

  it('authenticates the SSE request with a bearer token and no token in the URL', async () => {
    localStorage.setItem('activeJob', 'job1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/events')) return Promise.resolve(sseResponse([]));
      if (u.includes('/jobs/job1')) return Promise.resolve(jobJson('processing'));
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: false }) });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events'))).toBe(true);
    });

    const sseCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/events'));
    const [calledUrl, calledOpts] = sseCall;

    // The token must travel in the header, never in the URL/query string.
    expect(calledUrl).not.toContain('token=');
    expect(calledUrl).not.toContain('soundme-test-token');
    const headers = calledOpts.headers || {};
    expect(headers.Authorization).toBe('Bearer soundme-test-token');
    expect(headers['X-Access-Token']).toBe('soundme-test-token');
    expect(headers.Accept).toBe('text/event-stream');
  });

  it('stops polling once the job reaches a terminal status', async () => {
    localStorage.setItem('activeJob', 'job1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      // SSE immediately reports the finished job.
      if (u.includes('/events')) {
        return Promise.resolve(sseResponse([
          sseFrame({ id: 'job1', status: 'completed', stage: 'completed', progress: 100 }),
        ]));
      }
      if (u.includes('/jobs/job1')) return Promise.resolve(jobJson('processing'));
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: false }) });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    await vi.waitFor(() => {
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/events'))).toBe(true);
    });
    // The terminal status clears the stored active job.
    await vi.waitFor(() => {
      expect(localStorage.getItem('activeJob')).toBe(null);
    });

    const countPolls = () =>
      fetchMock.mock.calls.filter(([u]) => /\/api\/jobs\/job1$/.test(String(u))).length;
    const afterTerminal = countPolls();

    // Advance well past several 1.3 s poll intervals.
    await new Promise((r) => setTimeout(r, 4000));

    // No additional status polls may be issued after the job finished.
    expect(countPolls()).toBe(afterTerminal);
  }, 15000);

  it('issues a single status poll and a single history fetch once the job is done', async () => {
    localStorage.setItem('activeJob', 'job1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/events')) {
        // SSE never connects → the client falls back to polling.
        return Promise.resolve({ ok: false, status: 401, body: null });
      }
      // The job is already terminal, so every poll would report "completed".
      if (u.includes('/jobs/job1')) {
        return Promise.resolve({
          json: async () => ({ id: 'job1', status: 'completed', stage: 'completed', progress: 100 }),
        });
      }
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: false }) });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const countPolls = () =>
      fetchMock.mock.calls.filter(([u]) => /\/api\/jobs\/job1$/.test(String(u))).length;
    const countHistory = () =>
      fetchMock.mock.calls.filter(([u]) => /\/api\/jobs$/.test(String(u))).length;

    await vi.waitFor(() => {
      expect(countPolls()).toBeGreaterThan(0);
    }, { timeout: 8000 });

    // The job is terminal, so the poll loop stops and the history list is
    // fetched once (on the initial mount) plus once (to refresh the list).
    await vi.waitFor(() => {
      expect(countHistory()).toBeGreaterThan(0);
    }, { timeout: 8000 });
    await new Promise((r) => setTimeout(r, 1000));
    const historyAfterTerminal = countHistory();

    // Let several 1.3 s poll intervals elapse.
    await new Promise((r) => setTimeout(r, 4000));

    // The terminal status stops the poll loop…
    expect(countPolls()).toBe(1);
    // …and the history list is NOT re-fetched once per poll.
    expect(countHistory()).toBe(historyAfterTerminal);
    expect(countHistory()).toBeLessThanOrEqual(2);
  }, 20000);
});


describe('Background Audio Preservation setting', () => {
  it('defaults to unchecked and reveals volume slider when checked', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    const checkbox = screen.getByLabelText('Keep Original Background Music & Sound Effects');
    expect(checkbox.checked).toBe(false);
    expect(screen.queryByLabelText('Background volume')).toBeNull();

    // Check the box
    fireEvent.click(checkbox);
    expect(checkbox.checked).toBe(true);

    // Volume slider is revealed with 25% default
    const slider = screen.getByLabelText('Background volume');
    expect(slider).toBeTruthy();
    expect(slider.value).toBe('25');
    expect(screen.getByText('25%')).toBeTruthy();

    // Change volume
    fireEvent.change(slider, { target: { value: '40' } });
    expect(slider.value).toBe('40');
    expect(screen.getByText('40%')).toBeTruthy();

    // Supporting text is rendered
    expect(screen.getByText(/Preserve background audio where possible/)).toBeTruthy();
  });

  it('renders natural Khmer localization for background preservation', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);
    // In Khmer mode by default
    const checkbox = screen.getByLabelText('រក្សាតន្ត្រី និងសំឡេងបែបផែនដើម');
    expect(checkbox.checked).toBe(false);

    fireEvent.click(checkbox);
    expect(screen.getByLabelText('កម្រិតសំឡេងផ្ទៃខាងក្រោយ')).toBeTruthy();
    expect(screen.getByText(/រក្សាសំឡេងផ្ទៃខាងក្រោយតាមដែលអាចធ្វើទៅបាន/)).toBeTruthy();
  });
});

// ── Black screen regression & upload resilience ────────────────────────────
import ErrorBoundary from './components/ErrorBoundary';

describe('Black screen regression & upload resilience', () => {
  it('ErrorBoundary catches child crash and renders recovery screen with Try Again', () => {
    const ProblemChild = () => {
      throw new Error('Simulated renderer explosion');
    };
    // Suppress console.error in this test since throwing is expected
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <ProblemChild />
      </ErrorBoundary>
    );

    expect(screen.getByRole('alert')).toBeTruthy();
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    expect(screen.getByText(/Simulated renderer explosion/)).toBeTruthy();
    expect(screen.getByText('Try Again')).toBeTruthy();
    expect(screen.getByText('Reload App')).toBeTruthy();

    errSpy.mockRestore();
  });

  it('rejects oversized video file with exact limit message and no crash', () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ json: async () => [] }));
    render(<App />);

    // Mock 600 MB file (limit is 500 MB)
    const bigFile = new File([new Uint8Array(10)], 'long_presentation.mp4', { type: 'video/mp4' });
    Object.defineProperty(bigFile, 'size', { value: 600 * 1024 * 1024 });

    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [bigFile] } });

    const alert = screen.getByRole('alert');
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('600.0 MB');
    expect(alert.textContent).toContain('500 MB');
    // UI remains fully rendered
    expect(screen.getByText('Soundme AI')).toBeTruthy();
  });

  it('handles structured 429 daily quota error object during chunked complete without crashing', async () => {
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500, max_duration_min: 10 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/api/upload/init')) return Promise.resolve({ ok: true, json: async () => ({ upload_id: 'test-up-1', total_chunks: 1 }) });
      if (u.includes('/status')) return Promise.resolve({ ok: true, json: async () => ({ received_chunks: [] }) });
      if (u.includes('/chunk')) return Promise.resolve({ ok: true });
      if (u.includes('/complete')) {
        return Promise.resolve({
          ok: false,
          status: 429,
          json: async () => ({
            detail: {
              code: 'DAILY_QUOTA_EXCEEDED',
              message_en: 'Daily quota of 60 minutes reached for this account.',
              message_km: 'កូតាប្រចាំថ្ងៃ 60 នាទីត្រូវបានប្រើប្រាស់អស់ហើយ។',
            },
          }),
        });
      }
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    // File > 10MB triggers chunked upload
    const testVideo = new File([new Uint8Array(100)], 'presentation.mp4', { type: 'video/mp4' });
    Object.defineProperty(testVideo, 'size', { value: 15 * 1024 * 1024 });

    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [testVideo] } });

    const startBtn = await screen.findByRole('button', { name: /start dubbing/i });
    await fireEvent.click(startBtn);

    const alert = await screen.findByRole('alert');
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('[DAILY_QUOTA_EXCEEDED]');
    expect(alert.textContent).toContain('Daily quota of 60 minutes reached');
    // UI remains healthy and interactive
    expect(screen.getByText('Khmer Video Dubber')).toBeTruthy();
  });

  it('handles structured 400 duration error object during chunked complete without crashing', async () => {
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500, max_duration_min: 10 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/api/upload/init')) return Promise.resolve({ ok: true, json: async () => ({ upload_id: 'test-up-2', total_chunks: 1 }) });
      if (u.includes('/status')) return Promise.resolve({ ok: true, json: async () => ({ received_chunks: [] }) });
      if (u.includes('/chunk')) return Promise.resolve({ ok: true });
      if (u.includes('/complete')) {
        return Promise.resolve({
          ok: false,
          status: 400,
          json: async () => ({
            detail: {
              code: 'DURATION_TOO_LONG',
              message_en: 'Video duration (120.0 min) exceeds the 10-minute limit.',
              message_km: 'រយៈពេលវីដេអូវែងពេក (អតិបរមា 10 នាទី)',
            },
          }),
        });
      }
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    const testVideo = new File([new Uint8Array(100)], 'long_two_hour.mp4', { type: 'video/mp4' });
    Object.defineProperty(testVideo, 'size', { value: 15 * 1024 * 1024 });

    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [testVideo] } });

    const startBtn = await screen.findByRole('button', { name: /start dubbing/i });
    await fireEvent.click(startBtn);

    const alert = await screen.findByRole('alert');
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('[DURATION_TOO_LONG]');
    expect(alert.textContent).toContain('exceeds the 10-minute limit');
    // Screen is NOT black — header and options remain visible
    expect(screen.getByText('Khmer Video Dubber')).toBeTruthy();
  });

  it('handles backend connection failure during upload without crashing UI', async () => {
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/api/upload/init')) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    const testVideo = new File([new Uint8Array(100)], 'test.mp4', { type: 'video/mp4' });
    Object.defineProperty(testVideo, 'size', { value: 15 * 1024 * 1024 });

    fireEvent.change(screen.getByLabelText('Choose video'), { target: { files: [testVideo] } });

    const startBtn = await screen.findByRole('button', { name: /start dubbing/i });
    await fireEvent.click(startBtn);

    const alert = await screen.findByRole('alert');
    expect(alert).toBeTruthy();
    expect(alert.textContent).toContain('Unable to connect to backend server');
    expect(screen.getByText('Khmer Video Dubber')).toBeTruthy();
  });

  // ── Desktop Regression: lang is not defined & JobHistory bilingual render ──
  it('renders JobHistory without lang ReferenceError and switches languages cleanly', async () => {
    const mockJobs = [
      { id: 'job_comp_01', filename: 'clip1.mp4', status: 'completed', progress: 100 },
      { id: 'job_fail_02', filename: 'clip2.mp4', status: 'failed', progress: 50 },
      { id: 'job_canc_03', filename: 'clip3.mp4', status: 'canceled', progress: 20 },
      { id: 'job_qued_04', filename: 'clip4.mp4', status: 'queued', progress: 0 },
    ];

    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/api/jobs')) return Promise.resolve({ json: async () => mockJobs });
      return Promise.resolve({ json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    // Initial render in default Khmer mode
    const { container } = render(<App />);

    // Must NOT throw ReferenceError: lang is not defined
    expect(container.querySelector('[role="alert"]')?.textContent || '').not.toContain('lang is not defined');

    // Wait for history rows to appear
    const clip1 = await screen.findByText(/clip1\.mp4/);
    expect(clip1).toBeTruthy();

    // Verify Khmer filter text or elements exist
    expect(screen.getAllByText(/ទាំងអស់/).length).toBeGreaterThan(0);

    // Switch to English
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    // Verify English text appears in JobHistory
    expect(screen.getAllByText(/All/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Clear Completed/)).toBeTruthy();

    // Verify delete button is accessible on completed, failed, and queued rows
    const deleteButtons = screen.getAllByRole('button', { name: /លុប|Delete/i });
    expect(deleteButtons.length).toBeGreaterThan(0);

    // Switch back to Khmer
    fireEvent.click(screen.getByRole('button', { name: /ខ្មែរ/ }));
    expect(screen.getAllByText(/ទាំងអស់/).length).toBeGreaterThan(0);
  });

  // ── ErrorBoundary token redaction security check ──
  it('ErrorBoundary sanitizes Bearer tokens and sensitive query params from UI message', () => {
    // ProblemChild intentionally throws an error containing a token
    function LeakyChild() {
      throw new Error('Failed to connect with Bearer soundme-secret-token-123456 at /api?token=abc-secret');
    }

    render(
      <ErrorBoundary>
        <LeakyChild />
      </ErrorBoundary>
    );

    const alert = screen.getByRole('alert');
    expect(alert).toBeTruthy();
    // Must contain [REDACTED]
    expect(alert.textContent).toContain('Bearer [REDACTED]');
    expect(alert.textContent).toContain('token=[REDACTED]');
    // Must NOT leak the raw tokens
    expect(alert.textContent).not.toContain('soundme-secret-token-123456');
    expect(alert.textContent).not.toContain('abc-secret');
  });

  // ── Progress Semantics & History Synchronization Regression Tests ──
  it('Queued job never displays 100% or completed progress; shows waiting state', async () => {
    localStorage.setItem('activeJob', 'q-job-1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/jobs/q-job-1')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            id: 'q-job-1',
            status: 'queued',
            stage: 'queued',
            progress: 0,
            filename: 'long_video.mp4',
          }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    // Wait for the job to be fetched and applied
    const queuedText = await screen.findByText(/Queued \(waiting for slot\)/);
    expect(queuedText).toBeTruthy();

    // The text must NOT display 100%
    expect(queuedText.textContent).toContain('0%');
    expect(queuedText.textContent).not.toContain('100%');

    // Progress element must have value 0
    const progressEl = screen.getAllByRole('progressbar').pop();
    expect(progressEl.getAttribute('value')).toBe('0');
  });

  it('Completed job displays 100% progress and success notice', async () => {
    localStorage.setItem('activeJob', 'comp-job-1');
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/jobs/comp-job-1')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          blob: async () => new Blob(['video-bytes'], { type: 'video/mp4' }),
          json: async () => ({
            id: 'comp-job-1',
            status: 'completed',
            stage: 'done',
            progress: 100,
            filename: 'finished.mp4',
            outputs: { video: 'out.mp4' },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        blob: async () => new Blob(['test'], { type: 'video/mp4' }),
        json: async () => [],
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    const completeNotice = await screen.findAllByText(/Dubbing Complete/i);
    expect(completeNotice.length).toBeGreaterThan(0);

    const progressEl = screen.getAllByRole('progressbar').pop();
    expect(progressEl.getAttribute('value')).toBe('100');
  });

  it('History API failure displays error message instead of falsely claiming zero jobs', async () => {
    const fetchMock = vi.fn((url) => {
      const u = String(url);
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/api/jobs')) {
        // Return HTTP 500 error
        return Promise.resolve({
          ok: false,
          status: 500,
          json: async () => ({ error: 'Internal Server Error' }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));

    // Wait for history error state to appear
    const errorText = await screen.findByText(/Could not load history/i);
    expect(errorText).toBeTruthy();
    expect(screen.getByText(/backend may be unavailable/i)).toBeTruthy();

    // Must NOT say "No jobs found" when it actually failed to load
    expect(screen.queryByText('No jobs found.')).toBeNull();
  });

  it('Desktop reload loads existing activeJob from localStorage without resubmitting', async () => {
    localStorage.setItem('activeJob', 'persisted-active-99');
    const postCalls = [];
    const fetchMock = vi.fn((url, opts) => {
      const u = String(url);
      if (opts?.method === 'POST') {
        postCalls.push({ url: u, body: opts.body });
      }
      if (u.includes('/api/health')) return Promise.resolve({ json: async () => ({ max_upload_mb: 500 }) });
      if (u.includes('/auth/me')) return Promise.resolve({ json: async () => ({ authenticated: true }) });
      if (u.includes('/jobs/persisted-active-99')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            id: 'persisted-active-99',
            status: 'running',
            stage: 'translate',
            progress: 45,
            filename: 'saved.mp4',
          }),
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // Must poll for the existing job
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining('/jobs/persisted-active-99'), expect.anything());

    // Must NOT POST to /api/jobs (no accidental duplicate job creation)
    const jobSubmissions = postCalls.filter((c) => c.url.endsWith('/api/jobs'));
    expect(jobSubmissions.length).toBe(0);
  });
});
