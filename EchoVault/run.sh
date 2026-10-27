#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if [[ ! -f .venv/.echovault-deps-installed ]]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.echovault-deps-installed
fi
.venv/bin/python configure_openrouter.py
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
