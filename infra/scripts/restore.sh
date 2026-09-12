#!/usr/bin/env bash
# OneMarket Analyzer — restore (M8 hardening, spec Sec 6: database backup/restore tests).
#
# What: restores a pg_dump custom-format backup (+ optional Redis RDB) and
#   verifies row counts + the audit hash chain.
# Usage:
#   ./infra/scripts/restore.sh ./backups/onemarket-pg-<stamp>.dump [./backups/onemarket-redis-<stamp>.rdb]
#   DATABASE_URL=postgresql+psycopg://u:p@host:5432/db ./infra/scripts/restore.sh <dump>
# Env:
#   DATABASE_URL       SQLAlchemy URL (default: $DATABASE_URL or POSTGRES_* parts)
#   POSTGRES_*         fallback discrete parts (compose defaults)
#   REDIS_URL          informational for the operator (Redis restores need a restart)
#   ALLOW_RESTORE      must be set (any non-empty value) as a production guard
# Verify: pg_restore into target, table counts via psql, optional
#   `python -m backend.observability.audit_verify --database-url ...`.
# DANGER: overwrites the target database. Never point at prod without a fresh backup.
set -euo pipefail

if [ -z "${ALLOW_RESTORE:-}" ]; then
  echo "[restore] ERROR: set ALLOW_RESTORE=1 to confirm (production guard)" >&2
  exit 2
fi
if [ "$#" -lt 1 ]; then
  echo "usage: $0 <pg.dump> [redis.rdb]" >&2
  exit 2
fi
PG_DUMP="$1"
REDIS_RDB="${2:-}"
[ -f "$PG_DUMP" ] || { echo "[restore] ERROR: dump not found: $PG_DUMP" >&2; exit 1; }

# --- Postgres restore --------------------------------------------------------
DATABASE_URL="${DATABASE_URL:-}"
if [ -n "$DATABASE_URL" ]; then
  case "$DATABASE_URL" in
    postgresql*://*)
      PGURL="$(printf '%s' "$DATABASE_URL" | sed -e 's#postgresql+psycopg://#postgresql://#' -e 's#postgresql+psycopg2://#postgresql://#')"
      echo "[restore] pg_restore --clean --if-exists into $PGURL from $PG_DUMP"
      pg_restore --clean --if-exists --dbname="$PGURL" "$PG_DUMP"
      ;;
    *) echo "[restore] ERROR: DATABASE_URL is not postgres: $DATABASE_URL" >&2; exit 1 ;;
  esac
else
  PGHOST="${PGHOST:-localhost}"
  PGPORT="${POSTGRES_PORT:-5432}"
  PGUSER="${POSTGRES_USER:-onemarket}"
  PGDATABASE="${POSTGRES_DB:-onemarket}"
  export PGHOST PGPORT PGUSER PGDATABASE
  if [ -n "${POSTGRES_PASSWORD:-}" ]; then export PGPASSWORD="$POSTGRES_PASSWORD"; fi
  echo "[restore] pg_restore --clean --if-exists into $PGUSER@$PGHOST:$PGPORT/$PGDATABASE"
  pg_restore --clean --if-exists --dbname="$PGDATABASE" "$PG_DUMP"
fi

# --- Redis note ---------------------------------------------------------------
if [ -n "$REDIS_RDB" ]; then
  [ -f "$REDIS_RDB" ] || { echo "[restore] ERROR: rdb not found: $REDIS_RDB" >&2; exit 1; }
  echo "[restore] Redis RDB provided ($REDIS_RDB)."
  echo "[restore] Stop redis, replace dump.rdb (redisdata volume), then start redis."
  echo "[restore] Example (compose): docker compose stop redis && \\"
  echo "           docker cp $REDIS_RDB onemarket-redis:/data/dump.rdb && docker compose start redis"
else
  echo "[restore] no Redis RDB given; cache/queue rebuild from Postgres on boot."
fi

# --- Verify -------------------------------------------------------------------
echo "[restore] verifying restored database..."
if command -v psql >/dev/null 2>&1; then
  if [ -n "${DATABASE_URL:-}" ]; then
    case "${DATABASE_URL}" in postgresql*://*)
      PGURL="$(printf '%s' "$DATABASE_URL" | sed -e 's#postgresql+psycopg://#postgresql://#' -e 's#postgresql+psycopg2://#postgresql://#')"
      psql "$PGURL" -c "SELECT count(*) AS instruments FROM instruments;" \
                     -c "SELECT count(*) AS price_bars FROM price_bars;" \
                     -c "SELECT count(*) AS forecasts FROM forecasts;" \
                     -c "SELECT count(*) AS audit_logs FROM audit_logs;"
      ;;
    esac
  else
    psql -c "SELECT count(*) AS instruments FROM instruments;" \
         -c "SELECT count(*) AS price_bars FROM price_bars;" \
         -c "SELECT count(*) AS forecasts FROM forecasts;" \
         -c "SELECT count(*) AS audit_logs FROM audit_logs;"
  fi
else
  echo "[restore] WARN: psql not found; skipped table-count check"
fi

if [ -f "backend/observability/audit_verify.py" ]; then
  if [ -n "${DATABASE_URL:-}" ]; then
    python -m backend.observability.audit_verify --database-url "$DATABASE_URL" \
      || { echo "[restore] ERROR: audit hash-chain verification failed" >&2; exit 1; }
  else
    echo "[restore] WARN: DATABASE_URL unset; skipped audit_verify (run it manually post-restore)"
  fi
fi

echo "[restore] done."
