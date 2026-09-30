#!/bin/sh
# crond does not pass the container environment to jobs, so persist the two
# values the jobs need (readable by root only), then run crond in the
# foreground with job output going to the container log.
set -eu
: "${CRON_SECRET:?CRON_SECRET must be set}"
umask 077
{
  printf 'CRON_SECRET=%s\n' "$CRON_SECRET"
  printf 'BACKEND_URL=%s\n' "${BACKEND_URL:-http://backend:8000}"
} > /etc/scheduler.env
echo "scheduler: starting crond (UTC schedule in /etc/crontabs/root)"
exec crond -f -l 6 -L /dev/stdout
