"""Provider latency/error aggregator (Milestone 0 observability).

Pure helpers over raw call records plus a thin wrapper around
backend.market_data.health.ProviderHealthTracker. No Redis required;
production swaps the in-memory tracker for a Redis-backed one with the
same interface.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


#: Alert rule (README): error_rate above this over 5 minutes triggers an alert.
ERROR_THRESHOLD_DEFAULT = 0.05
#: Minimum calls before the error-rate alert can fire (burst rule excepted).
MIN_CALLS_DEFAULT = 5


def _as_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile over an already-sorted list (q in 0..1)."""
    if not sorted_vals:
        return 0.0
    if q <= 0:
        return float(sorted_vals[0])
    if q >= 1:
        return float(sorted_vals[-1])
    idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * q))
    return float(sorted_vals[idx])


def summarize_latencies(latencies: Iterable[float]) -> tuple[float | None, float | None]:
    """Return (p50_ms, p95_ms); (None, None) when empty (no samples measured)."""
    lat = sorted(float(v) for v in latencies)
    if not lat:
        return None, None
    return round(statistics.median(lat), 1), round(percentile(lat, 0.95), 1)


def windowed(calls: list[dict], window_s: float, *, now: Optional[float] = None) -> list[dict]:
    """Calls within the trailing window. Timeless records count as inside."""
    if now is None:
        now = _utcnow().timestamp()
    out = []
    for c in calls:
        ts = _as_timestamp(c.get("t"))
        if ts is None or now - ts <= window_s:
            out.append(c)
    return out


def error_rate(calls: list[dict]) -> float:
    if not calls:
        return 0.0
    bad = sum(1 for c in calls if not c.get("ok", True))
    return round(bad / len(calls), 4)


def provider_kind(name: str) -> str:
    """Distinct kind for data vs AI providers (mirrors health.provider_kind)."""
    try:
        key = str(name or "").strip().lower()
    except Exception:
        return "unknown"
    if key in ("gemini", "openai", "anthropic", "xai"):
        return "ai"
    if key in ("yfinance", "akshare", "alpaca", "stooq", "finnhub", "twelvedata", "fx"):
        return "data"
    return "unknown"


def aggregate_provider_calls(
    provider: str,
    calls: list[dict],
    *,
    circuit: str = "closed",
    last_check: Optional[str] = None,
    now: Optional[float] = None,
    last_success: Optional[str] = None,
    consecutive_failures: int = 0,
    quota: Optional[Mapping[str, Any]] = None,
    state: Optional[str] = None,
) -> dict:
    """Aggregate raw call records into the dashboard stat shape.

    Each record: {"latency_ms": float, "ok": bool, "t": datetime|epoch|None}.
    Timeless records count toward totals and the 1h/5m windows.
    Extra kwargs (last_success/consecutive_failures/quota/state) are
    passthrough enrichment from ProviderHealthTracker.stats(); when omitted
    they are derived locally so the pure helper stays usable standalone.
    """
    calls = list(calls or [])
    latencies = [float(c.get("latency_ms", 0.0)) for c in calls]
    p50, p95 = summarize_latencies(latencies)
    recent_1h = windowed(calls, 3600, now=now)
    recent_5m = windowed(calls, 300, now=now)
    if last_check is None and calls:
        last = calls[-1].get("t")
        if isinstance(last, datetime):
            last_check = last.isoformat()
        elif last is not None:
            last_check = str(last)
    if last_success is None:
        for c in reversed(calls):
            try:
                if c.get("ok"):
                    t = c.get("t")
                    if isinstance(t, datetime):
                        last_success = t.isoformat()
                    elif t is not None:
                        last_success = str(t)
                    break
            except Exception:
                continue
    try:
        consecutive = max(0, int(consecutive_failures))
    except (TypeError, ValueError):
        consecutive = 0
    if not consecutive:
        # Derive consecutive trailing failures when the tracker did not pass
        # an explicit counter (standalone use).
        n = 0
        for c in reversed(calls):
            try:
                if bool(c.get("ok", True)):
                    break
                if bool(c.get("quota", False)):
                    break  # quota-limited != consecutive breaker failures
                n += 1
            except Exception:
                break
        consecutive = n
    quota_out: dict[str, Any] = {
        "limited": False, "reason": None, "status_code": None,
        "updated_at": None,
        # TODO(tiers): per-tier quota headers stub — surface remaining quota
        # per subscription tier here when subscriptions exist (no-op today).
        "auth_required": provider_kind(provider) == "ai" or str(provider) in (
            "alpaca", "finnhub", "twelvedata"),
    }
    if isinstance(quota, Mapping):
        try:
            quota_out.update({k: quota.get(k, v) for k, v in quota_out.items()})
            quota_out["limited"] = bool(quota.get("limited", False))
        except Exception:
            pass
    else:
        # Derive a quota-limited flag from recent 429-ish records when the
        # tracker did not pass an explicit quota marker.
        try:
            for c in reversed(recent_5m or recent_1h):
                if bool(c.get("quota", False)):
                    quota_out["limited"] = True
                    quota_out["reason"] = "rate_limited"
                    quota_out["status_code"] = c.get("status_code")
                    break
                err = str(c.get("error") or "").lower()
                if "429" in err or "rate limit" in err:
                    quota_out["limited"] = True
                    quota_out["reason"] = "rate_limited"
                    quota_out["status_code"] = 429
                    break
        except Exception:
            pass
    err_1h = error_rate(recent_1h)
    err_5m = error_rate(recent_5m)
    if state is None:
        state = derive_state(
            kind=provider_kind(provider),
            circuit=str(circuit or "closed"),
            quota_limited=bool(quota_out["limited"]),
            quota_reason=quota_out.get("reason"),
            error_5m=err_5m, calls_5m=len(recent_5m),
            error_1h=err_1h, calls_1h=len(recent_1h),
            total=len(calls),
        )
    return {
        "provider": provider,
        "kind": provider_kind(provider),
        "state": state,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "error_rate_1h": err_1h,
        "error_rate_5m": err_5m,
        "calls_1h": len(recent_1h),
        "calls_5m": len(recent_5m),
        "total_calls": len(calls),
        "circuit": str(circuit or "closed"),
        "last_check": last_check,
        "last_success": last_success,
        "consecutive_failures": consecutive,
        "quota": quota_out,
    }


def derive_state(
    *,
    kind: str = "data",
    circuit: str = "closed",
    quota_limited: bool = False,
    quota_reason: Any = None,
    error_5m: float = 0.0,
    calls_5m: int = 0,
    error_1h: float = 0.0,
    calls_1h: int = 0,
    total: int = 0,
    threshold: float = ERROR_THRESHOLD_DEFAULT,
) -> str:
    """UI-facing state: up|degraded|down|unknown|unconfigured.

    Mirrors health._derive_state so standalone aggregation agrees with the
    tracker. 429/quota -> degraded (never down); AI unconfigured -> its own
    state; open breaker -> down unless quota-limited.
    """
    try:
        reason = str(quota_reason or "").lower()
    except Exception:
        reason = ""
    try:
        circ = str(circuit or "closed").strip().lower().replace("_", "-")
    except Exception:
        circ = "closed"
    if circ not in ("closed", "open", "half-open"):
        circ = "closed"
    if total == 0:
        if quota_limited and reason == "unconfigured" and kind == "ai":
            return "unconfigured"
        return "unknown"
    if circ == "open":
        return "degraded" if quota_limited else "down"
    if circ == "half-open":
        return "degraded"
    if quota_limited:
        if reason == "unconfigured" and kind == "ai":
            return "unconfigured"
        return "degraded"
    try:
        if calls_5m >= MIN_CALLS_DEFAULT and float(error_5m) > threshold:
            return "degraded"
        if calls_5m == 0 and calls_1h >= MIN_CALLS_DEFAULT and float(error_1h) > threshold:
            return "degraded"
        if calls_5m > 0 and float(error_5m) >= 1.0:
            return "degraded"
    except Exception:
        pass
    return "up"


def aggregate_all(
    calls_by_provider: Mapping[str, list[dict]],
    *,
    circuits: Optional[Mapping[str, str]] = None,
    now: Optional[float] = None,
) -> list[dict]:
    """Aggregate every provider's records; sorted by provider name."""
    circuits = circuits or {}
    return [
        aggregate_provider_calls(
            provider,
            calls_by_provider.get(provider, []),
            circuit=circuits.get(provider, "closed"),
            now=now,
        )
        for provider in sorted(calls_by_provider)
    ]


def check_error_alert(stats: Mapping[str, Any], *, threshold: float = ERROR_THRESHOLD_DEFAULT, min_calls: int = MIN_CALLS_DEFAULT) -> bool:
    """True when the 5m (fallback 1h) error rate breaches threshold.

    README rule: alert (log + audit event) on error_rate > 5%/5min.
    """
    for key in ("error_rate_5m", "error_rate_1h"):
        rate = stats.get(key)
        if rate is None:
            continue
        calls = stats.get("calls_5m", stats.get("calls_1h", 0)) if key == "error_rate_5m" else stats.get("calls_1h", 0)
        if calls >= min_calls and float(rate) > threshold:
            return True
    # Too few samples: still alert on a unanimous recent failure burst.
    if stats.get("calls_5m", 0) > 0 and float(stats.get("error_rate_5m", 0.0)) >= 1.0:
        return True
    return False


def record_call(
    tracker: Any,
    provider: str,
    latency_ms: float,
    ok: bool,
    *,
    status_code: Any = None,
    error: Any = None,
) -> None:
    """Thin wrapper so call sites depend on observability, not the tracker impl."""
    try:
        tracker.record(provider, float(latency_ms), bool(ok),
                       status_code=status_code, error=error)
    except TypeError:
        tracker.record(provider, float(latency_ms), bool(ok))


#: Per-(provider, reason) audit throttle so hot dashboard/cron loops cannot
#: flood the audit table while a breach persists (log still fires every time).
_ALERT_LAST_AUDIT: dict[tuple[str, str], float] = {}
ALERT_AUDIT_COOLDOWN_S = 300.0


def emit_alert(provider: str, reason: str, message: str, stats: Optional[Mapping[str, Any]] = None) -> None:
    """Alert hook: log + best-effort audit event. Never raises.

    README rule kept: error_rate > 5%/5min ALSO fires here (in addition to
    the dashboard alert list) so cron/worker paths without the dashboard
    still log + audit. ``stats`` is redacted to safe scalar fields only.
    Audit writes are throttled per (provider, reason) to one per
    ALERT_AUDIT_COOLDOWN_S; the log line always fires.
    """
    import logging as _logging
    import time as _time

    try:
        _logging.getLogger(__name__).warning(
            "provider health alert provider=%s reason=%s %s", provider, reason, message)
    except Exception:
        pass
    try:
        now = _time.monotonic()
        throttle_key = (str(provider), str(reason))
        last = _ALERT_LAST_AUDIT.get(throttle_key, 0.0)
        if now - last < ALERT_AUDIT_COOLDOWN_S:
            return
        _ALERT_LAST_AUDIT[throttle_key] = now
    except Exception:
        pass
    try:
        from backend.api.audit import append_audit_log
        from backend.db.session import get_session_factory

        safe: dict[str, Any] = {"provider": str(provider), "reason": reason,
                                "message": str(message)[:280]}
        if isinstance(stats, Mapping):
            for k in ("state", "circuit", "error_rate_5m", "error_rate_1h",
                      "calls_5m", "calls_1h", "consecutive_failures"):
                try:
                    v = stats.get(k)
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        safe[k] = v
                except Exception:
                    continue
        db = get_session_factory()()
        try:
            append_audit_log(db, actor="system", action="provider.health_alert",
                             entity_type="provider", entity_id=str(provider),
                             payload=safe)
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        pass
