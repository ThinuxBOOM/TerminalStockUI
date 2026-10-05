#!/bin/sh
# Usage: run-job <ingest|sp500|predict|train|snapshot|evaluate|score|health|retention>
# Calls the backend cron endpoint(s) for one job. Exit status is non-zero if
# any call failed. Output goes to the container log (docker compose logs scheduler).
set -u
# shellcheck disable=SC1091
. /etc/scheduler.env
JOB="${1:-}"
FAILED=0

call() {  # call <path> [POST-json]
  path="$1"; body="${2:-}"
  started=$(date -u +%FT%TZ)
  if [ -n "$body" ]; then
    out=$(curl -sS --fail-with-body --max-time 900 -X POST \
      -H "Authorization: Bearer $CRON_SECRET" -H "Content-Type: application/json" \
      --data "$body" "$BACKEND_URL$path" 2>&1)
  else
    out=$(curl -sS --fail-with-body --max-time 900 \
      -H "Authorization: Bearer $CRON_SECRET" "$BACKEND_URL$path" 2>&1)
  fi
  status=$?
  summary=$(printf '%s' "$out" | tr -d '\n' | cut -c1-300)
  if [ $status -eq 0 ]; then
    echo "$started [$JOB] OK $path $summary"
  else
    echo "$started [$JOB] FAILED($status) $path $summary"
    FAILED=1
  fi
}

case "$JOB" in
  ingest)    call /api/cron/ingest ;;
  predict)   call /api/cron/predict ;;
  train)     call /api/cron/train ;;
  snapshot)  call /api/cron/snapshot ;;
  evaluate)  call /api/cron/evaluate ;;
  score)     call /api/cron/score ;;
  health)    call /api/cron/health ;;
  retention) call /api/cron/retention '{"apply": true}' ;;
  sp500)
    shard=1
    while [ $shard -le 10 ]; do
      call "/api/cron/ingest?universe=sp500&shard=$shard&shards=10"
      shard=$((shard + 1))
    done
    ;;
  *) echo "usage: run-job <ingest|sp500|calibrate|snapshot|evaluate|score|health|retention>"; exit 2 ;;
esac
exit $FAILED
