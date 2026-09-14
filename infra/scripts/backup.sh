#!/usr/bin/env bash
# OneMarket Analyzer — backup (M8 hardening, spec Sec 6: database backup/restore tests).
#
# What: PostgreSQL custom-format dump (pg_dump -Fc) + Redis RDB snapshot,
#   checksums + verify step, optional S3 upload.
# Usage:
#   BACKUP_DIR=./backups ./infra/scripts/backup.sh
#   DATABASE_URL=postgresql+psycopg://u:p@host:5432/db REDIS_URL=redis://host:6379/0 ./infra/scripts/backup.sh
# Env:
#   DATABASE_URL       SQLAlchemy URL (default: $DATABASE_URL or postgres env parts)
#   POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB / POSTGRES_PORT (compose defaults)
#   REDIS_URL          redis://host:port/db (default redis://localhost:6379/0)
#   BACKUP_DIR         local output dir (default ./backups)
#   BACKUP_S3_BUCKET   optional; when set (and aws CLI present) uploads artifacts
#   BACKUP_S3_PREFIX   S3 key prefix (default onemarket/backups)
# Verify: pg_restore --list, non-empty files, sha256sums; exits non-zero on failure.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

PG_DUMP="$BACKUP_DIR/onemarket-pg-$STAMP.dump"
REDIS_RDB="$BACKUP_DIR/onemarket-redis-$STAMP.rdb"
SHA_FILE="$BACKUP_DIR/onemarket-backup-$STAMP.sha256"

# --- Postgres ---------------------------------------------------------------
# Prefer pg_dump against DATABASE_URL when it is a postgres URL; else fall back
# to discrete POSTGRES_* parts (docker-compose defaults).
DATABASE_URL="${DATABASE_URL:-}"
if [ -n "$DATABASE_URL" ]; then
  case "$DATABASE_URL" in
    postgresql*://*)
      # Strip the SQLAlchemy driver (+psycopg) for libpq tools.
      PGURL="$(printf '%s' "$DATABASE_URL" | sed -e 's#postgresql+psycopg://#postgresql://#' -e 's#postgresql+psycopg2://#postgresql://#')"
      if ! command -v pg_dump >/dev/null 2>&1; then
        echo "[backup] ERROR: pg_dump not found (install postgresql-client) — cannot dump $PGURL" >&2
        exit 1
      fi
      echo "[backup] pg_dump $PGURL -> $PG_DUMP"
      pg_dump --format=custom --file="$PG_DUMP" "$PGURL"
      ;;
    *) echo "[backup] DATABASE_URL is not postgres ($DATABASE_URL); skipping pg_dump" ;;
  esac
else
  PGHOST="${PGHOST:-localhost}"
  PGPORT="${POSTGRES_PORT:-5432}"
  PGUSER="${POSTGRES_USER:-onemarket}"
  PGDATABASE="${POSTGRES_DB:-onemarket}"
  export PGHOST PGPORT PGUSER PGDATABASE
  if [ -n "${POSTGRES_PASSWORD:-}" ]; then export PGPASSWORD="$POSTGRES_PASSWORD"; fi
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "[backup] ERROR: pg_dump not found (install postgresql-client)" >&2
    exit 1
  fi
  echo "[backup] pg_dump $PGUSER@$PGHOST:$PGPORT/$PGDATABASE -> $PG_DUMP"
  pg_dump --format=custom --file="$PG_DUMP"
fi

# --- Redis ------------------------------------------------------------------
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
if command -v redis-cli >/dev/null 2>&1; then
  # Parse host/port from REDIS_URL (redis:// or rediss://[:pass@]host:port/db).
  # TLS (rediss://, e.g. Upstash) needs `redis-cli --tls`; plain redis-cli
  # against a TLS endpoint fails fast with a clear message below.
  case "$REDIS_URL" in
    rediss://*) RURL="${REDIS_URL#rediss://}"; RTLS=1 ;;
    redis://*) RURL="${REDIS_URL#redis://}"; RTLS=0 ;;
    *) echo "[backup] WARN: REDIS_URL is not redis(s):// ($REDIS_URL); skipping Redis snapshot" >&2; RURL="" ;;
  esac
  case "$RURL" in *@*) RURL="${RURL#*@}";; esac
  RHOST="${RURL%%:*}"; RREST="${RURL#*:}"
  RPORT="${RREST%%/*}"
  [ -z "$RHOST" ] && RHOST="localhost"
  [ -z "$RPORT" ] && RPORT="6379"
  if [ -z "$RURL" ]; then
    echo "[backup] skipping Redis snapshot (unparseable REDIS_URL)"
  elif [ "${RTLS:-0}" = "1" ]; then
    echo "[backup] WARN: REDIS_URL uses rediss:// (TLS, e.g. Upstash); local redis-cli snapshot skipped — managed Redis is backed up by the provider"
  else
    echo "[backup] redis BGSAVE $RHOST:$RPORT, then --rdb $REDIS_RDB"
    redis-cli -h "$RHOST" -p "$RPORT" BGSAVE
    redis-cli -h "$RHOST" -p "$RPORT" --rdb "$REDIS_RDB"
  fi
else
  echo "[backup] redis-cli not found; skipping Redis snapshot (compose persists AOF at redisdata)"
fi

# --- Verify -----------------------------------------------------------------
echo "[backup] verifying artifacts..."
[ -f "$PG_DUMP" ] || { echo "[backup] ERROR: pg dump missing: $PG_DUMP" >&2; exit 1; }
[ -s "$PG_DUMP" ] || { echo "[backup] ERROR: pg dump is empty" >&2; exit 1; }
if command -v pg_restore >/dev/null 2>&1; then
  pg_restore --list "$PG_DUMP" >/dev/null || { echo "[backup] ERROR: pg_restore --list failed" >&2; exit 1; }
  echo "[backup] pg_restore --list OK"
else
  echo "[backup] WARN: pg_restore not found; skipped dump listing check"
fi
ARTIFACTS="$PG_DUMP"
if [ -f "$REDIS_RDB" ]; then
  [ -s "$REDIS_RDB" ] || { echo "[backup] ERROR: redis rdb is empty" >&2; exit 1; }
  echo "[backup] redis rdb OK ($(wc -c < "$REDIS_RDB") bytes)"
  ARTIFACTS="$ARTIFACTS $REDIS_RDB"
fi
# shellcheck disable=SC2086
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum $ARTIFACTS > "$SHA_FILE"
  sha256sum -c "$SHA_FILE"
elif command -v shasum >/dev/null 2>&1; then
  shasum -a 256 $ARTIFACTS > "$SHA_FILE"
  shasum -a 256 -c "$SHA_FILE"
else
  echo "[backup] ERROR: neither sha256sum nor shasum found — cannot checksum artifacts" >&2
  exit 1
fi
echo "[backup] checksums OK -> $SHA_FILE"

# --- Optional S3 upload ------------------------------------------------------
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  PREFIX="${BACKUP_S3_PREFIX:-onemarket/backups}"
  if command -v aws >/dev/null 2>&1; then
    echo "[backup] uploading to s3://$BACKUP_S3_BUCKET/$PREFIX/"
    aws s3 cp "$PG_DUMP" "s3://$BACKUP_S3_BUCKET/$PREFIX/"
    [ -f "$REDIS_RDB" ] && aws s3 cp "$REDIS_RDB" "s3://$BACKUP_S3_BUCKET/$PREFIX/"
    aws s3 cp "$SHA_FILE" "s3://$BACKUP_S3_BUCKET/$PREFIX/"
  else
    echo "[backup] WARN: BACKUP_S3_BUCKET set but aws CLI missing; skipped upload"
  fi
fi

echo "[backup] done: $PG_DUMP ${REDIS_RDB:-} $SHA_FILE"
