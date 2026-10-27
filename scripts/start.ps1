# Soundme 1-command startup for Windows PowerShell
$ROOT = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
Set-Location $ROOT

$PYTHON = Join-Path $ROOT ".venv-recovery\Scripts\python.exe"

# Do not start a duplicate backend if a healthy one already owns port 8000.
$healthy = $false
try {
    $res = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    if ($res.status -eq "ok" -or $res.status -eq "degraded") { $healthy = $true }
} catch {}

if ($healthy) {
    Write-Host "Backend already running on :8000 - skipping backend startup." -ForegroundColor Green
} else {
    Write-Host "Starting Soundme Backend (Uvicorn on :8000)..." -ForegroundColor Cyan
    $Backend = Start-Process $PYTHON -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -PassThru -WindowStyle Minimized

    Write-Host "Waiting for backend health check..." -ForegroundColor Cyan
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 1
        try {
            $res = Invoke-RestMethod "http://127.0.0.1:8000/api/health" -TimeoutSec 2
            if ($res.status -eq "ok" -or $res.status -eq "degraded") {
                $healthy = $true
                break
            }
        } catch {}
    }

    if (-not $healthy) {
        Write-Error "Backend failed to start on port 8000!"
        exit 1
    }

    Write-Host "Backend is READY! (http://127.0.0.1:8000/api/health)" -ForegroundColor Green
}

Write-Host "Starting Soundme Frontend (Vite on :5173)..." -ForegroundColor Cyan
Set-Location "$ROOT\frontend"
npm run dev
