# Soundme – Cloudflare Quick Tunnel launcher (Windows PowerShell)
# Usage: .\scripts\run_tunnel.ps1
# Requires: cloudflared on PATH; .env file or env vars set
param(
    [int]$Port = 8000
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir   = Split-Path -Parent $ScriptDir
$EnvFile   = Join-Path $RootDir ".env"

# Load .env if present
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^\s*([^#=\s][^=]*)=(.*)$") {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            if (-not [Environment]::GetEnvironmentVariable($key, "Process")) {
                [Environment]::SetEnvironmentVariable($key, $val, "Process")
            }
        }
    }
}

# Warn about cloud/virtual drives
$DataDir = if ($env:UPLOAD_DIR) { $env:UPLOAD_DIR } else { Join-Path $RootDir "uploads" }
$cloudKeywords = @("google drive", "my drive", "onedrive", "dropbox")
foreach ($kw in $cloudKeywords) {
    if ($DataDir.ToLower().Contains($kw)) {
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
        Write-Host "WARNING: DATA_DIR is on a cloud/network drive: $DataDir"            -ForegroundColor Yellow
        Write-Host "This causes severe I/O latency. Use a local SSD path."              -ForegroundColor Yellow
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
        break
    }
}

# Check cloudflared
if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: cloudflared not found on PATH." -ForegroundColor Red
    Write-Host "Download: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
    exit 1
}

# Build frontend
Write-Host "==> Building frontend..." -ForegroundColor Cyan
Push-Location (Join-Path $RootDir "frontend")
npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "Frontend build failed." -ForegroundColor Red; exit 1 }
Pop-Location

# Start backend
Write-Host "==> Starting Soundme backend on port $Port..." -ForegroundColor Cyan
$backend = Start-Process -FilePath "uvicorn" `
    -ArgumentList "app.main:app","--host","0.0.0.0","--port","$Port","--timeout-keep-alive","3600" `
    -WorkingDirectory $RootDir -PassThru -NoNewWindow

# Wait for healthy (models load: ~60-165 s cold start)
Write-Host "==> Waiting for backend (model load may take 60-165 s)..." -ForegroundColor Cyan
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $res = Invoke-WebRequest -Uri "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($res.StatusCode -eq 200) { $healthy = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Host "ERROR: Backend did not become healthy within 180 s." -ForegroundColor Red
    $backend.Kill()
    exit 1
}
Write-Host "==> Backend healthy." -ForegroundColor Green

# Start tunnel
Write-Host "==> Starting Cloudflare Quick Tunnel..." -ForegroundColor Cyan
$tunnel = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel","--url","http://localhost:$Port" `
    -PassThru -NoNewWindow

Write-Host ""
Write-Host "==> Soundme is running!" -ForegroundColor Green
Write-Host "    Local:  http://localhost:$Port" -ForegroundColor Cyan
Write-Host "    Tunnel: see cloudflared output above for the https://*.trycloudflare.com URL" -ForegroundColor Cyan
Write-Host ""
Write-Host "NOTE: Cloudflare Quick Tunnels do NOT support SSE." -ForegroundColor Yellow
Write-Host "      The frontend automatically falls back to polling. No action needed." -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop." -ForegroundColor Cyan

try {
    $backend.WaitForExit()
} finally {
    Write-Host "==> Shutting down..." -ForegroundColor Cyan
    try { $tunnel.Kill() } catch {}
    try { $backend.Kill() } catch {}
    Write-Host "==> Done." -ForegroundColor Green
}
