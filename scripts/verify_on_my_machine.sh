#!/usr/bin/env bash
# =============================================================================
# verify_on_my_machine.sh — Run on YOUR computer to confirm live dubbing works.
#
# Requires:
#   - Python 3.10+ in PATH (with all requirements.txt installed)
#   - ffmpeg + ffprobe in PATH or tools/ subdir
#   - Internet access (for edge-tts and HuggingFace model download on first run)
#
# Usage:
#   bash scripts/verify_on_my_machine.sh
# =============================================================================
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

PASS=0; FAIL=0
declare -a ROWS

ok()  { PASS=$((PASS+1)); ROWS+=("PASS  $1  $2"); echo "PASS  $1"; }
fail(){ FAIL=$((FAIL+1)); ROWS+=("FAIL  $1  $2"); echo "FAIL  $1  $2"; }
blocked(){ FAIL=$((FAIL+1)); ROWS+=("BLOCKED  $1  $2"); echo "BLOCKED  $1  $2"; }

# ── 1. Start server ──────────────────────────────────────────────────────────
echo "==> Starting backend server on :8000 ..."
uvicorn_pid=""
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 &>/tmp/soundme_server.log &
uvicorn_pid=$!
sleep 5
if ! curl -sf http://127.0.0.1:8001/api/health >/dev/null 2>&1; then
    fail "server-start" "uvicorn failed to start — see /tmp/soundme_server.log"
    kill "$uvicorn_pid" 2>/dev/null || true
    exit 1
fi
ok "server-start" "http://127.0.0.1:8001/api/health → ok"

# ── 2. Live Khmer TTS (Edge) ─────────────────────────────────────────────────
echo "==> Testing Edge TTS (live network probe) ..."
TMP_TTS=$(mktemp /tmp/soundme_edge_XXXXXX.wav)
HTTP_CODE=$(curl -sf -w "%{http_code}" -o "$TMP_TTS" \
    -X POST http://127.0.0.1:8001/api/tts/preview \
    -H "Content-Type: application/json" \
    -d '{"voice":"km-KH-SreymomNeural","text":"សួស្តី! នេះជាការសាកល្បងសំឡេង។"}')
if [[ "$HTTP_CODE" == "200" && -s "$TMP_TTS" ]]; then
    SIZE=$(wc -c < "$TMP_TTS")
    ok "edge-tts-preview" "HTTP 200, ${SIZE} bytes WAV"
else
    fail "edge-tts-preview" "HTTP $HTTP_CODE"
fi
rm -f "$TMP_TTS"

# ── 3. Full pipeline (en test video) ─────────────────────────────────────────
echo "==> Running full pipeline on sample_input.mp4 ..."
if [[ ! -f "sample_input.mp4" ]]; then
    blocked "pipeline-en" "sample_input.mp4 not found — run: python scripts/generate_test_videos.py"
else
    SUBMIT=$(curl -sf -X POST http://127.0.0.1:8001/api/jobs \
        -F "file=@sample_input.mp4;type=video/mp4" \
        -F "source_lang=en" \
        -F "voice=km-KH-SreymomNeural" \
        -F "keep_background=false" \
        -F "burn_subtitles=false" \
        -F "review_before_tts=false")
    JOB_ID=$(echo "$SUBMIT" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
    echo "   job_id: $JOB_ID"
    DEADLINE=$((SECONDS + 600))
    STATUS="queued"
    while [[ "$STATUS" != "completed" && "$STATUS" != "failed" && $SECONDS -lt $DEADLINE ]]; do
        sleep 5
        JOBDATA=$(curl -sf "http://127.0.0.1:8001/api/jobs/$JOB_ID")
        STATUS=$(echo "$JOBDATA" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
        STAGE=$(echo "$JOBDATA" | python3 -c "import sys,json;print(json.load(sys.stdin).get('stage','?'))")
        PROGRESS=$(echo "$JOBDATA" | python3 -c "import sys,json;print(json.load(sys.stdin).get('progress',0))")
        echo "   [$STAGE] $PROGRESS% — $STATUS"
    done
    if [[ "$STATUS" == "completed" ]]; then
        curl -sf "http://127.0.0.1:8001/api/jobs/$JOB_ID/download/video" -o demo/dubbed_verify.mp4
        DURATION=$(python3 -c "
import json, subprocess
r=subprocess.run(['ffprobe','-v','error','-show_format','-of','json','demo/dubbed_verify.mp4'],
    capture_output=True,text=True)
d=json.loads(r.stdout)
print(d['format']['duration'])
" 2>/dev/null || echo "0")
        if [[ "$DURATION" != "0" ]]; then
            ok "pipeline-en" "dubbed_verify.mp4 duration=${DURATION}s  (path: demo/dubbed_verify.mp4)"
        else
            fail "pipeline-en" "Output video has zero duration"
        fi
    else
        fail "pipeline-en" "Job status=$STATUS"
    fi
fi

# ── 4. Pytest ─────────────────────────────────────────────────────────────────
echo "==> Running pytest ..."
if python -m pytest -q --tb=short 2>&1 | tail -3 | grep -q "passed"; then
    ok "pytest" "all tests pass"
else
    fail "pytest" "one or more pytest failures"
fi

# ── Cleanup ───────────────────────────────────────────────────────────────────
kill "$uvicorn_pid" 2>/dev/null || true

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=============================="
printf "%-10s %-25s %s\n" "STATUS" "CHECK" "DETAIL"
echo "------------------------------"
for row in "${ROWS[@]}"; do
    status="${row%% *}"
    rest="${row#* }"
    check="${rest%% *}"
    detail="${rest#* }"
    printf "%-10s %-25s %s\n" "$status" "$check" "$detail"
done
echo "=============================="
echo "PASS: $PASS   FAIL: $FAIL"

if [[ -f "demo/dubbed_verify.mp4" ]]; then
    echo "Dubbed video: $(pwd)/demo/dubbed_verify.mp4"
fi

[[ $FAIL -eq 0 ]]
