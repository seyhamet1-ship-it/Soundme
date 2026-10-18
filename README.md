# Soundme — Khmer Video Dubbing Studio

[![Status](https://img.shields.io/badge/Release_Candidate-v1.0.0-green.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](#)
[![Vite](https://img.shields.io/badge/Frontend-Vite%20%2B%20React-purple.svg)](#)
[![Mode](https://img.shields.io/badge/Mode-REAL-success.svg)](#)

Soundme is a local-first AI video dubbing studio that transcribes Chinese (Mandarin) or English speech, translates it into natural Khmer, generates human-like Khmer voiceover, and exports dubbed videos with synchronized audio and Khmer subtitles.

---

## Architecture & Pipeline

```text
[Input Video] (MP4/WebM)
       │
       ▼
 [faster-whisper] ──> Speech Recognition (zh / en)
       │
       ▼
  [NLLB-200]     ──> Neural Machine Translation to Khmer (khm_Khmr)
       │
       ▼
  [Edge TTS]     ──> High-fidelity Khmer Speech Synthesis (km-KH-SreymomNeural / PisethNeural)
  (Fallback MMS) ──> Meta MMS-TTS (facebook/mms-tts-khm)
       │
       ▼
[Rate Fitting]   ──> Two-stage fit: TTS rate (up to +30%) + tempo stretch (up to 1.25x)
       │
       ▼
   [FFmpeg]      ──> Audio Ducking + Khmer OS Subtitle Burn-in (libx264/aac)
       │
       ▼
[Dubbed Video]   ──> MP4 Video + WAV Audio + SRT/VTT Subtitles + Transcript JSON
```

---

## System Requirements

- **Python:** 3.10 or higher
- **Node.js:** v18 or higher (with `npm`)
- **FFmpeg & FFprobe:** Installed and accessible in system `PATH` (or located under `tools/ffmpeg-*/bin`)
- **Operating System:** Windows 10/11, Ubuntu/Debian Linux, or macOS

---

## Installation

### 1. Clone & Set Up Backend

```bash
# Clone the repository
git clone https://github.com/soundme/soundme.git
cd soundme

# Create and activate Python virtual environment
python -m venv .venv
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### 2. Set Up Frontend

```bash
cd frontend
npm install
cd ..
```

---

## Quick Start (ONE Command)

### Windows (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

### Linux / macOS
```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

The script automatically starts the backend server on `http://127.0.0.1:8000`, waits for `/api/health` to turn green, launches the Vite frontend on `http://localhost:5173`, and opens the application in your browser.

### Manual 2-Process Start
If preferred, you can run the backend and frontend in separate terminals:

```bash
# Terminal 1: Backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: Frontend
cd frontend
npm run dev
```

---

## API Health & Key Endpoints

- **`GET /api/health`** — Health check returning FFmpeg status, loaded ASR/TTS providers, and worker capacity.
- **`POST /api/jobs`** — Submit video for dubbing (supports `file`, `source_lang`, `voice`, `keep_background`, `burn_subtitles`, `review_before_tts`).
- **`GET /api/jobs/{id}`** — Poll job processing state and progress percentage.
- **`GET /api/jobs/{id}/events`** — Server-Sent Events (SSE) live progress feed.
- **`POST /api/tts/preview`** — Synthesize instant Khmer audio preview snippet.
- **`GET /api/jobs/{id}/download/{video|audio|srt|vtt|json}`** — Download final media outputs.

---

## Verification & Quality Gates

Run the comprehensive test suite (unit tests, mypy typecheck, frontend tests, pipeline assertions, and Playwright E2E):

```bash
# Full verification suite
make verify

# Or on Windows without make:
python scripts/verify_all.py --e2e

# Interactive single-machine verification with live Edge TTS:
powershell -ExecutionPolicy Bypass -File scripts\verify_on_my_machine.ps1
```

### Docker Status
- **Docker Compose:** Configured with `docker-compose.yml` and `deploy/nginx.conf` (aligned for 4096 MB body size and 7200s proxy timeouts). Host Docker daemon was not tested during local dev.

---

## Long-Video Support (Up to 60 Minutes) & Honest Limits

Soundme supports long-form media dubbing up to 90 minutes (`MAX_DURATION_MIN=90`, `MAX_UPLOAD_MB=4096`) utilizing resumable chunked uploads, bounded-memory streaming, and windowed pipeline processing.

### Expected Processing Throughput (CPU vs GPU)

| Hardware Environment | Measured Throughput (60-min input) | Processing Ratio | Memory (RSS) | Intermediate Disk Space |
|---|---|---|---|---|
| **CPU (8-core Ryzen / i7)** | ~50 - 60 minutes wall-clock | ~0.85x – 1.0x real-time | < 500 MB (streamed) | ~3x input size (~20 GB) |
| **GPU (NVIDIA RTX / CUDA)** | ~8 - 12 minutes wall-clock | ~5.0x – 7.0x real-time | < 2 GB VRAM | ~3x input size (~20 GB) |

### Hardware Recommendations
- **Minimum:** 8-core CPU, 16 GB RAM, 20 GB free SSD disk space (for windowed audio chunks and temp files).
- **Recommended:** NVIDIA GPU with >= 8 GB VRAM (CUDA 12 support for faster-whisper fp16 + Demucs BGM separation), 32 GB RAM, fast NVMe SSD.

### Edge TTS Rate Limits & Production Azure Speech Recommendation
- **Edge TTS (Default):** Uses Microsoft Edge's read-aloud service without SLA. For large batches or 60-minute continuous production dubbing, Microsoft may enforce rate limits (HTTP 429 / connection drop). The pipeline includes exponential backoff with jitter and per-segment artifact caching.
- **Production Recommendation:** Configure `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` in `.env` to use the official Azure Cognitive Services Speech SDK. This provides enterprise SLA, parallel concurrency, and zero risk of ToS throttling.

### Model Licenses & Commercial Compliance
- **Meta NLLB-200 (`facebook/nllb-200-distilled-600M`):** Released under **CC-BY-NC 4.0 (Non-Commercial)**. For commercial deployments, configure commercial translation providers (e.g. Google Cloud Translation API or DeepL).
- **Meta MMS (`facebook/mms-tts-khm`):** Released under **CC-BY-NC 4.0 (Non-Commercial)**. Edge TTS / Azure Speech should be used for commercial production dubbing.

### Overnight 60-Minute Verification Scripts
To verify full 60-minute dubbing and fault injection on your host machine:
- **Windows (PowerShell):**
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\verify_long_on_my_machine.ps1
  ```
- **Linux / macOS (Bash):**
  ```bash
  bash scripts/verify_long_on_my_machine.sh
  ```

---

## សេចក្តីណែនាំសង្ខេបជាភាសាខ្មែរ (Khmer Quick-Start)

### សម្រាប់ Windows (PowerShell)

1. **ដំឡើងបណ្ណាល័យ:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   cd frontend; npm install; cd ..
   ```

2. **ដំណើរការកម្មវិធីដោយពាក្យបញ្ជាតែមួយ:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
   ```

3. **បើកប្រើប្រាស់:**
   បើកកម្មវិធីរុករក (Browser) ទៅកាន់ `http://localhost:5173` ដើម្បីបញ្ចូលវីដេអូ និងចាប់ផ្ដើមបញ្ចូលសំឡេងខ្មែរ។

### សម្រាប់ Linux / macOS

1. **ដំឡើងបណ្ណាល័យ:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cd frontend && npm install && cd ..
   ```

2. **ដំណើរការកម្មវិធី:**
   ```bash
   chmod +x scripts/start.sh
   ./scripts/start.sh
   ```

3. **បើកប្រើប្រាស់:**
   ចូលទៅកាន់ `http://localhost:5173`។ ជ្រើសរើសសំឡេងខ្មែរ (ស្រីមុំ ឬ ពិសិដ្ឋ) ហើយចុច "ចាប់ផ្ដើមបញ្ចូលសំឡេង"។
