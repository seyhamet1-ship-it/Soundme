/**
 * Cross-platform runner for Soundme AI Desktop
 */
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const rootDir = path.resolve(__dirname, '..');

// Find electron executable
const possibleElectronPaths = [
  path.join(process.env.USERPROFILE || '', 'soundme-desktop-builder', 'node_modules', '.bin', 'electron.cmd'),
  path.join(process.env.USERPROFILE || '', 'soundme-desktop-builder', 'node_modules', '.bin', 'electron'),
  path.join(rootDir, 'node_modules', '.bin', 'electron.cmd'),
  path.join(rootDir, 'node_modules', '.bin', 'electron'),
  'electron'
];

let electronBin = 'electron';
for (const p of possibleElectronPaths) {
  if (fs.existsSync(p)) {
    electronBin = p;
    break;
  }
}

console.log(`[Runner] Launching Electron using: ${electronBin}`);
const quotedBin = `"${electronBin}"`;

const child = spawn(quotedBin, [rootDir], {
  cwd: rootDir,
  stdio: 'inherit',
  shell: true,
  env: process.env
});

child.on('exit', (code) => {
  process.exit(code || 0);
});
