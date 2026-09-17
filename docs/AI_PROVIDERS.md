# OneMarket Analyzer — AI providers (M4/M5)

Spec §§1–2, 5 (M4/M5), §6 testing, §7 definition of done. AI adds **structured
event analysis and bounded forecast opinions only**. Deterministic analytics
and forecasting are the source of truth and work fully with AI disabled.

## Supported providers

| Provider | Key name in store | Default models | Notes |
|---|---|---|---|
| `gemini` | `api_key` | `gemini-3.7-flash` (free-first default) | Free-first operation; default for all profiles until budgets change |
| `openai` | `api_key` | operator-configured | Drop-in via the same interface; zero analytics/frontend changes |
| `anthropic` | `api_key` | operator-configured | Same |
| `xai` | `api_key` | operator-configured | Same |

Provider swap requires zero analytics/frontend changes (M4 acceptance): all
providers implement one interface (`generate(evidence_packet) -> strict-schema
JSON`), one evidence builder, one validator.

## Task profiles

| Profile | Identifier | Typical provider/model | Budget guidance |
|---|---|---|---|
| Quick Insight | `quick_insight` | `gemini` / `gemini-3.7-flash` | Small evidence packet, low randomness, cached by evidence hash |
| Forecast Assist | `forecast_assist` | `gemini` / `gemini-3.7-flash` | Bounded opinion only (`time_horizon_days` ∈ 1/7/14/21); `ai_weight ≤ 0.20` |
| Deep Research | `deep_research` | operator-selected | Larger packet; still within evidence limits below |
| Report | `report` | operator-selected (scheduled allowed) | Scheduled reports are the only non-explicit call path |

AI is called only on explicit request or scheduled report — never implicitly
per page view.

## Key setup (encrypted, never plaintext)

- Keys are stored server-side via `backend/security/secrets.py`
  (`EncryptedSecretStore`): ciphertext at rest (Fernet over `SECRET_KEY`),
  decrypted **only at call time**, never returned via any API (`describe()`
  exposes names only), redacted in logs and audit payloads (`redact_mapping` /
  `redact_string`, covered by `test_security.py`, `test_audit.py`).
- Setup:
  ```powershell
  # 1. put a strong SECRET_KEY in infra/docker/.env (never commit .env)
  # SECRET_KEY=<base64-urlsafe-32B>  (any string works; it is hashed to a Fernet key)
  # 2. store the provider key through the settings flow (server-side only):
  #    settings.put("gemini", "api_key", "<paste-once, never logged>")
  ```
- Rules: **no plaintext keys anywhere** — not in code, docs, logs, audit rows,
  evidence packets, or the browser. Audit rows are hash-chained, so a leaked
  key could never be removed; the append path redacts before hashing.
- Rotation: overwrite via `put(provider, "api_key", ...)`; old ciphertext is
  discarded. If `SECRET_KEY` changes, re-store all keys (decrypt raises
  `ValueError: cannot decrypt secret with current key` otherwise).

## Weight-cap policy (fixed for v1)

- `ai_weight ≤ 0.20`, **server-enforced** on every forecast row
  (`CHECK (ai_weight >= 0 AND ai_weight <= 0.20)`); values above 0.20 are
  rejected, not clamped silently.
- Not user-adjustable beyond safe presets (e.g. `0` = AI off, `≤0.20` = assist).
  Disabling AI leaves `/forecast` intact with `ai_weight = 0`.
- Learned weighting only after sufficient out-of-sample evidence, tracked per
  provider/model/exchange/horizon at `GET /api/ai/providers/performance`.
- AI may raise, lower, or leave unchanged the displayed confidence but never
  overrides the quantitative core; disagreement lowers confidence.

## Evidence packet limits (what the model may see)

Included: metadata (instrument, exchange, currency), quality/freshness grades,
deterministic outputs (indicators, forecast, calibration), **top 5 bullish
signals, top 5 risks**, material events (earnings/dividends/splits/filings),
limitations. Each item carries an `evidence_id` the opinion must cite.

Never included: raw candles, full financial statements, API keys or secrets,
user PII, or anything outside the packet (claims without `evidence_ids` are
rejected with `422 AI_VALIDATION_FAILED`).

Enforced limits: ≤5 bullish + ≤5 risks, bounded catalyst/risk string lengths,
low randomness (temperature pinned), response caching by evidence hash, token
usage logged per call, prompts + AI schemas versioned with the forecast row.

## Failure modes

| Failure | Behavior |
|---|---|
| Malformed provider JSON / missing `evidence_ids` / bad probability/horizon | Reject with `422 AI_VALIDATION_FAILED`; deterministic forecast stands; event audit-logged |
| Provider timeout / 5xx / rate limit | `502/429` with `retryable: true`; serve deterministic forecast; circuit breaker may open (`provider.circuit_open` audit event) |
| No key configured | `409 AI_DISABLED` on AI endpoints only; `/forecast`, analytics, backtest unaffected |
| Key decryption failure | `ValueError` server-side, never surfaced with key material; operator re-stores the key |
| High AI disagreement vs quantitative core | Confidence downgraded; opinion kept in the log with its weight for the scoreboard |
| Any AI outage | Full app runs with AI empty (spec §7 item 7); acceptance tests cover the disabled path |
