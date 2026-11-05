# Soundme AI Windows Desktop App & Setup.exe Build Report

## 1. Executive Summary

Soundme AI has been successfully adapted from a web-only service into a standalone Windows desktop application powered by Electron, with a complete Windows installer generated via Electron Builder (NSIS).

- **Installer Produced**: `release/Soundme-AI-Setup.exe` (115.94 MB)
- **Packaged Executable**: `release/win-unpacked/Soundme AI.exe`
- **Application Identification**: `com.soundme.ai` / Soundme AI (v1.0.0)
- **Visual Branding**: Native dark navy / teal aesthetic (`#020617` / `#0b132b`) matching the Khmer dubbing interface, including custom 256x256 icons (`desktop/icon.ico` and `desktop/icon.png`) with microphone glyph.
- **Dubbing Pipeline**: Preserved 100% untouched. Tested with live audio/video dubbing: Speech Recognition (Faster-Whisper small), Translation (NLLB-200 distilled 600M CT2 int8), and Khmer Neural Speech Synthesis (Edge TTS / MMS) all operating locally.

---

## 2. Desktop Architecture

```
+-------------------------------------------------------------------------+
|                       Soundme AI Desktop Application                    |
|                                                                         |
|  +-------------------------------------------------------------------+  |
|  | Electron Main Process (desktop/main.cjs)                          |  |
|  | - Security: contextIsolation: true, nodeIntegration: false,       |  |
|  |   sandbox: true, restricted preload bridge (desktop/preload.cjs)  |  |
|  | - Splash/Loading Window (desktop/splash.html): Navy/teal status   |  |
|  | - Native Desktop Window: 1366x850 (min 1024x640)                  |  |
|  +-------------------------------------------------------------------+  |
|                                     |                                   |
|             Spawns or Attaches to   | Serves Production SPA + API       |
|                                     v                                   |
|  +-------------------------------------------------------------------+  |
|  | Backend Manager (desktop/backend-manager.cjs)                     |  |
|  | - Probes http://127.0.0.1:8000/api/health                         |  |
|  |   * Reuses existing healthy Soundme backend without duplicate PID |  |
|  |   * Or spawns child Uvicorn using verified Python (Python 3.12)   |  |
|  | - Clean Shutdown: executes taskkill /pid <PID> /T /F on app quit  |  |
|  |   guaranteeing zero orphaned processes                            |  |
|  +-------------------------------------------------------------------+  |
|                                     |                                   |
|  +-------------------------------------------------------------------+  |
|  | Python Backend (Uvicorn / FastAPI)                                |  |
|  | - Serves static frontend from frontend/dist                       |  |
|  | - Serves REST API (/api, /auth, /uploads)                         |  |
|  | - Models: HuggingFace (~/.cache/huggingface/hub) + CT2 NLLB       |  |
|  | - Storage: Fast local SSD in %LOCALAPPDATA%\Soundme AI            |  |
|  | - Media: WinGet / project FFmpeg & FFprobe on PATH                |  |
|  +-------------------------------------------------------------------+  |
+-------------------------------------------------------------------------+
```

---

## 3. Storage & AI Model Resolution

To avoid Google Drive virtual filesystem latency, opportunistic locks, and synchronization contention (which introduces ~13s probe and ~160s cold load times):

1. **Persistent SSD Application Data**:
   - `UPLOAD_DIR`: `%LOCALAPPDATA%\Soundme AI\uploads`
   - `OUTPUT_DIR`: `%LOCALAPPDATA%\Soundme AI\outputs`
   - `JOB_DB`: `%LOCALAPPDATA%\Soundme AI\jobs.sqlite3`
   - `LOGS`: `%LOCALAPPDATA%\Soundme AI\logs\backend.log`
2. **AI Models (External & Reused)**:
   - **Faster-Whisper (`small`)**: Reuses cached weights in `%USERPROFILE%\.cache\huggingface\hub\models--Systran--faster-whisper-small`.
   - **NLLB-200 (`distilled-600M`)**: CTranslate2 int8 model in `tools/models/nllb-200-distilled-600M-ct2-int8` cached to `%LOCALAPPDATA%\Soundme\models`.
   - **Offline Flag**: Backend runtime sets `HF_HUB_OFFLINE=1`, `HF_HUB_LOCAL_FILES_ONLY=1`, and `TRANSFORMERS_OFFLINE=1`.
   - **Installer Exclusion**: Models and virtual environments are explicitly excluded from `Soundme-AI-Setup.exe` to keep the installer lightweight (~115 MB) and avoid 2.5GB+ packaging bloat.
3. **FFmpeg / FFprobe**:
   - Discovered automatically via system PATH (e.g. WinGet `Gyan.FFmpeg` build 9.0.2) or `tools/ffmpeg-master-latest-win64-gpl/bin`.

---

## 4. Google OAuth & Authentication Behavior

- **Session Handling**: Session cookies are signed with `SECRET_KEY`, flagged `HttpOnly`, `SameSite=Lax`.
- **Desktop Compatibility**:
  - When the app is accessed on `http://127.0.0.1:<PORT>`, local auth and token sessions (`soundme-test-token`, `soundme-dev`) function natively.
  - If Google OAuth is enabled in `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`), `desktop/main.cjs` intercepts external authentication URLs via `setWindowOpenHandler` and directs them securely through the user's default web browser or OAuth flow, preventing script injection or insecure inline navigation.
  - No secret tokens or credentials are leaked into URLs.

---

## 5. Security & Isolation Compliance

- `contextIsolation: true` is strictly enforced.
- `nodeIntegration: false` is strictly enforced.
- `sandbox: true` is enabled on all browser windows.
- Preload script (`desktop/preload.cjs`) exposes a minimal, safe API surface (`window.soundmeDesktop`):
  - `getVersion()`
  - `getBackendStatus()`
  - `openExternal(url)` (validates `http:`/`https:`)
  - `showItemInFolder(path)`
  - `onStatusUpdate(callback)`
- No raw `child_process`, `fs`, or internal Node paths are exposed to the renderer.
- Never bundles `.env`, secrets, uploaded files, or private SQLite databases into the installer.

---

## 6. Commands Reference

### Building the Windows Installer
```powershell
npm run desktop:dist
```
- Compiles the Vite React frontend into `frontend/dist`.
- Runs `electron-builder --win nsis`.
- Generates `release/Soundme-AI-Setup.exe` with desktop and start menu shortcuts.

### Running Desktop in Development Mode
```powershell
npm run desktop:start
```
or
```powershell
npm run desktop:dev
```
- Verifies frontend build.
- Launches Electron desktop shell and connects to the backend.

### Existing Web Development (Preserved)
```powershell
# Frontend
cd frontend
npm run dev

# Backend
.\start_soundme.bat
```

---

## 7. Testing & Verification Summary

| Test Area | Target / Verification | Result |
|---|---|---|
| **Frontend Production Build** | `npm run build` in `frontend/` (Vite 5.4.21) | **PASS** (12.1s, `frontend/dist` created) |
| **Frontend Unit Tests** | `vitest run` in `frontend/` (`App.test.jsx`) | **PASS** (10/10 passed) |
| **Desktop Integration Tests** | `pytest tests/test_desktop_integration.py` | **PASS** (7/7 passed) |
| **Backend Health** | `/api/health` reports status `ok`, `models_ready=true`, tools `ffmpeg/ffprobe=true` | **PASS** |
| **Process Management** | Detects existing backend on :8000 without starting duplicate process | **PASS** |
| **Clean Shutdown** | App exit terminates child process tree with `taskkill /pid /T /F`; does not kill external backend | **PASS** |
| **Job Submission & Progress** | Upload `sample_input.mp4`, transcribe, translate, synthesize, render | **PASS** (completed in 6s) |
| **Output Playback & Streams** | `ffprobe` verification of output: Video (H.264), Audio (AAC 48kHz), 6.0s duration | **PASS** |
| **Job Database Persistence** | `/api/jobs` persists completed jobs across server restarts in SQLite | **PASS** (19 jobs intact) |
| **Installer Generation** | `release/Soundme-AI-Setup.exe` generated with NSIS target and Soundme AI branding | **PASS** (115.94 MB) |
| **Desktop Shell Launch** | Compiled `Soundme AI.exe` window initializes and opens cleanly | **PASS** |

---

## 8. Known Limitations & Notes

1. **Unsigned Application Notice**:
   Because no paid code-signing certificate was used (per Safety Rule 12), Windows SmartScreen will display an unsigned application warning upon first launch of `Soundme-AI-Setup.exe`. Users can click "More info" -> "Run anyway".
2. **Model Warming on First Launch**:
   On a completely cold boot, loading Whisper ASR and NLLB CTranslate2 into RAM may take 5–15 seconds depending on CPU and disk speed. The desktop loading splash screen displays status text informing the user that models are warming up before transitioning to the studio view.
