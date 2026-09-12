"""Provider health: latency/error tracking, freshness, reconciliation.

Backed in-memory here; Redis-backed in production (same interface).
Grades follow docs/DATA_QUALITY.md; market states: open|closed|delayed|stale.
"""

from __future__ import annotations

import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone

from backend.instruments.calendars import market_state_at


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderHealthTracker:
    """Rolling per-provider call stats (latency ms, ok flag, timestamp)."""

    def __init__(self, max_samples: int = 500) -> None:
        self._calls: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_samples))
        self._circuits: dict[str, str] = {}

    def record(self, provider: str, latency_ms: float, ok: bool) -> None:
        self._calls[provider].append({"t": _utcnow(), "latency_ms": float(latency_ms), "ok": ok})

    def set_circuit(self, provider: str, state: str) -> None:
        self._circuits[provider] = state

    def get_circuit(self, provider: str) -> str:
        return self._circuits.get(provider, "closed")

    def stats(self, provider: str) -> dict:
        calls = list(self._calls.get(provider, []))
        lat = sorted(c["latency_ms"] for c in calls)
        if lat:
            p50 = statistics.median(lat)
            idx95 = min(len(lat) - 1, int(len(lat) * 0.95))
            p95 = lat[idx95]
        else:
            p50, p95 = 0.0, 0.0
        hour_ago = _utcnow().timestamp() - 3600
        recent = [c for c in calls if c["t"].timestamp() >= hour_ago]
        errors = sum(1 for c in recent if not c["ok"])
        return {
            "provider": provider,
            "latency_p50_ms": round(p50, 1),
            "latency_p95_ms": round(p95, 1),
            "error_rate_1h": round(errors / len(recent), 4) if recent else 0.0,
            "calls_1h": len(recent),
            "total_calls": len(calls),
            "circuit": self.get_circuit(provider),
            "last_check": calls[-1]["t"].isoformat() if calls else None,
        }

    def all_stats(self) -> list[dict]:
        providers = set(list(self._calls) + list(self._circuits))
        return [self.stats(p) for p in sorted(providers)]


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
    age_min = (now - as_of).total_seconds() / 60
    expected = max(int(delay_minutes), 1)
    stale_after = max(2 * expected, 24 * 60 if session_bars >= 1 else 2 * expected)
    if age_min > stale_after:
        return "stale"
    if mic is not None:
        try:
            cal = market_state_at(mic, wall)
        except Exception:
            cal = "open"  # unsupported MIC / tz issue: fall back to freshness-only
        if cal in ("closed", "lunch"):
            return "closed"
        # cal == "open": fall through to delay check below.
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
        pa, pb = float(a.get("price")), float(b.get("price"))
    except (TypeError, ValueError):
        result["reason"] = "missing-price"
        return result
    if pa <= 0 or pb <= 0:
        result["reason"] = "invalid-price"
        return result
    div = abs(pa - pb) / pb
    result.update({"compared": True, "divergence_pct": round(div, 6), "agree": div <= price_tolerance})
    return result
