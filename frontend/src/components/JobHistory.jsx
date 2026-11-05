import React, { useState, useMemo, useRef } from 'react';
import {
  Trash2,
  CheckSquare,
  Square,
  RefreshCw,
  AlertCircle,
  CheckCircle2,
  Clock,
  XCircle,
  Eye,
  Download,
  AlertTriangle,
  X,
  Loader2,
} from 'lucide-react';

export default function JobHistory({
  jobs = [],
  onSelectJob,
  onDownload,
  onRefresh,
  onCancelJob,
  lang = 'km',
  t,
  authFetch,
  A = '/api',
  loadError = false,
}) {
  const [selectedIds, setSelectedIds] = useState(new Set());
  // filter: 'all' | 'completed' | 'failed' | 'queued' | 'running'
  const [filter, setFilter] = useState('all');
  const [confirmModal, setConfirmModal] = useState({ open: false, target: null, count: 0, title: '' });
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState({ message: '', type: '' });
  const feedbackTimer = useRef(null);

  const en = lang === 'en';

  const showFeedback = (message, type = 'success') => {
    if (feedbackTimer.current) clearTimeout(feedbackTimer.current);
    setFeedback({ message, type });
    feedbackTimer.current = setTimeout(() => setFeedback({ message: '', type: '' }), 4500);
  };

  // Deduplicate by stable job id to prevent duplicate row rendering
  const deduplicatedJobs = useMemo(() => {
    const seen = new Set();
    return jobs.filter((j) => {
      if (!j.id || seen.has(j.id)) return false;
      seen.add(j.id);
      return true;
    });
  }, [jobs]);

  // Filter jobs by active tab
  const filteredJobs = useMemo(() => {
    switch (filter) {
      case 'completed': return deduplicatedJobs.filter((j) => j.status === 'completed');
      case 'failed':    return deduplicatedJobs.filter((j) => j.status === 'failed' || j.status === 'canceled');
      case 'queued':    return deduplicatedJobs.filter((j) => j.status === 'queued');
      case 'running':   return deduplicatedJobs.filter((j) => j.status === 'running' || j.status === 'awaiting_review');
      default:          return deduplicatedJobs;
    }
  }, [deduplicatedJobs, filter]);

  // Jobs eligible for direct deletion (no active processing)
  const isDeletable = (job) =>
    ['completed', 'failed', 'canceled', 'queued'].includes(job.status);

  const deletableJobs = useMemo(() => filteredJobs.filter(isDeletable), [filteredJobs]);
  const allDeletableSelected =
    deletableJobs.length > 0 && deletableJobs.every((j) => selectedIds.has(j.id));

  // Status counts
  const counts = useMemo(() => ({
    all:       deduplicatedJobs.length,
    completed: deduplicatedJobs.filter((j) => j.status === 'completed').length,
    failed:    deduplicatedJobs.filter((j) => j.status === 'failed' || j.status === 'canceled').length,
    queued:    deduplicatedJobs.filter((j) => j.status === 'queued').length,
    running:   deduplicatedJobs.filter((j) => j.status === 'running' || j.status === 'awaiting_review').length,
  }), [deduplicatedJobs]);

  // ── Selection helpers ──────────────────────────────────────────────────────
  const toggleSelect = (id, e) => {
    e?.stopPropagation();
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    setSelectedIds(allDeletableSelected ? new Set() : new Set(deletableJobs.map((j) => j.id)));
  };

  // ── Confirmation dialog triggers ──────────────────────────────────────────
  const promptDeleteSingle = (job, e) => {
    e?.stopPropagation();
    setConfirmModal({
      open: true,
      target: { type: 'single', jid: job.id, filename: job.filename || job.id, jobStatus: job.status },
      count: 1,
      title: job.filename || job.id,
    });
  };

  const promptDeleteSelected = () => {
    if (selectedIds.size === 0) return;
    setConfirmModal({
      open: true,
      target: { type: 'selected', ids: Array.from(selectedIds) },
      count: selectedIds.size,
      title: '',
    });
  };

  const promptClearCompleted = () => {
    const n = counts.completed;
    if (n === 0) return;
    setConfirmModal({
      open: true,
      target: { type: 'filter', filter: 'completed' },
      count: n,
      title: t('clear_completed_btn') || (en ? 'Clear Completed' : 'លុបការងារដែលបានបញ្ចប់'),
    });
  };

  const promptClearFailed = () => {
    const n = counts.failed;
    if (n === 0) return;
    setConfirmModal({
      open: true,
      target: { type: 'filter', filter: 'failed' },
      count: n,
      title: t('clear_failed_btn') || (en ? 'Clear Failed/Cancelled' : 'លុបការងារដែលបរាជ័យ/បោះបង់'),
    });
  };

  const promptClearAllFinished = () => {
    const n = counts.completed + counts.failed;
    if (n === 0) return;
    setConfirmModal({
      open: true,
      target: { type: 'filter', filter: 'all_finished' },
      count: n,
      title: t('clear_all_finished_btn') || (en ? 'Clear All Finished' : 'លុបការងារដែលបានបញ្ចប់ទាំងអស់'),
    });
  };

  // ── Execute confirmed deletion ─────────────────────────────────────────────
  const executeDelete = async () => {
    setDeleting(true);
    const { target } = confirmModal;
    try {
      if (target.type === 'single') {
        // If queued, cancel first (fire-and-forget), then delete
        if (target.jobStatus === 'queued') {
          await authFetch(`${A}/jobs/${target.jid}/cancel`, { method: 'POST' }).catch(() => {});
        }
        const res = await authFetch(`${A}/jobs/${target.jid}`, { method: 'DELETE' });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData?.detail?.message_en || `HTTP ${res.status}`);
        }
        setSelectedIds((prev) => { const n = new Set(prev); n.delete(target.jid); return n; });
        showFeedback(t('delete_success') || (en ? 'Job deleted successfully.' : 'បានលុបការងារដោយជោគជ័យ។'));
      } else if (target.type === 'selected') {
        const res = await authFetch(`${A}/jobs/bulk-delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_ids: target.ids }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSelectedIds(new Set());
        const n = data.count ?? target.ids.length;
        showFeedback(`${t('delete_success') || (en ? 'Deleted' : 'បានលុប')} (${n})`);
      } else if (target.type === 'filter') {
        const res = await authFetch(`${A}/jobs/bulk-delete`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filter: target.filter }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSelectedIds(new Set());
        showFeedback(`${t('delete_success') || (en ? 'Cleared' : 'បានលុប')} (${data.count ?? 0})`);
      }
      setConfirmModal({ open: false, target: null, count: 0, title: '' });
      if (onRefresh) onRefresh();
    } catch (err) {
      showFeedback(
        (t('delete_error') || (en ? 'Failed to delete: ' : 'មិនអាចលុបបានទេ៖ ')) + (err?.message || ''),
        'error',
      );
    } finally {
      setDeleting(false);
    }
  };

  // ── Status Badge ──────────────────────────────────────────────────────────
  const renderStatusBadge = (job) => {
    const status = job.status || 'unknown';
    if (status === 'completed')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
          <CheckCircle2 className="w-3.5 h-3.5" />
          {t('status_completed') || (en ? 'Completed' : 'បានបញ្ចប់')}
        </span>
      );
    if (status === 'failed')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30">
          <AlertCircle className="w-3.5 h-3.5" />
          {t('status_failed') || (en ? 'Failed' : 'បរាជ័យ')}
        </span>
      );
    if (status === 'canceled')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-500/20 text-slate-400 border border-slate-500/30">
          <XCircle className="w-3.5 h-3.5" />
          {t('status_canceled') || (en ? 'Cancelled' : 'បានបោះបង់')}
        </span>
      );
    if (status === 'awaiting_review')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/30">
          <AlertTriangle className="w-3.5 h-3.5" />
          {t('status_awaiting_review') || (en ? 'Awaiting Review' : 'រង់ចាំការពិនិត្យ')}
        </span>
      );
    if (status === 'queued')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/30">
          <Clock className="w-3.5 h-3.5" />
          {t('status_queued') || (en ? 'Queued' : 'ក្នុងជួររង់ចាំ')}
        </span>
      );
    if (status === 'running')
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-sky-500/15 text-sky-400 border border-sky-500/30">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          {t('status_processing') || (en ? 'Processing' : 'កំពុងដំណើរការ')}
          {job.progress ? ` (${job.progress}%)` : ''}
        </span>
      );
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-700/40 text-slate-300">
        {status}
      </span>
    );
  };

  // ── Date formatter ────────────────────────────────────────────────────────
  const formatDate = (job) => {
    const raw = job.created_at || job.updated_at;
    if (!raw) return '—';
    try {
      const d = typeof raw === 'number' ? new Date(raw * 1000) : new Date(raw);
      if (isNaN(d.getTime())) return '—';
      return d.toLocaleString(en ? 'en-US' : 'km-KH', {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch {
      return '—';
    }
  };

  // ── Filter tab helper ─────────────────────────────────────────────────────
  const FilterTab = ({ id, label, count: c, activeColor }) => (
    <button
      onClick={() => setFilter(id)}
      className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
        filter === id
          ? `${activeColor} border border-current/40`
          : 'text-slate-400 hover:text-slate-200'
      }`}
    >
      {label} ({c})
    </button>
  );

  return (
    <div className="card card-full" style={{ padding: '1.5rem', background: '#0a0f1d' }}>
      {/* ── Header & Toolbar ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-4 pb-4 border-b border-slate-800">
        <div>
          <h2 style={{ margin: 0, fontSize: '1.35rem', fontWeight: 700, color: '#f8fafc' }}>
            {t('job_history') || (en ? 'Recent Jobs' : 'ប្រវត្តិការងារ')}
          </h2>
          <p style={{ margin: '0.25rem 0 0', fontSize: '0.85rem', color: '#94a3b8' }}>
            {deduplicatedJobs.length} {en ? 'total jobs recorded' : 'ការងារសរុប'}
          </p>
        </div>

        {/* Filter Tabs */}
        <div className="flex flex-wrap items-center gap-1 bg-slate-900/90 p-1 rounded-xl border border-slate-800">
          <FilterTab id="all"       label={t('filter_all')       || (en ? 'All'             : 'ទាំងអស់')}          count={counts.all}       activeColor="bg-slate-600/30 text-slate-200" />
          <FilterTab id="completed" label={t('filter_completed') || (en ? 'Completed'       : 'បានបញ្ចប់')}         count={counts.completed} activeColor="bg-emerald-600/30 text-emerald-400" />
          <FilterTab id="failed"    label={t('filter_failed')    || (en ? 'Failed/Cancelled': 'បរាជ័យ/បោះបង់')}    count={counts.failed}    activeColor="bg-rose-600/30 text-rose-400" />
          {counts.queued > 0 && (
            <FilterTab id="queued"  label={t('filter_queued')    || (en ? 'Queued'          : 'ក្នុងជួររង់ចាំ')}   count={counts.queued}    activeColor="bg-sky-600/30 text-sky-400" />
          )}
          {counts.running > 0 && (
            <FilterTab id="running" label={t('filter_running')   || (en ? 'Running'         : 'កំពុងដំណើរការ')}    count={counts.running}   activeColor="bg-sky-600/30 text-sky-400" />
          )}
        </div>

        {/* Bulk Action Buttons */}
        <div className="flex items-center gap-2 flex-wrap">
          {selectedIds.size > 0 && (
            <button
              onClick={promptDeleteSelected}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-rose-600/20 text-rose-300 border border-rose-500/40 hover:bg-rose-600/30 transition-all"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {(t('bulk_delete_btn') || (en ? 'Delete Selected ({count})' : 'លុបដែលបានជ្រើស ({count})')).replace('{count}', selectedIds.size)}
            </button>
          )}

          <button
            onClick={promptClearCompleted}
            disabled={counts.completed === 0}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700/80 text-slate-300 disabled:opacity-40 disabled:pointer-events-none transition-all border border-slate-700"
          >
            {t('clear_completed_btn') || (en ? 'Clear Completed' : 'លុបបញ្ចប់')}
          </button>

          <button
            onClick={promptClearFailed}
            disabled={counts.failed === 0}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700/80 text-slate-300 disabled:opacity-40 disabled:pointer-events-none transition-all border border-slate-700"
          >
            {t('clear_failed_btn') || (en ? 'Clear Failed' : 'លុបបរាជ័យ')}
          </button>

          {(counts.completed + counts.failed) > 0 && (
            <button
              onClick={promptClearAllFinished}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium bg-rose-900/40 hover:bg-rose-900/60 text-rose-300 disabled:opacity-40 transition-all border border-rose-700/40"
            >
              {t('clear_all_finished_btn') || (en ? 'Clear All Finished' : 'លុបទាំងអស់')}
            </button>
          )}

          {onRefresh && (
            <button
              onClick={onRefresh}
              title={en ? 'Refresh list' : 'ផ្ទុកទិន្នន័យឡើងវិញ'}
              className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {/* ── Feedback Banner ───────────────────────────────────────────────── */}
      {feedback.message && (
        <div
          className={`mt-4 p-3 rounded-xl border flex items-center gap-2 text-sm ${
            feedback.type === 'error'
              ? 'bg-rose-500/10 border-rose-500/30 text-rose-300'
              : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
          }`}
        >
          {feedback.type === 'error'
            ? <AlertCircle className="w-4 h-4 shrink-0" />
            : <CheckCircle2 className="w-4 h-4 shrink-0" />}
          <span>{feedback.message}</span>
        </div>
      )}

      {/* ── Job List ──────────────────────────────────────────────────────── */}
      {filteredJobs.length === 0 ? (
        <div className="py-12 text-center text-slate-400">
          {loadError ? (
            <>
              <AlertCircle className="w-10 h-10 mx-auto mb-3 opacity-60 text-rose-400" />
              <p className="text-base font-medium text-rose-400">
                {en ? 'Could not load history.' : 'មិនអាចផ្ទុកប្រវត្តិបានទេ។'}
              </p>
              <p className="text-sm mt-1">
                {en ? 'The backend may be unavailable. Try refreshing.' : 'ម៉ាស៊ីនខាងក្រោយប្រហែលជានៅក្រៅបណ្ដាញ។ សូមសាកល្បងផ្ទុកឡើងវិញ។'}
              </p>
            </>
          ) : (
            <>
              <Clock className="w-10 h-10 mx-auto mb-3 opacity-30 text-emerald-400" />
              <p className="text-base font-medium">
                {t('history_empty') || (en ? 'No jobs found.' : 'មិនទាន់មានការងារទេ។')}
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          {/* Select-all bar */}
          {deletableJobs.length > 0 && (
            <div className="flex items-center gap-2 pb-2 text-xs text-slate-400">
              <button
                onClick={toggleSelectAll}
                className="inline-flex items-center gap-1.5 hover:text-slate-200 transition-colors"
              >
                {allDeletableSelected
                  ? <CheckSquare className="w-4 h-4 text-emerald-400" />
                  : <Square className="w-4 h-4 text-slate-500" />}
                <span>
                  {allDeletableSelected
                    ? (t('deselect_all') || (en ? 'Deselect All' : 'ដកការជ្រើសរើសទាំងអស់'))
                    : (t('select_all')   || (en ? 'Select All Deletable' : 'ជ្រើសរើសទាំងអស់'))}
                </span>
              </button>
              <span>•</span>
              <span>{deletableJobs.length} {en ? 'eligible for deletion' : 'អាចលុបបាន'}</span>
            </div>
          )}

          <div className="divide-y divide-slate-800/80">
            {filteredJobs.map((j) => {
              const deletable = isDeletable(j);
              const isSelected = selectedIds.has(j.id);
              const hasOutputs = j.status === 'completed' && j.outputs && Object.keys(j.outputs).length > 0;
              const isQueuedOrRunning = j.status === 'queued' || j.status === 'running';

              return (
                <div
                  key={j.id}
                  className={`py-3.5 px-3 flex flex-col md:flex-row md:items-center justify-between gap-3 rounded-xl transition-all ${
                    isSelected ? 'bg-emerald-950/20 border border-emerald-500/20' : 'hover:bg-slate-900/60'
                  }`}
                >
                  {/* Left: Checkbox & Info */}
                  <div className="flex items-start md:items-center gap-3 min-w-0 flex-1">
                    {deletable ? (
                      <button
                        onClick={(e) => toggleSelect(j.id, e)}
                        className="mt-1 md:mt-0 text-slate-500 hover:text-emerald-400 transition-colors shrink-0"
                        title={en ? 'Select for deletion' : 'ជ្រើសរើសសម្រាប់លុប'}
                      >
                        {isSelected
                          ? <CheckSquare className="w-4 h-4 text-emerald-400" />
                          : <Square className="w-4 h-4 text-slate-500" />}
                      </button>
                    ) : (
                      <div className="w-4 shrink-0" />
                    )}

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className="font-semibold text-slate-100 text-sm truncate max-w-md"
                          title={j.filename || j.id}
                        >
                          {j.filename || j.id}
                        </span>
                        {renderStatusBadge(j)}
                      </div>

                      <div className="flex items-center gap-3 mt-1 text-xs text-slate-400 flex-wrap">
                        <span>{formatDate(j)}</span>
                        <span>•</span>
                        <span>
                          {t('target_language') || (en ? 'Target' : 'គោលដៅ')}:{' '}
                          <strong className="text-slate-300">ភាសាខ្មែរ (Khmer)</strong>
                        </span>
                        {j.duration_seconds && (
                          <>
                            <span>•</span>
                            <span>{Math.round(j.duration_seconds / 60)} min</span>
                          </>
                        )}
                        {/* Error summary */}
                        {j.error?.message_en && (
                          <>
                            <span>•</span>
                            <span className="text-rose-400 truncate max-w-xs" title={j.error.message_en}>
                              {en ? j.error.message_en : (j.error.message_km || j.error.message_en)}
                            </span>
                          </>
                        )}
                      </div>

                      {/* Job ID copy row */}
                      <div className="flex items-center gap-1 mt-0.5">
                        <span className="text-xs text-slate-600 font-mono truncate max-w-[140px]">{j.id}</span>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            navigator.clipboard?.writeText(j.id).catch(() => {});
                          }}
                          className="text-slate-600 hover:text-slate-400 transition-colors text-xs px-1"
                          title={en ? 'Copy job ID' : 'ចម្លងលេខ ID'}
                        >
                          ⎘
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* Right: Actions */}
                  <div className="flex items-center gap-2 shrink-0 self-end md:self-center flex-wrap">
                    {/* View completed result */}
                    {j.status === 'completed' && onSelectJob && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onSelectJob(j); }}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-500/20 transition-all"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        {t('view_result_btn') || (en ? 'View' : 'មើល')}
                      </button>
                    )}

                    {/* Review awaiting_review */}
                    {j.status === 'awaiting_review' && onSelectJob && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onSelectJob(j); }}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/30 hover:bg-amber-500/20 transition-all"
                      >
                        <Eye className="w-3.5 h-3.5" />
                        {t('review_heading') || (en ? 'Review' : 'ពិនិត្យ')}
                      </button>
                    )}

                    {/* Cancel running/queued */}
                    {isQueuedOrRunning && onCancelJob && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onCancelJob(j.id).then(() => { if (onRefresh) onRefresh(); }).catch(() => {});
                        }}
                        className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-rose-500/10 text-rose-300 border border-rose-500/20 hover:bg-rose-500/20 transition-all"
                      >
                        <X className="w-3.5 h-3.5" />
                        {t('cancel_btn') || (en ? 'Cancel' : 'បោះបង់')}
                      </button>
                    )}

                    {/* Downloads */}
                    {hasOutputs && onDownload && (
                      <div className="flex items-center gap-1">
                        <button
                          onClick={(e) => { e.stopPropagation(); onDownload('video', j.id, `dubbed_${j.filename || j.id}.mp4`); }}
                          title={en ? 'Download MP4 video' : 'ទាញយកវីដេអូ'}
                          className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs inline-flex items-center gap-1"
                        >
                          <Download className="w-3.5 h-3.5 text-emerald-400" />
                          <span>MP4</span>
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); onDownload('srt', j.id, `subtitles_${j.filename || j.id}.srt`); }}
                          title={en ? 'Download SRT subtitles' : 'ទាញយក SRT'}
                          className="p-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-all text-xs inline-flex items-center gap-1"
                        >
                          <Download className="w-3.5 h-3.5 text-sky-400" />
                          <span>SRT</span>
                        </button>
                      </div>
                    )}

                    {/* Delete button (completed/failed/canceled/queued) */}
                    {deletable && (
                      <button
                        onClick={(e) => promptDeleteSingle(j, e)}
                        title={en ? 'Delete job' : 'លុបការងារ'}
                        className="p-1.5 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/20 hover:border-rose-500/40 transition-all"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Confirmation Modal ────────────────────────────────────────────── */}
      {confirmModal.open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm"
          onClick={() => !deleting && setConfirmModal({ open: false, target: null, count: 0, title: '' })}
        >
          <div
            className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-lg font-bold text-white mb-3">
              {t('confirm_delete_title') || (en ? 'Confirm Deletion' : 'បញ្ជាក់ការលុប')}
            </h3>
            <p className="text-sm text-slate-300 mb-6 leading-relaxed">
              {confirmModal.target?.type === 'single'
                ? (t('confirm_delete_single') || (en
                    ? `Delete job "${confirmModal.title}"? All associated files will be permanently removed.`
                    : `លុបការងារ "${confirmModal.title}"? ឯកសារទាំងអស់នឹងត្រូវបានលុបចេញជាអចិន្ត្រៃយ៍។`))
                : (t('confirm_delete_msg') || (en
                    ? `Delete ${confirmModal.count} job(s)? All associated files will be permanently removed.`
                    : `លុប ${confirmModal.count} ការងារ? ឯកសារទាំងអស់នឹងត្រូវបានលុបចេញជាអចិន្ត្រៃយ៍។`))}
            </p>

            {/* Warn about queued jobs */}
            {confirmModal.target?.jobStatus === 'queued' && (
              <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
                {en
                  ? 'This queued job will be cancelled before deletion.'
                  : 'ការងារដែលរង់ចាំនេះនឹងត្រូវបានបោះបង់មុនពេលលុប។'}
              </div>
            )}

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setConfirmModal({ open: false, target: null, count: 0, title: '' })}
                disabled={deleting}
                className="px-4 py-2 rounded-xl text-sm font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 transition-all disabled:opacity-50"
              >
                {t('cancel_modal_btn') || (en ? 'Cancel' : 'ថយក្រោយ')}
              </button>
              <button
                onClick={executeDelete}
                disabled={deleting}
                className="px-4 py-2 rounded-xl text-sm font-semibold text-white bg-rose-600 hover:bg-rose-700 transition-all disabled:opacity-50 inline-flex items-center gap-2"
              >
                {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {t('confirm_delete_action') || (en ? 'Confirm Delete' : 'យល់ព្រមលុប')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
