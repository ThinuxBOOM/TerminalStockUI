#!/usr/bin/env bash
# Cron caller for the Oracle backend: hits localhost (no network/CORS/auth
# surface beyond the VM) with the CRON_SECRET from infra/oracle/backend.env.
# Usage: cron-call.sh <path>   e.g. cron-call.sh /api/cron/ingest
# Called by crontab.example — not by Vercel (Vercel crons stay until cutover).
set -euo pipefail

ENV_FILE="$(dirname "$0")/backend.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

: "${CRON_SECRET:?CRON_SECRET not set in infra/oracle/backend.env}"
PATH_ARG="${1:?usage: cron-call.sh <api-path>}"

curl -sS --max-time 55 --connect-timeout 10 -o /tmp/onemarket-cron.json -w "%{http_code}\n" \
  -H "Authorization: Bearer $CRON_SECRET" \
  "http://127.0.0.1:8000${PATH_ARG}"
