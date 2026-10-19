#!/usr/bin/env bash
# Soundme 30/60-Minute Verification Script (Linux/macOS)
# Run: bash scripts/verify_long_on_my_machine.sh [30|60]

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DURATION_MIN="${1:-30}"
DURATION_SEC=$(( DURATION_MIN * 60 ))

echo "=========================================================="
echo " Soundme: ${DURATION_MIN}-Minute Real-Mode Long-Video Verification"
echo "=========================================================="

# 1. Health check
echo -e "\n[1/4] Checking backend server..."
if ! curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
    echo "  Starting uvicorn server on port 8000..."
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
    sleep 5
fi

# 2. Generate test video (defaults to 30 min, 60 optional)
echo -e "\n[2/4] Verifying ${DURATION_MIN}-minute test media..."
python scripts/generate_long_test_media.py "$DURATION_SEC"

# 3. Execute fault injection and pipeline quality assertions
echo -e "\n[3/4] Running long-video verification and fault injection..."
python tests/test_long_pipeline_verification.py

# 4. Final summary
echo -e "\n[4/4] Long-video verification complete."
echo "Finished dubbed video located in: demo/long_media/ or outputs/"
