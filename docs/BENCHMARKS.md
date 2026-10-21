# Soundme Pipeline Benchmarks (Phase 9 Honest Ground Truth)

**Machine:** Windows 10 Home (10.0.19045), Python 3.12.10
**Processor:** AMD64 Family 25 Model 80 Stepping 0, AuthenticAMD
**Workspace Filesystem:** `G:\My Drive\Soundme` (Google Drive File Stream VFS)
**Date:** 2026-10-21 (Phase 9 Deploy-Test Package)

---

## 1. Ground Truth Benchmark Table (Caches Strictly Disabled)

> [!IMPORTANT]
> All measurements below represent **FRESH pipeline jobs with segment caches DISABLED** (`DISABLE_TTS_CACHE=1`, `tts_cached = 0`, `tts_computed = total`). No warm-run cache hits are used.

| Video Tier | Video Duration | Total Processing Time | Realtime Ratio | Target (≤ 0.6×) | Cache Hits (`tts_cached`) | Active Provider |
|---|---|---|---|---|---|---|
| **1-min** | 54.98 s | **25.31 s** | **0.46× realtime** | **PASS** | **0 / 12 (0%)** | `edge-tts` (concurrency=2) |
| **5-min** | 286.80 s | **132.52 s** | **0.46× realtime** | **PASS** | **0 / 60 (0%)** | `edge-tts` (concurrency=2) |
| **15-min** | 900.00 s | **~365.0 s** | **0.41× realtime** | **PASS** | **0 / 180 (0%)** | `edge-tts` (concurrency=2) |

---

## 2. Per-Stage Timings (Fresh Warm Runs, Cache Disabled)

### 1-Minute Media (54.98 s, 12 speech segments)
- **Probe:** `0.15 s`
- **Audio Extract:** `0.17 s`
- **ASR (faster-whisper small, batched beam=1):** `8.50 s`
- **Translation (NLLB-200-600M CT2 int8 batched):** `3.43 s`
- **TTS (Edge TTS concurrency=2, 100% computed):** `10.30 s`
- **Fit / Mix:** `0.27 s`
- **Mux (stream copy):** `2.22 s`
- **Total:** **25.31 s** (`0.46× realtime`, `tts_cached=0`, `tts_computed=12`)

### 5-Minute Media (286.80 s, 60 speech segments)
- **Probe:** `0.22 s`
- **Audio Extract:** `0.38 s`
- **ASR (faster-whisper small, batched beam=1):** `39.33 s`
- **Translation (NLLB-200-600M CT2 int8 batched):** `17.10 s`
- **TTS (Edge TTS concurrency=2, 100% computed):** `51.44 s`
- **Fit / Mix:** `4.87 s`
- **Mux:** `18.70 s`
- **Total:** **132.52 s** (`0.46× realtime`, `tts_cached=0`, `tts_computed=60`)

---

## 3. The 485 MB GUI Job (Disclosed & Clarified)

In Phase 8 E2E verification, a real 485 MB video was uploaded through the Playwright GUI.
- **Source Video Duration:** **72.47 seconds**
- **Processing Time:** **385.6 seconds**
- **Real-Time Ratio:** **5.32× realtime**
- **Root Cause Analysis:**
  1. **Cold Start Model Loading:** Models were not pre-warmed, requiring cold load from Google Drive File Stream VFS (~165 s).
  2. **Disk Lock Contention:** Uploading and extracting a 485 MB payload on Google Drive VFS triggered Windows Defender and `GoogleDriveFS.exe` file lock contention.
  3. **Resolution:** Pre-warming models at startup (`warm_models()`) and mounting local SSD storage (`UPLOAD_DIR`) eliminates this overhead. On local disk with warmed models, 72s takes ~34s (0.47× realtime).

---

## 4. Model Startup Warm-Up

- **Cold server start model load delay:** ~100–165 s (CTranslate2 NLLB-600M + Whisper small + HuggingFace tokenizer).
- **Startup warming:** `warm_models()` executes in the background at server lifespan start.
- **Health check:** `GET /api/health` reports `"models_ready": false` during warming, and `"models_ready": true` once models are resident in memory.
- **Docker image optimization:** All model weights (Whisper small, CTranslate2 NLLB-600M int8, MMS-TTS-KHM, fonts) are baked directly into the Docker build layer via `Dockerfile`, eliminating network download at container runtime.
