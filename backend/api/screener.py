"""GET /api/screener — rank the seed universe by forecast direction probability.

Deterministic, no AI, no network beyond what the market-data providers
already do (fail-closed: outage raises 502, never a stub scan).
For each registry instrument in scope: quote (price/currency/market_state/
provenance via MarketDataService) + deterministic forecast at the
requested horizon (raw direction_probability/confidence/model_version —
label bands are the caller's job) + one quality signal.

Quality note: the statement feed is not wired, so the analytics quality
modules report "unavailable" by design. The scan reuses
``piotroski_score({})`` (cheap, no I/O) and surfaces its
quality_flag/reason per row instead of fabricating a score.

Per-symbol failures degrade to ``skipped: [{symbol, reason}]`` — the
endpoint never 500s because of one bad symbol. Ranked by
direction_probability desc, filtered to direction >= min_direction.

UTC/provenance conventions: timestamps and provenance envelopes are
passed through untouched from the underlying services (UTC ISO).
"""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.analytics.quality import piotroski_score
from backend.api.deps import get_market_service, get_registry
from backend.cache import get_cache
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

try:  # V2 Phase 2 canonical guards
    from backend.auth.guards import require_tier  # type: ignore
except ImportError:  # pragma: no cover - fallback until Phase 2 lands
    from typing import Any as _Any

    from fastapi import Request as _Request

    from backend.auth.tiers import _TIER_RANK as _RANK
    from backend.auth.tiers import normalize_tier as _norm

    _TEST_TOKENS: dict[str, dict[str, _Any]] = {
        "test-free": {"user_id": "user-free", "tier": "free", "is_admin": False},
        "test-silver": {"user_id": "user-silver", "tier": "silver", "is_admin": False},
        "test-gold": {"user_id": "user-gold", "tier": "gold", "is_admin": False},
        "test-platinum": {"user_id": "user-platinum", "tier": "platinum", "is_admin": False},
        "test-admin": {"user_id": "admin-1", "tier": "platinum", "is_admin": True},
    }

    def require_tier(min_tier: str):  # type: ignore[no-redef]
        need = _norm(min_tier)

        async def _dep(request: _Request) -> dict[str, _Any]:
            try:
                auth = (request.headers.get("authorization") or "").strip()
            except Exception:
                auth = ""
            token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            user = _TEST_TOKENS.get(token)
            if user is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            if bool(user.get("is_admin")):
                return dict(user)
            if _RANK[_norm(user.get("tier"))] >= _RANK[need]:
                return dict(user)
            raise HTTPException(status_code=402, detail={"message": f"upgrade required: {need} or higher", "upgrade_required": True, "min_tier": need})

        return _dep


def _scope_prefix(user: dict | None) -> str:
    """User-scoped cache prefix u:{id}:t:{tier}: (never trusts X-Tier)."""
    try:
        uid = str((user or {}).get("user_id") or (user or {}).get("id") or "anon")
    except Exception:
        uid = "anon"
    try:
        from backend.auth.tiers import normalize_tier as _n

        tier = _n((user or {}).get("tier"))
    except Exception:
        tier = "free"
    return f"u:{uid}:t:{tier}:"


log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])

_BUILTIN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})

#: Index universes (not venues): pseudo-markets grouping registry symbols
#: across MICs. S&P 500 spans XNYS+XNAS so it can never be a MIC market.
_BUILTIN_UNIVERSES = frozenset({"SP500"})


def _known_markets() -> frozenset:
    """Canonical venue set: config/markets.yaml union built-ins (never empty).

    Adding a venue to the YAML needs no code edit here; EXTRA_SYMBOLS-fed
    registry rows resolve through the same set.
    """
    try:
        from backend.instruments.config import market_mics

        configured = set(market_mics(enabled_only=True))
    except Exception:
        configured = set()
    return frozenset(configured | set(_BUILTIN_MARKETS))


KNOWN_MARKETS = _BUILTIN_MARKETS  # compat alias (tests import this name)

DISCLOSURE = "Not investment advice. For informational purposes only."

#: Screener perf tuning: quotes dominate wall time (~93% of a cold scan:
#: 14.6s quotes vs 1.0s forecasts for 17 symbols, 21.4s end-to-end cold).
#: Parallelize the per-symbol fan-out and cache the ranked envelope so
#: slider drags / repeats don't rescan. TTL 45s balances freshness (quote
#: cache itself is 60s) with repeat-view speed.
_SCREENER_CACHE_TTL_S = 45
#: SP500 envelopes cache longer: same-day repeats skip the 500-quote
#: fan-out entirely (forecasts are data-version keyed per day anyway).
_SCREENER_CACHE_TTL_SP500_S = 300
_SCREENER_MAX_WORKERS = 8
#: Large universes (SP500) fan out wider: quotes dominate and are
#: I/O-bound, so 16 workers halves a 500-quote scan vs 8.
_SCREENER_MAX_WORKERS_LARGE = 16
_LARGE_UNIVERSE_CUTOVER = 100
_PER_SYMBOL_TIMEOUT_S = 60
#: Global scan budget: a cold 500-symbol SP500 scan at ~2s/symbol over
#: 8 workers (~125s) would otherwise hit the Vercel 60s kill with zero
#: bytes. When the budget runs out the scan ranks what finished and marks
#: the rest skipped — partial 200 beats a timeout into nothing.
_SCAN_BUDGET_S = 50

#: Quality signal inputs: no statement feed in this phase, so the shared
#: EMPTY mapping mirrors backend/api/analytics_api.py (modules return
#: their own "unavailable" results instead of fabricated numbers).
EMPTY_STATEMENTS: dict = {}


def _quality_signal() -> dict:
    """One cheap quality signal reused from the analytics quality module."""
    try:
        result = piotroski_score(EMPTY_STATEMENTS)
        return {
            "metric": "piotroski",
            "quality_flag": result.quality_flag,
            "reason": result.reason,
        }
    except Exception:
        return {
            "metric": "piotroski",
            "quality_flag": "unavailable",
            "reason": "quality signal unavailable",
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _combine_provenance(entries: list[dict]) -> dict:
    """Merge per-row quote provenance dicts into one scan-level envelope.

    Oldest as_of wins, sources are joined, fallback is sticky (any row on
    fallback flags the scan), missing fields are unioned, and the grade is
    recomputed via grade_quality. Empty scan: honest non-fallback envelope
    (nothing served, nothing fallback). Additive only; per-row envelopes
    are untouched.
    """
    if not entries:
        return build_provenance(
            "screener", as_of=_utcnow(), delay_minutes=15,
            quality_grade="B", fallback_used=False, missing_fields=[],
        ).model_dump(mode="json")
    stamps: list[datetime] = []
    for entry in entries:
        try:
            if not isinstance(entry, dict):
                raise ValueError("bad provenance entry")
            raw = entry.get("as_of")
            stamp = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamps.append(stamp)
        except Exception:
            stamps.append(_utcnow())
    oldest = min(stamps)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    try:
        sources = sorted({str(e.get("source", "unknown")) for e in entries if isinstance(e, dict)})
        fallback = any(bool(e.get("fallback_used")) for e in entries if isinstance(e, dict))
        missing = sorted({m for e in entries if isinstance(e, dict) for m in (e.get("missing_fields") or [])})
        delays = [int(e.get("delay_minutes", 15) or 0) for e in entries if isinstance(e, dict)]
        max_delay = max(delays) if delays else 15
        grade, _ = grade_quality(
            delay_minutes=max_delay,
            age_minutes=max(0.0, (_utcnow() - oldest).total_seconds() / 60),
            missing_fields=missing,
            fallback_used=fallback,
            reconciled=False,
        )
    except Exception:
        sources, fallback, missing, max_delay, grade = ["screener"], False, [], 15, "B"
    return build_provenance(
        "+".join(sources), as_of=oldest,
        delay_minutes=max_delay,
        quality_grade=grade, fallback_used=fallback, missing_fields=missing,
    ).model_dump(mode="json")


def _normalize_market(market: str | None) -> str | None:
    mic = (market or "").strip().upper()
    if not mic or mic == "ALL":
        return None
    if mic in _BUILTIN_UNIVERSES:
        return mic
    if mic not in _known_markets():
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {market!r}: expected one of "
            f"{sorted(_known_markets())} or ALL or {sorted(_BUILTIN_UNIVERSES)}",
        )
    return mic


def _universe_for(mic: str | None, registry: InstrumentRegistry) -> list:
    """Registry rows for a MIC scope, ALL, or an index universe (SP500)."""
    try:
        universe = registry.all()
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("screener universe failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="screener universe failed") from exc
    if mic is None:
        return universe
    if mic == "SP500":
        try:
            from backend.instruments.sp500 import SP500_SYMBOLS as _sp

            wanted = {str(s).upper() for s in _sp}
        except Exception as exc:
            log.warning("screener sp500 universe failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="screener universe failed") from exc

        def _key(inst) -> str:
            try:
                return str(getattr(inst, "provider_symbol", None)
                           or getattr(inst, "exchange_symbol", "") or "").upper()
            except Exception:
                return ""

        return [i for i in universe if _key(i) in wanted]
    try:
        return [i for i in universe if getattr(i, "exchange_mic", None) == mic]
    except Exception as exc:
        log.warning("screener filter failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="screener filter failed") from exc


def _screener_cache_key(
    mic: str | None, horizon: int, min_direction: float, limit: int, offset: int,
    user: dict | None = None,
) -> str:
    """User-scoped key: u:{id}:t:{tier}: prefix prevents cross-tier poisoning."""
    try:
        min_dir = float(min_direction)
    except (TypeError, ValueError):
        min_dir = 0.5
    base = f"screener:{mic or 'ALL'}:{int(horizon)}:{min_dir:.4f}:{int(limit)}:{int(offset)}"
    try:
        return f"{_scope_prefix(user)}{base}"
    except Exception:
        return base


#: Minimum cached 1d bars for a symbol to enter a scan (mirrors the
#: get_bars serve gate min(limit,100)): below this the scan would pay a live
#: vendor fetch per symbol. Uncached symbols degrade to skipped with an
#: honest reason (daily cron warming covers them) instead of stalling the
#: batch past maxDuration. Single-symbol views (brief/forecast/backtest)
#: still fetch on demand — this gate is scan-only.
_SCAN_MIN_WARM_BARS = 100


def _scan_one(inst, svc: MarketDataService, horizon: int, quality: dict,
              warm: dict | None = None) -> tuple[dict | None, dict | None]:
    """Fetch quote + forecast for one instrument. Returns (row, skipped)."""
    try:
        symbol_key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
    except Exception:
        return None, {"symbol": "UNKNOWN", "reason": "bad registry entry"}
    if isinstance(warm, dict):
        try:
            have = int(warm.get(str(symbol_key).upper(), 0))
        except Exception:
            have = 0
        if have < _SCAN_MIN_WARM_BARS:
            return None, {"symbol": str(symbol_key),
                          "reason": "no cached bars yet (daily warming covers it)"}
    try:
        quote = svc.get_quote(symbol_key, getattr(inst, "exchange_mic", None))
        if not isinstance(quote, dict):
            raise ValueError("quote unavailable")
        # Per-task forecaster isolates the in-memory records list so
        # ThreadPool workers don't race on ForecastService.records append.
        fc = ForecastService(market_service=svc).forecast(symbol_key, horizon)
        if not isinstance(fc, dict):
            raise ValueError("forecast unavailable")
        direction = float(fc["direction_probability"])
        if not (0.0 <= direction <= 1.0):
            raise ValueError(f"direction_probability out of range: {direction!r}")
        row = {
            "symbol": symbol_key,
            "company_name": inst.company_name,
            "exchange_mic": inst.exchange_mic,
            "currency": quote.get("currency") or inst.currency,
            "price": quote.get("price"),
            "change_pct": quote.get("change_pct"),
            "market_state": quote.get("market_state"),
            "direction_probability": direction,
            "confidence": fc.get("confidence"),
            "model_version": fc.get("model_version"),
            "horizon": horizon,
            "horizons": [horizon],
            "quality": quality,
            "provenance": quote.get("provenance"),
        }
        return row, None
    except HTTPException as exc:
        # Per-symbol degrade (never abort the batch on one bad symbol).
        detail = getattr(exc, "detail", exc)
        reason = str(detail)[:200] if not isinstance(detail, str) else detail[:200]
        return None, {"symbol": symbol_key, "reason": f"{type(exc).__name__}: {reason}"}
    except Exception as exc:  # per-symbol degrade, never 500
        return None, {"symbol": symbol_key, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


@router.get("")
def screen(
    market: str | None = Query(default=None, description="MIC scope, ALL, or SP500"),
    min_direction: float = Query(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum direction_probability to include",
    ),
    horizon: int = Query(default=21, description="Trading-day horizon: 1, 7, 14 or 21"),
    limit: int = Query(default=20, ge=1, le=50, description="Max rows (cap 50)"),
    offset: int = Query(default=0, ge=0, le=200, description="Skip first N filtered rows"),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
    user: dict = Depends(require_tier("silver")),
) -> dict:
    """Scan the registry universe, rank by forecast direction probability.

    V2 HARD gate: silver+ (free -> 402, admin bypasses).
    """
    try:
        horizon_int = int(horizon)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        ) from None
    if horizon_int not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    horizon = horizon_int
    mic = _normalize_market(market)

    # Result cache: identical scans within TTL skip the quote/forecast fan-out.
    # User-scoped so silver/gold/platinum result sets never poison each other.
    cache_key = _screener_cache_key(mic, horizon, float(min_direction), int(limit), int(offset), user)
    try:
        cached = get_cache().get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("results"), list):
            return cached
    except Exception:
        pass

    try:
        universe = _universe_for(mic, registry)
    except HTTPException:
        raise
    universe_size = len(universe)

    quality = _quality_signal()

    # Warm-bars gate (one cheap COUNT query, no network): symbols without
    # cached history skip fast instead of each paying a live vendor fetch.
    # DB unreachable (None) fails open to the legacy per-symbol path.
    try:
        _keys = [str(getattr(i, "provider_symbol", None)
                     or getattr(i, "exchange_symbol", "") or "")
                 for i in universe]
        warm = svc.warm_bar_counts(_keys, timeframe="1d")
    except Exception:
        warm = None

    results: list[dict] = []
    skipped: list[dict] = []
    truncated = False
    if universe:
        import time as _time

        _workers_cap = (_SCREENER_MAX_WORKERS_LARGE
                        if len(universe) > _LARGE_UNIVERSE_CUTOVER
                        else _SCREENER_MAX_WORKERS)
        workers = max(1, min(_workers_cap, len(universe)))
        _scan_start = _time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(_scan_one, inst, svc, horizon, quality, warm): inst
                for inst in universe
            }
            for fut in concurrent.futures.as_completed(future_map):
                remaining = _SCAN_BUDGET_S - (_time.monotonic() - _scan_start)
                if remaining <= 0:
                    truncated = True
                    break
                try:
                    row, skip = fut.result(timeout=min(_PER_SYMBOL_TIMEOUT_S, remaining))
                except concurrent.futures.TimeoutError:
                    inst = future_map[fut]
                    try:
                        sym = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
                    except Exception:
                        sym = "UNKNOWN"
                    skipped.append({"symbol": sym, "reason": "TimeoutError: per-symbol budget exceeded"})
                    continue
                except Exception as exc:
                    skipped.append({"symbol": "UNKNOWN", "reason": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    continue
                if row is not None:
                    results.append(row)
                elif skip is not None:
                    skipped.append(skip)
            if truncated:
                for fut, inst in future_map.items():
                    if fut.done():
                        continue
                    try:
                        sym = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
                    except Exception:
                        sym = "UNKNOWN"
                    if not any(s.get("symbol") == sym for s in skipped):
                        skipped.append({"symbol": sym, "reason": "skipped: scan budget exceeded (retry warm)"})
                    try:
                        fut.cancel()
                    except Exception:
                        pass

    ranked = sorted(results, key=lambda r: r["direction_probability"], reverse=True)
    filtered = [r for r in ranked if r["direction_probability"] >= float(min_direction)]
    filtered_total = len(filtered)
    start = max(0, int(offset))
    page = filtered[start: start + int(limit)]
    response = {
        "results": page,
        "count": len(page),
        "universe_size": universe_size,
        "filtered_total": filtered_total,
        "offset": start,
        "skipped": skipped,
        "horizon": horizon,
        "truncated": truncated,
        "provenance": _combine_provenance(
            [r["provenance"] for r in page if isinstance(r.get("provenance"), dict)]
        ),
        "disclosure": DISCLOSURE,
    }
    try:
        get_cache().set(cache_key, response,
                         ttl_s=(_SCREENER_CACHE_TTL_SP500_S if mic == "SP500"
                                else _SCREENER_CACHE_TTL_S))
    except Exception:
        pass
    return response
