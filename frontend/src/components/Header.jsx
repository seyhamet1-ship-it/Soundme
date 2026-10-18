import { Cpu } from 'lucide-react';

export default function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-800 bg-slate-900/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
        <div>
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-sky-400">Local Studio</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight text-white">Video Dubbing Studio</h1>
        </div>

        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs font-medium text-emerald-300">
          <Cpu className="h-3.5 w-3.5" />
          CPU Optimized
        </div>
      </div>
    </header>
  );
}