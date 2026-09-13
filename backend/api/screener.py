"""GET /api/screener — rank the seed universe by forecast direction probability.

Deterministic, no AI, no network beyond what the market-data providers
already do (offline stub fallback keeps the scan usable on outage).
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

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.analytics.quality import piotroski_score
from backend.api.deps import get_market_service, get_registry
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/screener", tags=["screener"])

KNOWN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})

DISCLOSURE = "Not investment advice. For informational purposes only."

#: Quality signal inputs: no statement feed in this phase, so the shared
#: EMPTY mapping mirrors backend/api/analytics_api.py (modules return
#: their own "unavailable" results instead of fabricated numbers).
EMPTY_STATEMENTS: dict = {}


def _quality_signal() -> dict:
    """One cheap quality signal reused from the analytics quality module."""
    result = piotroski_score(EMPTY_STATEMENTS)
    return {
        "metric": "piotroski",
        "quality_flag": result.quality_flag,
        "reason": result.reason,
    }


def _normalize_market(market: str | None) -> str | None:
    mic = (market or "").strip().upper()
    if not mic or mic == "ALL":
        return None
    if mic not in KNOWN_MARKETS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {market!r}: expected one of "
            f"{sorted(KNOWN_MARKETS)} or ALL",
        )
    return mic


@router.get("")
def screen(
    market: str | None = Query(default=None, description="MIC scope or ALL"),
    min_direction: float = Query(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum direction_probability to include",
    ),
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    limit: int = Query(default=20, ge=1, le=50, description="Max rows (cap 50)"),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Scan the registry universe, rank by forecast direction probability."""
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    horizon = int(horizon)
    mic = _normalize_market(market)

    universe = registry.all()
    if mic is not None:
        universe = [i for i in universe if i.exchange_mic == mic]
    universe_size = len(universe)

    forecaster = ForecastService(market_service=svc)
    quality = _quality_signal()

    results: list[dict] = []
    skipped: list[dict] = []
    for inst in universe:
        symbol_key = inst.provider_symbol or inst.exchange_symbol
        try:
            quote = svc.get_quote(symbol_key, inst.exchange_mic)
            fc = forecaster.forecast(symbol_key, horizon)
            direction = float(fc["direction_probability"])
            if not (0.0 <= direction <= 1.0):
                raise ValueError(f"direction_probability out of range: {direction!r}")
            results.append({
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
            })
        except HTTPException:
            raise
        except Exception as exc:  # per-symbol degrade, never 500
            skipped.append({
                "symbol": symbol_key,
                "reason": f"{type(exc).__name__}: {exc}",
            })

    ranked = sorted(results, key=lambda r: r["direction_probability"], reverse=True)
    filtered = [r for r in ranked if r["direction_probability"] >= float(min_direction)]
    page = filtered[: int(limit)]
    return {
        "results": page,
        "count": len(page),
        "universe_size": universe_size,
        "skipped": skipped,
        "horizon": horizon,
        "disclosure": DISCLOSURE,
    }
