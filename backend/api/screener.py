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

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["screener"])

_BUILTIN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})


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
_SCREENER_MAX_WORKERS = 8
_PER_SYMBOL_TIMEOUT_S = 15

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
    if mic not in _known_markets():
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {market!r}: expected one of "
            f"{sorted(_known_markets())} or ALL",
        )
    return mic


def _screener_cache_key(mic: str | None, horizon: int, min_direction: float, limit: int, offset: int) -> str:
    try:
        min_dir = float(min_direction)
    except (TypeError, ValueError):
        min_dir = 0.5
    return f"screener:{mic or 'ALL'}:{int(horizon)}:{min_dir:.4f}:{int(limit)}:{int(offset)}"


def _scan_one(inst, svc: MarketDataService, horizon: int, quality: dict) -> tuple[dict | None, dict | None]:
    """Fetch quote + forecast for one instrument. Returns (row, skipped)."""
    try:
        symbol_key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
    except Exception:
        return None, {"symbol": "UNKNOWN", "reason": "bad registry entry"}
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
    market: str | None = Query(default=None, description="MIC scope or ALL"),
    min_direction: float = Query(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum direction_probability to include",
    ),
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    limit: int = Query(default=20, ge=1, le=50, description="Max rows (cap 50)"),
    offset: int = Query(default=0, ge=0, le=200, description="Skip first N filtered rows"),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Scan the registry universe, rank by forecast direction probability."""
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
    cache_key = _screener_cache_key(mic, horizon, float(min_direction), int(limit), int(offset))
    try:
        cached = get_cache().get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("results"), list):
            return cached
    except Exception:
        pass

    try:
        universe = registry.all()
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("screener universe failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="screener universe failed") from exc
    if mic is not None:
        try:
            universe = [i for i in universe if getattr(i, "exchange_mic", None) == mic]
        except Exception as exc:
            log.warning("screener filter failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="screener filter failed") from exc
    universe_size = len(universe)

    quality = _quality_signal()

    results: list[dict] = []
    skipped: list[dict] = []
    if universe:
        workers = max(1, min(_SCREENER_MAX_WORKERS, len(universe)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(_scan_one, inst, svc, horizon, quality): inst
                for inst in universe
            }
            for fut in concurrent.futures.as_completed(future_map):
                try:
                    row, skip = fut.result(timeout=_PER_SYMBOL_TIMEOUT_S)
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
        "provenance": _combine_provenance(
            [r["provenance"] for r in page if isinstance(r.get("provenance"), dict)]
        ),
        "disclosure": DISCLOSURE,
    }
    try:
        get_cache().set(cache_key, response, ttl_s=_SCREENER_CACHE_TTL_S)
    except Exception:
        pass
    return response
