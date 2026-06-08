#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"

# Point Python at certifi's CA bundle — Python 3.14 on macOS ships no default
# cert.pem, which makes the URL validator strip every real source URL.
if [[ -z "${SSL_CERT_FILE:-}" ]]; then
  CERTIFI_CA=$(python3 -c "import certifi; print(certifi.where())" 2>/dev/null || true)
  if [[ -n "$CERTIFI_CA" && -f "$CERTIFI_CA" ]]; then
    export SSL_CERT_FILE="$CERTIFI_CA"
    export REQUESTS_CA_BUNDLE="$CERTIFI_CA"
  fi
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck source=/dev/null
  source .env
  set +a
fi

HOST="${APP_HOST:-127.0.0.1}"
PORT="${APP_PORT:-8000}"

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
