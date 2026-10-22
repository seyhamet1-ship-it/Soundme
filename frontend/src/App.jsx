import { useEffect, useMemo, useState } from 'react';
import strings from './i18n/strings.json';

const A = '/api';

// ── Auth token management ──────────────────────────────────────────────────
function getToken() {
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
  if (TOKEN) return { Authorization: `Bearer ${TOKEN}`, 'X-Access-Token': TOKEN, ...extra };
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

function fetchVideoBlobUrl(kind, jid) {
  return authFetch(`${A}/jobs/${jid}/download/${kind}`)
    .then((r) => (r.ok ? r.blob() : null))
    .then((blob) => (blob ? URL.createObjectURL(blob) : null));
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
  const [file, F] = useState(null);
  const [source, S] = useState('auto');
  const [voice, V] = useState('km-KH-SreymomNeural');
  const [bg, B] = useState(false);
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
  const [noToken, setNoToken] = useState(() => !TOKEN);
  const [videoBlobUrls, setVideoBlobUrls] = useState({});
  const [tokenInput, setTokenInput] = useState('');

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
  }, [en]);

  function submitToken() {
    const v = tokenInput.trim();
    if (!v) return;
    TOKEN = v;
    localStorage.setItem('soundme_token', v);
    setNoToken(false);
  }

  // ── Load active job and history on mount ───────────────────────────────────
  useEffect(() => {
    let id = localStorage.getItem('activeJob');
    if (id) {
      J({ id });
    }
    authFetch(A + '/jobs')
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) {
          H(data);
          if (!id && data.length > 0) {
            const comp = data.find((x) => x.status === 'completed') || data[0];
            if (comp) {
              J(comp);
              if (comp.segments) Q(comp.segments);
            }
          }
        }
      })
      .catch(() => {});
  }, [noToken]);

  // ── Job polling with SSE → polling fallback ────────────────────────────────
  useEffect(() => {
    if (!job?.id) return;
    let live = true;
    let sseAlive = false;
    let timer = null;
    let stopSse = null;
    let sseTimeout = null;
    let finished = false;

    const TERMINAL = ['completed', 'failed', 'canceled'];

    const refreshHistory = () => {
      authFetch(A + '/jobs')
        .then((r) => r.json())
        .then((data) => Array.isArray(data) && H(data))
        .catch(() => {});
    };

    const applyJob = (d) => {
      if (!live) return;
      J(d);
      if (d.segments) Q(d.segments);
      if (TERMINAL.includes(d.status)) {
        finished = true;
        if (timer) {
          clearInterval(timer);
          timer = null;
        }
        stopSse?.();
        stopSse = null;
        localStorage.removeItem('activeJob');
        refreshHistory();
      }
    };

    const poll = () =>
      authFetch(`${A}/jobs/${job.id}`)
        .then((r) => r.json())
        .then(applyJob)
        .catch(() => {});

    const startPolling = () => {
      if (timer || !live || finished) return;
      poll();
      timer = setInterval(poll, 1300);
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
      stopSse?.();
      if (timer) clearInterval(timer);
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
    if (f.size > 500 * 1024 * 1024) {
      let actualMb = (f.size / (1024 * 1024)).toFixed(1);
      let kmMsg = `ទំហំឯកសារ ${actualMb} MB លើសពីកម្រិតកំណត់ 500 MB។`;
      let enMsg = `File size (${actualMb} MB) exceeds the 500 MB limit.`;
      X(`${kmMsg} (${enMsg})`);
      return;
    }
    if (f.size === 0) {
      X(en ? 'File is empty. Please choose a valid video.' : t('file_empty'));
      return;
    }
    F(f);
    J(null);
  }

  async function uploadChunked() {
    setSubmitting(true);
    const CHUNK_SIZE = 5 * 1024 * 1024;
    const PARALLEL = 3;
    let totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    let idempotencyKey = window.crypto?.randomUUID ? window.crypto.randomUUID() : (Math.random().toString(36).substring(2) + Date.now());

    try {
      let initRes = await authFetch(A + '/upload/init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, total_size: file.size, chunk_size: CHUNK_SIZE }),
      });
      if (!initRes.ok) {
        let errData = await initRes.json().catch(() => ({}));
        setSubmitting(false);
        X(errData.detail || t('processing_failed'));
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
          if (!chunkRes.ok) throw new Error('Chunk upload failed');
        }));
        P(Math.round((Math.min(b + PARALLEL, pending.length) / totalChunks) * 100));
      }

      let compRes = await authFetch(`${A}/upload/${upload_id}/complete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_lang: source,
          voice,
          keep_background: bg,
          burn_subtitles: burn,
          review_before_tts: review,
          idempotency_key: idempotencyKey,
        }),
      });
      setSubmitting(false);
      localStorage.removeItem('pendingUploadId');
      if (!compRes.ok) {
        let errData = await compRes.json().catch(() => ({}));
        X(errData.detail || t('processing_failed'));
        return;
      }
      let r = await compRes.json();
      localStorage.setItem('activeJob', r.id);
      J(r);
    } catch {
      setSubmitting(false);
      X(t('processing_failed'));
    }
  }

  function upload() {
    if (submitting) return;
    if (noToken && !userSession?.authenticated) {
      X(en ? 'Please sign in with Google or enter an access code first.' : 'សូមចូលដោយប្រើ Google ឬបញ្ចូលលេខកូដចូលជាមុនសិន។');
      return;
    }
    if (!file || file.size === 0) { X(t('file_empty')); return; }
    if (file.size > 500 * 1024 * 1024) {
      let actualMb = (file.size / (1024 * 1024)).toFixed(1);
      let kmMsg = `ទំហំឯកសារ ${actualMb} MB លើសពីកម្រិតកំណត់ 500 MB។`;
      let enMsg = `File size (${actualMb} MB) exceeds the 500 MB limit.`;
      X(`${kmMsg} (${enMsg})`);
      return;
    }
    if (file.size > 10 * 1024 * 1024) { uploadChunked(); return; }
    setSubmitting(true);
    let idempotencyKey = window.crypto?.randomUUID ? window.crypto.randomUUID() : (Math.random().toString(36).substring(2) + Date.now());
    let d = new FormData();
    d.append('file', file);
    d.append('source_lang', source);
    d.append('voice', voice);
    d.append('keep_background', bg);
    d.append('burn_subtitles', burn);
    d.append('review_before_tts', review);
    let x = new XMLHttpRequest();
    x.open('POST', A + '/jobs');
    x.withCredentials = true;
    x.setRequestHeader('Idempotency-Key', idempotencyKey);
    if (TOKEN) x.setRequestHeader('Authorization', `Bearer ${TOKEN}`);
    x.upload.onprogress = (e) => e.lengthComputable && P(Math.round((e.loaded / e.total) * 100));
    x.onload = () => {
      setSubmitting(false);
      if (x.status > 299) {
        let errDetail;
        try {
          let errObj = JSON.parse(x.responseText);
          errDetail = errObj.detail || x.responseText;
          if (typeof errDetail === 'object') {
            errDetail = (en ? errDetail.message_en : errDetail.message_km) || errDetail.code || JSON.stringify(errDetail);
          }
        } catch { errDetail = x.responseText || 'Error'; }
        X(errDetail);
        return;
      }
      let r = JSON.parse(x.responseText);
      localStorage.setItem('activeJob', r.id);
      J(r);
    };
    x.onerror = () => { setSubmitting(false); X(t('processing_failed')); };
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
      if (!r.ok) { let d = await r.json().catch(() => ({})); X(d.detail || 'Voice preview failed.'); return; }
      let url = URL.createObjectURL(await r.blob());
      new Audio(url).play();
    } catch { X('Voice preview failed. Server unavailable.'); }
  }

  async function save() {
    await authFetch(`${A}/jobs/${job.id}/segments`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rows),
    });
    await authFetch(`${A}/jobs/${job.id}/resume`, { method: 'POST' });
    J({ ...job, status: 'running' });
  }

  async function logout() {
    try {
      await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
    } catch (_) { /* ignore */ }
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
          ) : (
            <a
              href="/auth/google"
              className="google-nav-btn"
              style={{
                textDecoration: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                color: '#f8fafc',
                background: 'rgba(255, 255, 255, 0.08)',
                padding: '0.4rem 0.8rem',
                borderRadius: '8px',
                fontSize: '0.825rem',
                border: '1px solid rgba(255, 255, 255, 0.12)',
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
              </svg>
              <span>{en ? 'Continue with Google' : 'ចូលដោយប្រើ Google'}</span>
            </a>
          )}

          <button className="lang-btn" onClick={() => E(!en)}>{en ? 'ខ្មែរ' : 'EN'}</button>
          {(!noToken || userSession?.authenticated) && (
            <button className="signout-btn" onClick={logout} title={en ? 'Sign out' : 'ចាកចេញ'}>
              {en ? 'Sign out' : 'ចាកចេញ'}
            </button>
          )}
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

        {/* ── Prominent Google Login Banner (when not authenticated) ──── */}
        {noToken && !userSession?.authenticated && (
          <section className="login-banner card card-full" style={{ marginBottom: '1.5rem', background: 'linear-gradient(135deg, rgba(30, 41, 59, 0.8), rgba(15, 23, 42, 0.95))', borderColor: 'rgba(99, 102, 241, 0.25)' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '1.25rem' }}>
              <div>
                <h3 style={{ margin: '0 0 0.35rem 0', fontSize: '1.15rem', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <span>🔐</span> {en ? 'Sign in to Soundme AI' : 'ចូលប្រើប្រាស់ Soundme AI'}
                </h3>
                <p style={{ margin: 0, fontSize: '0.875rem', color: '#94a3b8' }}>
                  {en
                    ? 'Private access. Continue with Google or enter your access code.'
                    : 'សេវាកម្មឯកជន។ សូមបន្តជាមួយ Google ឬបញ្ចូលលេខកូដចូល។'}
                </p>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.75rem' }}>
                <a href="/auth/google" className="google-login-btn" style={{ padding: '0.65rem 1.25rem', fontSize: '0.9rem' }}>
                  <svg width="18" height="18" viewBox="0 0 24 24">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z" />
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z" />
                  </svg>
                  <span>{en ? 'Continue with Google' : 'បន្តជាមួយ Google'}</span>
                </a>

                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                  <input
                    type="password"
                    className="login-input"
                    placeholder={en ? 'Access code' : 'លេខកូដចូល'}
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && submitToken()}
                    style={{ width: '150px', padding: '0.6rem 0.75rem', fontSize: '0.85rem' }}
                  />
                  <button className="primary" onClick={submitToken} style={{ padding: '0.6rem 1rem', fontSize: '0.85rem' }}>
                    {en ? 'Enter' : 'ចូល'}
                  </button>
                </div>
              </div>
            </div>
            {err && <p className="error" style={{ marginTop: '0.75rem' }}>{err}</p>}
          </section>
        )}

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
                  <span>MP4 · MOV · MKV · WEBM · 500 MB max</span>
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
                  {[
                    [bg, B, t('keep_background')],
                    [burn, U, t('burn_subtitles')],
                    [review, R, t('review_before_tts')],
                  ].map(([v, set, label]) => (
                    <label key={label}>
                      <input type="checkbox" checked={v} onChange={(e) => set(e.target.checked)} />
                      {label}
                    </label>
                  ))}
                </div>

                <p className="notice">
                  {t('ownership_notice')} {en ? 'Files are deleted after 24 h.' : 'ឯកសារត្រូវបានលុបបន្ទាប់ពី 24 ម៉ោង។'}
                </p>

                {bg && (
                  <p className="notice" style={{ fontSize: '0.75rem', marginTop: '-0.25rem', color: '#94a3b8' }}>
                    {en ? 'Audio ducking enabled for background preservation.' : 'ប្រើប្រាស់ Audio Ducking សម្រាប់រក្សាតន្ត្រីផ្ទៃខាងក្រោយ។'}
                  </p>
                )}

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

                <progress value={job?.progress || pct} max="100" />

                <p style={{ margin: '0.5rem 0' }}>
                  {job?.status === 'queued'
                    ? (en ? 'Queued (waiting for slot)' : 'កំពុងរង់ចាំក្នុងជួរ · Queued')
                    : (job?.stage || t('stage_waiting'))} · {job?.progress || pct || 0}%
                  {job?.eta_seconds > 0 && (
                    <span className="eta-badge" style={{ marginLeft: '0.75rem', color: '#10b981', fontWeight: 600 }}>
                      ({en ? 'ETA: ~' : 'នៅសល់ប្រហែល ~'}{Math.ceil(job.eta_seconds / 60)} {en ? 'min' : 'នាទី'})
                    </span>
                  )}
                </p>

                {job?.error && (
                  <p role="alert" className="error">
                    [{job.error.code || 'PROCESSING_FAILED'}] {en ? job.error.message_en : job.error.message_km}
                  </p>
                )}

                {job && ['queued', 'running'].includes(job.status) && (
                  <button
                    className="secondary"
                    onClick={() => authFetch(`${A}/jobs/${job.id}/cancel`, { method: 'POST' })}
                    style={{ marginTop: '0.75rem', width: '100%' }}
                  >
                    {t('cancel_btn')}
                  </button>
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
                {rows.map((r, i) => (
                  <label className="review" key={r.id}>
                    <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>{r.start.toFixed(1)}s · {r.source_text}</span>
                    <textarea
                      value={r.text_km}
                      onChange={(e) => Q(rows.map((x, n) => (n === i ? { ...x, text_km: e.target.value } : x)))}
                    />
                  </label>
                ))}
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
          <section className="card card-full">
            <h2>{t('job_history')}</h2>
            {jobs.length ? (
              jobs.slice(0, 8).map((j) => (
                <p className="history" key={j.id}>
                  {j.filename || j.id} · {j.status}{' '}
                  {j.status === 'completed' && (
                    <>
                      <button
                        className="download-link"
                        onClick={async (e) => {
                          e.preventDefault();
                          try {
                            await downloadWithAuth('video', j.id);
                          } catch {
                            X(en ? 'Download failed.' : t('processing_failed'));
                          }
                        }}
                      >
                        {t('download_history')}
                      </button>
                      <button
                        className="view-btn"
                        onClick={() => {
                          J(j);
                          if (j.segments) Q(j.segments);
                          setActiveTab('workspace');
                          window.scrollTo({ top: 0, behavior: 'smooth' });
                        }}
                      >
                        {t('view_result_btn')}
                      </button>
                    </>
                  )}
                </p>
              ))
            ) : (
              <p>{t('history_empty')}</p>
            )}
          </section>
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
