"""Latency/error dashboard data (Milestone 0 observability, M8 hardening).

Builds the payload the Frontend Home page renders: per-provider cards
(latency p50/p95, error rates, circuit) plus a global banner flag when any
circuit is open. Alert rule (README): error_rate > 5% over 5 minutes
(fallback: 1h rate from ProviderHealthTracker) triggers an alert entry.

M8: the dashboard always includes rows for the known providers (market-data:
yfinance/alpaca/finnhub/twelvedata/fx; AI: gemini/openai/anthropic/xai) even before they have
recorded calls, so GET /health (via build_dashboard) shows FX + AI
coverage. Output is JSON-serializable (no datetime objects leak).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from backend.observability.provider_metrics import (
    ERROR_THRESHOLD_DEFAULT,
    MIN_CALLS_DEFAULT,
    aggregate_provider_calls,
    check_error_alert,
    emit_alert,
)

# Market-data providers first, then FX, then bounded-AI-opinion providers.
KNOWN_PROVIDERS: tuple[str, ...] = (
    "yfinance",
    "alpaca",
    "finnhub",
    "twelvedata",
    "fx",
    "gemini",
    "openai",
    "anthropic",
    "xai",
)

_PROVIDER_KEYS: tuple[str, ...] = (
    "provider",
    "kind",
    "state",
    "latency_p50_ms",
    "latency_p95_ms",
    "error_rate_1h",
    "error_rate_5m",
    "calls_1h",
    "calls_5m",
    "total_calls",
    "circuit",
    "last_check",
    "last_success",
    "consecutive_failures",
    "quota",
)


def _zero_quota(provider: str) -> dict:
    try:
        auth = str(provider) in ("alpaca", "finnhub", "twelvedata", "gemini", "openai", "anthropic", "xai")
    except Exception:
        auth = False
    return {"limited": False, "reason": None, "status_code": None,
            "updated_at": None, "auth_required": auth}


def _zero_stat(provider: str) -> dict:
    """Zero-filled row so known providers render before their first call."""
    try:
        kind = "ai" if str(provider) in ("gemini", "openai", "anthropic", "xai") else (
            "data" if str(provider) in (
                "yfinance", "alpaca", "finnhub", "twelvedata", "fx") else "unknown")
    except Exception:
        kind = "unknown"
    return {
        "provider": provider,
        "kind": kind,
        "state": "unknown",
        "latency_p50_ms": 0.0,
        "latency_p95_ms": 0.0,
        "error_rate_1h": 0.0,
        "error_rate_5m": 0.0,
        "calls_1h": 0,
        "calls_5m": 0,
        "total_calls": 0,
        "circuit": "closed",
        "last_check": None,
        "last_success": None,
        "consecutive_failures": 0,
        "quota": _zero_quota(provider),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _tracker_snapshot(tracker: Any) -> tuple[list[dict], dict[str, list[dict]]]:
    """Return (stats, raw_calls_by_provider). Works with the in-memory tracker."""
    try:
        stats = tracker.all_stats()
    except AttributeError:
        stats = []
    raw: dict[str, list[dict]] = {}
    calls = getattr(tracker, "_calls", None)
    if isinstance(calls, Mapping):
        raw = {p: list(v) for p, v in calls.items()}
    elif hasattr(calls, "items"):
        try:
            raw = {p: list(v) for p, v in calls.items()}  # type: ignore[union-attr]
        except Exception:
            raw = {}
    return stats, raw


def build_dashboard(
    tracker: Any = None,
    *,
    error_threshold: float = ERROR_THRESHOLD_DEFAULT,
    min_calls: int = MIN_CALLS_DEFAULT,
    known_providers: Optional[Iterable[str]] = None,
) -> dict:
    """Assemble dashboard data. Never raises on an empty/unknown tracker.

    Every returned provider row carries p50/p95/error_1h (+ 5m)/circuit and
    is JSON-serializable.     ``known_providers`` (default: KNOWN_PROVIDERS)
    guarantees FX + AI rows even with zero calls; pass ``()`` to
    disable the guarantee. Shape is consumed by the /health route.
    """
    if tracker is None:
        from backend.market_data.health import ProviderHealthTracker

        try:
            from backend.api.deps import get_health_tracker

            tracker = get_health_tracker()
        except Exception:
            tracker = ProviderHealthTracker()

    stats, raw = _tracker_snapshot(tracker)
    if not stats and not raw:
        stats = [
            {
                "provider": "yfinance",
                "latency_p50_ms": 0.0,
                "latency_p95_ms": 0.0,
                "error_rate_1h": 0.0,
                "calls_1h": 0,
                "total_calls": 0,
                "circuit": "closed",
                "last_check": None,
            }
        ]

    providers: list[dict] = []
    alerts: list[dict] = []
    seen: set[str] = set()
    for entry in stats:
        enriched = dict(entry)
        provider = str(entry.get("provider", "unknown"))
        seen.add(provider)
        # Prefer a 5-minute error rate computed from raw calls when available,
        # preserving the tracker's enriched passthroughs (kind/state/quota/
        # last_success/consecutive) across the recompute.
        if provider in raw:
            agg = aggregate_provider_calls(
                provider,
                raw[provider],
                circuit=str(entry.get("circuit", "closed")),
                last_check=entry.get("last_check"),
                last_success=entry.get("last_success"),
                consecutive_failures=entry.get("consecutive_failures", 0),
                quota=entry.get("quota") if isinstance(entry.get("quota"), dict) else None,
                state=entry.get("state"),
            )
            enriched["error_rate_5m"] = agg["error_rate_5m"]
            enriched["calls_5m"] = agg["calls_5m"]
            for _k in ("kind", "state", "last_success", "consecutive_failures", "quota"):
                if _k not in enriched or enriched[_k] is None:
                    enriched[_k] = agg.get(_k)
        else:
            enriched.setdefault("error_rate_5m", enriched.get("error_rate_1h", 0.0))
            enriched.setdefault("calls_5m", enriched.get("calls_1h", 0))
        # Normalize: every row carries p50/p95/error_1h/circuit (+ 5m windows
        # + kind/state/quota/last_success/consecutive).
        base = _zero_stat(provider)
        base.update({k: v for k, v in enriched.items() if v is not None or k in ("last_check", "last_success")})
        # Preserve explicit None last_check/last_success; fill missing keys.
        for key in _PROVIDER_KEYS:
            base.setdefault(key, _zero_stat(provider)[key])
        base["provider"] = provider
        providers.append(_jsonable(base))

        _circuit = str(base.get("circuit", "closed"))
        _quota = base.get("quota") if isinstance(base.get("quota"), dict) else {}
        _quota_limited = bool(_quota.get("limited", False))
        if _circuit == "open":
            alerts.append(
                {
                    "severity": "critical",
                    "provider": provider,
                    "reason": "circuit_open",
                    "message": f"{provider} circuit is open; serving fallback/cached data",
                }
            )
            try:
                emit_alert(provider, "circuit_open",
                           f"{provider} circuit is open; serving fallback/cached data", base)
            except Exception:
                pass
        elif _circuit == "half-open":
            alerts.append(
                {
                    "severity": "warning",
                    "provider": provider,
                    "reason": "circuit_half_open",
                    "message": f"{provider} circuit half-open; probing recovery",
                }
            )
        if _quota_limited:
            alerts.append(
                {
                    "severity": "warning",
                    "provider": provider,
                    "reason": "quota_limited",
                    "message": (
                        f"{provider} quota limited ({_quota.get('reason', 'rate_limited')}); "
                        f"degraded, not down"
                    ),
                }
            )
        if check_error_alert(base, threshold=error_threshold, min_calls=min_calls):
            alerts.append(
                {
                    "severity": "warning",
                    "provider": provider,
                    "reason": "high_error_rate",
                    "message": (
                        f"{provider} error rate {base.get('error_rate_5m', base.get('error_rate_1h'))} "
                        f"exceeds {error_threshold:.0%} threshold"
                    ),
                }
            )
            # Alert hook kept: log + audit event on error_rate > 5%/5min
            # (throttled per provider inside emit_alert; never raises).
            try:
                emit_alert(provider, "high_error_rate",
                           f"{provider} error rate {base.get('error_rate_5m', base.get('error_rate_1h'))} "
                           f"exceeds {error_threshold:.0%} threshold", base)
            except Exception:
                pass

    # M8 guarantee: FX + AI providers always have a (possibly zero) row.
    if known_providers is None:
        known_providers = KNOWN_PROVIDERS
    for name in known_providers:
        if name not in seen:
            providers.append(dict(_zero_stat(name)))

    providers.sort(key=lambda p: str(p.get("provider")))
    open_circuits = [p["provider"] for p in providers if p.get("circuit") == "open"]
    degraded_states = [p["provider"] for p in providers
                       if str(p.get("state", "up")) in ("degraded", "down", "unconfigured")]
    degraded = bool(open_circuits) or bool(degraded_states)
    banner: Optional[str] = None
    if open_circuits:
        banner = f"Degraded: {', '.join(open_circuits)} circuit open; showing fallback/cached data"
    elif degraded_states:
        banner = f"Degraded: {', '.join(degraded_states)} degraded; showing fallback/cached data"

    return _jsonable(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "providers": providers,
            "alerts": alerts,
            "summary": {
                "total_providers": len(providers),
                "open_circuits": open_circuits,
                "degraded_providers": degraded_states,
                "degraded": degraded,
                "banner": banner,
                "total_alerts": len(alerts),
            },
        }
    )
