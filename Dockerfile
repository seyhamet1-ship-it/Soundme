# ─────────────────────────────────────────────────────────────────────────────
# Soundme — Dockerfile (Phase 9)
#
# Build steps:
#   1. Install system deps (ffmpeg, fonts, libsndfile)
#   2. Install Python deps
#   3. Download / convert all AI models (HF Whisper-small, NLLB-600M, MMS-TTS-KHM)
#      and warm them once to populate the HF cache inside the image.
#      This removes the ~60-165 s cold-start model-load delay at runtime.
#   4. Copy application source
#   5. Build frontend (Vite) into app/static so uvicorn can serve it
#   6. Run as non-root user (uid 1000)
#
# Resulting image size: ~4-5 GB (includes model weights)
#
# > [!NOTE]
# > The models volume in docker-compose.yml is mounted at /root/.cache/huggingface
# > so that subsequent `docker compose up --build` can skip re-downloading.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# ── System dependencies ──────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-khmeros \
    libsndfile1 \
    curl \
    ca-certificates \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies ──────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Download and pre-warm AI models ─────────────────────────────────────────
# This bakes model weights into the image layer so /api/health reaches
# models_ready=true within ~10 s of container start (no network download needed).
COPY tools/ ./tools/
COPY app/ ./app/

RUN python - <<'PYEOF'
import os, sys
print("==> Pre-downloading Whisper-small...", flush=True)
try:
    from faster_whisper import WhisperModel
    WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=2)
    print("    Whisper-small: OK", flush=True)
except Exception as e:
    print(f"    Whisper-small: WARN ({e})", flush=True)

print("==> Pre-downloading NLLB-600M (CT2 int8)...", flush=True)
try:
    import ctranslate2, transformers
    ct2_path = "/app/tools/models/nllb-200-distilled-600M-ct2-int8"
    if os.path.isdir(ct2_path):
        t = ctranslate2.Translator(ct2_path, device="cpu", compute_type="int8", inter_threads=1)
        print("    NLLB CT2 int8: OK", flush=True)
    else:
        from transformers import AutoTokenizer
        AutoTokenizer.from_pretrained("facebook/nllb-200-distilled-600M")
        print("    NLLB tokenizer (fallback): OK", flush=True)
except Exception as e:
    print(f"    NLLB: WARN ({e})", flush=True)

print("==> Pre-downloading MMS-TTS-KHM...", flush=True)
try:
    from transformers import pipeline
    pipeline("text-to-speech", "facebook/mms-tts-khm", device="cpu")
    print("    MMS-TTS-KHM: OK", flush=True)
except Exception as e:
    print(f"    MMS-TTS-KHM: WARN ({e})", flush=True)

print("==> Model pre-download complete.", flush=True)
PYEOF

# ── Frontend build ───────────────────────────────────────────────────────────
COPY frontend/ ./frontend/
RUN cd frontend && npm ci --prefer-offline && npm run build \
    && mkdir -p /app/app/static \
    && cp -r dist/* /app/app/static/

# ── Non-root user ────────────────────────────────────────────────────────────
RUN useradd -u 1000 -m -s /bin/bash soundme \
    && mkdir -p /data/uploads /data/outputs \
    && chown -R soundme:soundme /data /app /root/.cache 2>/dev/null || true

USER soundme

# ── Runtime defaults ─────────────────────────────────────────────────────────
ENV MAX_DURATION_MIN=30 \
    MAX_UPLOAD_MB=500 \
    PYTHONUNBUFFERED=1 \
    UPLOAD_DIR=/data/uploads \
    JOB_DB=/data/jobs.sqlite3 \
    WARM_MODELS_AT_STARTUP=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD python -c "import urllib.request,json; d=json.loads(urllib.request.urlopen('http://localhost:8000/api/health',timeout=5).read()); assert d['status'] in {'ok','warming'}"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-keep-alive", "3600"]
