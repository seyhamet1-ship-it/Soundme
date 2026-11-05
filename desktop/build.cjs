/**
 * Cross-platform installer builder for Soundme AI
 * Generates: release/Soundme-AI-Setup.exe
 */
const { execSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const rootDir = path.resolve(__dirname, '..');

if (process.env.SKIP_VITE_BUILD !== '1') {
  console.log('[Builder] Step 1: Building production frontend with Vite...');
  try {
    execSync('npm --prefix frontend run build', {
      cwd: rootDir,
      stdio: 'inherit',
      shell: true
    });
  } catch (err) {
    if (fs.existsSync(path.join(rootDir, 'frontend', 'dist', 'index.html'))) {
      console.warn('[Builder] Frontend build warning; using existing production frontend/dist.');
    } else {
      console.error('[Builder] Frontend build failed:', err.message);
      process.exit(1);
    }
  }
} else {
  console.log('[Builder] Step 1: Skipping Vite build (SKIP_VITE_BUILD=1), using existing production frontend/dist.');
}

console.log('[Builder] Step 2: Locating electron-builder...');
const possibleBuilderPaths = [
  path.join(process.env.USERPROFILE || '', 'soundme-desktop-builder', 'node_modules', '.bin', 'electron-builder.cmd'),
  path.join(process.env.USERPROFILE || '', 'soundme-desktop-builder', 'node_modules', '.bin', 'electron-builder'),
  path.join(rootDir, 'node_modules', '.bin', 'electron-builder.cmd'),
  path.join(rootDir, 'node_modules', '.bin', 'electron-builder'),
  'electron-builder'
];

let builderBin = 'electron-builder';
for (const p of possibleBuilderPaths) {
  if (fs.existsSync(p)) {
    builderBin = p;
    break;
  }
}

console.log(`[Builder] Step 3: Packaging Windows installer using: ${builderBin}`);
const builderArgs = ['--win', 'nsis', 'dir', '--config', 'electron-builder.json'];

// On Windows with spaces in path, quote the executable
const quotedBin = `"${builderBin}"`;

const result = spawnSync(quotedBin, builderArgs, {
  cwd: rootDir,
  stdio: 'inherit',
  shell: true,
  env: {
    ...process.env,
    NODE_PATH: path.join(process.env.USERPROFILE || '', 'soundme-desktop-builder', 'node_modules')
  }
});

if (result.status !== 0) {
  console.error(`[Builder] Packaging failed with exit code ${result.status}`);
  process.exit(result.status || 1);
}

const installerPath = path.join(rootDir, 'release', 'Soundme-AI-Setup.exe');
if (fs.existsSync(installerPath)) {
  const stats = fs.statSync(installerPath);
  const sizeMb = (stats.size / (1024 * 1024)).toFixed(2);
  console.log('====================================================');
  console.log(`[Builder] SUCCESS! Installer generated at:`);
  console.log(`  ${installerPath} (${sizeMb} MB)`);
  console.log('====================================================');
} else {
  console.warn('[Builder] Build completed, but Soundme-AI-Setup.exe was not found at expected path.');
}
