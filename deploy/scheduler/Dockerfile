# Scheduler: busybox crond calling the backend's /api/cron/* endpoints over
# the internal compose network with CRON_SECRET. Build context: deploy/scheduler.
FROM alpine:3.20
RUN apk add --no-cache curl tzdata
COPY crontab /etc/crontabs/root
COPY run-job.sh /usr/local/bin/run-job
COPY entrypoint.sh /usr/local/bin/entrypoint
RUN chmod 0755 /usr/local/bin/run-job /usr/local/bin/entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint"]
