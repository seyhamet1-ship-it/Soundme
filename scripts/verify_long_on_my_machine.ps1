param(
    [int]$DurationMin = 30
)
$DurationSec = $DurationMin * 60

# Soundme 30/60-Minute Verification Script (Windows PowerShell)
# Run: .\scripts\verify_long_on_my_machine.ps1 [-DurationMin 30|60]

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Soundme: $DurationMin-Minute Real-Mode Long-Video Verification" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

# 1. Health check
Write-Host "`n[1/4] Checking backend server..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    Write-Host "  Server is healthy: status=$($health.status)" -ForegroundColor Green
} catch {
    Write-Host "  Starting background server on port 8000..." -ForegroundColor Yellow
    Start-Process -FilePath "python" -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -WindowStyle Hidden
    Start-Sleep -Seconds 5
}

# 2. Generate test video (defaults to 30 min, 60 optional)
Write-Host "`n[2/4] Verifying $DurationMin-minute test media..." -ForegroundColor Yellow
python scripts\generate_long_test_media.py $DurationSec

# 3. Execute fault injection and pipeline quality assertions
Write-Host "`n[3/4] Running long-video verification and fault injection..." -ForegroundColor Yellow
python tests\test_long_pipeline_verification.py

# 4. Final summary
Write-Host "`n[4/4] Long-video verification complete." -ForegroundColor Green
Write-Host "Finished dubbed video located in: demo/long_media/ or outputs/" -ForegroundColor Cyan
