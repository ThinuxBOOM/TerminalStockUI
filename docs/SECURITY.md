# Security

What the application enforces, and what the operator must configure.

## Authentication

- Every `/api/*` route requires a signed-in user, except `/api/auth/*`
  (login itself) and `/api/cron/*` (scheduler, see below). `/health` is
  public. The dependency is declared on each router, and
  `backend/tests/test_auth.py::test_every_data_route_requires_login` fails if
  any route answers without a token.
- Passwords: bcrypt, 10 to 72 bytes. Login failures all return the same 401;
  an unknown email still costs one bcrypt check.
- Tokens: HS256 JWTs signed with `SECRET_KEY`. Access tokens live 15 minutes
  and are kept in browser memory only (never localStorage). Refresh tokens
  live 7 days in an `HttpOnly; Secure; SameSite=Strict` cookie scoped to
  `/api/auth`.
- Revocation: each token carries the user's `token_version`. Logout,
  password resets and admin changes bump it, which invalidates every
  outstanding access and refresh token for that user.
- Registration is closed by default in production (`ALLOW_REGISTRATION`).
- Admins (`users.is_admin`) are the only users who can read or change AI
  provider keys and budgets, or trigger provider probes.
- Alerts and AI research jobs are visible only to the user who created them.

## Scheduler (`/api/cron/*`)

Requires `Authorization: Bearer <CRON_SECRET>` (constant-time compare). No
header, user agent or source address is trusted instead of the secret.
Retention windows cannot be set over HTTP.

## Secrets

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | signs JWTs and derives the Fernet key that encrypts provider API keys stored through the UI |
| `CRON_SECRET` | scheduler bearer token |
| `DATABASE_URL` | Supabase connection string |
| `*_API_KEY` | optional provider keys (alternatively stored encrypted via the UI) |

- In production the backend refuses to start when `SECRET_KEY` or
  `CRON_SECRET` is missing, shorter than 32 characters, or a placeholder.
  Generate them with `openssl rand -hex 32`.
- Keep secrets only in `.env` on the server (gitignored). Never pass
  passwords on a command line; `scripts/create_user.py` prompts for them.
- Provider keys are decrypted only at call time, never returned by the API,
  and redacted from logs and audit payloads (`backend/security/secrets.py`).
- If a secret is ever committed, rotate it first, then purge it from history
  (`git filter-repo`); rotation alone leaves it readable in old commits.

## Network and HTTP hardening

- Only Caddy is exposed (80/443). The backend and Redis are reachable only on
  the compose network. Caddy overwrites client-supplied `X-Forwarded-For`,
  and uvicorn trusts forwarded headers only because nothing else can reach it.
- Browser pages: CSP with no inline or third-party scripts
  (`script-src 'self'`, `connect-src 'self'`), `frame-ancestors 'none'`, HSTS,
  `nosniff`, strict referrer policy (`deploy/Caddyfile`).
- API responses: `default-src 'none'` CSP, `X-Frame-Options: DENY`, HSTS in
  production (`backend/security/middleware.py`).
- The frontend talks only to its own origin; the backend URL is fixed at
  build time (no query-string or storage override).
- Rate limits per client address: `RATE_LIMIT_PER_MIN` for the API (default
  300) and `AUTH_RATE_LIMIT_PER_MIN` for login/register/refresh (default 10).
  Live AI calls are capped per user per 24 h (`AI_DAILY_CALLS_PER_USER`).
- Request bodies over `MAX_REQUEST_BYTES` (default 1 MB) get 413.
- Symbols and instrument ids are allow-listed before they reach providers or
  the database (`backend/security/validation.py`).
- API docs (`/docs`, `/openapi.json`) are disabled in production.

## Database

The backend connects with the Supabase owner role, which bypasses row-level
security. RLS is enabled on every table with no grants to the `anon` and
`authenticated` roles (`migrations/0010_rls_hardening.sql`), so the Supabase
REST API exposes nothing even if its anon key leaks. Never put the service
role key or database password in the frontend.

## Reporting a problem

Open a private security advisory on the repository rather than a public
issue.
