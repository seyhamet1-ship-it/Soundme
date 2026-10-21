# Soundme — Deployment Guide

> [!IMPORTANT]
> Soundme processes video locally on the server. No video is sent to external services (only TTS *text* goes to Azure Speech if configured). See [SECURITY.md](SECURITY.md) for the full privacy policy.

---

## Mode A — Windows PC + Cloudflare Quick Tunnel

### Requirements
- Windows 10/11 with PowerShell 5.1+
- Python 3.11+ with pip
- Node.js 18+ and npm
- FFmpeg on PATH (`winget install ffmpeg` or from https://ffmpeg.org)
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/) on PATH

### One-time setup
```powershell
cd "C:\path\to\Soundme"
pip install -r requirements.txt
cd frontend; npm install; cd ..
```

### Configure tokens
```powershell
Copy-Item .env.example .env
notepad .env
# Set: ACCESS_TOKENS=your-secret-token-1,tester-token-2
# Set: TTS_PROVIDER_ORDER=edge,mms
```

> [!WARNING]
> **Google Drive / OneDrive path warning**: If the project is in a cloud-synced folder, the backend prints a startup warning and `ffprobe` takes ~13 s per call. Move `uploads/` to a local SSD by setting `UPLOAD_DIR=C:\soundme-data\uploads` in `.env`.

### Start the tunnel
```powershell
.\scripts\run_tunnel.ps1
```

The script:
1. Builds the frontend (`npm run build`)
2. Starts the backend on port 8000
3. Waits for `/api/health` (model load: **~60–165 s cold start**)
4. Launches a Cloudflare Quick Tunnel
5. On Ctrl+C, cleanly shuts down everything

Share the `https://*.trycloudflare.com` URL and token(s) with testers.

> [!WARNING]
> **Cloudflare Quick Tunnels do NOT support Server-Sent Events (SSE)**. The frontend automatically falls back to 1.3 s polling within 5 s if SSE yields nothing. No action needed — this is transparent to users.

---

## Mode B — Linux VPS with Docker Compose

### Requirements
- Linux VPS with ≥4 GB RAM, ≥2 vCPU, ≥15 GB disk
- Docker Engine 24+ and Docker Compose v2 (`docker compose version`)
- Domain name with DNS A record pointing to VPS IP

> [!NOTE]
> Edge TTS is typically blocked on datacenter IPs. Configure Azure Speech as the primary TTS provider.

### One-time setup
```bash
git clone <repo> soundme
cd soundme
cp .env.example .env
nano .env
# Required:
#   ACCESS_TOKENS=token-1,token-2
#   DOMAIN=soundme.example.com
#   AZURE_SPEECH_KEY=<key>
#   AZURE_SPEECH_REGION=eastus
#   TTS_PROVIDER_ORDER=azure,mms
```

### Start
```bash
docker compose up --build -d
docker compose logs -f soundme   # watch startup
```

First build: downloads ~1.6 GB of AI models (Whisper-small, NLLB-600M, MMS-TTS-KHM). Subsequent starts are instant because weights are cached in the `soundme_models` volume.

### Verify healthy
```bash
curl https://soundme.example.com/api/health
# Expected: {"status": "ok", "models_ready": true, "tts": {"primary": "azure", ...}}
```

### Resource sizing (measured)

| Workload | Peak RSS | CPU |
|---|---|---|
| Idle (models loaded) | ~1.5 GB | <5% |
| 1-min clip processing | ~1.8 GB | 100–400% |
| 15-min clip processing | ~2.0 GB | 100–400% |

Docker Compose sets `mem_limit: 3g`, `cpus: 2.0`. Adjust in `docker-compose.yml` for your VPS tier.

---

## Rotating tokens
```bash
# Edit .env, change ACCESS_TOKENS
docker compose restart soundme
```
Old tokens are immediately invalidated. In-flight jobs may fail — re-submit with the new token.

---

## Troubleshooting

### 403 / NoAudioReceived (Azure TTS)
1. Verify `AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` in `.env`
2. Check `/api/health` → `tts.azure_chars_used_month` is below `azure_monthly_cap`
3. If cap exceeded: raise `AZURE_MONTHLY_CHAR_CAP` or wait for month reset

### Edge TTS fails on VPS
Expected. Datacenter IPs are blocked by Microsoft's Edge TTS endpoint.  
Solution: configure `AZURE_SPEECH_KEY` or set `TTS_PROVIDER_ORDER=mms`.

### Container OOM killed
Increase `mem_limit` in `docker-compose.yml`. Minimum 2.5 GB recommended.

### Slow first job (model load)
If models are not baked into the image, the first job waits ~165 s for model download.  
The image `FROM Dockerfile` pre-downloads models. If you skip `--build`, models are loaded from the `soundme_models` volume on first run.

### Logs
```bash
docker compose logs soundme --tail=200 -f
```

### Stopping
```bash
docker compose down          # stop containers, keep volumes (safe)
docker compose down -v       # also delete volumes ⚠ deletes ALL uploads and outputs
```

---

## Data directory warning
If `DATA_DIR` / `UPLOAD_DIR` is a network or virtual filesystem (Google Drive, OneDrive, Dropbox, NFS, SMB), the backend prints a `[STORAGE_CONTENTION]` warning at startup.  
**Always use a local SSD path for production.**
```bash
# .env
UPLOAD_DIR=/local/ssd/soundme-data/uploads
JOB_DB=/local/ssd/soundme-data/jobs.sqlite3
```
