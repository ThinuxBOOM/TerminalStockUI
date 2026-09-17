#!/usr/bin/env bash
# Single cron call against a LOCAL backend (no GitHub cloud involved).
# GitHub-hosted runners cannot reach your machine's localhost, so these
# scripts run ON the host itself (cron / Task Scheduler / self-hosted runner).
#
# Usage: cron-call.sh <api-path> [METHOD] [JSON-body]
#   e.g. cron-call.sh /api/cron/snapshot
#        cron-call.sh /api/cron/retention POST '{"apply": true}'
#
# Env:
#   BACKEND_URL   base URL, default http://127.0.0.1:8000
#   CRON_SECRET   Bearer for /api/cron/* (read from infra/docker/.env when set
#                 there; omit the header entirely when empty = open local dev)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PATH_ARG="${1:?usage: cron-call.sh <api-path> [METHOD] [JSON-body]}"
METHOD="${2:-GET}"
BODY="${3:-}"

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
BACKEND_URL="${BACKEND_URL%/}"

if [ -z "${CRON_SECRET:-}" ] && [ -f "$REPO_ROOT/infra/docker/.env" ]; then
  # shellcheck disable=SC1090
  set -a; source "$REPO_ROOT/infra/docker/.env"; set +a
fi

AUTH_ARGS=()
if [ -n "${CRON_SECRET:-}" ]; then
  AUTH_ARGS=(-H "Authorization: Bearer $CRON_SECRET")
fi

CURL_ARGS=(-sS --max-time 55 --connect-timeout 10 -o /tmp/onemarket-cron.json -w "%{http_code}")
if [ "$METHOD" = "POST" ]; then
  CURL_ARGS+=(-X POST -H "Content-Type: application/json" -d "${BODY:-{}}")
fi

code=$(curl "${CURL_ARGS[@]}" "${AUTH_ARGS[@]}" "$BACKEND_URL$PATH_ARG") || curl_exit=$?
curl_exit=${curl_exit:-0}
if [ "$curl_exit" != "0" ]; then
  echo "[$METHOD $PATH_ARG] FAILED: curl exit $curl_exit — is the backend running at $BACKEND_URL?"
  exit 1
fi
echo "[$METHOD $PATH_ARG] HTTP $code"
cat /tmp/onemarket-cron.json; echo ""
test "$code" = "200"
