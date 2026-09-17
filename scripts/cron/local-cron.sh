#!/usr/bin/env bash
# Local cron dispatcher — runs the scheduled data jobs against your LOCAL
# backend. This is the localhost replacement for the GitHub Actions workflows
# (.github/workflows/*.yml), which cannot reach your machine: GitHub-hosted
# runners live in the cloud and your localhost is not publicly routable.
#
# Usage: local-cron.sh <job>        (BACKEND_URL / CRON_SECRET via env)
#   jobs: ingest | sp500 | calibrate | snapshot | evaluate | score |
#         health | retention | daily | all [--with-retention]
#
#   daily  = ingest + calibrate + score + health   (the overnight batch)
#   all    = daily + sp500 + snapshot + evaluate   (everything but retention)
#            add --with-retention to include the weekly purge.
#
# Schedule it with cron (see crontab.local.example) or run jobs by hand.
# Every job is independent — a failing job never blocks the rest; the script
# exits non-zero if ANY job failed. Endpoints are idempotent, so overlapping
# or retried runs are safe.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WITH_RETENTION=0
ARGS=()
for a in "$@"; do
  if [ "$a" = "--with-retention" ]; then WITH_RETENTION=1; else ARGS+=("$a"); fi
done
JOB="${ARGS[0]:-}"

FAILED=0
run_job() {
  local label="$1"; shift
  echo "=== [$label] $(date -u +%FT%TZ) ==="
  if "$SCRIPT_DIR/cron-call.sh" "$@"; then
    echo "--- [$label] OK ---"
  else
    echo "--- [$label] FAILED ---"
    FAILED=1
  fi
}

case "$JOB" in
  ingest)    run_job "ingest"    "/api/cron/ingest" ;;
  calibrate) run_job "calibrate" "/api/cron/calibrate" ;;
  snapshot)  run_job "snapshot"  "/api/cron/snapshot" ;;
  evaluate)  run_job "evaluate"  "/api/cron/evaluate" ;;
  score)     run_job "score"     "/api/cron/score" ;;
  health)    run_job "health"    "/api/cron/health" ;;
  retention) run_job "retention" "/api/cron/retention" POST '{"apply": true}' ;;
  sp500)
    for shard in 1 2 3 4 5 6 7 8 9 10; do
      run_job "sp500-shard-$shard" "/api/cron/ingest?universe=sp500&shard=$shard&shards=10"
    done
    ;;
  daily)
    for j in ingest calibrate score health; do
      "$SCRIPT_DIR/local-cron.sh" "$j" || FAILED=1
    done
    ;;
  all)
    for j in ingest calibrate score health snapshot evaluate; do
      "$SCRIPT_DIR/local-cron.sh" "$j" || FAILED=1
    done
    "$SCRIPT_DIR/local-cron.sh" sp500 || FAILED=1
    if [ "$WITH_RETENTION" = "1" ]; then "$SCRIPT_DIR/local-cron.sh" retention || FAILED=1; fi
    ;;
  *)
    echo "usage: local-cron.sh <ingest|sp500|calibrate|snapshot|evaluate|score|health|retention|daily|all> [--with-retention]"
    exit 2
    ;;
esac

exit "$FAILED"
