#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Starting Soundme Backend (Uvicorn on :8000)..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
trap "kill $BACKEND_PID 2>/dev/null || true" EXIT

echo "Waiting for backend health check..."
healthy=0
for i in {1..30}; do
    if curl -s http://127.0.0.1:8000/api/health | grep -q '"status"'; then
        healthy=1
        break
    fi
    sleep 1
done

if [ "$healthy" -ne 1 ]; then
    echo "Backend failed to start!"
    exit 1
fi

echo "Backend is READY! (http://127.0.0.1:8000/api/health)"
echo "Starting Soundme Frontend (Vite on :5173)..."
cd "$ROOT/frontend"
npm run dev
