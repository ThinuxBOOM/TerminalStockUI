# OneMarket Analyzer — V2 Plan (auth + subscriptions + compliant ads)

> Branch: `v2-auth-billing-ads` (branched from `main`, V1 locked).
> Status: PLAN ONLY — no behavior change yet. Implement in phases below, in order.
> Decisions (owner delegated, chosen for this stack):
> - Billing: **Stripe** (Checkout + Customer Portal + webhooks). PCI offloaded, FastAPI+React native.
> - Auth: **Custom JWT** (bcrypt + PyJWT, portable Docker/SQLite/Postgres/Supabase). No Supabase Auth coupling.
> - Ads: **Google AdSense, visible slots only**. Zero-traffic starter. Hidden-layer ads explicitly OUT (ad fraud, ban risk).

V1 seams this builds on (verified in tree):
- `backend/auth/tiers.py` — `VALID_TIERS=(free,silver,gold,platinum)`, `TIER_QUOTAS`, `FEATURE_MIN_TIER`, `can_use_feature()`, `get_current_user_stub()` (guest, never gates).
- `frontend/src/api/authStub.js` + `frontend/src/hooks/useCurrentUserStub.js` + `frontend/src/pages/LoginStubPage.jsx` (`/login`, never gates) + `frontend/src/api/client.js:388-394` (`PLAN_TIERS`, `TIER_FEATURES`).
- `backend/api/main.py::create_app` — 14 router prefixes + `/health`; CORS already allows `Authorization`.
- `backend/api/deps.py` — singleton wiring point (registry/health/service).
- `backend/db/models.py` — append-only convention; `AiTokenLedger(user_id,tier NULL)` + `alerts/forecasts.user_id+tier` migration-only stubs already exist.
- Migrations: raw `psql -f infra/migrations/00XX.sql`, twin `supabase/migrations/00XX.sql`, RLS deny-by-default, backend bypasses RLS via `DATABASE_URL` owner creds.
- Frontend shell: `frontend/src/components/Layout.jsx` (sticky header + `main key={pathname}` + footer), `frontend/src/App.jsx` (lazy routes), `frontend/index.html` (no ad script yet), `vercel.json` (no CSP headers yet), `backend/security/middleware.py` (API-only `default-src 'none'` — DO NOT loosen for ads).

---

## 0. Non-negotiables

1. **Backend is the enforcer.** Frontend `<RequireTier>` mirrors for UX/upsell only. Every paid endpoint has `Depends(require_tier(...))`. No client-supplied tier is trusted (`X-Tier` stub retired for auth; webhook is source of truth for paid tier).
2. **Passwords:** bcrypt hash only (`password_hash TEXT`), never plaintext, never logged, never in audit payloads. Redact via existing `redact_mapping`.
3. **Fail-closed preserved:** unknown symbol `404`, bad horizon/profile `422`, stale/missing FX `423`, no live data `502`, unauthenticated `401`, tier too low `402`, admin-only `403`. Never `200` with stub data.
4. **Hidden ads are OUT.** No `opacity:0`, `display:none`, `visibility:hidden`, `z-index:-1`, 1x1, off-screen, stacked, or background-layer impressions. That is invalid traffic per AdSense policy (suspension + clawback + domain ban). Premium = **don't mount / don't request**, never render-then-hide.
5. **Additive DB only.** No renames/removes. New `0008_users_auth.sql` twin + appended ORM. Old stub columns untouched in 0008 (FK backfill deferred).

---

## Phase 0 — Deps + env (no behavior change)

**Why first:** everything else imports these.

- `backend/requirements.txt` + `api/requirements.txt` add:
  - `bcrypt>=4.1` (password hashing)
  - `pyjwt>=2.8` (access tokens, HS256 with existing `SECRET_KEY`)
  - `stripe>=8.0` (Checkout + Portal + webhook verify)
  - `email-validator>=2.0` (register validation)
- `infra/docker/.env.example` add (empty values, never real secrets):
  - `ADMIN_EMAIL=` + `ADMIN_PASSWORD_HASH=` (bcrypt string, offline-generated)
  - `STRIPE_SECRET_KEY=` + `STRIPE_WEBHOOK_SECRET=` + `STRIPE_PRICE_SILVER=` + `STRIPE_PRICE_GOLD=` + `STRIPE_PRICE_PLATINUM=`
  - `FRONTEND_URL=` (already exists as CORS alias — reuse for Stripe success/cancel URLs)
  - `VITE_ADS_ENABLED=` + `VITE_ADS_CLIENT_ID=` (frontend `frontend/.env.example` too)
- Acceptance: `pip install` green, `docker compose config` green, no route changes.

---

## Phase 1 — Users DB (`0008_users_auth.sql`)

**Files:**
- NEW `infra/migrations/0008_users_auth.sql` (canonical)
- NEW `supabase/migrations/0008_users_auth.sql` (twin + RLS block; apply on direct `:5432` only, never `:6543` pooler)
- EDIT `backend/db/models.py` (append `User` at EOF, keep append-only convention)
- EDIT `docs/DB_SCHEMA.md` (document `users`)

**DDL (idempotent guards, `IF NOT EXISTS`):**

```sql
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,               -- app normalizes lower(trim())
  password_hash TEXT NOT NULL,              -- bcrypt $2b$, never plaintext
  tier TEXT NOT NULL DEFAULT 'free'
    CHECK (tier IN ('free','silver','gold','platinum')),
  stripe_customer_id TEXT NULL UNIQUE,
  stripe_subscription_id TEXT NULL,
  subscription_status TEXT NULL,            -- active/trialing/past_due/canceled/comped
  is_admin BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_tier ON users(tier);
CREATE INDEX IF NOT EXISTS ix_users_stripe_customer
  ON users(stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;
```

- Supabase twin adds: `ALTER TABLE users ENABLE ROW LEVEL SECURITY;` + single policy `users_read_own: SELECT TO authenticated USING (auth.uid() = id)`. No INSERT/UPDATE/DELETE policies for anon/authenticated — all writes via backend `DATABASE_URL` (existing bypass pattern). No recursive `is_admin` RLS policy (admin enforced in app).
- ORM: `class User(Base)` with `__tablename__="users"`, portable `ID_TYPE` for `id` (same as `Instrument.instrument_id`).
- Do NOT FK existing `user_id` stubs (`alerts`, `forecasts`, `ai_token_ledger`, snapshots) in 0008 (avoids backfill lock). App-layer enforcement only. Optional later: `ADD CONSTRAINT ... NOT VALID` + `VALIDATE`.
- Audit reuse: `audit_logs(actor='system'|'user:<id>', action IN (user.created, admin.bootstrapped, subscription.changed), entity_type='users')`, payload redacted (never hash).
- Acceptance: both migrations apply clean on fresh + existing DB (rerun safe); `pytest backend/tests/test_db.py -q` green; RLS: anon reads 0 rows.

---

## Phase 2 — Auth (register / login / me + JWT)

**Files:**
- NEW `backend/security/passwords.py` — `hash_password()` / `verify_password()` (bcrypt wrapper, stdlib-only fallback refuses in prod)
- NEW `backend/api/auth.py` — `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/refresh` (refresh via httpOnly Secure cookie or hashed DB token — pick cookie for V2)
- NEW `backend/auth/guards.py` — `get_current_user()` + `require_tier(min_tier)` + `require_admin()` (reuses `_TIER_RANK` from `tiers.py`)
- EDIT `backend/api/main.py` — `include_router(auth_router)`; CORS already allows `Authorization`; webhook route exempt from API-key auth (see Phase 4)
- EDIT `frontend/src/api/` — NEW `auth.js` (login/register/me, token storage, `Authorization: Bearer` injection into `client.js` axios instance)
- EDIT `frontend/src/App.jsx` — replace `/login` stub with real `LoginPage` + NEW `/pricing`, `/checkout/success`, `/account` routes; NEW `<RequireTier>` wrapper (UX mirror only)
- Tests: NEW `backend/tests/test_auth.py` (register/login/me, wrong password 401, expired token 401, tier default free)

**Semantics:**
- Register: validate email + password >=10 chars, `lower(trim(email))`, bcrypt-hash, `INSERT tier='free'`, return 201 + access token (15min HS256, `sub=user_id`, `tier`, `is_admin`) + set refresh cookie. Duplicate email -> `409`.
- Login: lookup by normalized email, `verify_password`, 401 on miss/mismatch (same message, no user enumeration), return access token + rotate refresh.
- `GET /api/auth/me`: authed, loads live `users` row (tier/subscription_status fresh, not JWT-cached) -> `{id, email, tier, subscription_status, is_admin}`.
- `get_current_user`: decodes Bearer, loads row, 401 on missing/expired/unknown user. `require_tier(min)`: 401 unauthenticated, **402** when rank insufficient (body includes `upgrade_required: true, min_tier`), 403 admin-only via `require_admin`.
- Frontend: `useAuth()` hook (replaces `useCurrentUserStub` call sites gradually), token in memory + localStorage fallback, axios interceptor injects Bearer, 401 -> redirect `/login`, 402 -> upsell modal linking `/pricing`.
- Acceptance: register->login->me roundtrip works with AI keys empty; no plaintext password anywhere (`grep -ri password_hash` only in DDL + hasher + bootstrap).

---

## Phase 3 — HARD tier gating (backend enforces, frontend mirrors)

**Files:**
- EDIT `backend/auth/tiers.py` — extend `FEATURE_MIN_TIER` to cover V2 surface (keep `can_use_feature()` pure):
  - `quick_insight: free, forecast_assist: free, report: silver, deep_research: silver, screener: silver, backtest: gold, providers_configure: platinum(admin or platinum), all_providers: platinum`
- EDIT routers (add `Depends(require_tier(...))`):
  - `backend/api/ai.py` — `POST /api/ai/insight` (quick_insight/report by body `profile`), `POST /api/ai/forecast_opinion` (forecast_assist), `POST /api/ai/deep_research_job` + `GET /api/ai/jobs/{id}` (deep_research)
  - `backend/api/forecast.py` — `GET /api/forecast/{symbol}` (+ calibration history) — min `free` today, ready to raise per-horizon later
  - `backend/api/analytics_api.py` — `GET /api/analytics/{symbol}`
  - `backend/api/backtest.py` — `POST /api/backtest/run` + `GET /api/backtest/{symbol}` (gold)
  - `backend/api/screener.py` — `GET /api/screener` (silver)
  - `backend/api/alerts.py`, `backend/api/providers.py` (configure -> admin/platinum)
  - `market_data.py` quote/bars stays Free (top-of-funnel)
- EDIT cache keys (prevent cross-tier poisoning):
  - `backend/ai/router.py:446-451` key += `u:{id}:t:{tier}`
  - `backend/api/screener.py:220`, `market_index.py:74`, `news.py:69`, `markets.py:63-92`, `market_data/service.py:444` (`quote:{sym}:{mic}`, `_bars_cache_key`) — prefix `u:{id}:t:{tier}:` for AI/screener; keep `indicator_cache` **unscoped by design** (deterministic identical per tier)
  - `backend/db/writers.py::log_ai_tokens(user_id,tier)` — start passing real values (today no-op)
- EDIT frontend:
  - `TIER_FEATURES` in `client.js` stays as UX map; `authStub.js` kept for guest path, new `auth.js` for authed path
  - `<RequireTier min="silver">` gates screener/deep-research/report UI with upsell (never the sole gate)
  - `WelcomePage.jsx:512-583` pricing copy wired to `/pricing` (Stripe Checkout buttons)
- Tests: NEW `backend/tests/test_tier_gates.py` — matrix: free->deep_research 402, free->backtest 402, silver->backtest 402, gold->backtest 200, tampered `X-Tier: platinum` header still 402 (header ignored when JWT present), admin bypass 200. Frontend `auth.test.js` for `RequireTier` mirror.
- Acceptance: lower tier CANNOT reach higher feature via direct `curl` with valid low-tier token (402), even with spoofed headers/body tier fields.

---

## Phase 4 — Stripe billing (Checkout + Portal + webhook)

**Files:**
- NEW `backend/api/billing.py` — `/api/billing/checkout`, `/api/billing/portal`, `/api/billing/webhook`, `GET /api/billing/status`
- EDIT `backend/api/main.py` — mount billing router; exempt `/api/billing/webhook` from `API_KEY` check (raw-body HMAC verify instead)
- EDIT `frontend/src/pages/` — NEW `PricingPage.jsx`, `CheckoutSuccessPage.jsx`, `AccountPage.jsx` (manage + status)
- Tests: NEW `backend/tests/test_billing.py` (checkout creates session (mocked `stripe` SDK), webhook sig-fail 400, `checkout.session.completed` upgrades tier, `customer.subscription.deleted` downgrades to free, idempotent replay)

**Flow:**
1. Authed `POST /api/billing/checkout {tier: silver|gold|platinum}` -> lookup/create `stripe_customer_id` for user -> `stripe.checkout.Session.create(customer|_customer_email, line_items=[{price: PRICE_X, quantity:1}], mode='subscription', metadata={user_id}, success_url={FRONTEND_URL}/checkout/success?session_id={CHECKOUT_SESSION_ID}, cancel_url={FRONTEND_URL}/pricing)` -> `{url}`.
2. User pays on Stripe (card never touches our server — PCI offloaded).
3. Stripe -> `POST /api/billing/webhook` (raw body `await request.body()`, `stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)`, 400 on fail):
   - `checkout.session.completed` -> retrieve subscription -> first `price.id` -> tier map -> `UPDATE users SET tier, stripe_customer_id, stripe_subscription_id, subscription_status='active'` + audit `subscription.changed`.
   - `customer.subscription.updated` -> sync tier/status.
   - `customer.subscription.deleted` / `past_due` / `incomplete_expired` -> `tier='free'`, status synced + audit.
   - Unknown price -> `free` + warn log (never crash webhook; return 200 after logging so Stripe doesn't infinite-retry a poison event — or 500 only on transient DB error).
   - Idempotent on Stripe `event.id` (store processed IDs or rely on convergent UPDATE + audit dedupe).
4. `POST /api/billing/portal` (authed) -> `stripe.billing_portal.Session.create(customer, return_url={FRONTEND_URL}/account)` -> `{url}` for cancel/upgrade/downgrade.
5. `GET /api/billing/status` (authed) -> `{tier, subscription_status, stripe_customer_id?}` (no secrets).
- Price map server-side dict only: `{PRICE_SILVER:'silver', PRICE_GOLD:'gold', PRICE_PLATINUM:'platinum'}` from env. Never accept tier from client body for DB write.
- Local dev: `stripe listen --forward-to localhost:8000/api/billing/webhook`, trigger `stripe trigger checkout.session.completed`.
- Acceptance: test-mode card `4242...` upgrades user to paid tier via webhook only (no manual DB edit); cancel via portal webhook downgrades to free; sig-less POST 400s.

---

## Phase 5 — Admin bootstrap (platinum without paying)

**Files:**
- NEW `scripts/bootstrap_admin.py` (CLI, `getpass`, bcrypt, idempotent upsert)
- EDIT `infra/docker/.env.example` (placeholder `ADMIN_EMAIL=` + `ADMIN_PASSWORD_HASH=`), `docs/OPERATIONS.md` (rotation procedure)

**Semantics:**
- Preferred: `ADMIN_EMAIL` + `ADMIN_PASSWORD_HASH` (bcrypt string generated offline: `python -c "import bcrypt; print(bcrypt.hashpw(b'...', bcrypt.gensalt()).decode())"`).
- CLI alt: reads `ADMIN_EMAIL` + `ADMIN_PASSWORD` from env/`getpass` in memory only, hashes, then:
  `INSERT INTO users(email,password_hash,is_admin,tier,subscription_status) VALUES (...) ON CONFLICT(email) DO UPDATE SET password_hash=EXCLUDED.password_hash, is_admin=true, tier='platinum', subscription_status='comped', updated_at=now()` + audit `admin.bootstrapped(actor='system')`.
- Why `comped` + `is_admin` bypass (not fake Stripe): Stripe stays source-of-truth for payers only — no fake `stripe_subscription_id`, no webhook spoof, no charge/refund churn. Entitlement = `if user.is_admin: allow all`.
- Rotation: set new hash in `.env` + rerun script (or `UPDATE users SET password_hash=... WHERE email=...`), restart backend. Never commit `.env`, never hardcode creds.
- Acceptance: fresh DB + script -> login as admin -> `GET /api/auth/me` shows `{tier:'platinum', is_admin:true, subscription_status:'comped'}` -> all tier gates pass, no Stripe objects created.

---

## Phase 6 — Compliant ads (AdSense visible only)

**Prereqs (before applying):** ship `/privacy /terms /about /contact /disclaimer` pages, SSL, GA4 + Search Console, 2-4 weeks original content. Finance is YMYL — AdSense rejects thin/scraped/login-walled SPAs.

**Files:**
- NEW `frontend/src/components/AdSlot.jsx` — visible container (`min-height` reserve to avoid CLS + `Advertisement` label + `aria-label`), `IntersectionObserver rootMargin ~200px` (only `push()/display()` near viewport), `useLocation().pathname` refresh (SPA navigation doesn't reload ads), props `{slotId, format, responsive, tier}`, dev `data-adtest=on`, `VITE_ADS_ENABLED=false` in dev collapses gracefully, error boundary (no retry loop)
- NEW `frontend/src/config/ads.js` — `AD_INTENSITY = {free: 3, silver: 2, gold: 1, platinum: 0}` (by **not mounting**, never hiding)
- EDIT `frontend/src/components/Layout.jsx` — leaderboard below header (1x, all routes) + footer in-feed above disclaimer (1x); never inside sticky header
- EDIT `frontend/src/pages/HomePage.jsx` — in-feed between TopSignals and watchlist/news grid (1x); never inside `CollapsibleSection defaultOpen=false`
- EDIT `SecurityBriefPage/ScreenerPage/ForecastDetailsPage` — max 1 in-article/below-filters slot each, never in table rows, max 1 per viewport (valuable-inventory policy)
- NEW `frontend/public/ads.txt` (`google.com, pub-XXXX, DIRECT` + intermediaries) -> ships to `dist/ads.txt`
- EDIT `frontend/index.html` — `preconnect` + `async` AdSense loader (`pagead2.googlesyndication.com`), Funding Choices/CMP stub; never bundle ad script via Vite
- EDIT `vercel.json` — add `headers:` frontend CSP allowing ad origins (`script-src 'self' https: pagead2... securepubads... googlesyndication... googletagservices... adservice... fundingschoices... cdn.consent-framework + 'unsafe-inline'`; `frame-src *.googlesyndication.com *.doubleclick.net *.google.com ...`; `img-src https: data:`; keep `frame-ancestors 'none'` on API). Do NOT copy API `default-src 'none'` to frontend.
- Consent: Google Funding Choices (TCF 2.2 CMP) + Consent Mode v2 + IAB GPP; block personalized load until consent, NPA/contextual fallback.
- Tests: `AdSlot.test.js` — unmounted for suppressed tiers (zero network requests), `min-height` reserve present, `adtest` in dev.
- Acceptance: Free sees <=3 visible labeled slots, Platinum sees 0 (DOM has no ad requests, not hidden nodes); Policy Center clean; no CLS regression; `ads.txt` crawler check passes.

**Provider ladder:** AdSense now -> Ezoic backfill if approval slow -> EthicalAds filler (zero CMP friction) -> later BuySellAds direct (fintech sponsors) -> GAM Small Business -> Monumetric (10k pv) -> Mediavine Journey/Mediavine (50k sessions). Carbon secondary only (dev-tool mismatch).

---

## File-by-file change list (execution order)

1. `backend/requirements.txt`, `api/requirements.txt` (+bcrypt, pyjwt, stripe, email-validator)
2. `infra/docker/.env.example`, `frontend/.env.example` (new keys, empty)
3. `infra/migrations/0008_users_auth.sql` + `supabase/migrations/0008_users_auth.sql`
4. `backend/db/models.py` (append `User`)
5. `backend/security/passwords.py` (new) + `backend/auth/guards.py` (new: `get_current_user`, `require_tier`, `require_admin`)
6. `backend/api/auth.py` (new) + `backend/api/main.py` (mount)
7. `backend/auth/tiers.py` (extend `FEATURE_MIN_TIER`)
8. Routers: `ai.py`, `forecast.py`, `analytics_api.py`, `backtest.py`, `screener.py`, `alerts.py`, `providers.py` (add `Depends`)
9. Cache keys: `ai/router.py`, `screener.py`, `market_index.py`, `news.py`, `markets.py`, `market_data/service.py` (scope `u:{id}:t:{tier}`) + `db/writers.py` (real user/tier)
10. `scripts/bootstrap_admin.py` (new)
11. `backend/api/billing.py` (new) + `main.py` (mount + webhook exempt)
12. Frontend: `api/auth.js` (new), `client.js` (Bearer injection), `hooks/useAuth.js` (new), `components/RequireTier.jsx` (new), `App.jsx` (real login/pricing/account routes), `pages/PricingPage.jsx` + `CheckoutSuccessPage.jsx` + `AccountPage.jsx` + `LoginPage.jsx` (new)
13. Ads: `components/AdSlot.jsx` (new), `config/ads.js` (new), `Layout.jsx`, `HomePage.jsx`, brief/screener/forecast pages, `public/ads.txt` (new), `index.html`, `vercel.json` (CSP headers)
14. Docs: `DB_SCHEMA.md`, `OPERATIONS.md`, `API_CONTRACT.md` (new auth/billing appendix), `SECURITY.md` (password/JWT/webhook policy)
15. Tests: `test_auth.py`, `test_tier_gates.py`, `test_billing.py`, `AdSlot.test.js`, `auth.test.js`

---

## Verification (run after each phase)

```powershell
cd "C:\Users\thinu\OneDrive\Pictures\Documents\STOCK ANALYSIS MAIN\TerminalStockUI"
# DB
psql "postgresql://onemarket:<pw>@localhost:5432/onemarket" -f infra\migrations\0008_users_auth.sql
# backend
python -m pytest backend/tests/test_auth.py backend/tests/test_tier_gates.py backend/tests/test_billing.py -q -p no:cacheprovider
python scripts/verify_v1.py  # must stay 16 PASS, 2 PARTIAL, 0 FAIL (no V1 regression)
# frontend
cd frontend; npm test -- --run; npm run build
# E2E manual
python scripts/bootstrap_admin.py  # -> admin platinum comped
# register free user -> free->deep_research 402, tampered X-Tier still 402
# Stripe CLI test-mode checkout -> webhook upgrades -> portal cancel downgrades
# ads: VITE_ADS_ENABLED=false dev shows no requests; prod Free <=3 visible slots, Platinum 0 nodes
```

---

## Rollout + acceptance for V2 DONE

1. Fresh DB + `0001..0008` applies clean; RLS anon reads 0.
2. Register->login->me works; wrong password 401s; no plaintext secrets.
3. `curl` with low-tier token to high-tier endpoint -> `402` even with spoofed headers/body.
4. Stripe test checkout upgrades via webhook only; cancel downgrades; sig-less webhook 400s; replay idempotent.
5. Admin bootstrap -> `platinum/comped/is_admin` with zero Stripe objects; all gates pass.
6. Ads: visible labeled slots only, per-tier counts by not-mounting, `ads.txt` + CMP + CSP live, Policy Center clean.
7. `verify_v1.py` + full `pytest` + `npm test` + `npm run build` all green (no V1 regression).
