import { AlertTriangle, CheckCircle2, LoaderCircle, TimerReset } from 'lucide-react';

const statusConfig = {
  queued: { label: 'Queued', className: 'status-badge status-queued', icon: TimerReset },
  processing: { label: 'Processing', className: 'status-badge status-processing', icon: LoaderCircle },
  completed: { label: 'Completed', className: 'status-badge status-completed', icon: CheckCircle2 },
  failed: { label: 'Failed', className: 'status-badge status-failed', icon: AlertTriangle },
  idle: { label: 'Idle', className: 'status-badge status-queued', icon: TimerReset },
};

export default function StatusCard({ status, progress, message, error }) {
  const activeStatus = statusConfig[status] || statusConfig.idle;
  const Icon = activeStatus.icon;

  return (
    <aside className="glass-panel p-6">
      <div className="text-xs font-medium uppercase tracking-[0.2em] text-violet-400">Processing</div>

      <div className="mt-4 rounded-xl border border-slate-700 bg-slate-950/70 p-4">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-slate-300">Status</span>
          <span className={activeStatus.className}>
            <Icon className={`h-3.5 w-3.5 ${status === 'processing' ? 'animate-spin' : ''}`} />
            {activeStatus.label}
          </span>
        </div>

        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between text-xs text-slate-400">
            <span>Progress</span>
            <span>{Math.max(0, Math.min(100, progress))}%</span>
          </div>

          <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-gradient-to-r from-sky-500 to-emerald-400 transition-all duration-300"
              style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
            />
          </div>
        </div>

        <p className="mt-4 text-sm text-slate-300">{message}</p>

        {error && (
          <div className="mt-4 rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-300">
            {error}
          </div>
        )}
      </div>

      <div className="mt-6 rounded-2xl border border-slate-700 bg-slate-950/60 p-4">
        <div className="mb-2 text-xs uppercase tracking-[0.2em] text-slate-400">Workflow</div>
        <ul className="space-y-2 text-sm text-slate-300">
          <li>• Extract source audio</li>
          <li>• Transcribe with Whisper</li>
          <li>• Translate to target language</li>
          <li>• Generate voiceover + subtitles</li>
          <li>• Merge final dubbed video</li>
        </ul>
      </div>
    </aside>
  );
}