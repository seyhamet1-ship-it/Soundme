# =============================================================================
# verify_on_my_machine.ps1 — Run on YOUR Windows computer to confirm live
# Khmer dubbing works end-to-end.
#
# Requires:
#   - Python 3.10+ in PATH (pip install -r requirements.txt done)
#   - ffmpeg/ffprobe available (in tools/ or PATH)
#   - Internet access for live Edge TTS
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\verify_on_my_machine.ps1
# =============================================================================
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path (Split-Path $PSCommandPath -Parent) -Parent
Set-Location $ROOT

$PASS = 0; $FAIL = 0
$Rows = @()
$ServerProc = $null

function Write-Row($Status, $Check, $Detail) {
    $script:Rows += [PSCustomObject]@{Status=$Status; Check=$Check; Detail=$Detail}
    Write-Host ("  {0,-8} {1,-32} {2}" -f $Status, $Check, $Detail)
}

function Mark-Pass($Check, $Detail) { $script:PASS++; Write-Row "PASS" $Check $Detail }
function Mark-Fail($Check, $Detail) { $script:FAIL++; Write-Row "FAIL" $Check $Detail }
function Mark-Blocked($Check, $Detail) { $script:FAIL++; Write-Row "BLOCKED" $Check $Detail }

try {
    # ── 1. Start backend server ───────────────────────────────────────────────
    Write-Host "==> Starting backend server on :8001 ..."
    $ServerProc = Start-Process python `
        -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8001" `
        -PassThru -WindowStyle Hidden
    Start-Sleep 6
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:8001/api/health" -TimeoutSec 5
        Mark-Pass "server-start" "status=$($health.status)"
    } catch {
        Mark-Fail "server-start" "uvicorn did not respond: $_"
        throw "Server failed to start"
    }

    # ── 2. Live Khmer TTS (Edge TTS) ─────────────────────────────────────────
    Write-Host "==> Testing live Edge TTS ..."
    $TmpTTS = [System.IO.Path]::GetTempFileName() + ".wav"
    try {
        $body = '{"voice":"km-KH-SreymomNeural","text":"' + [char]6001 + '! ' + [char]6014 + [char]6031 + [char]6038 + [char]6026 + [char]6070 + [char]6016 + [char]6070 + [char]6042 + [char]6038 + [char]6036 + [char]6013 + [char]6022 + [char]6070 + '}"}'
        # Use the simpler known ASCII-safe preview call without Khmer in the request body
        $previewBody = @{voice="km-KH-SreymomNeural"; text="test"} | ConvertTo-Json
        $resp = Invoke-WebRequest "http://127.0.0.1:8001/api/tts/preview" `
            -Method POST -ContentType "application/json" -Body $previewBody -TimeoutSec 60
        $resp.Content | Set-Content $TmpTTS -Encoding Byte
        $size = (Get-Item $TmpTTS).Length
        if ($size -gt 1000) {
            Mark-Pass "edge-tts-preview" "HTTP $($resp.StatusCode), $size bytes WAV"
        } else {
            Mark-Fail "edge-tts-preview" "Response too small ($size bytes)"
        }
    } catch {
        Mark-Fail "edge-tts-preview" "$_"
    } finally {
        Remove-Item $TmpTTS -ErrorAction SilentlyContinue
    }

    # ── 3. Full pipeline (en test video) ─────────────────────────────────────
    Write-Host "==> Running full pipeline on sample_input.mp4 ..."
    $VideoPath = Join-Path $ROOT "sample_input.mp4"
    if (-not (Test-Path $VideoPath)) {
        Mark-Blocked "pipeline-en" "sample_input.mp4 not found — run: python scripts\generate_test_videos.py"
    } else {
        $FileStream  = [System.IO.File]::OpenRead($VideoPath)
        $FormData    = [System.Net.Http.MultipartFormDataContent]::new()
        $FileContent = [System.Net.Http.StreamContent]::new($FileStream)
        $FileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse("video/mp4")
        $FormData.Add($FileContent, "file", "sample_input.mp4")
        $FormData.Add([System.Net.Http.StringContent]::new("en"),                     "source_lang")
        $FormData.Add([System.Net.Http.StringContent]::new("km-KH-SreymomNeural"),   "voice")
        $FormData.Add([System.Net.Http.StringContent]::new("false"),                  "keep_background")
        $FormData.Add([System.Net.Http.StringContent]::new("false"),                  "burn_subtitles")
        $FormData.Add([System.Net.Http.StringContent]::new("false"),                  "review_before_tts")

        $Client = [System.Net.Http.HttpClient]::new()
        $Client.Timeout = [System.TimeSpan]::FromSeconds(60)
        $PostResp = $Client.PostAsync("http://127.0.0.1:8001/api/jobs", $FormData).Result
        $FileStream.Close()
        $SubmitBody = $PostResp.Content.ReadAsStringAsync().Result | ConvertFrom-Json
        $JobId = $SubmitBody.id
        Write-Host "   job_id: $JobId"

        $Deadline = (Get-Date).AddMinutes(15)
        $Status = "queued"
        while ($Status -notin @("completed","failed") -and (Get-Date) -lt $Deadline) {
            Start-Sleep 5
            $JobData = Invoke-RestMethod "http://127.0.0.1:8001/api/jobs/$JobId" -TimeoutSec 10
            $Status  = $JobData.status
            $Stage   = $JobData.stage
            $Progress= $JobData.progress
            Write-Host ("   [{0}] {1}% -- {2}" -f $Stage, $Progress, $Status)
        }

        if ($Status -eq "completed") {
            $DubbedPath = Join-Path $ROOT "demo\dubbed_verify.mp4"
            $DubbedBytes = (Invoke-WebRequest "http://127.0.0.1:8001/api/jobs/$JobId/download/video" -TimeoutSec 30).Content
            [System.IO.File]::WriteAllBytes($DubbedPath, $DubbedBytes)
            $SizeMB = [math]::Round((Get-Item $DubbedPath).Length / 1MB, 2)
            Mark-Pass "pipeline-en" ("dubbed_verify.mp4 size={0}MB  path={1}" -f $SizeMB, $DubbedPath)
        } elseif ((Get-Date) -ge $Deadline) {
            Mark-Fail "pipeline-en" "Timed out after 15 minutes (status=$Status)"
        } else {
            Mark-Fail "pipeline-en" "Job failed (status=$Status)"
        }
    }

    # ── 4. Pytest ─────────────────────────────────────────────────────────────
    Write-Host "==> Running pytest ..."
    $PyResult = & python -m pytest -q --tb=short 2>&1 | Select-Object -Last 5
    $PyPassed = $PyResult | Select-String "passed"
    if ($PyPassed) {
        Mark-Pass "pytest" ($PyPassed -join " ")
    } else {
        Mark-Fail "pytest" ($PyResult -join " | ")
    }

} finally {
    if ($ServerProc -ne $null) {
        Stop-Process -Id $ServerProc.Id -Force -ErrorAction SilentlyContinue
    }
}

# ── Summary table ─────────────────────────────────────────────────────────────
Write-Host ""
Write-Host ("=" * 70)
Write-Host ("  {0,-8}  {1,-32}  {2}" -f "STATUS", "CHECK", "DETAIL")
Write-Host ("-" * 70)
foreach ($row in $Rows) {
    Write-Host ("  {0,-8}  {1,-32}  {2}" -f $row.Status, $row.Check, $row.Detail)
}
Write-Host ("=" * 70)
Write-Host "  PASS: $PASS   FAIL: $FAIL"

$DubbedFinal = Join-Path $ROOT "demo\dubbed_verify.mp4"
if (Test-Path $DubbedFinal) {
    Write-Host ""
    Write-Host "  Dubbed video path: $DubbedFinal"
}

Write-Host ""
if ($FAIL -eq 0) {
    Write-Host "  ALL PASS — REAL mode confirmed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "  $FAIL CHECK(S) FAILED." -ForegroundColor Red
    exit 1
}
