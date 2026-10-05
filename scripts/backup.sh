#!/usr/bin/env bash
# Dump the Supabase database to ./backups/onemarket-<UTC stamp>.dump
# (pg_dump custom format), using the official Postgres image so no local
# client install is needed. Reads DATABASE_URL from .env unless it is
# already exported. The URL is never printed.
#
#   scripts/backup.sh
#   restore:  docker run --rm -i -v "$PWD/backups:/backups" postgres:17-alpine \
#               pg_restore --clean --if-exists --no-owner -d "$URL" /backups/<file>.dump
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${DATABASE_URL:-}" ] && [ -f .env ]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' .env | tail -n1 | cut -d= -f2-)"
fi
: "${DATABASE_URL:?DATABASE_URL is not set (export it or put it in .env)}"
# libpq tools want a plain postgresql:// URL (no SQLAlchemy driver suffix).
PGURL="$(printf '%s' "$DATABASE_URL" | sed -E 's#^postgres(ql)?(\+[a-z0-9]+)?://#postgresql://#')"

mkdir -p backups
OUT="onemarket-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker run --rm -e PGURL="$PGURL" -v "$PWD/backups:/backups" postgres:17-alpine \
  sh -c 'pg_dump --format=custom --no-owner --file="/backups/'"$OUT"'" "$PGURL"'
docker run --rm -v "$PWD/backups:/backups" postgres:17-alpine pg_restore --list "/backups/$OUT" > /dev/null
echo "backup written: backups/$OUT ($(du -h "backups/$OUT" | cut -f1)), verified readable"
