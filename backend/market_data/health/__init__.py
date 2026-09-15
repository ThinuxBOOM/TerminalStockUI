"""Provider health: latency/error tracking, freshness, reconciliation.

Backed in-memory here; Redis-backed in production (same interface).
Grades follow docs/DATA_QUALITY.md; market states: open|closed|delayed|stale.

Health schema (per provider, backward-compatible + enriched):
    {
      "provider": str,               # legacy key, always present
      "kind": "data" | "ai" | "unknown",
      "state": "up"|"degraded"|"down"|"unknown"|"unconfigured",
      "circuit": "closed"|"open"|"half-open",
      "latency_p50_ms": float, "latency_p95_ms": float,
      "error_rate_1h": float, "error_rate_5m": float,
      "calls_1h": int, "calls_5m": int, "total_calls": int,
      "last_check": iso|None, "last_success": iso|None,
      "consecutive_failures": int,
      "quota": {"limited": bool, "reason": str|None,
                "status_code": int|None, "updated_at": iso|None,
                "auth_required": bool},
    }

Breaker thresholds (per-provider, passive + active share one tracker):
    BREAKER_FAILURE_THRESHOLD = 5  (consecutive non-quota fails, or >=5
        non-quota fails inside BREAKER_WINDOW_S)
    BREAKER_WINDOW_S = 300.0       (5 minutes)
    BREAKER_OPEN_S = 60.0          (cooldown; open -> half-open probe)

Quota rule: HTTP 429 / "rate limited" / "quota exceeded" -> degraded,
never down. Quota-limited failures do NOT trip the breaker and do NOT
increment consecutive_failures. Auth errors (401/403) and AI
"unconfigured" (no key) are likewise degraded/unconfigured, not down.

Tier-aware future-proofing (stub only, no subscriptions implemented):
    # TODO(tiers): when subscriptions exist, parse per-tier quota headers
    # (X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset,
    #  plus vendor-specific e.g. x-ratelimit-*) per provider tier
    #  (free vs authenticated) and surface remaining quota in
    #  quota.remaining / quota.reset_at. Currently quota tracks only
    #  limited/reason/status/updated_at.
"""

from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from backend.instruments.calendars import market_state_at

log = logging.getLogger(__name__)

#: Staleness rule (shared with grade_quality in market_data.quality):
#: data older than STALE_MULTIPLE x the expected feed delay is "stale".
STALE_MULTIPLE = 2

#: All providers with first-class health rows (dashboard guarantee).
#: Core set per spec: yfinance/akshare/alpaca/stooq/fx + AI
#: (gemini/openai/anthropic/xai); finnhub/twelvedata free-tier gap-fillers
#: included additively (same envelope, kind=data).
KNOWN_PROVIDERS: tuple[str, ...] = (
    "yfinance",
    "akshare",
    "alpaca",
    "stooq",
    "finnhub",
    "twelvedata",
    "fx",
    "gemini",
    "openai",
    "anthropic",
    "xai",
)
DATA_PROVIDERS: tuple[str, ...] = (
    "yfinance",
    "akshare",
    "alpaca",
    "stooq",
    "finnhub",
    "twelvedata",
    "fx",
)
AI_PROVIDERS: tuple[str, ...] = ("gemini", "openai", "anthropic", "xai")

#: Per-provider circuit breaker thresholds.
BREAKER_FAILURE_THRESHOLD = 5
BREAKER_WINDOW_S = 300.0
BREAKER_OPEN_S = 60.0

#: State degrade threshold (mirrors observability ERROR_THRESHOLD_DEFAULT).
HEALTH_ERROR_THRESHOLD = 0.05

#: Lightweight active-probe inputs (no costly calls: single quote / FX pair).
PROBE_SYMBOL = "AAPL"
FX_PROBE_BASE = "EUR"
FX_PROBE_QUOTE = "USD"

#: Providers that require a configured credential (authenticated tier).
#: Free-tier: yfinance / akshare / stooq / fx (no key). finnhub/twelvedata
#: free tiers require a (free) API key, so they count as authenticated.
#: Used for authenticated-vs-free quota messaging and quota.auth_required.
AUTH_REQUIRED_PROVIDERS = frozenset({
    "alpaca", "finnhub", "twelvedata",
    "gemini", "openai", "anthropic", "xai",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            return dt.isoformat()
        except Exception:
            return str(value)
    try:
        return str(value)
    except Exception:
        return None


def provider_kind(name: str) -> str:
    """Distinct kind for data vs AI providers (frontend grouping)."""
    try:
        key = str(name or "").strip().lower()
    except Exception:
        return "unknown"
    if key in AI_PROVIDERS:
        return "ai"
    if key in DATA_PROVIDERS:
        return "data"
    return "unknown"


def _parse_status_code(status_code) -> int | None:
    try:
        if status_code is None or isinstance(status_code, bool):
            return None
        code = int(status_code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if 100 <= code <= 599:
        return code
    return None


def classify_provider_error(error=None, status_code=None) -> tuple[bool, str | None, int | None]:
    """Classify a provider failure for quota-aware health.

    Returns (is_quota_limited, reason, status_int) where reason is one of
    None | "rate_limited" | "quota_exceeded" | "auth_error" | "unconfigured".
    429 (or rate-limit/quota wording) -> quota-limited (degraded, not down).
    401/403 auth failures and missing-key/unconfigured -> auth/unconfigured
    (also degraded/unconfigured, never down, never trips the breaker).
    """
    status = _parse_status_code(status_code)
    text = ""
    try:
        text = str(error or "").lower()
    except Exception:
        text = ""
    if status == 429:
        if "quota" in text or "exceed" in text:
            return True, "quota_exceeded", status
        return True, "rate_limited", status
    if status in (401, 403):
        return True, "auth_error", status
    if "429" in text:
        if "quota" in text or "exceed" in text:
            return True, "quota_exceeded", 429
        return True, "rate_limited", 429
    if "rate limit" in text or "ratelimit" in text or "too many requests" in text:
        if "quota" in text or "exceed" in text:
            return True, "quota_exceeded", status or 429
        return True, "rate_limited", status or 429
    if "quota" in text and ("exceed" in text or "exhaust" in text or "limit" in text):
        return True, "quota_exceeded", status or 429
    if any(k in text for k in ("unconfigured", "no api key", "no api_key", "api keys missing", "keys missing", "missing keys", "not configured")):
        return True, "unconfigured", status
    if any(k in text for k in ("unauthorized", "forbidden", "invalid key", "invalid api key", "bad key", "check alpaca keys")):
        return True, "auth_error", status
    return False, None, status


class ProviderHealthTracker:
    """Rolling per-provider call stats (latency ms, ok flag, timestamp).

    Passive metrics flow in via record() from every provider ``_emit`` hook;
    active probing (probe_provider/probe_all_providers below) records the
    same way with richer error/status context. Circuit state is derived
    per-provider: closed -> open after BREAKER_FAILURE_THRESHOLD
    consecutive (non-quota) failures or >=threshold non-quota fails inside
    BREAKER_WINDOW_S; open -> half-open after BREAKER_OPEN_S cooldown.
    """

    def __init__(
        self,
        max_samples: int = 500,
        failure_threshold: int = BREAKER_FAILURE_THRESHOLD,
        open_cooldown_s: float = BREAKER_OPEN_S,
        window_s: float = BREAKER_WINDOW_S,
    ) -> None:
        self._calls: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_samples))
        self._circuits: dict[str, str] = {}
        self._consecutive: dict[str, int] = {}
        self._last_success: dict[str, datetime] = {}
        self._quota: dict[str, dict] = {}
        self._opened_at: dict[str, float] = {}
        try:
            self._failure_threshold = max(1, int(failure_threshold))
        except (TypeError, ValueError):
            self._failure_threshold = BREAKER_FAILURE_THRESHOLD
        try:
            self._open_cooldown_s = max(1.0, float(open_cooldown_s))
        except (TypeError, ValueError):
            self._open_cooldown_s = BREAKER_OPEN_S
        try:
            self._window_s = max(60.0, float(window_s))
        except (TypeError, ValueError):
            self._window_s = BREAKER_WINDOW_S

    def record(
        self,
        provider: str,
        latency_ms: float,
        ok: bool,
        *,
        status_code=None,
        error=None,
        quota_limited: bool | None = None,
    ) -> None:
        try:
            key = str(provider or "unknown")
        except Exception:
            key = "unknown"
        try:
            latency = float(latency_ms or 0.0)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            latency = 0.0
        if not math.isfinite(latency) or latency < 0:
            latency = 0.0
        if key not in self._calls and len(self._calls) >= 64:
            try:
                self._calls.pop(next(iter(self._calls)), None)
            except Exception:
                pass
        ok_bool = bool(ok)
        status = _parse_status_code(status_code)
        if quota_limited is True:
            is_quota, reason, _ = True, "rate_limited", status
            try:
                auto_quota, auto_reason, _ = classify_provider_error(error, status)
                if auto_quota and auto_reason:
                    reason = auto_reason
            except Exception:
                pass
        elif quota_limited is False:
            is_quota, reason = False, None
        else:
            try:
                is_quota, reason, status = classify_provider_error(error, status)
            except Exception:
                is_quota, reason = False, None
        err_text = None
        if error is not None:
            try:
                err_text = str(error)[:280] or None
            except Exception:
                err_text = None
        self._calls[key].append(
            {"t": _utcnow(), "latency_ms": latency, "ok": ok_bool,
             "status_code": status, "error": err_text,
             "quota": bool(is_quota)}
        )
        if ok_bool:
            self._consecutive[key] = 0
            try:
                self._last_success[key] = _utcnow()
            except Exception:
                pass
            if key in self._quota:
                try:
                    if bool(self._quota[key].get("limited")):
                        self._quota.pop(key, None)
                except Exception:
                    pass
            try:
                if self._circuits.get(key) == "half-open":
                    self._circuits[key] = "closed"
                    self._opened_at.pop(key, None)
            except Exception:
                pass
            return
        if is_quota:
            try:
                self._quota[key] = {
                    "limited": True,
                    "reason": reason or "rate_limited",
                    "status_code": status,
                    "updated_at": _utcnow(),
                    "auth_required": key in AUTH_REQUIRED_PROVIDERS,
                }
            except Exception:
                pass
            return
        try:
            self._consecutive[key] = int(self._consecutive.get(key, 0)) + 1
        except Exception:
            self._consecutive[key] = 1
        try:
            if self._circuits.get(key) == "half-open":
                self._circuits[key] = "open"
                self._opened_at[key] = time.monotonic()
                self._emit_alert(key, "circuit_open", "half-open probe failed; breaker reopened")
                return
        except Exception:
            pass
        self._evaluate_breaker(key)

    def _window_failures(self, key: str) -> int:
        try:
            calls = list(self._calls.get(key, []))
        except Exception:
            return 0
        try:
            now_ts = _utcnow().timestamp()
        except Exception:
            return 0
        fails = 0
        for c in calls:
            try:
                if bool(c.get("ok", True)) or bool(c.get("quota", False)):
                    continue
                ts = c.get("t")
                if ts is None:
                    fails += 1
                    continue
                if isinstance(ts, datetime):
                    t_ts = ts.timestamp()
                else:
                    t_ts = float(ts)  # type: ignore[arg-type]
                if now_ts - t_ts <= self._window_s:
                    fails += 1
            except Exception:
                continue
        return fails

    def _evaluate_breaker(self, key: str) -> None:
        try:
            if self._circuits.get(key) == "open":
                return
            consecutive = int(self._consecutive.get(key, 0))
        except Exception:
            consecutive = 0
        if consecutive >= self._failure_threshold:
            self._open_breaker(key, f"{consecutive} consecutive failures >= {self._failure_threshold}")
            return
        try:
            if self._window_failures(key) >= self._failure_threshold:
                self._open_breaker(key, f">= {self._failure_threshold} fails / {int(self._window_s // 60)}m window")
        except Exception:
            pass

    def _open_breaker(self, key: str, why: str) -> None:
        try:
            self._circuits[key] = "open"
            self._opened_at[key] = time.monotonic()
        except Exception:
            pass
        self._emit_alert(key, "circuit_open", f"breaker open ({why}); serving fallback/cached data")

    def _emit_alert(self, provider: str, reason: str, message: str) -> None:
        """Alert hook: log + best-effort audit event. Never raises."""
        try:
            log.warning("provider health alert provider=%s reason=%s %s", provider, reason, message)
        except Exception:
            pass
        try:
            from backend.api.audit import append_audit_log
            from backend.db.session import get_session_factory

            db = get_session_factory()()
            try:
                append_audit_log(
                    db,
                    actor="system",
                    action="provider.health_alert",
                    entity_type="provider",
                    entity_id=str(provider),
                    payload={"provider": str(provider), "reason": reason, "message": message[:280]},
                )
            finally:
                try:
                    db.close()
                except Exception:
                    pass
        except Exception:
            pass

    def set_circuit(self, provider: str, state: str) -> None:
        try:
            key = str(provider or "unknown")
        except Exception:
            key = "unknown"
        if key not in self._circuits and len(self._circuits) >= 64:
            try:
                self._circuits.pop(next(iter(self._circuits)), None)
            except Exception:
                pass
        try:
            norm = str(state or "closed").strip().lower().replace("_", "-")
        except Exception:
            norm = "closed"
        if norm not in ("closed", "open", "half-open"):
            norm = "closed"
        self._circuits[key] = norm
        try:
            if norm == "open":
                self._opened_at.setdefault(key, time.monotonic())
            elif norm == "closed":
                self._opened_at.pop(key, None)
                self._consecutive[key] = 0
        except Exception:
            pass

    def get_circuit(self, provider: str) -> str:
        try:
            key = str(provider)
        except Exception:
            return "closed"
        try:
            state = self._circuits.get(key, "closed")
        except Exception:
            return "closed"
        if state == "open":
            try:
                opened = self._opened_at.get(key)
                if opened is not None and (time.monotonic() - float(opened)) >= self._open_cooldown_s:
                    self._circuits[key] = "half-open"
                    return "half-open"
            except Exception:
                pass
            return "open"
        if state in ("half-open", "half_open"):
            return "half-open"
        return "closed"

    def record_quota(
        self,
        provider: str,
        *,
        limited: bool,
        reason: str | None = None,
        status_code=None,
    ) -> None:
        try:
            key = str(provider or "unknown")
        except Exception:
            key = "unknown"
        if not limited:
            try:
                self._quota.pop(key, None)
            except Exception:
                pass
            return
        try:
            self._quota[key] = {
                "limited": True,
                "reason": reason or "rate_limited",
                "status_code": _parse_status_code(status_code),
                "updated_at": _utcnow(),
                "auth_required": key in AUTH_REQUIRED_PROVIDERS,
            }
        except Exception:
            pass

    def stats(self, provider: str) -> dict:
        try:
            name = str(provider)
        except Exception:
            name = "unknown"
        calls = list(self._calls.get(name, []))
        lat = sorted(c["latency_ms"] for c in calls)
        if lat:
            p50 = statistics.median(lat)
            idx95 = min(len(lat) - 1, int(len(lat) * 0.95))
            p95 = lat[idx95]
        else:
            # No samples: None, never 0.0. A zero latency was never measured
            # and rendering "0ms" for an uncalled provider is fabricated data.
            p50, p95 = None, None
        try:
            now_ts = _utcnow().timestamp()
        except Exception:
            now_ts = 0.0
        recent_1h: list[dict] = []
        recent_5m: list[dict] = []
        for c in calls:
            try:
                ts = c.get("t")
                if ts is None:
                    recent_1h.append(c)
                    recent_5m.append(c)
                    continue
                t_ts = ts.timestamp() if isinstance(ts, datetime) else float(ts)  # type: ignore[arg-type]
            except Exception:
                continue
            if now_ts - t_ts <= 3600:
                recent_1h.append(c)
            if now_ts - t_ts <= 300:
                recent_5m.append(c)
        errors_1h = sum(1 for c in recent_1h if not c.get("ok", True))
        errors_5m = sum(1 for c in recent_5m if not c.get("ok", True))
        circuit = self.get_circuit(name)
        last_check = calls[-1]["t"].isoformat() if calls else None
        last_success = None
        try:
            marker = self._last_success.get(name)
            if marker is not None:
                last_success = marker.isoformat()
            else:
                for c in reversed(calls):
                    if c.get("ok"):
                        t = c.get("t")
                        last_success = t.isoformat() if isinstance(t, datetime) else (str(t) if t is not None else None)
                        break
        except Exception:
            last_success = None
        try:
            consecutive = int(self._consecutive.get(name, 0))
        except Exception:
            consecutive = 0
        quota_raw = self._quota.get(name)
        if isinstance(quota_raw, dict) and bool(quota_raw.get("limited")):
            quota = {
                "limited": True,
                "reason": quota_raw.get("reason"),
                "status_code": quota_raw.get("status_code"),
                "updated_at": _iso_or_none(quota_raw.get("updated_at")),
                "auth_required": bool(quota_raw.get("auth_required", name in AUTH_REQUIRED_PROVIDERS)),
            }
            quota_limited = True
            quota_reason = quota.get("reason")
        else:
            quota = {
                "limited": False,
                "reason": None,
                "status_code": None,
                "updated_at": None,
                "auth_required": bool(name in AUTH_REQUIRED_PROVIDERS),
            }
            quota_limited = False
            quota_reason = None
        kind = provider_kind(name)
        error_1h = round(errors_1h / len(recent_1h), 4) if recent_1h else 0.0
        error_5m = round(errors_5m / len(recent_5m), 4) if recent_5m else 0.0
        state = _derive_state(
            kind=kind,
            circuit=circuit,
            quota_limited=quota_limited,
            quota_reason=quota_reason,
            error_5m=error_5m,
            calls_5m=len(recent_5m),
            error_1h=error_1h,
            calls_1h=len(recent_1h),
            total=len(calls),
        )
        return {
            "provider": name,
            "kind": kind,
            "state": state,
            "latency_p50_ms": round(p50, 1) if isinstance(p50, (int, float)) else None,
            "latency_p95_ms": round(p95, 1) if isinstance(p95, (int, float)) else None,
            "error_rate_1h": error_1h,
            "error_rate_5m": error_5m,
            "calls_1h": len(recent_1h),
            "calls_5m": len(recent_5m),
            "total_calls": len(calls),
            "circuit": circuit,
            "last_check": last_check,
            "last_success": last_success,
            "consecutive_failures": consecutive,
            "quota": quota,
        }

    def all_stats(self) -> list[dict]:
        providers = set(list(self._calls) + list(self._circuits))
        return [self.stats(p) for p in sorted(providers)]


def _derive_state(
    *,
    kind: str,
    circuit: str,
    quota_limited: bool,
    quota_reason,
    error_5m: float,
    calls_5m: int,
    error_1h: float,
    calls_1h: int,
    total: int,
) -> str:
    """Map breaker + quota + error windows to a UI-facing state.

    Data vs AI distinction: AI providers surface ``unconfigured`` when the
    quota marker says so (no key); data providers never report unconfigured
    (missing free-tier fetch is down/degraded instead).
    """
    try:
        reason = str(quota_reason or "").lower()
    except Exception:
        reason = ""
    if total == 0:
        if quota_limited and reason == "unconfigured" and kind == "ai":
            return "unconfigured"
        return "unknown"
    if circuit == "open":
        return "degraded" if quota_limited else "down"
    if circuit == "half-open":
        return "degraded"
    if quota_limited:
        if reason == "unconfigured" and kind == "ai":
            return "unconfigured"
        return "degraded"
    try:
        if calls_5m >= 5 and float(error_5m) > HEALTH_ERROR_THRESHOLD:
            return "degraded"
        if calls_5m == 0 and calls_1h >= 5 and float(error_1h) > HEALTH_ERROR_THRESHOLD:
            return "degraded"
        if calls_5m > 0 and float(error_5m) >= 1.0:
            return "degraded"
    except Exception:
        pass
    return "up"


def _probe_record(
    tracker,
    provider: str,
    latency_ms: float,
    ok: bool,
    *,
    status_code=None,
    error=None,
    quota_limited: bool | None = None,
) -> None:
    if tracker is None:
        return
    try:
        rec = getattr(tracker, "record", None)
        if rec is None:
            return
        try:
            rec(provider, float(latency_ms), bool(ok),
                status_code=status_code, error=error, quota_limited=quota_limited)
        except TypeError:
            rec(provider, float(latency_ms), bool(ok))
    except Exception:
        pass


def probe_data_provider(name: str, tracker=None, *, timeout_s: float = 5.0) -> dict:
    """Lightweight quote/FX ping for one market-data provider.

    Single-symbol quote (AAPL) or single FX pair (EUR/USD); no history
    fan-out, no costly calls. Returns a result dict and records
    passive-compatible metrics into ``tracker`` (when given).
    """
    import time as _time

    key = (name or "").strip().lower() or "yfinance"
    started = _time.perf_counter()
    latency = 0.0
    try:
        timeout = max(1.0, min(15.0, float(timeout_s)))
    except (TypeError, ValueError):
        timeout = 5.0
    if key == "fx":
        try:
            from backend.market_data.fx.provider import FXProvider

            fx = FXProvider()
            try:
                fx.cache_ttl_s = 0
            except Exception:
                pass
            entry = fx.get_rate(FX_PROBE_BASE, FX_PROBE_QUOTE)
            latency = (_time.perf_counter() - started) * 1000.0
            fallback = bool(entry.get("fallback_used", False))
            ok = not fallback
            err = None if ok else "fx probe fallback (upstream unavailable)"
            _probe_record(tracker, "fx", latency, ok, error=err)
            if tracker is not None:
                try:
                    return tracker.stats("fx")
                except Exception:
                    pass
            return {"provider": "fx", "ok": ok, "latency_ms": round(latency, 1), "error": err}
        except Exception as exc:
            latency = (_time.perf_counter() - started) * 1000.0
            _probe_record(tracker, "fx", latency, False, error=f"{type(exc).__name__}: {exc}")
            if tracker is not None:
                try:
                    return tracker.stats("fx")
                except Exception:
                    pass
            return {"provider": "fx", "ok": False, "latency_ms": round(latency, 1),
                    "error": f"{type(exc).__name__}: {exc}"[:280]}
    try:
        provider = _build_data_provider(key)
    except ValueError as exc:
        latency = (_time.perf_counter() - started) * 1000.0
        return {"provider": key, "ok": False, "latency_ms": round(latency, 1), "error": str(exc)[:280]}
    except Exception as exc:
        latency = (_time.perf_counter() - started) * 1000.0
        _probe_record(tracker, key, latency, False, error=f"{type(exc).__name__}: {exc}")
        return {"provider": key, "ok": False, "latency_ms": round(latency, 1),
                "error": f"{type(exc).__name__}: {exc}"[:280]}
    try:
        _ = timeout
        quote = provider.get_quote(PROBE_SYMBOL)
    except Exception as exc:
        latency = (_time.perf_counter() - started) * 1000.0
        msg = f"{type(exc).__name__}: {exc}"
        try:
            is_quota, _reason, status = classify_provider_error(msg)
        except Exception:
            is_quota, status = False, None
        _probe_record(tracker, key, latency, False, error=msg, status_code=status,
                      quota_limited=True if is_quota else None)
        if tracker is not None:
            try:
                return tracker.stats(key)
            except Exception:
                pass
        return {"provider": key, "ok": False, "latency_ms": round(latency, 1), "error": msg[:280]}
    latency = (_time.perf_counter() - started) * 1000.0
    try:
        fallback = bool(quote.get("fallback_used", False))
        circuit_open = bool(quote.get("circuit_open", False))
    except Exception:
        fallback, circuit_open = True, False
    ok = not fallback
    err = None
    if not ok:
        err = "probe fallback (upstream unavailable)" + ("; circuit open" if circuit_open else "")
    _probe_record(tracker, key, latency, ok, error=err)
    if tracker is not None:
        try:
            return tracker.stats(key)
        except Exception:
            pass
    return {"provider": key, "ok": ok, "latency_ms": round(latency, 1), "error": err}


def _build_data_provider(key: str):
    """Instantiate a market-data provider for probing (no health hook)."""
    if key == "yfinance":
        from backend.market_data.providers.yfinance import YFinanceProvider

        return YFinanceProvider(on_call=None)
    if key == "akshare":
        try:
            from backend.market_data.providers.akshare import AKShareProvider
        except Exception as exc:
            raise ValueError(f"akshare unavailable: {exc}") from exc
        if AKShareProvider is None:  # type: ignore[truthy-function]
            raise ValueError("akshare unavailable")
        return AKShareProvider(on_call=None)
    if key == "alpaca":
        try:
            from backend.market_data.providers.alpaca import AlpacaProvider
        except Exception as exc:
            raise ValueError(f"alpaca unavailable: {exc}") from exc
        if AlpacaProvider is None:  # type: ignore[truthy-function]
            raise ValueError("alpaca unavailable")
        return AlpacaProvider(on_call=None)
    if key == "stooq":
        try:
            from backend.market_data.providers.stooq import StooqProvider
        except Exception as exc:
            raise ValueError(f"stooq unavailable: {exc}") from exc
        if StooqProvider is None:  # type: ignore[truthy-function]
            raise ValueError("stooq unavailable")
        return StooqProvider(on_call=None)
    if key == "finnhub":
        try:
            from backend.market_data.providers.finnhub_free import FinnhubProvider
        except Exception as exc:
            raise ValueError(f"finnhub unavailable: {exc}") from exc
        return FinnhubProvider(on_call=None)
    if key == "twelvedata":
        try:
            from backend.market_data.providers.twelvedata_free import TwelveDataProvider
        except Exception as exc:
            raise ValueError(f"twelvedata unavailable: {exc}") from exc
        return TwelveDataProvider(on_call=None)
    raise ValueError(f"unknown data provider; expected one of {list(DATA_PROVIDERS)}")


_AI_PING_PATHS: dict[str, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "xai": "https://api.x.ai/v1/models",
}


def probe_ai_provider(name: str, tracker=None, *, timeout_s: float = 5.0) -> dict:
    """Lightweight AI model-list ping (no completion = no costly call).

    Unconfigured (no key) short-circuits without network and records an
    ``unconfigured`` quota marker (state ``unconfigured``, not down).
    HTTP 429 records quota-limited (degraded, not down); 401/403 records
    auth_error (degraded). Never raises, never logs key material.
    """
    import time as _time

    key = (name or "").strip().lower()
    if key not in AI_PROVIDERS:
        # Unknown provider: no work was done, so there is no latency to
        # report (None, never a fabricated 0.0).
        return {"provider": key or "unknown", "ok": False, "latency_ms": None,
                "error": f"unknown AI provider; expected one of {list(AI_PROVIDERS)}"}
    started = _time.perf_counter()
    try:
        timeout = max(1.0, min(15.0, float(timeout_s)))
    except (TypeError, ValueError):
        timeout = 5.0
    api_key: str | None = None
    model = ""
    try:
        from backend.ai.providers import PROVIDER_CLASSES

        cls = PROVIDER_CLASSES.get(key)
        inst = None
        if cls is not None:
            try:
                inst = cls()
            except Exception:
                inst = None
        if inst is not None:
            try:
                model = str(getattr(inst, "model", "") or "")
            except Exception:
                model = ""
            try:
                api_key = inst._load_api_key()  # type: ignore[attr-defined]
            except Exception:
                api_key = None
    except Exception:
        api_key = None
    if not (isinstance(api_key, str) and api_key.strip()):
        latency = (_time.perf_counter() - started) * 1000.0
        _probe_record(tracker, key, latency, False, error="unconfigured (no API key)",
                      quota_limited=True)
        try:
            if tracker is not None and hasattr(tracker, "record_quota"):
                tracker.record_quota(key, limited=True, reason="unconfigured")
        except Exception:
            pass
        if tracker is not None:
            try:
                return tracker.stats(key)
            except Exception:
                pass
        out = {"provider": key, "ok": False, "latency_ms": round(latency, 1),
               "error": "unconfigured (no API key)", "configured": False}
        if model:
            out["model"] = model
        return out
    url = _AI_PING_PATHS[key]
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if key == "gemini":
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        params = {"pageSize": "1"}
    elif key == "openai":
        headers = {"Authorization": f"Bearer {api_key}"}
    elif key == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    elif key == "xai":
        headers = {"Authorization": f"Bearer {api_key}"}
    api_key = ""
    try:
        import httpx  # type: ignore[import-not-found]
    except Exception as exc:
        latency = (_time.perf_counter() - started) * 1000.0
        _probe_record(tracker, key, latency, False, error=f"httpx unavailable: {type(exc).__name__}")
        if tracker is not None:
            try:
                return tracker.stats(key)
            except Exception:
                pass
        return {"provider": key, "ok": False, "latency_ms": round(latency, 1),
                "error": "httpx unavailable", "configured": True}
    try:
        resp = httpx.get(url, headers=headers, params=params or None, timeout=timeout)
        latency = (_time.perf_counter() - started) * 1000.0
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        if 200 <= status < 300:
            _probe_record(tracker, key, latency, True)
            if tracker is not None:
                try:
                    return tracker.stats(key)
                except Exception:
                    pass
            out = {"provider": key, "ok": True, "latency_ms": round(latency, 1),
                   "configured": True, "status_code": status}
            if model:
                out["model"] = model
            return out
        try:
            body = (resp.text or "")[:180]
        except Exception:
            body = ""
        msg = f"provider HTTP {status}: {body}".strip()
        try:
            is_quota, _reason, _ = classify_provider_error(msg, status)
        except Exception:
            is_quota = status in (401, 403, 429)
        _probe_record(tracker, key, latency, False, error=msg, status_code=status,
                      quota_limited=True if is_quota else None)
        if tracker is not None:
            try:
                return tracker.stats(key)
            except Exception:
                pass
        out = {"provider": key, "ok": False, "latency_ms": round(latency, 1),
               "error": msg[:280], "configured": True, "status_code": status}
        if model:
            out["model"] = model
        return out
    except Exception as exc:
        latency = (_time.perf_counter() - started) * 1000.0
        msg = f"{type(exc).__name__}: {exc}"
        try:
            is_quota, _reason, _ = classify_provider_error(msg)
        except Exception:
            is_quota = False
        _probe_record(tracker, key, latency, False, error=msg,
                      quota_limited=True if is_quota else None)
        if tracker is not None:
            try:
                return tracker.stats(key)
            except Exception:
                pass
        return {"provider": key, "ok": False, "latency_ms": round(latency, 1),
                "error": msg[:280], "configured": True}


def probe_provider(name: str, tracker=None, *, timeout_s: float = 5.0) -> dict:
    """Dispatch to the data or AI lightweight probe. Never raises."""
    key = (name or "").strip().lower()
    try:
        if key in AI_PROVIDERS:
            return probe_ai_provider(key, tracker, timeout_s=timeout_s)
        if key in DATA_PROVIDERS:
            return probe_data_provider(key, tracker, timeout_s=timeout_s)
        return {"provider": key or "unknown", "ok": False, "latency_ms": None,
                "error": f"unknown provider; expected one of {list(KNOWN_PROVIDERS)}"}
    except Exception as exc:
        return {"provider": key or "unknown", "ok": False, "latency_ms": None,
                "error": f"{type(exc).__name__}: {exc}"[:280]}


def probe_all_providers(tracker=None, *, timeout_s: float = 5.0, include_ai: bool = True) -> list[dict]:
    """Probe every known provider (data + optionally AI). Never raises.

    Sequential and bounded: one lightweight ping per provider (quote/FX/AI
    model-list). Intended for cron (GET /api/cron/health) and manual
    POST /api/providers/health/test without a provider arg.
    """
    names = list(KNOWN_PROVIDERS) if include_ai else list(DATA_PROVIDERS)
    out: list[dict] = []
    for name in names:
        try:
            out.append(probe_provider(name, tracker, timeout_s=timeout_s))
        except Exception as exc:
            out.append({"provider": name, "ok": False, "latency_ms": None,
                        "error": f"{type(exc).__name__}: {exc}"[:280]})
    return out


def market_state(
    as_of: datetime,
    *,
    delay_minutes: int = 15,
    now: datetime | None = None,
    session_bars: int = 1,
    mic: str | None = None,
    at: datetime | None = None,
) -> str:
    """Freshness + calendar state: open | closed | delayed | stale.

    Without ``mic`` this is the legacy freshness check (open/delayed/stale
    by data age; preserved for existing callers/tests). With ``mic`` the
    exchange wall-clock from ``backend.instruments.calendars.market_state_at``
    is layered in: stale wins over everything; a closed market (including
    the XSHG lunch break, mapped to "closed" here to match the
    open|closed|delayed|stale API contract, and Euronext XPAR/XAMS/XBRU
    09:00-17:30 sessions with the six-feast holiday stub) reports "closed"
    even for fresh data; an open market with data older than the expected
    delay reports "delayed".

    stale = now - as_of > max(2x expected delay, 1 trading session ~ 24h).
    ``at`` optionally overrides the wall-clock used for the calendar
    (defaults to ``now``); deterministic tests should pass both.
    """
    now = now or _utcnow()
    wall = at or now
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if wall.tzinfo is None:
        wall = wall.replace(tzinfo=timezone.utc)
    age_min = (now - as_of).total_seconds() / 60
    expected = max(int(delay_minutes), 1)
    stale_after = max(STALE_MULTIPLE * expected, 24 * 60 if session_bars >= 1 else STALE_MULTIPLE * expected)
    if age_min > stale_after:
        return "stale"
    if mic is not None:
        try:
            cal = market_state_at(mic, wall)
        except Exception:
            cal = "open"
        if cal in ("closed", "lunch"):
            return "closed"
    if age_min > expected:
        return "delayed"
    return "open"


def freshness_ok(
    as_of: datetime,
    *,
    delay_minutes: int = 15,
    now: datetime | None = None,
    mic: str | None = None,
    at: datetime | None = None,
) -> bool:
    return market_state(as_of, delay_minutes=delay_minutes, now=now, mic=mic, at=at) != "stale"


def reconcile_quotes(a: dict, b: dict, *, price_tolerance: float = 0.01) -> dict:
    """Compare two sources for the same symbol (docs/DATA_QUALITY.md).

    Divergence downgrades grade to B/C and must be audit-logged by callers.
    """
    result: dict = {"compared": False, "agree": False, "divergence_pct": None}
    try:
        if not isinstance(a, dict) or not isinstance(b, dict):
            result["reason"] = "missing-price"
            return result
        pa, pb = float(a.get("price")), float(b.get("price"))  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        result["reason"] = "missing-price"
        return result
    if not (math.isfinite(pa) and math.isfinite(pb)):
        result["reason"] = "invalid-price"
        return result
    if pa <= 0 or pb <= 0:
        result["reason"] = "invalid-price"
        return result
    div = abs(pa - pb) / pb
    if not math.isfinite(div):
        result["reason"] = "invalid-price"
        return result
    result.update({"compared": True, "divergence_pct": round(div, 6), "agree": div <= price_tolerance})
    return result
