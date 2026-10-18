#!/usr/bin/env bash
# Soundme 60-Minute Overnight Verification Script (Linux/macOS)
# Run: bash scripts/verify_long_on_my_machine.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "=========================================================="
echo " Soundme: 60-Minute Real-Mode Long-Video Verification"
echo "=========================================================="

# 1. Health check
echo -e "\n[1/4] Checking backend server..."
if ! curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
    echo "  Starting uvicorn server on port 8000..."
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
    sleep 5
fi

# 2. Generate 60-min test video (if not already present)
echo -e "\n[2/4] Verifying 60-minute test media..."
python scripts/generate_long_test_media.py 3600

# 3. Execute fault injection and pipeline quality assertions
echo -e "\n[3/4] Running long-video verification and fault injection..."
python tests/test_long_pipeline_verification.py

# 4. Final summary
echo -e "\n[4/4] Long-video verification complete."
echo "Finished dubbed video located in: demo/long_media/ or outputs/"
