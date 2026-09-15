"""Latency/error dashboard data (Milestone 0 observability, M8 hardening).

Builds the payload the Frontend Home page renders: per-provider cards
(latency p50/p95, error rates, circuit) plus a global banner flag when any
circuit is open. Alert rule (README): error_rate > 5% over 5 minutes
(fallback: 1h rate from ProviderHealthTracker) triggers an alert entry.

M8: the dashboard always includes rows for the known providers (market-data:
yfinance/akshare/alpaca/stooq/fx; AI: gemini/openai/anthropic/xai) even before they have
recorded calls, so GET /health (via build_dashboard) shows FX + AKShare + AI
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
)

# Market-data providers first, then FX, then bounded-AI-opinion providers.
KNOWN_PROVIDERS: tuple[str, ...] = (
    "yfinance",
    "akshare",
    "alpaca",
    "stooq",
    "fx",
    "gemini",
    "openai",
    "anthropic",
    "xai",
)

_PROVIDER_KEYS: tuple[str, ...] = (
    "provider",
    "latency_p50_ms",
    "latency_p95_ms",
    "error_rate_1h",
    "error_rate_5m",
    "calls_1h",
    "calls_5m",
    "total_calls",
    "circuit",
    "last_check",
)


def _zero_stat(provider: str) -> dict:
    """Zero-filled row so known providers render before their first call."""
    return {
        "provider": provider,
        "latency_p50_ms": 0.0,
        "latency_p95_ms": 0.0,
        "error_rate_1h": 0.0,
        "error_rate_5m": 0.0,
        "calls_1h": 0,
        "calls_5m": 0,
        "total_calls": 0,
        "circuit": "closed",
        "last_check": None,
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
    is JSON-serializable. ``known_providers`` (default: KNOWN_PROVIDERS)
    guarantees FX + AKShare + AI rows even with zero calls; pass ``()`` to
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
        # Prefer a 5-minute error rate computed from raw calls when available.
        if provider in raw:
            agg = aggregate_provider_calls(
                provider,
                raw[provider],
                circuit=str(entry.get("circuit", "closed")),
                last_check=entry.get("last_check"),
            )
            enriched["error_rate_5m"] = agg["error_rate_5m"]
            enriched["calls_5m"] = agg["calls_5m"]
        else:
            enriched.setdefault("error_rate_5m", enriched.get("error_rate_1h", 0.0))
            enriched.setdefault("calls_5m", enriched.get("calls_1h", 0))
        # Normalize: every row carries p50/p95/error_1h/circuit (+ 5m windows).
        base = _zero_stat(provider)
        base.update({k: v for k, v in enriched.items() if v is not None or k == "last_check"})
        # Preserve explicit None last_check; fill any other missing key.
        for key in _PROVIDER_KEYS:
            base.setdefault(key, _zero_stat(provider)[key])
        base["provider"] = provider
        providers.append(_jsonable(base))

        if str(base.get("circuit", "closed")) == "open":
            alerts.append(
                {
                    "severity": "critical",
                    "provider": provider,
                    "reason": "circuit_open",
                    "message": f"{provider} circuit is open; serving fallback/cached data",
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

    # M8 guarantee: FX + AKShare + AI providers always have a (possibly zero) row.
    if known_providers is None:
        known_providers = KNOWN_PROVIDERS
    for name in known_providers:
        if name not in seen:
            providers.append(dict(_zero_stat(name)))

    providers.sort(key=lambda p: str(p.get("provider")))
    open_circuits = [p["provider"] for p in providers if p.get("circuit") == "open"]
    degraded = bool(open_circuits)
    banner: Optional[str] = None
    if degraded:
        banner = f"Degraded: {', '.join(open_circuits)} circuit open; showing fallback/cached data"

    return _jsonable(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "providers": providers,
            "alerts": alerts,
            "summary": {
                "total_providers": len(providers),
                "open_circuits": open_circuits,
                "degraded": degraded,
                "banner": banner,
                "total_alerts": len(alerts),
            },
        }
    )
