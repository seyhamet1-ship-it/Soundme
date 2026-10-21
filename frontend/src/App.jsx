import { useEffect, useState } from 'react';
import strings from './i18n/strings.json';

const A = '/api';

// ── Auth token management ──────────────────────────────────────────────────
// Token is read from localStorage 'soundme_token'.
// URL ?token= param is consumed on first load, saved, then removed from URL.
// ALL API calls attach Authorization: Bearer <token> header — NEVER in URL.
// For Google OAuth sessions, the browser sends the HttpOnly cookie automatically.
function getToken() {
  try {
    const params = new URLSearchParams(window.location?.search || '');
    const urlToken = params.get('token');
    if (urlToken) {
      localStorage.setItem('soundme_token', urlToken);
      // Remove token from URL to prevent it appearing in server logs / Referer headers
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
  if (TOKEN) return { Authorization: `Bearer ${TOKEN}`, ...extra };
  return extra;
}

function authFetch(url, opts = {}) {
  const headers = authHeaders(opts.headers || {});
  return fetch(url, { credentials: 'include', ...opts, headers });
}

export default function App() {
  const [en, E] = useState(false);
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

  const t = (key) => strings[key]?.[en ? 'en' : 'km'] || key;

  // ── Handle OAuth callback messages and session state on mount ───────────────
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

    // Check /auth/me for active session cookie (e.g. from Google OAuth)
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

  // ── Token gate: show input if missing ──────────────────────────────────────
  const [tokenInput, setTokenInput] = useState('');
  function submitToken() {
    const v = tokenInput.trim();
    if (!v) return;
    TOKEN = v;
    localStorage.setItem('soundme_token', v);
    setNoToken(false);
  }

  // ── Load active job and history on mount ───────────────────────────────────
  useEffect(() => {
    if (noToken) return;
    let id = localStorage.getItem('activeJob');
    if (id) J({ id });
    authFetch(A + '/jobs').then((r) => r.json()).then(H).catch(() => {});
  }, [noToken]);

  // ── Job polling with SSE → polling fallback ────────────────────────────────
  useEffect(() => {
    if (!job?.id || noToken) return;
    let live = true;
    let sseAlive = false;
    let timer = null;

    const poll = () =>
      authFetch(`${A}/jobs/${job.id}`)
        .then((r) => r.json())
        .then((d) => {
          if (!live) return;
          J(d);
          if (d.segments) Q(d.segments);
          if (['completed', 'failed', 'canceled'].includes(d.status)) {
            localStorage.removeItem('activeJob');
            authFetch(A + '/jobs').then((r) => r.json()).then(H);
          }
        })
        .catch(() => {});

    // Attempt SSE first; fall back to polling after 5 s if nothing arrives
    let sse = null;
    let sseTimeout = null;
    try {
      // Auth via Authorization header is not supported by EventSource API.
      // SSE authenticates via session cookie (preferred) or falls back to polling.
      // ?token= query param intentionally removed to prevent tokens in server logs.
      const sseUrl = `${A}/jobs/${job.id}/events`;
      sse = new EventSource(sseUrl);
      sseTimeout = setTimeout(() => {
        // If SSE hasn't delivered any message within 5 s, switch to polling
        if (!sseAlive && live) {
          sse?.close();
          poll();
          timer = setInterval(poll, 1300);
        }
      }, 5000);

      sse.onmessage = (e) => {
        sseAlive = true;
        clearTimeout(sseTimeout);
        if (!live) return;
        try {
          const d = JSON.parse(e.data);
          J(d);
          if (d.segments) Q(d.segments);
          if (['completed', 'failed', 'canceled'].includes(d.status)) {
            sse?.close();
            localStorage.removeItem('activeJob');
            authFetch(A + '/jobs').then((r) => r.json()).then(H);
          }
        } catch (err) {
          void err;
        }
      };

      sse.onerror = () => {
        if (!sseAlive && live) {
          // SSE failed before delivering anything → fall back to polling
          clearTimeout(sseTimeout);
          sse?.close();
          poll();
          timer = setInterval(poll, 1300);
        } else {
          sse?.close();
        }
      };
    } catch {
      // EventSource not supported → polling only
      poll();
      timer = setInterval(poll, 1300);
    }

    return () => {
      live = false;
      clearTimeout(sseTimeout);
      sse?.close();
      if (timer) clearInterval(timer);
    };
  }, [job?.id, noToken]);

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
    // Chunk size ≤ 8 MB as required (Cloudflare limit); upload ≤ 3 chunks in parallel
    const CHUNK_SIZE = 5 * 1024 * 1024; // 5 MB chunks (well within 8 MB limit)
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

      // Upload chunks in batches of PARALLEL (≤ 3 in flight at once)
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

  // ── Token gate UI ──────────────────────────────────────────────────────────
  if (noToken) {
    return (
      <main>
        <header>
          <div>
            <small>SOUNDME · KHMER STUDIO</small>
            <h1>{t('app_title')}</h1>
          </div>
          <button onClick={() => E(!en)}>{en ? 'ខ្មែរ' : 'EN'}</button>
        </header>
        <div className="grid">
          <section className="card">
            <h2>{en ? 'Sign In' : 'ចូលប្រើប្រាស់'}</h2>
            <p className="notice">
              {en
                ? 'This service is private. Enter your access code or sign in with Google.'
                : 'សេវាកម្មនេះជាឯកជន។ សូមបញ្ចូលលេខកូដចូល ឬចូលដោយប្រើ Google។'}
            </p>
            {/* Google OAuth login */}
            <a href="/auth/google" style={{ display: 'block', textAlign: 'center', background: '#fff', border: '1px solid #dadce0', borderRadius: '0.375rem', padding: '0.6rem', marginBottom: '0.75rem', color: '#3c4043', textDecoration: 'none', fontWeight: 500 }}>
              <span style={{ marginRight: '0.5rem' }}>🔵</span>
              {en ? 'Continue with Google' : 'បន្តជាមួយ Google'}
            </a>
            <div style={{ textAlign: 'center', color: '#94a3b8', marginBottom: '0.75rem', fontSize: '0.85rem' }}>
              {en ? '— or enter access code —' : '— ឬបញ្ចូលលេខកូដចូល —'}
            </div>
            <input
              type="password"
              placeholder={en ? 'Access code' : 'លេខកូដចូល'}
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submitToken()}
              style={{ width: '100%', padding: '0.6rem', borderRadius: '0.375rem', border: '1px solid #e2e8f0', marginBottom: '0.75rem' }}
            />
            <button className="primary" onClick={submitToken}>
              {en ? 'Continue' : 'បន្ត'}
            </button>
          </section>
        </div>
      </main>
    );
  }

  let stages = ['queued', 'transcribe', 'translate', 'voice', 'mix', 'done'];

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

  return (
    <main>
      <header>
        <div>
          <small>SOUNDME · KHMER STUDIO</small>
          <h1>{t('app_title')}</h1>
          <p>{t('app_subtitle')}</p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {userSession?.google_email && (
            <span style={{ fontSize: '0.8rem', color: '#64748b', background: '#f1f5f9', padding: '0.25rem 0.5rem', borderRadius: '0.25rem' }}>
              👤 {userSession.google_email}
            </span>
          )}
          <button onClick={() => E(!en)}>{en ? 'ខ្មែរ' : 'EN'}</button>
          <button onClick={logout} title={en ? 'Sign out' : 'ចាកចេញ'} style={{ fontSize: '0.8rem', padding: '0.3rem 0.7rem' }}>
            {en ? 'Sign out' : 'ចាកចេញ'}
          </button>
        </div>
      </header>
      <div className="grid">
        <section className="card">
          <h2>{t('upload_heading')}</h2>
          {pendingUpload && (
            <div className="notice resume-notice" style={{ background: '#e0e7ff', color: '#3730a3', padding: '0.6rem 0.8rem', borderRadius: '0.375rem', marginBottom: '0.75rem', fontSize: '0.875rem', border: '1px solid #c7d2fe' }}>
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
          {file && file.size > 0 && typeof URL.createObjectURL === 'function' && <video className="preview" controls src={URL.createObjectURL(file)} />}
          <div className="fields">
            <label>
              {t('source_lang')}
              <select value={source} onChange={(e) => S(e.target.value)}>
                <option value="auto">Auto</option>
                <option value="zh">中文</option>
                <option value="en">English</option>
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
          <button className="secondary" onClick={previewVoice}>{t('preview_voice_btn')}</button>
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
          <p className="notice">{t('ownership_notice')} {en ? 'Files are deleted after 24 h.' : 'ឯកសារត្រូវបានលុបបន្ទាប់ពី 24 ម៉ោង។'}</p>
          {bg && (
            <p className="notice" style={{ fontSize: '0.75rem', marginTop: '-0.25rem', color: '#64748b' }}>
              {en ? 'Audio ducking enabled for background preservation.' : 'ប្រើប្រាស់ Audio Ducking សម្រាប់រក្សាតន្ត្រីផ្ទៃខាងក្រោយ។'}
            </p>
          )}
          {file && file.size > 20e6 && (
            <p className="notice long-job-warning" style={{ color: '#d97706', fontWeight: 500 }}>
              {t('long_job_eta')}
            </p>
          )}
          {file && !job && (
            <button className="primary" onClick={upload} disabled={submitting}>{t('start_dubbing')}</button>
          )}
          {pct > 0 && <progress value={pct} max="100" aria-label="Upload progress" />}
          {err && <p role="alert" className="error">{err}</p>}
        </section>
        <section className="card">
          <h2>{t('processing_heading')}</h2>
          {stages.map((s, i) => (
            <div key={s} className={'stage ' + (stages.indexOf(job?.stage) >= i ? 'done' : '')}>
              <i>{i + 1}</i>
              {['Upload', 'Transcribe', 'Translate', 'Voice', 'Mix', 'Done'][i]}
            </div>
          ))}
          <progress value={job?.progress || pct} max="100" />
          <p>
            {job?.status === 'queued' ? (en ? 'Queued (waiting for slot)' : 'កំពុងរង់ចាំក្នុងជួរ · Queued') : (job?.stage || t('stage_waiting'))} · {job?.progress || pct || 0}%
            {job?.eta_seconds > 0 && (
              <span className="eta-badge" style={{ marginLeft: '0.75rem', color: '#6366f1', fontWeight: 600 }}>
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
            <button className="secondary" onClick={() => authFetch(`${A}/jobs/${job.id}/cancel`, { method: 'POST' })}>
              {t('cancel_btn')}
            </button>
          )}
          {job?.status === 'completed' && (
            <>
              <div className="players-compare" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '1rem', marginTop: '1rem' }}>
                <div>
                  <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.875rem' }}>{en ? 'Original Video' : 'វីដេអូដើម · Original'}</h4>
                  <video className="preview original-player" aria-label="Original video" controls src={file ? URL.createObjectURL(file) : `${A}/jobs/${job.id}/download/source?token=${encodeURIComponent(TOKEN)}`} style={{ width: '100%', borderRadius: '0.5rem' }} />
                </div>
                <div>
                  <h4 style={{ margin: '0 0 0.5rem 0', fontSize: '0.875rem' }}>{en ? 'Dubbed Video (Khmer)' : 'វីដេអូបកប្រែជាភាសាខ្មែរ · Dubbed'}</h4>
                  <video className="preview dubbed-player" aria-label="Dubbed video" controls src={`${A}/jobs/${job.id}/download/video?token=${encodeURIComponent(TOKEN)}`} style={{ width: '100%', borderRadius: '0.5rem' }} />
                </div>
              </div>
              <nav style={{ marginTop: '1rem' }}>
                {['video', 'audio', 'srt', 'vtt'].map((k) => (
                  <a key={k} href={`${A}/jobs/${job.id}/download/${k}?token=${encodeURIComponent(TOKEN)}`} download>
                    Download {k.toUpperCase()}
                  </a>
                ))}
              </nav>
            </>
          )}
        </section>
      </div>
      {job?.status === 'awaiting_review' && (
        <section className="card">
          <h2>{t('review_heading')}</h2>
          {rows.map((r, i) => (
            <label className="review" key={r.id}>
              {r.start.toFixed(1)}s · {r.source_text}
              <textarea
                value={r.text_km}
                onChange={(e) => Q(rows.map((x, n) => (n === i ? { ...x, text_km: e.target.value } : x)))}
              />
            </label>
          ))}
          <button className="primary" onClick={save}>{t('save_and_continue')}</button>
        </section>
      )}
      <section className="card">
        <h2>{t('job_history')}</h2>
        {jobs.length ? (
          jobs
            .slice(0, 8)
            .map((j) => (
              <p className="history" key={j.id}>
                {j.filename || j.id} · {j.status}{' '}
                {j.status === 'completed' && <a href={`${A}/jobs/${j.id}/download/video?token=${encodeURIComponent(TOKEN)}`}>MP4</a>}
              </p>
            ))
        ) : (
          <p>{t('history_empty')}</p>
        )}
      </section>
      <footer>Local processing · Upload content you have permission to use · Files deleted after 24 h.</footer>
    </main>
  );
}
