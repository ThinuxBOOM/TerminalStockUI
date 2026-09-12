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


def summarize_latencies(latencies: Iterable[float]) -> tuple[float, float]:
    """Return (p50_ms, p95_ms); (0.0, 0.0) when empty."""
    lat = sorted(float(v) for v in latencies)
    if not lat:
        return 0.0, 0.0
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


def aggregate_provider_calls(
    provider: str,
    calls: list[dict],
    *,
    circuit: str = "closed",
    last_check: Optional[str] = None,
    now: Optional[float] = None,
) -> dict:
    """Aggregate raw call records into the dashboard stat shape.

    Each record: {"latency_ms": float, "ok": bool, "t": datetime|epoch|None}.
    Timeless records count toward totals and the 1h/5m windows.
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
    return {
        "provider": provider,
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "error_rate_1h": error_rate(recent_1h),
        "error_rate_5m": error_rate(recent_5m),
        "calls_1h": len(recent_1h),
        "calls_5m": len(recent_5m),
        "total_calls": len(calls),
        "circuit": circuit,
        "last_check": last_check,
    }


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


def check_error_alert(stats: Mapping[str, Any], *, threshold: float = 0.05, min_calls: int = 5) -> bool:
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


def record_call(tracker: Any, provider: str, latency_ms: float, ok: bool) -> None:
    """Thin wrapper so call sites depend on observability, not the tracker impl."""
    tracker.record(provider, float(latency_ms), bool(ok))
