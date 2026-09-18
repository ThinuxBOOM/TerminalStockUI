# Security — operations guide

## Secret handling

- **Never commit real secrets.** `infra/docker/.env` and any `.env` are
  gitignored (see `.gitignore`). Verified: `infra/docker/.env` is NOT tracked
  by git (`git ls-files` shows only `*.env.example`). Keep it that way —
  before every commit, run `git status --porcelain` and confirm no `.env`
  file is staged.
- **If a real credential ever lands in git history**, rotation alone is not
  enough (history keeps it forever):
  1. Rotate the credential immediately at the provider (Supabase dashboard
     → project → Database → reset password; update `DATABASE_URL`).
  2. Purge history (`git filter-repo --path infra/docker/.env --invert-paths`
     or BFG), force-push, and invalidate any exposed keys.
- **Generate secrets** with `openssl rand -hex 32` (SECRET_KEY, CRON_SECRET,
  API_KEY). The backend logs an ERROR at startup when `SECRET_KEY` is
  missing/a placeholder (`backend/security/secrets.py::warn_if_default_secret_key`).

## Production checklist (env vars)

| Var | Purpose | Required in prod |
|---|---|---|
| `SECRET_KEY` | Fernet key for provider secrets at rest | YES — 64-hex random |
| `CRON_SECRET` | Bearer auth for `/api/cron/*` | YES |
| `API_KEY` | Bearer/`X-API-Key` auth for all `/api/*` except `/health` | YES |
| `CORS_ORIGINS` | Exact frontend origin(s), comma-separated | YES — never `*` |
| `RATE_LIMIT_PER_MIN` | Per-IP sliding window (default 300, `0` disables) | recommended |
| `MAX_REQUEST_BYTES` | Body cap in bytes (default 1000000) | recommended |
| `DATABASE_URL` | Postgres (Supabase pooler `:6543` supported) | YES |
| `APP_ENV=production` | Enables HSTS + serverless pool posture | YES |

Local dev stays open by design: with `API_KEY`/`CRON_SECRET` unset, all
routes are unauthenticated and CORS falls back to localhost Vite origins.

## What changed (audit remediation)

- `backend/security/auth.py` — opt-in API-key enforcement (constant-time
  compare, rotation via comma-separated keys, `/health` always open).
- `backend/security/rate_limit.py` + middleware — 300 req/min/IP sliding
  window with `429 + Retry-After` and `X-RateLimit-*` headers.
- `backend/security/middleware.py` — `SecurityHeadersMiddleware` (CSP,
  `X-Frame-Options: DENY`, `X-Content-Type-Options`, HSTS in prod) and
  `RequestSizeLimitMiddleware` (413 over the cap).
- `backend/api/main.py` — explicit CORS allow-list, middleware wiring,
  startup placeholder-key warning.
- `backend/security/validation.py` — ticker allow-list
  (`^[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$`) enforced on quote/bars/AI/alerts;
  `instrument_id` length/control-char checks.
- `backend/api/alerts.py` — `GET /api/alerts` paginated
  (`limit` ≤ 500, `offset`, `total`); `evaluate_due_alerts` batch-capped
  (`max_alerts`, default 500).
- `backend/api/ai.py` — blocking market-data lookups run via
  `asyncio.to_thread` so the event loop stays free.
- `backend/market_data/service.py` — single-query bars join (was 2
  round-trips), cached deterministic stub bases (LRU 1024/day-bucketed),
  cached provisional instruments (LRU 512, defensive copies).
- `backend/ai/router.py` — evidence-hash cache now TTL-bounded (default 1h).
- `frontend/src/hooks/useWatchlist.js` — localStorage symbols allow-listed
  before render/storage (XSS hardening; React escaping remains the primary
  defense).
- `frontend/src/components/ErrorBoundary.jsx` — route-level crash
  containment (wired in `App.jsx`).
- `frontend/src/main.jsx` — React Query `gcTime` (already present on disk).

## Known non-issues (audit false positives, verified)

- **`infra/docker/.env` "committed"** — false: the file is gitignored and
  untracked (`git ls-files` / `git log --all -- infra/docker/.env` empty).
- **SQL injection via `cast(Alert.alert_id, SAString)`** — false: the
  comparison value is a bound parameter, not string interpolation; the cast
  exists for SQLite CHAR(32) UUID portability.
- **`price_bars` DESC-then-reverse** — intentional: `DESC + LIMIT + reverse`
  returns the *latest* N rows; `ASC + LIMIT` would return the oldest N.
- **`random` in stub quotes/bars** — intentional: seeded `random.Random`
  (never `secrets`) for deterministic offline filler; documented at the call
  sites. Not used for any cryptographic purpose.
- **SQLite "no pooling"** — dev-only posture; Postgres already uses
  `QueuePool` locally and `NullPool` on serverless poolers
  (`backend/db/session.py`).
- **Per-request DB sessions** — correct SQLAlchemy posture (cached factory,
  short-lived sessions); not a bottleneck.

## V2 auth (JWT + bcrypt — `backend/security/passwords.py`, `backend/auth/guards.py`, `backend/api/auth.py`)

- **Passwords:** bcrypt hashes only (`users.password_hash`, `$2b$`), never
  plaintext, never logged, never serialized (`GET /api/auth/me` returns
  `{id, email, tier, subscription_status, is_admin}` only). Register
  validates email (email-validator, `lower(trim())`) + password ≥ 10 chars;
  duplicate email → `409`. Login uses ONE 401 message
  (`invalid email or password`) for miss/mismatch (no user enumeration).
  When bcrypt is missing, dev/test fall back to stdlib PBKDF2-HMAC-SHA256
  (`pbkdf2_sha256$...`); production (`APP_ENV=production`) refuses the
  fallback with `RuntimeError` (fail-closed).
- **Tokens:** HS256 with the existing `SECRET_KEY` (15min access
  `{sub, tier, is_admin, type: access}`, 7-day refresh `{sub, type:
  refresh}` in an httpOnly cookie, `Secure` on https/prod, `SameSite=Lax`).
  JWT `tier` is informational only — every gate reloads the LIVE `users`
  row (`GET /api/auth/me` likewise; tier/subscription always fresh).
- **Gates:** missing/expired/tampered token or unknown user → `401`;
  live tier rank below `min_tier` → `402` with
  `{upgrade_required: true, min_tier}`; non-admin on admin routes → `403`.
  `is_admin` bypasses all tier gates. The legacy `X-Tier` header is never
  read (spoofed headers cannot escalate). Unknown `min_tier` at wiring
  time raises `ValueError` (misconfigured gates never open).
- **Rotation:** login and `POST /api/auth/refresh` (cookie) both rotate
  the refresh cookie; refresh with a missing/invalid/expired cookie → `401`.
- **Ops:** same `SECRET_KEY` strength rules as provider secrets
  (`openssl rand -hex 32`, rotation invalidates all tokens); no password
  or hash material in audit payloads (redact via `redact_mapping`).
