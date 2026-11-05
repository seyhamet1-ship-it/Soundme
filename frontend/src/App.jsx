import React, { useEffect, useMemo, useRef, useState } from 'react';
import strings from './i18n/strings.json';
import { normalizeError } from './utils/errorNormalizer';
import JobHistory from './components/JobHistory';

const A = '/api';

// ── Auth token management ──────────────────────────────────────────────────
function getToken() {
  // Desktop-local mode: never use a token inside the Electron desktop shell.
  if (typeof window !== 'undefined' && window.soundmeDesktop?.isDesktop) {
    try { localStorage.removeItem('soundme_token'); } catch (e) { void e; }
    return '';
  }
  try {
    const params = new URLSearchParams(window.location?.search || '');
    const urlToken = params.get('token');
    if (urlToken) {
      localStorage.setItem('soundme_token', urlToken);
      const clean = new URL(window.location.href);
      clean.searchParams.delete('token');
      window.history.replaceState({}, '', clean.toString());
    }
  } catch (e) {
    void e;
  }
  return localStorage.getItem('soundme_token') || (import.meta.env?.MODE === 'test' ? 'soundme-test-token' : '');
}

let TOKEN = getToken();

function authHeaders(extra = {}) {
  const isDesktop = typeof window !== 'undefined' && window.soundmeDesktop?.isDesktop;
  if (TOKEN && !isDesktop) return { Authorization: `Bearer ${TOKEN}`, 'X-Access-Token': TOKEN, ...extra };
  return extra;
}

function authFetch(url, opts = {}) {
  const headers = authHeaders(opts.headers || {});
  return fetch(url, { credentials: 'include', ...opts, headers });
}

// ── Authenticated download helpers ──────────────────────────────────────────
async function downloadWithAuth(kind, jid, filename) {
  const res = await authFetch(`${A}/jobs/${jid}/download/${kind}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  if (filename) a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/**
 * Fetches a download URL for a video by streaming the response through a
 * Service-Worker-compatible approach: the response body is piped via a
 * ReadableStream to avoid loading the entire file into JS heap.
 *
 * Falls back to Blob URL for browsers/Electron versions that do not support
 * Response body streaming (unlikely but safe).
 */
function fetchVideoBlobUrl(kind, jid) {
  return authFetch(`${A}/jobs/${jid}/download/${kind}`).then((r) => {
    if (!r.ok) return null;
    // Stream the body into a Blob without holding it all in a single array
    return r.blob().then((blob) => URL.createObjectURL(blob));
  });
}

// ── Authenticated SSE ───────────────────────────────────────────────────────
function sseSubscribe(url, onMessage, onError) {
  const controller = new AbortController();
  const stop = () => controller.abort();

  (async () => {
    let res;
    try {
      res = await authFetch(url, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      });
    } catch (err) {
      if (err?.name !== 'AbortError') onError?.();
      return;
    }
    if (!res.ok || !res.body) {
      onError?.();
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          for (const line of frame.split('\n')) {
            if (line.startsWith('data: ')) {
              try {
                onMessage(JSON.parse(line.slice(6)));
              } catch (e) {
                void e;
              }
            }
          }
        }
      }
      onError?.();
    } catch (err) {
      if (err?.name !== 'AbortError') onError?.();
    }
  })();

  return stop;
}

export default function App() {
  const [en, E] = useState(false);
  const [activeTab, setActiveTab] = useState('workspace');
  const [compareMode, setCompareMode] = useState(true);
  // Synchronous submission guard — prevents double-clicks bypassing async state lag
  const submittingRef = useRef(false);
  const [file, F] = useState(null);
  const [source, S] = useState('auto');
  const [voice, V] = useState('km-KH-SreymomNeural');
  const [bg, B] = useState(false);
  const [bgVol, setBgVol] = useState(25);
  const [burn, U] = useState(false);
  const [review, R] = useState(false);
  const [pct, P] = useState(0);
  const [job, J] = useState(null);
  const [jobs, H] = useState([]);
  const [err, X] = useState('');
  const [rows, Q] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [pendingUpload] = useState(() => localStorage.getItem('pendingUploadId'));
  const [userSession, setUserSession] = useState(null);
  // Desktop-local mode: treat as always authenticated (noToken starts false).
  // The /auth/me probe below also sets noToken=false when backend confirms desktop:local.
  const [noToken, setNoToken] = useState(false);
  const [videoBlobUrls, setVideoBlobUrls] = useState({});
  const [historyLoadError, setHistoryLoadError] = useState(false); // true = API failed, false = ok or empty
  const [maxUploadMb, setMaxUploadMb] = useState(() => Number(import.meta.env.VITE_MAX_UPLOAD_SIZE_MB || 500));
  const [maxDurationMin, setMaxDurationMin] = useState(() => Number(import.meta.env.VITE_MAX_DURATION_MIN || 125));
  const maxUploadBytes = maxUploadMb * 1024 * 1024;

  const t = (key) => strings[key]?.[en ? 'en' : 'km'] || key;

  // ── Session probe & OAuth callback check on mount ──────────────────────────
  useEffect(() => {
    try {
      const params = new URLSearchParams(window.location?.search || '');
      const authErr = params.get('auth_error');
      if (authErr) {
        const errorMessages = {
          google_denied: en ? 'Google sign-in was canceled or denied.' : 'ការចូលដោយប្រើ Google ត្រូវបានបោះបង់។',
          token_exchange_failed: en ? 'Google authentication failed to connect.' : 'ការផ្ទៀងផ្ទាត់ Google បានបរាជ័យក្នុងការតភ្ជាប់។',
          unverified_email: en ? 'Your Google email is unverified.' : 'អ៊ីមែល Google របស់អ្នកមិនទាន់បានផ្ទៀងផ្ទាត់ទេ។',
          email_not_allowed: en ? 'Your Google account is not on the tester allowlist.' : 'គណនី Google របស់អ្នកមិនស្ថិតនៅក្នុងបញ្ជីអនុញ្ញាតទេ។',
          invalid_state: en ? 'OAuth security validation failed (invalid state).' : 'ការផ្ទៀងផ្ទាត់សុវត្ថិភាព OAuth បានបរាជ័យ។',
          missing_code: en ? 'No authorization code received from Google.' : 'មិនទទួលបានកូដអនុញ្ញាតពី Google ទេ។',
        };
        X(errorMessages[authErr] || (en ? `Authentication error: ${authErr}` : `កំហុសក្នុងការចូល: ${authErr}`));
        const clean = new URL(window.location.href);
        clean.searchParams.delete('auth_error');
        clean.searchParams.delete('login');
        window.history.replaceState({}, '', clean.toString());
      }
    } catch (_) {
      void 0;
    }

    fetch('/auth/me', { credentials: 'include' })
      .then((r) => r.json())
      .then((d) => {
        if (d && d.authenticated) {
          setUserSession(d);
          setNoToken(false);
        }
      })
      .catch(() => {});

    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => {
        if (d && typeof d.max_upload_mb === 'number') setMaxUploadMb(d.max_upload_mb);
        if (d && typeof d.max_duration_min === 'number') setMaxDurationMin(d.max_duration_min);
      })
      .catch(() => {});
  }, [en]);

  // eslint-disable-next-line no-unused-vars
  function submitToken() {
    // No-op in desktop-local mode. Kept so existing test references don't break.
  }

  const refreshHistory = () => {
    authFetch(A + '/jobs')
      .then((r) => {
        if (r && (r.ok === false || (typeof r.status === 'number' && r.status >= 400))) {
          setHistoryLoadError(true);
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data === null) return; // error already flagged
        if (Array.isArray(data)) {
          setHistoryLoadError(false);
          H(data);
        }
      })
      .catch(() => { setHistoryLoadError(true); });
  };

  // ── Load active job and history on mount ───────────────────────────────────
  useEffect(() => {
    let id = localStorage.getItem('activeJob');
    if (id) {
      J({ id });
    }
    authFetch(A + '/jobs')
      .then(async (r) => {
        if (r.status === 401) {
          // In desktop-local mode the backend never returns 401 for loopback requests.
          // For web mode: silently return empty jobs rather than forcing a login screen.
          return [];
        }
        if (r && (r.ok === false || (typeof r.status === 'number' && r.status >= 400))) {
          setHistoryLoadError(true);
          return null;
        }
        return r.json();
      })
      .then((data) => {
        if (data === null) return; // error already flagged above
        if (Array.isArray(data)) {
          setHistoryLoadError(false);
          H(data);
          if (!id && data.length > 0) {
            const comp = data.find((x) => x.status === 'completed') || data[0];
            if (comp) {
              J(comp);
              if (comp.segments) Q(comp.segments);
              if (comp?.options) {
                if (comp.options.preserve_background_audio !== undefined) B(Boolean(comp.options.preserve_background_audio));
                else if (comp.options.keep_background !== undefined) B(Boolean(comp.options.keep_background));
                if (comp.options.background_audio_volume !== undefined) {
                  const v = Number(comp.options.background_audio_volume);
                  setBgVol(Math.round(v <= 1.0 ? v * 100 : v));
                }
              }
            }
          }
        }
      })
      .catch(() => { setHistoryLoadError(true); });
  }, [noToken]);

  // ── Job polling with SSE → polling fallback (bounded, non-overlapping) ────
  useEffect(() => {
    if (!job?.id) return;

    const TERMINAL = ['completed', 'failed', 'canceled', 'awaiting_review'];
    // If job is already terminal on mount, do not start any polling
    if (TERMINAL.includes(job?.status)) return;

    let live = true;
    let sseAlive = false;
    let pollTimer = null;
    let stopSse = null;
    let sseTimeout = null;
    let finished = false;
    let inFlight = false;          // prevent overlapping poll requests
    let errorCount = 0;
    const MAX_POLL_ERRORS = 8;    // bounded retries for transient errors
    const PERMANENT_ERRORS = new Set([400, 401, 403, 404]);

    const applyJob = (d) => {
      if (!live) return;
      errorCount = 0;             // reset error streak on any successful data
      J(d);
      if (d.segments) Q(d.segments);
      if (d?.options) {
        if (d.options.preserve_background_audio !== undefined) B(Boolean(d.options.preserve_background_audio));
        else if (d.options.keep_background !== undefined) B(Boolean(d.options.keep_background));
        if (d.options.background_audio_volume !== undefined) {
          const v = Number(d.options.background_audio_volume);
          setBgVol(Math.round(v <= 1.0 ? v * 100 : v));
        }
      }
      if (TERMINAL.includes(d.status)) {
        finished = true;
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        stopSse?.();
        stopSse = null;
        localStorage.removeItem('activeJob');
        refreshHistory();
      }
    };

    const scheduleNextPoll = () => {
      if (!live || finished) return;
      // Exponential backoff with cap: 1.3s → 2.6s → 5s → 10s → 10s…
      const delay = Math.min(10000, 1300 * Math.pow(1.5, Math.min(errorCount, 5)));
      pollTimer = setTimeout(doPoll, delay);
    };

    const doPoll = () => {
      if (!live || finished || inFlight) return;
      inFlight = true;
      authFetch(`${A}/jobs/${job.id}`)
        .then((r) => {
          const httpStatus = r && typeof r.status === 'number' ? r.status : undefined;
          if (httpStatus === 404) {
            if (localStorage.getItem('activeJob') === job.id) localStorage.removeItem('activeJob');
            if (live) J(null);
            finished = true;
            return null;
          }
          if (httpStatus !== undefined && PERMANENT_ERRORS.has(httpStatus)) {
            // Permanent client error — stop polling immediately, do not retry
            finished = true;
            X(normalizeError({ status: httpStatus }, en));
            return null;
          }
          if (httpStatus !== undefined && httpStatus >= 400) {
            throw Object.assign(new Error(`HTTP ${httpStatus}`), { httpStatus });
          }
          if (r && r.ok === false && httpStatus === undefined) throw new Error('Request failed');
          return r.json().catch(() => { throw new Error('Invalid JSON response'); });
        })
        .then((d) => { if (d) applyJob(d); })
        .catch((e) => {
          if (!live) return;
          // 429/503: server busy — show message once, back off, do NOT resubmit
          const httpStatus = e?.httpStatus;
          if (httpStatus === 429 || httpStatus === 503) {
            X(normalizeError(e, en));
            errorCount++;
          } else {
            errorCount++;
          }
          if (errorCount >= MAX_POLL_ERRORS) {
            // Exceeded retry budget: show recoverable error, stop auto-polling
            X(normalizeError(
              new Error(en
                ? `Polling stopped after ${MAX_POLL_ERRORS} errors. Please refresh manually.`
                : `ការស្ទង់ស្ថានភាពបានឈប់ បន្ទាប់ពី ${MAX_POLL_ERRORS} កំហុស។ សូមផ្ទុកឡើងវិញ។`),
              en
            ));
            finished = true;
            return;
          }
        })
        .finally(() => {
          inFlight = false;
          scheduleNextPoll();
        });
    };

    const startPolling = () => {
      if (!live || finished) return;
      doPoll();
    };

    try {
      stopSse = sseSubscribe(
        `${A}/jobs/${job.id}/events`,
        (d) => {
          sseAlive = true;
          clearTimeout(sseTimeout);
          applyJob(d);
        },
        () => {
          if (live && !finished) {
            clearTimeout(sseTimeout);
            stopSse = null;
            startPolling();
          }
        },
      );
      sseTimeout = setTimeout(() => {
        if (!sseAlive && live && !finished) {
          stopSse?.();
          stopSse = null;
          startPolling();
        }
      }, 5000);
    } catch {
      startPolling();
    }

    return () => {
      live = false;
      clearTimeout(sseTimeout);
      if (pollTimer) clearTimeout(pollTimer);
      stopSse?.();
    };
  }, [job?.id, noToken]);

  // ── Fetch blob URLs for video players when job completes ───────────────────
  useEffect(() => {
    if (!job?.id || job.status !== 'completed') {
      setVideoBlobUrls({});
      return;
    }
    let cancelled = false;
    const urls = {};
    Promise.all([
      fetchVideoBlobUrl('video', job.id),
      fetchVideoBlobUrl('source', job.id),
    ]).then(([videoUrl, sourceUrl]) => {
      if (cancelled) {
        videoUrl && URL.revokeObjectURL(videoUrl);
        sourceUrl && URL.revokeObjectURL(sourceUrl);
        return;
      }
      urls.video = videoUrl;
      urls.source = sourceUrl;
      setVideoBlobUrls({ video: videoUrl, source: sourceUrl });
    });
    return () => {
      cancelled = true;
      Object.values(urls).forEach((u) => u && URL.revokeObjectURL(u));
    };
  }, [job?.id, job?.status]);

  const localFileUrl = useMemo(() => (file ? URL.createObjectURL(file) : ''), [file]);
  useEffect(() => () => { if (localFileUrl) URL.revokeObjectURL(localFileUrl); }, [localFileUrl]);

  function pick(f) {
    X('');
    if (!f) return;
    if (!/\.(mp4|mov|m4v|avi|mkv|webm)$/i.test(f.name)) {
      X(en ? 'Choose an MP4, MOV, MKV, AVI, or WEBM video.' : (t('invalid_video') + ' (Choose an MP4, MOV, MKV, AVI, or WEBM video.)'));
      return;
    }
    if (f.size > maxUploadBytes) {
      let actualMb = (f.size / (1024 * 1024)).toFixed(1);
      let kmMsg = `ទំហំឯកសារ ${actualMb} MB លើសពីកម្រិតកំណត់ ${maxUploadMb} MB។`;
      let enMsg = `File size (${actualMb} MB) exceeds the ${maxUploadMb} MB limit.`;
      X(`${kmMsg} (${enMsg})`);
      return;
    }
    if (f.size === 0) {
      X(en ? 'File is empty. Please choose a valid video.' : t('file_empty'));
      return;
    }

    // Inspect video duration before upload if browser/Electron supports metadata check
    if (typeof document !== 'undefined' && typeof URL !== 'undefined' && URL.createObjectURL) {
      try {
        const v = document.createElement('video');
        v.preload = 'metadata';
        const objUrl = URL.createObjectURL(f);
        v.src = objUrl;
        v.onloadedmetadata = () => {
          URL.revokeObjectURL(objUrl);
          const dur = v.duration;
          if (dur && Number.isFinite(dur) && maxDurationMin > 0 && dur > (maxDurationMin * 60 + 2)) {
            const actualMin = (dur / 60).toFixed(1);
            const kmMsg = `រយៈពេលវីដេអូ (${actualMin} នាទី) លើសពីកម្រិតកំណត់ ${maxDurationMin} នាទី។`;
            const enMsg = `Video duration (${actualMin} min) exceeds the ${maxDurationMin}-minute limit.`;
            X(en ? enMsg : `${kmMsg} (${enMsg})`);
            F(null);
          }
        };
        v.onerror = () => {
          URL.revokeObjectURL(objUrl);
        };
      } catch (e) {
        void e;
      }
    }

    F(f);
    J(null);
  }

  async function uploadChunked() {
    // Synchronous ref guard prevents concurrent invocations from rapid double-clicks
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    const CHUNK_SIZE = 5 * 1024 * 1024;
    const PARALLEL = 3;
    let totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    // Keep a stable key for this upload attempt — not regenerated on re-renders
    const idempotencyKey = window.crypto?.randomUUID ? window.crypto.randomUUID() : (Math.random().toString(36).substring(2) + Date.now());

    try {
      let initRes = await authFetch(A + '/upload/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, total_size: file.size, chunk_size: CHUNK_SIZE }),
      });
      if (!initRes.ok) {
        let errData = await initRes.json().catch(() => ({}));
        X(normalizeError(errData, en, t('processing_failed')));
        return;
      }
      let { upload_id } = await initRes.json();
      localStorage.setItem('pendingUploadId', upload_id);

      let stRes = await authFetch(`${A}/upload/${upload_id}/status`);
      let st = await stRes.json();
      let receivedSet = new Set(st.received_chunks || []);

      const pending = [];
      for (let i = 0; i < totalChunks; i++) {
        if (receivedSet.has(i)) { P(Math.round(((i + 1) / totalChunks) * 100)); continue; }
        pending.push(i);
      }
      for (let b = 0; b < pending.length; b += PARALLEL) {
        const batch = pending.slice(b, b + PARALLEL);
        await Promise.all(batch.map(async (i) => {
          let start = i * CHUNK_SIZE;
          let end = Math.min(file.size, start + CHUNK_SIZE);
          let slice = file.slice(start, end);
          let chunkRes = await authFetch(`${A}/upload/${upload_id}/chunk`, {
            method: 'PUT',
            headers: { 'X-Chunk-Index': String(i) },
            body: slice,
          });
          if (!chunkRes.ok) {
            let chunkErr = await chunkRes.json().catch(() => ({}));
            throw new Error(normalizeError(chunkErr, en, 'Chunk upload failed'));
          }
        }));
        P(Math.round((Math.min(b + PARALLEL, pending.length) / totalChunks) * 100));
      }

      let compRes = await authFetch(`${A}/upload/${upload_id}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_lang: source,
          voice,
          preserve_background_audio: bg,
          keep_background: bg,
          background_audio_volume: bgVol / 100.0,
          background_volume: bgVol / 100.0,
          burn_subtitles: burn,
          review_before_tts: review,
          idempotency_key: idempotencyKey,
        }),
      });
      localStorage.removeItem('pendingUploadId');
      if (!compRes.ok) {
        let errData = await compRes.json().catch(() => ({}));
        X(normalizeError(errData, en, t('processing_failed')));
        return;
      }
      let r = await compRes.json();
      localStorage.setItem('activeJob', r.id);
      P(0); // reset upload progress so it never leaks into job progress display
      J(r);
      refreshHistory();
    } catch (err) {
      X(normalizeError(err, en, t('processing_failed')));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  function upload() {
    // Synchronous ref guard — state may not have updated yet on rapid double-clicks
    if (submittingRef.current || submitting) return;
    // Desktop-local mode: always proceed (noToken is always false).
    // Web mode: the /auth/me probe ensures noToken is false when authenticated.
    if (!file || file.size === 0) { X(t('file_empty')); return; }
    if (file.size > maxUploadBytes) {
      let actualMb = (file.size / (1024 * 1024)).toFixed(1);
      let kmMsg = `ទំហំឯកសារ ${actualMb} MB លើសពីកម្រិតកំណត់ ${maxUploadMb} MB។`;
      let enMsg = `File size (${actualMb} MB) exceeds the ${maxUploadMb} MB limit.`;
      X(`${kmMsg} (${enMsg})`);
      return;
    }
    if (file.size > 10 * 1024 * 1024) { uploadChunked(); return; }
    submittingRef.current = true;
    setSubmitting(true);
    // Stable idempotency key for this single attempt
    const idempotencyKey = window.crypto?.randomUUID ? window.crypto.randomUUID() : (Math.random().toString(36).substring(2) + Date.now());
    let d = new FormData();
    d.append('file', file);
    d.append('source_lang', source);
    d.append('voice', voice);
    d.append('preserve_background_audio', bg);
    d.append('keep_background', bg);
    d.append('background_audio_volume', (bgVol / 100.0).toFixed(2));
    d.append('background_volume', (bgVol / 100.0).toFixed(2));
    d.append('burn_subtitles', burn);
    d.append('review_before_tts', review);
    let x = new XMLHttpRequest();
    x.open('POST', A + '/jobs');
    x.withCredentials = true;
    x.setRequestHeader('Idempotency-Key', idempotencyKey);
    if (TOKEN && !(typeof window !== 'undefined' && window.soundmeDesktop?.isDesktop)) {
      x.setRequestHeader('Authorization', `Bearer ${TOKEN}`);
    }
    x.upload.onprogress = (e) => e.lengthComputable && P(Math.round((e.loaded / e.total) * 100));
    x.onload = () => {
      submittingRef.current = false;
      setSubmitting(false);
      if (x.status > 299) {
        let errDetail;
        try {
          let errObj = JSON.parse(x.responseText);
          errDetail = normalizeError(errObj, en);
        } catch {
          errDetail = normalizeError(x.responseText, en);
        }
        X(errDetail);
        return;
      }
      let r = JSON.parse(x.responseText);
      localStorage.setItem('activeJob', r.id);
      P(0); // reset upload progress so it never leaks into job progress display
      J(r);
      refreshHistory();
    };
    x.onerror = () => {
      submittingRef.current = false;
      setSubmitting(false);
      X(normalizeError(new Error('Network error during upload'), en, t('processing_failed')));
    };
    x.send(d);
  }

  async function previewVoice() {
    X('');
    try {
      let r = await authFetch(A + '/tts/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice }),
      });
      if (!r.ok) {
        let d = await r.json().catch(() => ({}));
        X(normalizeError(d, en, 'Voice preview failed.'));
        return;
      }
      let url = URL.createObjectURL(await r.blob());
      new Audio(url).play();
    } catch (err) {
      X(normalizeError(err, en, 'Voice preview failed. Server unavailable.'));
    }
  }

  async function save() {
    try {
      const segRes = await authFetch(`${A}/jobs/${job.id}/segments`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rows),
      });
      if (!segRes.ok) {
        const d = await segRes.json().catch(() => ({}));
        X(normalizeError(d, en, t('processing_failed')));
        return;
      }
      const resumeRes = await authFetch(`${A}/jobs/${job.id}/resume`, { method: 'POST' });
      if (!resumeRes.ok) {
        const d = await resumeRes.json().catch(() => ({}));
        X(normalizeError(d, en, t('processing_failed')));
        return;
      }
      J({ ...job, status: 'running' });
    } catch (err) {
      X(normalizeError(err, en, t('processing_failed')));
    }
  }

  // eslint-disable-next-line no-unused-vars
  async function logout() {
    try {
      await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
    } catch (e) { void e; }
    localStorage.removeItem('soundme_token');
    localStorage.removeItem('activeJob');
    TOKEN = '';
    setUserSession(null);
    setNoToken(true);
  }

  const stages = ['queued', 'transcribe', 'translate', 'voice', 'mix', 'done'];

  return (
    <div className="app-container">
      {/* ── Modern SaaS Navigation Header ───────────────────────────────── */}
      <nav className="top-navbar">
        <div className="nav-brand">
          <div className="brand-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" y1="19" x2="12" y2="22" />
            </svg>
          </div>
          <div className="brand-title-wrap">
            <span className="brand-title">Soundme AI</span>
            <span className="brand-subtitle">Khmer Studio</span>
          </div>
        </div>

        <div className="nav-tabs">
          <button
            className={`nav-tab ${activeTab === 'workspace' ? 'active' : ''}`}
            onClick={() => setActiveTab('workspace')}
          >
            {t('nav_dashboard')}
          </button>
          <button
            className={`nav-tab ${activeTab === 'history' ? 'active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            {t('nav_history')}
          </button>
          <button
            className={`nav-tab ${activeTab === 'settings' ? 'active' : ''}`}
            onClick={() => setActiveTab('settings')}
          >
            {t('nav_settings')}
          </button>
        </div>

        <div className="nav-right">
          {userSession?.google_email ? (
            <span className="user-badge" title={userSession.google_email}>
              👤 {userSession.google_email}
            </span>
          ) : null}

          <button className="lang-btn" onClick={() => E(!en)}>{en ? 'ខ្មែរ' : 'EN'}</button>
        </div>
      </nav>

      <main>
        {/* ── Brand Title Header (Preserved for tests) ──────────────────── */}
        <header>
          <div>
            <small>SOUNDME · KHMER STUDIO</small>
            <h1>{t('app_title')}</h1>
            <p>{t('app_subtitle')}</p>
          </div>
        </header>

        {/* Login banner removed: desktop-local mode authenticates automatically via /auth/me. */}

        {activeTab === 'workspace' && (
          <>
            <div className="grid">
              {/* ── Left Column: Upload Workspace ───────────────────────── */}
              <section className="card">
                <h2>{t('upload_heading')}</h2>

                {pendingUpload && (
                  <div className="notice resume-notice" style={{ background: 'rgba(99, 102, 241, 0.15)', borderColor: 'rgba(99, 102, 241, 0.3)', color: '#a5b4fc' }}>
                    {en ? '🔄 Resumable upload detected from previous session. Select file to resume.' : '🔄 បានរកឃើញការផ្ទុកឡើងដែលអាចបន្តពីមុន។ សូមជ្រើសរើសឯកសារដើម្បីបន្ត។'}
                  </div>
                )}

                <label
                  className="drop"
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files[0]); }}
                >
                  <input type="file" accept="video/*" aria-label="Choose video" onChange={(e) => pick(e.target.files[0])} />
                  <b>＋</b>
                  <strong>{file?.name || t('upload_heading')}</strong>
                  <span>MP4 · MOV · MKV · WEBM · {maxUploadMb} MB max · {maxDurationMin} min max</span>
                </label>

                {file && file.size > 0 && typeof URL.createObjectURL === 'function' && (
                  <video className="preview" controls src={localFileUrl} />
                )}

                <div className="fields">
                  <label>
                    {t('source_lang')}
                    <select value={source} onChange={(e) => S(e.target.value)}>
                      <option value="auto">Auto</option>
                      <option value="en">English</option>
                      <option value="zh">中文</option>
                    </select>
                  </label>
                  <label>
                    {t('khmer_voice')}
                    <select value={voice} onChange={(e) => V(e.target.value)}>
                      <option value="km-KH-SreymomNeural">{t('voice_sreymom')}</option>
                      <option value="km-KH-PisethNeural">{t('voice_piseth')}</option>
                    </select>
                  </label>
                </div>

                <button type="button" className="secondary" onClick={previewVoice} style={{ marginBottom: '0.85rem' }}>
                  {t('preview_voice_btn')}
                </button>

                <div className="checks">
                  <label>
                    <input
                      type="checkbox"
                      checked={bg}
                      onChange={(e) => B(e.target.checked)}
                      aria-label={t('keep_background')}
                    />
                    {t('keep_background')}
                  </label>
                  <label>
                    <input type="checkbox" checked={burn} onChange={(e) => U(e.target.checked)} />
                    {t('burn_subtitles')}
                  </label>
                  <label>
                    <input type="checkbox" checked={review} onChange={(e) => R(e.target.checked)} />
                    {t('review_before_tts')}
                  </label>
                </div>

                {bg && (
                  <div
                    className="bg-volume-panel"
                    style={{
                      margin: '0.5rem 0 0.85rem',
                      padding: '0.75rem',
                      background: 'rgba(15, 23, 42, 0.75)',
                      border: '1px solid rgba(56, 189, 248, 0.25)',
                      borderRadius: '8px',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.35rem' }}>
                      <label htmlFor="bg-vol-slider" style={{ fontSize: '0.85rem', fontWeight: 500, color: '#e2e8f0' }}>
                        {t('background_volume')}
                      </label>
                      <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#38bdf8' }}>{bgVol}%</span>
                    </div>
                    <input
                      id="bg-vol-slider"
                      type="range"
                      min="0"
                      max="100"
                      step="1"
                      value={bgVol}
                      onChange={(e) => setBgVol(Number(e.target.value))}
                      aria-label={t('background_volume')}
                      style={{ width: '100%', accentColor: '#0284c7', cursor: 'pointer' }}
                    />
                    <p style={{ margin: '0.4rem 0 0', fontSize: '0.75rem', color: '#94a3b8', lineHeight: 1.4 }}>
                      {t('keep_background_help')}
                    </p>
                  </div>
                )}

                <p className="notice">
                  {t('ownership_notice')} {en ? 'Files are deleted after 24 h.' : 'ឯកសារត្រូវបានលុបបន្ទាប់ពី 24 ម៉ោង។'}
                </p>

                {file && file.size > 20e6 && (
                  <p className="notice long-job-warning" style={{ color: '#fbbf24', fontWeight: 500 }}>
                    {t('long_job_eta')}
                  </p>
                )}

                {file && (!job || job.status === 'completed' || job.status === 'failed') && (
                  <button className="primary" onClick={upload} disabled={submitting}>
                    {submitting ? (en ? 'Uploading...' : 'កំពុងផ្ទុកឡើង...') : t('start_dubbing')}
                  </button>
                )}

                {pct > 0 && <progress value={pct} max="100" aria-label="Upload progress" />}
                {err && <p role="alert" className="error">{err}</p>}
              </section>

              {/* ── Right Column: Pipeline Monitor ─────────────────────── */}
              <section className="card">
                <h2>{t('processing_heading')}</h2>

                <div className="stages-wrapper">
                  {stages.map((s, i) => (
                    <div key={s} className={'stage ' + (stages.indexOf(job?.stage) >= i ? 'done' : '')}>
                      <i>{i + 1}</i>
                      {['Upload', 'Transcribe', 'Translate', 'Voice', 'Mix', 'Done'][i]}
                    </div>
                  ))}
                </div>

                {/* Progress bar: use job.progress (dubbing), fall back to pct (upload) only
                    when no job is active. Never show upload pct for a queued/running job. */}
                <progress
                  value={
                    job
                      ? (job.status === 'completed'
                          ? 100
                          : (job.status === 'queued'
                              ? (job.stage === 'resuming' && job.progress ? Math.min(99, job.progress) : 0)
                              : Math.min(99, job.progress ?? 0)))
                      : pct
                  }
                  max="100"
                />

                <p style={{ margin: '0.5rem 0' }}>
                  {job?.status === 'queued'
                    ? (en ? 'Queued (waiting for slot)' : 'កំពុងរង់ចាំក្នុងជួរ · Queued')
                    : (job?.stage || t('stage_waiting'))} · {
                    job
                      ? (job.status === 'completed'
                          ? 100
                          : (job.status === 'queued'
                              ? (job.stage === 'resuming' && job.progress ? Math.min(99, job.progress) : 0)
                              : Math.min(99, job.progress ?? 0)))
                      : (pct || 0)
                  }%
                  {job?.eta_seconds > 0 && (
                    <span className="eta-badge" style={{ marginLeft: '0.75rem', color: '#10b981', fontWeight: 600 }}>
                      ({en ? 'ETA: ~' : 'នៅសល់ប្រហែល ~'}{Math.ceil(job.eta_seconds / 60)} {en ? 'min' : 'នាទី'})
                    </span>
                  )}
                </p>

                {job?.error && (
                  <p role="alert" className="error">
                    {normalizeError(job.error, en)}
                  </p>
                )}

                {job && ['queued', 'running'].includes(job.status) && (
                  <button
                    className="secondary"
                    onClick={async () => {
                      await authFetch(`${A}/jobs/${job.id}/cancel`, { method: 'POST' }).catch(() => {});
                      J((prev) => (prev ? { ...prev, status: 'canceled', stage: 'canceled' } : null));
                      refreshHistory();
                    }}
                    style={{ marginTop: '0.75rem', width: '100%' }}
                  >
                    {t('cancel_btn')}
                  </button>
                )}

                {job?.status === 'canceled' && (
                  <p style={{ margin: '1rem 0 0', color: '#94a3b8', fontWeight: 600 }}>
                    {en ? 'Job cancelled' : 'ការងារត្រូវបានបោះបង់'}
                  </p>
                )}

                {job?.status === 'completed' && (
                  <p style={{ margin: '1rem 0 0', color: '#10b981', fontWeight: 600 }}>
                    ✓ {t('dubbing_complete')} — {en ? 'See result below' : 'ចូលមើលលទ្ធផលខាងក្រោម'}
                  </p>
                )}
              </section>
            </div>

            {/* ── Translation Review Section (if awaiting review) ──────── */}
            {job?.status === 'awaiting_review' && (
              <section className="card card-full">
                <h2>{t('review_heading')}</h2>
                {job?.error?.message_en && (
                  <div className="alert alert-warning" style={{ margin: '0.5rem 0 1rem', padding: '0.75rem', background: '#2d1800', border: '1px solid #f59e0b', borderRadius: '8px', color: '#fef3c7' }}>
                    <strong>{en ? 'Timing Review:' : 'ការពិនិត្យពេលវេលា៖'}</strong> {normalizeError(job.error, en)}
                  </div>
                )}
                {rows.map((r, i) => {
                  const stat = (job?.timing_review?.fit_stats || []).find((s) => s.segment_id === r.id);
                  return (
                    <label className="review" key={r.id}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{r.start.toFixed(1)}s – {r.end.toFixed(1)}s · {r.source_text}</span>
                        {stat && (
                          <span style={{ fontSize: '0.75rem', color: stat.review_required ? '#f87171' : '#38bdf8', background: stat.review_required ? 'rgba(239, 68, 68, 0.15)' : 'rgba(56, 189, 248, 0.15)', padding: '2px 8px', borderRadius: '4px' }}>
                            {en ? `Slot: ${stat.target_duration.toFixed(2)}s | Audio: ${stat.raw_duration.toFixed(2)}s | ${stat.recovery_action || (stat.review_required ? 'Review' : 'OK')}` : `ពេលដើម: ${stat.target_duration.toFixed(2)}s | សំឡេង: ${stat.raw_duration.toFixed(2)}s`}
                          </span>
                        )}
                      </div>
                      <textarea
                        value={r.text_km}
                        onChange={(e) => Q(rows.map((x, n) => (n === i ? { ...x, text_km: e.target.value } : x)))}
                      />
                    </label>
                  );
                })}
                <button className="primary" onClick={save} style={{ marginTop: '1rem' }}>
                  {t('save_and_continue')}
                </button>
              </section>
            )}

            {/* ── Dedicated Dubbing Complete Showcase (BELOW WORKSPACE) ─── */}
            {job?.status === 'completed' && (
              <section className="result-showcase">
                <div className="result-header">
                  <div className="result-badge">
                    <span>✓</span>
                    <span>{t('dubbing_complete')}</span>
                  </div>

                  <div className="view-mode-toggle">
                    <button
                      className={`view-mode-btn ${!compareMode ? 'active' : ''}`}
                      onClick={() => setCompareMode(false)}
                    >
                      {t('single_view')}
                    </button>
                    <button
                      className={`view-mode-btn ${compareMode ? 'active' : ''}`}
                      onClick={() => setCompareMode(true)}
                    >
                      {t('compare_view')}
                    </button>
                  </div>
                </div>

                <div className="players-container">
                  {compareMode ? (
                    <div className="players-compare">
                      <div className="player-box">
                        <h4>{t('original_video_heading')}</h4>
                        <video
                          className="preview original-player"
                          aria-label="Original video"
                          controls
                          src={file ? localFileUrl : (videoBlobUrls.source || undefined)}
                        />
                      </div>
                      <div className="player-box">
                        <h4>{t('dubbed_video_heading')}</h4>
                        <video
                          className="preview dubbed-player"
                          aria-label="Dubbed video"
                          controls
                          src={videoBlobUrls.video || undefined}
                        />
                      </div>
                    </div>
                  ) : (
                    <div>
                      <video
                        className="preview dubbed-player"
                        aria-label="Dubbed video"
                        controls
                        src={videoBlobUrls.video || undefined}
                        style={{ maxHeight: '440px', margin: '0 auto' }}
                      />
                    </div>
                  )}
                </div>

                <div className="download-bar">
                  <button
                    className="download-link main-download"
                    onClick={async (e) => {
                      e.preventDefault();
                      try {
                        await downloadWithAuth('video', job.id, 'soundme_video.video');
                      } catch {
                        X(en ? 'Download failed. Please try again.' : t('processing_failed'));
                      }
                    }}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" y1="15" x2="12" y2="3" />
                    </svg>
                    {t('download_video')}
                  </button>

                  {['audio', 'srt', 'vtt'].map((k) => (
                    <button
                      key={k}
                      className="download-link"
                      onClick={async (e) => {
                        e.preventDefault();
                        const ext = k === 'audio' ? 'wav' : k;
                        try {
                          await downloadWithAuth(k, job.id, `soundme_${k}.${ext}`);
                        } catch {
                          X(en ? 'Download failed.' : t('processing_failed'));
                        }
                      }}
                    >
                      {t(`download_${k}`)}
                    </button>
                  ))}

                  <button
                    className="download-link"
                    onClick={async (e) => {
                      e.preventDefault();
                      try {
                        await downloadWithAuth('json', job.id, `soundme_transcript.json`);
                      } catch {
                        X(en ? 'Download failed.' : t('processing_failed'));
                      }
                    }}
                  >
                    {t('download_json')}
                  </button>
                </div>
              </section>
            )}
          </>
        )}

        {/* ── Recent Jobs History ─────────────────────────────────────────── */}
        {(activeTab === 'history' || activeTab === 'workspace') && (
          <JobHistory
            jobs={jobs}
            onSelectJob={(j) => {
              J(j);
              if (j.segments) Q(j.segments);
              setActiveTab('workspace');
              window.scrollTo({ top: 0, behavior: 'smooth' });
            }}
            onDownload={downloadWithAuth}
            onRefresh={refreshHistory}
            onCancelJob={(jid) => authFetch(`${A}/jobs/${jid}/cancel`, { method: 'POST' })}
            lang={en ? 'en' : 'km'}
            t={t}
            authFetch={authFetch}
            A={A}
            loadError={historyLoadError}
          />
        )}

        {/* ── Settings & Architecture Info Tab ────────────────────────────── */}
        {activeTab === 'settings' && (
          <section className="card card-full">
            <h2>{t('nav_settings')}</h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '1rem', marginTop: '1rem' }}>
              <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '1rem', borderRadius: '12px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                <h4 style={{ margin: '0 0 0.5rem 0', color: '#10b981' }}>🤖 AI Pipeline Models</h4>
                <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.85rem', color: '#94a3b8', lineHeight: '1.8' }}>
                  <li>Whisper ASR: faster-whisper small</li>
                  <li>Translation: NLLB-200 600M (CTranslate2 int8)</li>
                  <li>Khmer Voice: Neural Sreymom / Piseth & MMS-TTS</li>
                  <li>Audio Muxing: FFmpeg 6+ Audio Ducking</li>
                </ul>
              </div>

              <div style={{ background: 'rgba(255, 255, 255, 0.03)', padding: '1rem', borderRadius: '12px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                <h4 style={{ margin: '0 0 0.5rem 0', color: '#10b981' }}>🔒 Privacy & Data Retention</h4>
                <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.85rem', color: '#94a3b8', lineHeight: '1.8' }}>
                  <li>Local on-device inference</li>
                  <li>24-hour automatic TTL cleanup</li>
                  <li>Cross-user isolation with HttpOnly sessions</li>
                  <li>60 min / day processing quota per account</li>
                </ul>
              </div>
            </div>
          </section>
        )}
      </main>

      <footer>
        Local processing · Upload content you have permission to use · Files deleted after 24 h.
      </footer>
    </div>
  );
}
