/**
 * Electron Main Process for Soundme AI
 * 
 * - Secure Desktop Window configuration (contextIsolation: true, nodeIntegration: false)
 * - Splash screen with live backend startup progress
 * - Seamless connection to FastAPI backend (serves production SPA + API)
 * - Safe external link delegation
 * - Guaranteed clean shutdown of Python processes
 */

const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const BackendManager = require('./backend-manager.cjs');

// Prevent multiple instances of the Electron app
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
}

process.on('uncaughtException', (err) => {
  console.error('[Main] Uncaught exception:', err);
});

process.on('unhandledRejection', (reason) => {
  console.error('[Main] Unhandled rejection:', reason);
});

let mainWindow = null;
let splashWindow = null;
let backendManager = null;
let currentBackendInfo = null;

const isDev = process.env.NODE_ENV === 'development';

function createSplashWindow() {
  splashWindow = new BrowserWindow({
    width: 500,
    height: 400,
    resizable: false,
    frame: false,
    center: true,
    show: false,
    backgroundColor: '#020617',
    icon: path.join(__dirname, 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  splashWindow.loadFile(path.join(__dirname, 'splash.html'));
  splashWindow.once('ready-to-show', () => {
    splashWindow.show();
  });

  splashWindow.on('closed', () => {
    splashWindow = null;
  });
}

function createMainWindow(backendPort) {
  mainWindow = new BrowserWindow({
    width: 1366,
    height: 850,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#020617',
    icon: path.join(__dirname, 'icon.png'),
    title: 'Soundme AI - Khmer Video Dubbing Studio',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // Intercept new window requests (e.g. target="_blank" or external links)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      shell.openExternal(url);
    }
    return { action: 'deny' };
  });

  const appUrl = `http://127.0.0.1:${backendPort}/`;
  mainWindow.loadURL(appUrl);

  mainWindow.webContents.once('did-finish-load', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    console.error(`[Main] Page load failed (${errorCode}): ${errorDescription}`);
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.send('backend:status-update', {
        error: 'Failed to load user interface',
        detail: `${errorDescription} (${errorCode})`
      });
    }
  });

  // Handle unexpected renderer process termination (e.g. OOM, GPU crash)
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error(`[Main] Renderer process gone (${details.reason}, exitCode: ${details.exitCode})`);
    dialog.showMessageBox(mainWindow, {
      type: 'error',
      title: 'Soundme AI - Unexpected Error',
      message: 'The user interface stopped unexpectedly.',
      detail: `Reason: ${details.reason} (exit code ${details.exitCode}).\nClick Reload to recover.`,
      buttons: ['Reload', 'Quit']
    }).then(({ response }) => {
      if (response === 0 && mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL(appUrl);
      } else if (response === 1) {
        app.quit();
      }
    });
  });

  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (level >= 2) {
      console.warn(`[Renderer ${level === 3 ? 'Error' : 'Warn'}] ${message} (${sourceId}:${line})`);
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// IPC Handlers
ipcMain.handle('app:version', () => app.getVersion());

ipcMain.handle('backend:status', async () => {
  if (backendManager) {
    const health = await backendManager.checkHealth(backendManager.port);
    return { port: backendManager.port, attached: backendManager.isAttached, health: health.data };
  }
  return { port: 8000, attached: false, health: null };
});

ipcMain.handle('shell:openExternal', async (_event, url) => {
  if (typeof url === 'string' && (url.startsWith('http://') || url.startsWith('https://'))) {
    await shell.openExternal(url);
    return true;
  }
  return false;
});

ipcMain.handle('shell:showItemInFolder', async (_event, fullPath) => {
  if (typeof fullPath === 'string') {
    shell.showItemInFolder(fullPath);
    return true;
  }
  return false;
});

// App Lifecycle
app.whenReady().then(async () => {
  createSplashWindow();

  backendManager = new BackendManager({
    projectRoot: path.resolve(__dirname, '..'),
    port: 8000
  });

  const sendStatus = (data) => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.webContents.send('backend:status-update', data);
    }
  };

  try {
    const result = await backendManager.start(sendStatus);
    currentBackendInfo = result;
    sendStatus({ status: 'Starting Studio...', detail: 'Ready' });

    // Short delay so the user sees readiness before window swaps
    setTimeout(() => {
      createMainWindow(result.port);
    }, 600);
  } catch (err) {
    console.error('[Main] Startup failure:', err);
    sendStatus({
      error: 'Backend initialization failed',
      detail: err.message
    });
  }
});

app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  }
});

const cleanup = () => {
  if (backendManager) {
    backendManager.stop();
    backendManager = null;
  }
};

app.on('before-quit', cleanup);
app.on('will-quit', cleanup);

process.on('SIGINT', () => {
  cleanup();
  app.exit(0);
});

process.on('SIGTERM', () => {
  cleanup();
  app.exit(0);
});

app.on('window-all-closed', () => {
  cleanup();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
