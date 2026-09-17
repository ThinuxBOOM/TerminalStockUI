# Local hosting: schedulers + URLs

Two things work differently the moment the stack leaves Vercel and runs on
your own machine:

1. **The cloud schedulers can't reach you.** GitHub-hosted runners live in
   Microsoft's cloud; your `localhost` is not publicly routable, so every
   workflow in `.github/workflows/*.yml` that curls `$APP_URL` keeps hitting
   Vercel, never your PC. Run the same jobs **on the machine itself**.
2. **The backend URL is no longer fixed.** The frontend now resolves it at
   runtime (no rebuild) — pick whichever knob fits.

## A. Data jobs on your machine (pick ONE option)

All three options call the same endpoints the cloud used
(`/api/cron/ingest|calibrate|snapshot|evaluate|score|health|retention` —
see `docs/OPERATIONS.md` for what each does). Every endpoint is idempotent,
so overlapping/retried runs are safe — but keep exactly ONE scheduler active
per database, or two writers burn vendor quota for nothing.

| Option | How | Best for |
|---|---|---|
| `local-cron.*` + OS scheduler (**recommended**) | `scripts/cron/local-cron.ps1` / `.sh` per job | Zero GitHub setup |
| Self-hosted runner | rename `.github/workflows/local-selfhosted.yml.example` → `.yml` | You want schedules visible in Actions |
| By hand | `.\cron-call.ps1 -Path /api/cron/snapshot` | Catch-up runs, debugging |

### Option 1 — OS scheduler (recommended)

Windows (PowerShell, runs Task Scheduler):

```powershell
cd scripts\cron
.\local-cron.ps1 -Job daily          # smoke test: ingest+calibrate+score+health
.\install-windows-tasks.ps1          # install all 8 OneMarket\* tasks
schtasks.exe /Query /TN 'OneMarket\*'   # verify
.\install-windows-tasks.ps1 -Uninstall   # remove again
```

With a non-default backend: add `-BackendUrl http://192.168.1.20:8000 -CronSecret xxx`
to both scripts (or set `$env:BACKEND_URL` / `$env:CRON_SECRET`; `CRON_SECRET`
is otherwise read from `infra/docker/.env`).

Linux/macOS (cron):

```bash
chmod +x scripts/cron/*.sh
./scripts/cron/local-cron.sh daily    # smoke test
crontab -e                            # paste lines from crontab.local.example
```

Job → cloud counterpart: `ingest` (vercel.json 05:30), `sp500`
(sp500-ingest.yml 06:00, 10 shards), `calibrate` (vercel.json 06:30),
`snapshot` (snapshots.yml hourly), `evaluate` (alerts.yml every 15 min),
`score` (score.yml 07:00), `health` (health.yml 08:00), `retention`
(retention.yml Sun 03:00, posts `{"apply": true}` like the workflow).

### Option 2 — self-hosted runner

For teams that want the schedules in the Actions tab. Steps are in the header
of `local-selfhosted.yml.example`: install the runner on the backend machine,
rename to `.yml`, set `vars.LOCAL_APP_URL` + `secrets.LOCAL_CRON_SECRET`,
then disable the cloud counterparts (and the Vercel crons if the backend is
fully local).

## B. Pointing the frontend at your backend (no rebuild)

Resolution order (`frontend/src/api/baseUrl.js` — shared by all api modules):

1. `?api=<url>` — e.g. `http://localhost:5173/?api=http://192.168.1.20:8000`
   (remembered in that browser; `?api=clear` undoes it). Fastest for try-outs.
2. Provider Settings page → **Backend connection** card → SAVE + RELOAD
   (stored in `localStorage`, survives refresh).
3. `frontend/public/config.js` in dev, `frontend/dist/config.js` after
   `npm run build` — edit the file (or mount your own in Docker) and refresh.
   Permanent, survives browser wipes. See `config.example.js` for LAN setups.
4. `VITE_API_BASE_URL` baked at build time (unchanged behaviour).
5. Smart default: same-origin `/api` on https/remote hosts, else
   `http://localhost:8000`.

The card shows which layer won (`via …` badge), so "which URL am I on?" is
always one glance away.

## C. Backend side (CORS + ports)

- The backend only accepts listed frontend origins. Defaults cover
  `localhost:5173/3000`; for anything else set **one** of these on the backend
  host (`infra/docker/.env`, Render/Vercel dashboard, or shell):
  `FRONTEND_URL=http://<frontend-host>:<port>` (simplest) or
  `CORS_ORIGINS=http://a:5173,http://b:5173` (comma-separated, no `*`).
  Same-origin deploys (proxy serves `/api` from the frontend host) need nothing.
- `vite dev` proxy for `/api`: `VITE_PROXY_TARGET=http://<backend-host>:8000 npm run dev`
  (`BACKEND_URL` works as an alias). Compose: `BACKEND_PORT` / `FRONTEND_PORT` /
  `VITE_API_BASE_URL` in `infra/docker/.env`.

## D. Worked example — backend on PC-A, browser on PC-B

```
# PC-A (backend, 192.168.1.20): allow PC-B's frontend origin
FRONTEND_URL=http://192.168.1.10:5173
uvicorn api.main:app --host 0.0.0.0 --port 8000   # 0.0.0.0, not 127.0.0.1!

# PC-B (browser): open once with
http://192.168.1.10:5173/?api=http://192.168.1.20:8000
# …or set API_BASE_URL in public/config.js on PC-B's frontend host.
```

Windows firewall must allow the backend port on private networks. Never expose
this without `CRON_SECRET` + `SECRET_KEY` set — `?api=` accepts only
`http(s)` URLs, and the backend stays fail-closed in production.
