#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
