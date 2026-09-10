#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' '请先执行：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then
  printf '%s\n' '请先执行：cd frontend && npm ci && npm run build'
  exit 1
fi
exec .venv/bin/python -m uvicorn analyzer.app:create_app --factory --host 127.0.0.1 --port 8765
