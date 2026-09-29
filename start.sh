#!/usr/bin/env bash
# Dev Workbench launcher (Linux/macOS)
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[1/4] Creating venv..."
  python3 -m venv .venv
fi

echo "[2/4] Installing backend deps (skipped if present)..."
.venv/bin/python -c "import fastapi, uvicorn, httpx, pydantic" 2>/dev/null || \
  .venv/bin/python -m pip install fastapi uvicorn httpx pydantic python-multipart

echo "[3/4] Checking frontend build..."
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && [ -d node_modules ] || npm install --no-audit --no-fund && npm run build)
fi

echo "[4/4] Starting server on http://127.0.0.1:8642 ..."
exec .venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8642
