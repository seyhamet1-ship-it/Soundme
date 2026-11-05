/**
 * Backend Manager for Soundme AI Desktop
 * 
 * Manages FastAPI/Uvicorn lifecycle:
 * - Detects active Soundme backend on port 8000 before spawning to prevent duplicates
 * - Locates verified Python 3.12 interpreter (.venv-recovery, SOUNDME_PYTHON, or system)
 * - Configures fast, persistent local SSD storage in %LOCALAPPDATA%\Soundme AI
 * - Ensures FFmpeg/FFprobe and offline AI models are discovered
 * - Monitors /api/health until ready
 * - Gracefully and completely terminates child process trees on app exit
 */

const { spawn, execSync } = require('child_process');
const http = require('http');
const net = require('net');
const path = require('path');
const fs = require('fs');

class BackendManager {
  constructor(options = {}) {
    this.projectRoot = options.projectRoot || path.resolve(__dirname, '..');
    this.port = options.port || 8000;
    this.childProcess = null;
    this.isAttached = false;
    this.lastHealth = null;

    // Local SSD storage path
    const localAppData = process.env.LOCALAPPDATA || (process.env.USERPROFILE ? path.join(process.env.USERPROFILE, 'AppData', 'Local') : '');
    this.dataDir = localAppData ? path.join(localAppData, 'Soundme AI') : path.join(this.projectRoot, 'desktop_data');
    this.logDir = path.join(this.dataDir, 'logs');
    this.uploadsDir = path.join(this.dataDir, 'uploads');
    this.outputsDir = path.join(this.dataDir, 'outputs');
    this.jobDbPath = path.join(this.dataDir, 'jobs.sqlite3');

    this._ensureDirectories();
  }

  _ensureDirectories() {
    [this.dataDir, this.logDir, this.uploadsDir, this.outputsDir].forEach(dir => {
      try {
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      } catch (err) {
        console.error(`[BackendManager] Failed to create dir ${dir}:`, err.message);
      }
    });
  }

  async checkHealth(port = this.port) {
    return new Promise((resolve) => {
      const req = http.get(`http://127.0.0.1:${port}/api/health`, { timeout: 2500 }, (res) => {
        let raw = '';
        res.on('data', chunk => { raw += chunk; });
        res.on('end', () => {
          try {
            const data = JSON.parse(raw);
            if (data && (data.status === 'ok' || data.status === 'warming' || data.status === 'degraded')) {
              resolve({ ok: true, data });
              return;
            }
          } catch (_) {}
          resolve({ ok: false, data: null });
        });
      });
      req.on('error', () => resolve({ ok: false, data: null }));
      req.on('timeout', () => { req.destroy(); resolve({ ok: false, data: null }); });
    });
  }

  async checkDesktopAuth(port = this.port) {
    return new Promise((resolve) => {
      const req = http.get(`http://127.0.0.1:${port}/auth/me`, { timeout: 2500 }, (res) => {
        let raw = '';
        res.on('data', chunk => { raw += chunk; });
        res.on('end', () => {
          try {
            const data = JSON.parse(raw);
            if (data && data.authenticated && data.auth_mode === 'desktop_local') {
              resolve(true);
              return;
            }
          } catch (_) {}
          resolve(false);
        });
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => { req.destroy(); resolve(false); });
    });
  }

  async isPortAvailable(port) {
    return new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => resolve(false));
      server.once('listening', () => {
        server.close();
        resolve(true);
      });
      server.listen(port, '127.0.0.1');
    });
  }

  findPythonExecutable() {
    const candidates = [
      process.env.SOUNDME_PYTHON,
      // .venv-recovery in project root
      path.join(this.projectRoot, '.venv-recovery', 'Scripts', 'python.exe'),
      // .venv in project root
      path.join(this.projectRoot, '.venv', 'Scripts', 'python.exe'),
      // Resources folder if running from packaged build
      process.resourcesPath ? path.join(process.resourcesPath, '.venv-recovery', 'Scripts', 'python.exe') : null,
      process.resourcesPath ? path.join(process.resourcesPath, 'python', 'python.exe') : null,
      // Standard Windows Python 3.12 location
      process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python312', 'python.exe') : null,
      // In PATH
      'python.exe',
      'python'
    ].filter(Boolean);

    for (const p of candidates) {
      if (p === 'python.exe' || p === 'python') return p;
      if (fs.existsSync(p)) return p;
    }
    return 'python';
  }

  findBackendDirectory() {
    // 1. If running in packaged app, check unpacked asar first (where Python can read files)
    if (process.resourcesPath) {
      const unpackedAsar = path.join(process.resourcesPath, 'app.asar.unpacked');
      if (fs.existsSync(path.join(unpackedAsar, 'app', 'main.py'))) {
        return unpackedAsar;
      }
      const packagedApp = path.join(process.resourcesPath, 'app');
      if (fs.existsSync(path.join(packagedApp, 'app', 'main.py'))) return packagedApp;
      const packagedBackend = path.join(process.resourcesPath, 'backend');
      if (fs.existsSync(path.join(packagedBackend, 'app', 'main.py'))) return packagedBackend;
    }
    // 2. Explicit environment variable override
    if (process.env.SOUNDME_BACKEND_DIR && fs.existsSync(path.join(process.env.SOUNDME_BACKEND_DIR, 'app', 'main.py'))) {
      return process.env.SOUNDME_BACKEND_DIR;
    }
    // 3. Development or source mode
    if (fs.existsSync(path.join(this.projectRoot, 'app', 'main.py'))) {
      return this.projectRoot;
    }
    // 4. In case projectRoot is a subfolder or asar, look upwards
    let cur = this.projectRoot;
    for (let i = 0; i < 4; i++) {
      if (fs.existsSync(path.join(cur, 'app', 'main.py'))) return cur;
      const parent = path.dirname(cur);
      if (parent === cur) break;
      cur = parent;
    }
    return this.projectRoot;
  }

  async start(statusCallback = () => {}) {
    statusCallback({ status: 'Checking backend...', detail: 'Looking for existing Soundme service on port 8000' });

    // Step 1: Detect if an existing Soundme backend is already running on port 8000
    // CRITICAL: We only attach if it also has DESKTOP_LOCAL_MODE active (verified via /auth/me).
    // An external development backend running without DESKTOP_LOCAL_MODE will reject desktop requests with 401 UNAUTHORIZED!
    const healthCheck = await this.checkHealth(8000);
    if (healthCheck.ok) {
      const isDesktopMode = await this.checkDesktopAuth(8000);
      const backendJobDb = healthCheck.data && healthCheck.data.job_db;
      // Verify the running backend is using the same database file.
      // If a development backend is running on a different DB (e.g. project workspace vs LocalAppData),
      // we must NOT attach to it because user history and uploads will be partitioned.
      const dbMatches = !backendJobDb || (path.resolve(backendJobDb).toLowerCase() === path.resolve(this.jobDbPath).toLowerCase());
      if (isDesktopMode && dbMatches) {
        this.isAttached = true;
        this.port = 8000;
        this.lastHealth = healthCheck.data;
        console.log('[BackendManager] Attached to existing healthy Soundme backend (desktop-local mode and matching DB confirmed) on port 8000');
        statusCallback({ status: 'Connected to existing backend', detail: `API status: ${healthCheck.data.status}` });
        return { port: 8000, attached: true, health: healthCheck.data };
      } else {
        console.log(`[BackendManager] Port 8000 backend is active but not suitable (desktopMode=${isDesktopMode}, dbMatches=${dbMatches}). Spawning dedicated desktop backend on alternative port.`);
      }
    }

    // Step 2: Determine port to use
    let targetPort = 8000;
    const is8000Free = await this.isPortAvailable(8000);
    if (!is8000Free) {
      // Find next free port
      for (let p = 8001; p < 8050; p++) {
        if (await this.isPortAvailable(p)) {
          targetPort = p;
          break;
        }
      }
    }
    this.port = targetPort;

    // Step 3: Find Python interpreter
    const pythonExe = this.findPythonExecutable();
    const backendDir = this.findBackendDirectory();

    statusCallback({ status: 'Starting Soundme backend...', detail: `Using Python: ${path.basename(pythonExe)} on port ${targetPort}` });
    console.log(`[BackendManager] Launching backend from ${backendDir} with ${pythonExe} on port ${targetPort}`);

    // Build environment variables — start from parent env but sanitise Python
    // environment that could contaminate the child process (e.g. a stale
    // .venv-1 VIRTUAL_ENV or PYTHONPATH left over from a dev shell).
    const env = { ...process.env };
    env.PYTHONUNBUFFERED = '1';
    // Always override PYTHONPATH with only the backend dir — never inherit
    // stale entries from a development venv that may have incompatible packages.
    env.PYTHONPATH = backendDir;
    // Clear venv activation artifacts so the explicit pythonExe path controls
    // which site-packages are loaded, not a lingering VIRTUAL_ENV variable.
    delete env.VIRTUAL_ENV;
    delete env.CONDA_DEFAULT_ENV;
    delete env.CONDA_PREFIX;
    env.UPLOAD_DIR = this.uploadsDir;
    env.OUTPUT_DIR = this.outputsDir;
    env.JOB_DB = this.jobDbPath;
    env.TIMING_MAX_TEMPO = env.TIMING_MAX_TEMPO || '2.0';
    env.MAX_UPLOAD_SIZE_MB = env.MAX_UPLOAD_SIZE_MB || '500';
    env.MAX_DURATION_MIN = env.MAX_DURATION_MIN || '125';
    env.DAILY_QUOTA_MINUTES = env.DAILY_QUOTA_MINUTES || '180';
    env.WARM_MODELS_AT_STARTUP = '1';
    env.HF_HUB_LOCAL_FILES_ONLY = '1';
    env.HF_HUB_OFFLINE = '1';
    env.TRANSFORMERS_OFFLINE = '1';
    // Desktop-local mode: allows loopback requests without Bearer token or Google OAuth.
    // Only loopback connections (127.0.0.1) benefit from this — remote access is unaffected.
    env.DESKTOP_LOCAL_MODE = '1';

    // Add tools and ffmpeg directories to PATH
    const toolsBin = path.join(backendDir, 'tools', 'ffmpeg-master-latest-win64-gpl', 'bin');
    const toolsDir = path.join(backendDir, 'tools');
    const wingetFfmpeg = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'Microsoft', 'WinGet', 'Packages') : '';
    
    let pathPrefixes = [toolsDir, toolsBin];
    if (wingetFfmpeg && fs.existsSync(wingetFfmpeg)) {
      try {
        const dirs = fs.readdirSync(wingetFfmpeg).filter(d => d.toLowerCase().includes('ffmpeg'));
        dirs.forEach(d => {
          const binPath = path.join(wingetFfmpeg, d, 'ffmpeg-9.0.2-full_build', 'bin');
          if (fs.existsSync(binPath)) pathPrefixes.push(binPath);
        });
      } catch (_) {}
    }
    env.PATH = pathPrefixes.join(path.delimiter) + path.delimiter + (env.PATH || '');

    // Log file
    const logFile = path.join(this.logDir, 'backend.log');
    const logStream = fs.createWriteStream(logFile, { flags: 'a' });

    // Spawn Uvicorn child process
    const args = ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(targetPort)];
    
    const child = spawn(pythonExe, args, {
      cwd: backendDir,
      env,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe']
    });

    this.childProcess = child;
    this.isAttached = false;

    child.stdout.on('data', chunk => {
      const msg = chunk.toString();
      logStream.write(msg);
      if (msg.includes('Application startup complete')) {
        statusCallback({ status: 'AI Server started', detail: 'Warming speech models...' });
      }
    });

    let lastStderr = '';
    child.stderr.on('data', chunk => {
      const msg = chunk.toString();
      lastStderr += msg;
      if (lastStderr.length > 2000) lastStderr = lastStderr.slice(-2000);
      logStream.write(msg);
    });

    child.on('error', err => {
      console.error('[BackendManager] Child process error:', err);
      statusCallback({ error: 'Failed to start Python backend', detail: err.message });
    });

    child.on('exit', (code, signal) => {
      console.log(`[BackendManager] Child process exited with code ${code}, signal ${signal}`);
      this.childProcess = null;
    });

    // Step 4: Poll /api/health until ready or timeout (60 seconds)
    const startTime = Date.now();
    const timeoutMs = 60000;

    while (Date.now() - startTime < timeoutMs) {
      if (!this.childProcess && !this.isAttached) {
        const detail = lastStderr.trim() ? ` Error: ${lastStderr.trim().split('\n').pop()}` : '';
        throw new Error(`Backend process terminated unexpectedly.${detail} Check logs at: ${logFile}`);
      }

      const res = await this.checkHealth(targetPort);
      if (res.ok) {
        this.lastHealth = res.data;
        statusCallback({ status: 'Backend Ready', detail: `Models status: ${res.data.status}` });
        return { port: targetPort, attached: false, health: res.data };
      }

      const elapsed = Math.round((Date.now() - startTime) / 1000);
      statusCallback({
        status: 'Starting AI engine...',
        detail: `Initializing models and API (${elapsed}s)...`
      });

      await new Promise(r => setTimeout(r, 1000));
    }

    throw new Error(`Backend startup timed out after 60s. Check logs at: ${logFile}`);
  }

  stop() {
    if (this.isAttached) {
      console.log('[BackendManager] Attached backend left running (not spawned by this session)');
      return;
    }

    if (this.childProcess && this.childProcess.pid) {
      const pid = this.childProcess.pid;
      console.log(`[BackendManager] Terminating backend process tree (PID: ${pid})...`);
      try {
        // Kill entire process tree on Windows
        execSync(`taskkill /pid ${pid} /T /F`, { stdio: 'ignore' });
        console.log(`[BackendManager] Successfully terminated PID ${pid}`);
      } catch (err) {
        console.warn(`[BackendManager] taskkill warning: ${err.message}`);
      }
      this.childProcess = null;
    }
  }
}

module.exports = BackendManager;
