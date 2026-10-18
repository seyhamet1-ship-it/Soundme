import { Download, PlayCircle } from 'lucide-react';

export default function ResultsGallery({ jobs }) {
  return (
    <section className="glass-panel p-6">
      <div className="mb-5 flex items-center justify-between">
        <h2 className="text-2xl font-bold text-white">Output Gallery</h2>
      </div>

      {jobs.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-950/50 p-10 text-center text-slate-500">
          No processed videos yet.
        </div>
      ) : (
        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
          {jobs.map((job) => (
            <div key={job.id || job.name} className="overflow-hidden rounded-2xl border border-slate-700 bg-slate-950/70">
              <div className="flex aspect-video items-center justify-center bg-slate-900 text-slate-400">
                <div className="flex flex-col items-center gap-2">
                  <PlayCircle className="h-10 w-10 text-slate-500" />
                  <span className="text-xs uppercase tracking-[0.2em] text-slate-500">Preview</span>
                </div>
              </div>

              <div className="p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="truncate text-sm font-medium text-slate-200">{job.name}</div>
                  <span
                    className={`rounded-full px-2 py-1 text-[10px] font-medium ${
                      job.status === 'completed'
                        ? 'bg-emerald-500/10 text-emerald-300'
                        : 'bg-slate-700 text-slate-300'
                    }`}
                  >
                    {job.status}
                  </span>
                </div>

                {job.downloadUrl ? (
                  <a
                    href={job.downloadUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-500 px-4 py-2.5 font-semibold text-slate-950 transition hover:bg-emerald-400"
                  >
                    <Download className="h-4 w-4" />
                    Download
                  </a>
                ) : (
                  <button
                    type="button"
                    disabled
                    className="w-full cursor-not-allowed rounded-xl bg-slate-800 px-4 py-2.5 text-slate-400"
                  >
                    Processing...
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}