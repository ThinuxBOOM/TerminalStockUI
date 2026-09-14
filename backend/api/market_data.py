"""Market-data routers: quote + bars. Every response carries provenance."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.market_data.health import market_state as _calendar_market_state
from backend.market_data.providers.base import ProviderError
from backend.market_data.service import MarketDataService
from backend.security.validation import validate_instrument_id, validate_symbol
from backend.api.deps import get_market_service, get_registry
from backend.api.schemas import BarsResponse, QuoteResponse

router = APIRouter(prefix="/api/market_data", tags=["market_data"])

#: Contract alias router: /api/securities/{instrument_id}/quote|bars
securities_router = APIRouter(prefix="/api/securities", tags=["securities"])


def _utcnow():  # type: ignore[no-untyped-def]
    """Wall-clock hook (module-level so tests can pin `now` deterministically)."""
    from datetime import datetime as _dt, timezone as _tz

    return _dt.now(_tz.utc)


def _enrich_market_state(out: dict) -> dict:
    """M6/M7: layer exchange calendar (open/closed) over service freshness.

    Service owns freshness (open/delayed/stale); here we upgrade to a
    calendar-aware state via health.market_state(mic=...) so closed markets
    (weekend/holiday/off-hours, XSHG lunch -> closed) are correctly
    identified. Never breaks the contract: falls back to the service value
    on any failure. Currency + exchange are already surfaced via
    out["currency"] and out["instrument"].
    """
    try:
        inst = out.get("instrument") or {}
        mic = inst.get("exchange_mic") if isinstance(inst, dict) else None
        prov = out.get("provenance") or {}
        as_of_raw = prov.get("as_of") if isinstance(prov, dict) else None
        if not mic or not as_of_raw:
            return out
        from datetime import datetime as _dt, timezone as _tz
        as_of = _dt.fromisoformat(as_of_raw) if isinstance(as_of_raw, str) else as_of_raw
        delay = prov.get("delay_minutes", 15) if isinstance(prov, dict) else 15
        # Wall-clock now: staleness/calendar at the present moment, never at
        # the data timestamp (now=as_of would pin age to 0 and badge stale
        # data MARKET OPEN).
        out["market_state"] = _calendar_market_state(
            as_of, delay_minutes=int(delay), mic=mic, now=_utcnow()
        )
    except Exception:
        pass
    return out


@router.get("/quote", response_model=QuoteResponse)
def quote(
    symbol: str = Query(..., min_length=1, description="e.g. AAPL, 600519.SS, MC.PA"),
    market: str | None = Query(default=None, description="Optional MIC scope"),
    svc: MarketDataService = Depends(get_market_service),
):
    """GET /api/market_data/quote?symbol=AAPL"""
    symbol = validate_symbol(symbol)
    try:
        return _enrich_market_state(svc.get_quote(symbol, market))
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"quote failed: {exc}") from exc


@router.get("/bars", response_model=BarsResponse)
def bars(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=30, ge=1, le=250),
    svc: MarketDataService = Depends(get_market_service),
):
    symbol = validate_symbol(symbol)
    timeframe = _check_timeframe(timeframe)
    try:
        return svc.get_bars(symbol, timeframe, limit)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"bars failed: {exc}") from exc


def _check_timeframe(value: object) -> str:
    text = str(value or "1d").strip()
    if len(text) > 8 or not text.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail="invalid timeframe")
    return text


@securities_router.get("/{instrument_id}/quote", response_model=QuoteResponse)
def security_quote(
    instrument_id: str,
    svc: MarketDataService = Depends(get_market_service),
    registry=Depends(get_registry),
):
    instrument_id = validate_instrument_id(instrument_id)
    try:
        inst = registry.get_by_id(instrument_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"instrument lookup failed: {exc}") from exc
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown instrument_id")
    try:
        return _enrich_market_state(svc.get_quote(inst.provider_symbol or inst.exchange_symbol, inst.exchange_mic))
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"quote failed: {exc}") from exc


@securities_router.get("/{instrument_id}/bars", response_model=BarsResponse)
def security_bars(
    instrument_id: str,
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=30, ge=1, le=250),
    svc: MarketDataService = Depends(get_market_service),
    registry=Depends(get_registry),
):
    instrument_id = validate_instrument_id(instrument_id)
    timeframe = _check_timeframe(timeframe)
    try:
        inst = registry.get_by_id(instrument_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"instrument lookup failed: {exc}") from exc
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown instrument_id")
    try:
        out = svc.get_bars(inst.provider_symbol or inst.exchange_symbol, timeframe, limit)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"bars failed: {exc}") from exc
    out["instrument_id"] = instrument_id
    return out
