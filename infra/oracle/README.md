# Split deploy: frontend on Vercel, backend on Oracle Cloud

Status: **PREPARED, not moved.** The site still runs 100% on Vercel today.
Nothing in `vercel.json`, `api/index.py`, or the GitHub workflows was changed —
Vercel keeps serving frontend + Python API exactly as before until YOU confirm
the cutover (Phase 3 below).

Why this saves Fluid CPU: every `/api/*` call and every Vercel cron currently
burns serverless CPU-hours. After the split, Vercel serves only static files
(free/cheap bandwidth, ~zero compute) and the always-on Oracle VM does all
Python work on its flat/free compute.

## What the code already handled (no changes needed)

- **Frontend is split-ready.** `client.js`, `news.js`, `signals.js` all read
  `VITE_API_BASE_URL` and fall back to same-origin. Set it to the Oracle host
  and every call goes there; unset and it behaves exactly as today.
  (Caveat: Vite embeds it at BUILD time — changing it needs a frontend redeploy.)
- **Supabase needs no code change.** The backend only ever sees `DATABASE_URL`;
  the same pooled `:6543` URL works from Oracle. Connection code already
  detects the pooler and stays pgbouncer-safe.
- **GitHub workflows need no code change.** They all curl `$APP_URL` — after
  the move, repoint the `APP_URL` repo secret at the Oracle host (or disable
  the workflows you replace with the VM crontab; never run both writers).

## What changed in this prep (backward-compatible)

1. `backend/api/main.py` — `_cors_origins()` additionally accepts a single-
   origin `FRONTEND_URL` alias, strips trailing slashes, dedupes. With neither
   `CORS_ORIGINS` nor `FRONTEND_URL` set, behavior is byte-identical to before
   (localhost defaults; same-origin Vercel needs no CORS at all).
2. `backend/db/session.py` — new `DB_POOL_MODE` override: `queue` forces
   connection reuse on a long-lived host even with `APP_ENV=production` or a
   `:6543` URL; `null` forces NullPool; unset/`auto` keeps the exact legacy
   serverless detection (Vercel untouched). QueuePool-over-pooler keeps
   `prepare_threshold=None` so transaction-mode pgbouncer stays safe.
3. `backend/tests/test_split_deploy.py` — 8 offline tests pinning the above.

## Do I connect Upstash Redis to Oracle? YES

Point the Oracle backend at the **same** Upstash instance
(`UPSTASH_REDIS_URL` in `infra/oracle/backend.env`). Reasons:

- The cache layer (`backend/cache.py`) reads `UPSTASH_REDIS_URL`/`REDIS_URL`
  lazily and falls back to in-memory — same code, same keys, same TTLs.
- On a persistent VM a shared Redis is *more* valuable than on Vercel: one
  warm cache for all requests instead of per-instance memory.
- Sharing during the transition is safe (namespaced TTL keys; no locks).
- After cutover, Vercel functions stop touching Redis entirely, so there is
  no double-billing — Upstash bills ops/data, and total ops go DOWN (no more
  per-invocation cold misses).

## Phase 1 — Oracle VM setup (do anytime; zero effect on the live site)

1. Create an Ampere (free) Ubuntu 24.04 VM. Open ingress **80 + 443** in the
   subnet Security List AND on the VM (`iptables`/`firewalld`).
2. Install Docker + Caddy. Point `api.<your-domain>` DNS at the VM IP.
3. `git clone <repo> /opt/onemarket`, then:
   `cp infra/oracle/backend.env.example infra/oracle/backend.env` and fill in
   (Supabase pooled URL, Upstash URL, SECRET_KEY, **same CRON_SECRET as Vercel**,
   `CORS_ORIGINS=https://<your-app>.vercel.app`).
4. `docker compose -f infra/oracle/docker-compose.oracle.yml up -d --build`
5. Install the `Caddyfile.example` (edit host), `sudo systemctl reload caddy`.
6. Verify from your laptop:
   - `https://api.<your-domain>/health` → 200
   - `GET /api/instruments/search?q=AAPL` with `Origin: https://<your-app>.vercel.app`
     → 200 with `access-control-allow-origin` echoed (proves CORS).
7. Warm once: `infra/oracle/cron-call.sh /api/cron/ingest` on the VM.

## Phase 2 — Point staging at Oracle (reversible, Vercel still serves API)

1. Vercel Dashboard → Project → Settings → Environment Variables:
   `VITE_API_BASE_URL=https://api.<your-domain>` (Preview only, first).
2. Redeploy a preview branch → click through screener/brief/forecast.
   Rollback = delete the variable + redeploy (same-origin Vercel API is
   still deployed and untouched).

## Phase 3 — Cutover (only when YOU confirm; this is what stops the CPU burn)

1. Set `VITE_API_BASE_URL` for **Production**, redeploy.
2. Repoint GitHub `APP_URL` secret at Oracle — or disable the cron workflows
   you moved to the VM crontab (`crontab.example`). Single writer only.
3. Remove from `vercel.json`: the `functions` entry, the `crons` array, and
   the `/api/*` + `/health` rewrites. Redeploy → Vercel is static-only.
4. Optional: keep `api/index.py` in the repo (harmless, undeployed) as the
   instant rollback path — re-adding the `functions` entry restores the
   monorepo in one deploy.

## Files in this folder

| File | Purpose |
|---|---|
| `backend.env.example` | All backend env for Oracle (Supabase + Upstash + CORS + keys) |
| `docker-compose.oracle.yml` | Backend-only stack (no local Postgres/Redis) |
| `onemarket-backend.service` | systemd unit (optional; compose restart policy suffices) |
| `Caddyfile.example` | TLS reverse proxy for `api.<domain>` → :8000 |
| `cron-call.sh` + `crontab.example` | Vercel-cron replacements (enable post-cutover only) |
