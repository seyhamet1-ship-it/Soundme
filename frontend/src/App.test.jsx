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
