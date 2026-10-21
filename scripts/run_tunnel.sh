#!/usr/bin/env bash
# Soundme – Cloudflare Quick Tunnel launcher (Linux/Mac)
# Usage: bash scripts/run_tunnel.sh
# Requires: cloudflared on PATH, .env file (or env vars set)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${PORT:-8000}"
ENV_FILE="$ROOT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  export $(grep -v '^#' "$ENV_FILE" | xargs -d '\n') 2>/dev/null || true
fi

# Warn if DATA_DIR is on a network or cloud-synced drive
DATA_DIR="${UPLOAD_DIR:-$ROOT_DIR/uploads}"
if echo "$DATA_DIR" | grep -qiE "(google.drive|onedrive|dropbox|/mnt/smb|/mnt/nfs)"; then
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "WARNING: DATA_DIR is on a cloud/network drive: $DATA_DIR"
  echo "This will cause severe I/O latency. Use a local SSD path."
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
fi

if ! command -v cloudflared &>/dev/null; then
  echo "ERROR: cloudflared not found on PATH."
  echo "Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/"
  exit 1
fi

echo "==> Building frontend..."
cd "$ROOT_DIR/frontend" && npm run build && cd "$ROOT_DIR"

echo "==> Starting Soundme backend on port $PORT..."
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --timeout-keep-alive 3600 &
BACKEND_PID=$!

# Wait for backend to become healthy
echo "==> Waiting for backend to start..."
for i in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    echo "==> Backend healthy."
    break
  fi
  sleep 2
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Backend did not start within 60s."
    kill "$BACKEND_PID" 2>/dev/null || true
    exit 1
  fi
done

echo "==> Starting Cloudflare Quick Tunnel..."
cloudflared tunnel --url "http://localhost:$PORT" &
CF_PID=$!

echo ""
echo "==> Soundme is running!"
echo "    Local:  http://localhost:$PORT"
echo "    Tunnel: Check cloudflared output above for the public URL"
echo ""
echo "Press Ctrl+C to stop."

cleanup() {
  echo ""
  echo "==> Shutting down..."
  kill "$CF_PID" 2>/dev/null || true
  kill "$BACKEND_PID" 2>/dev/null || true
  echo "==> Done."
}
trap cleanup INT TERM

wait "$BACKEND_PID"
