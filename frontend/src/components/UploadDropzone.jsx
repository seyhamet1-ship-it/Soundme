import { CloudUpload, Film } from 'lucide-react';

const supportedFormats = ['MP4', 'MOV', 'MKV', 'AVI', 'WEBM'];

export default function UploadDropzone({ file, onFileSelect, onDrop }) {
  const handleInputChange = (event) => {
    const nextFile = event.target.files?.[0];
    if (nextFile) onFileSelect(nextFile);
  };

  return (
    <div className="glass-panel p-6">
      <div className="mb-5">
        <p className="text-xs font-medium uppercase tracking-[0.2em] text-sky-400">New Project</p>
        <h2 className="mt-2 text-3xl font-bold text-white">Upload a video</h2>
      </div>

      <label
        onDragOver={(event) => {
          event.preventDefault();
          event.currentTarget.classList.add('dropzone-active');
        }}
        onDragLeave={(event) => {
          event.currentTarget.classList.remove('dropzone-active');
        }}
        onDrop={(event) => {
          event.preventDefault();
          event.currentTarget.classList.remove('dropzone-active');
          const droppedFile = event.dataTransfer.files?.[0];
          if (droppedFile) onDrop(droppedFile);
        }}
        className="relative block cursor-pointer rounded-2xl border-2 border-dashed border-slate-700 bg-slate-950/50 p-8 text-center transition-all duration-200 hover:border-sky-500/70 hover:bg-sky-500/5"
      >
        <input type="file" accept="video/mp4,video/mov,video/m4v,video/avi,video/mkv,video/webm" onChange={handleInputChange} />

        <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-sky-500/10 text-sky-300">
          <CloudUpload className="h-8 w-8" />
        </div>

        <div className="text-lg font-semibold text-white">Drop a video here or click to browse</div>
        <div className="mt-2 text-sm text-slate-400">
          Supported: {supportedFormats.join(', ')} · Max 500 MB · Up to 125 min
        </div>

        {file && (
          <div className="mt-6 flex items-center justify-center gap-3 rounded-xl border border-slate-700 bg-slate-900/80 p-3 text-left text-sm text-slate-200">
            <Film className="h-5 w-5 text-sky-400" />
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{file.name}</div>
              <div className="text-xs text-slate-400">{(file.size / 1024 / 1024).toFixed(2)} MB</div>
            </div>
          </div>
        )}
      </label>
    </div>
  );
}
