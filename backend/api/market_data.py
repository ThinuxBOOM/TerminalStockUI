"""Market-data routers: quote + bars. Every response carries provenance."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.market_data.health import market_state as _calendar_market_state
from backend.market_data.providers.base import ProviderError
from backend.market_data.service import MarketDataService
from backend.security.validation import (
    sanitize_error,
    validate_instrument_id,
    validate_symbol,
    validate_timeframe,
)
from backend.analytics.technical.overlays import compute_indicators, parse_indicators
from backend.api.deps import get_market_service, get_registry
from backend.api.schemas import BarsResponse, ChartResponse, QuoteResponse

router = APIRouter(prefix="/api/market_data", tags=["market_data"])

INDICATOR_MAX_POINTS = 1000

#: Chart response cache (anonymous, identical for all callers): the
#: quote+bars dual fetch + stitch dominates per-view cost. TTL 60s mirrors
#: the quote cache. The calendar enrichment stays OUTSIDE the cache (it
#: reads wall-clock now) so market_state never goes stale.
_CHART_TTL_S = 60


def _chart_cache_key(symbol: str, timeframe: str, limit: int) -> str:
    try:
        return f"market_data:chart:{str(symbol).upper()}:{str(timeframe)}:{int(limit)}"
    except Exception:
        return f"market_data:chart:{symbol}:{timeframe}:{limit}"

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
    symbol: str = Query(..., min_length=1, max_length=32, description="e.g. AAPL, 600519.SS, MC.PA"),
    market: str | None = Query(default=None, description="Optional MIC scope"),
    svc: MarketDataService = Depends(get_market_service),
):
    """GET /api/market_data/quote?symbol=AAPL"""
    symbol = validate_symbol(symbol)
    if market is not None and str(market).strip():
        market = validate_symbol(str(market).strip(), field="market")
    try:
        return _enrich_market_state(svc.get_quote(symbol, market))
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="quote failed")) from exc


@router.get("/bars", response_model=BarsResponse)
def bars(
    symbol: str = Query(..., min_length=1, max_length=32),
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=30, ge=1, le=1000),
    svc: MarketDataService = Depends(get_market_service),
):
    symbol = validate_symbol(symbol)
    timeframe = validate_timeframe(timeframe)
    try:
        return svc.get_bars(symbol, timeframe, limit)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="bars failed")) from exc


@router.get("/chart", response_model=ChartResponse)
def chart(
    symbol: str = Query(..., min_length=1, max_length=32),
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=90, ge=1, le=1000),
    svc: MarketDataService = Depends(get_market_service),
):
    """GET /api/market_data/chart?symbol=AAPL&timeframe=1d&limit=90.

    One backend handling returns the live quote AND the bars series synced
    with it, so the header price and the chart's last print are the same
    number from the same call (no quote-vs-bars time skew). Same-date
    sessions update the terminal bar in place; newer sessions append an
    honest forming bar (``forming=true``); quote failure degrades to
    ``quote=null`` + ``stitched=false`` (bars still served); bars failure
    is 502 as today.
    """
    symbol = validate_symbol(symbol)
    timeframe = validate_timeframe(timeframe)
    # Response cache: identical charts within TTL skip the dual fetch.
    _ck: str | None = None
    try:
        from backend.cache import get_cache as _get_cache

        _ck = _chart_cache_key(symbol, timeframe, int(limit))
        _cached = _get_cache().get(_ck)
        if isinstance(_cached, dict) and isinstance(_cached.get("bars"), list):
            try:
                if isinstance(_cached.get("quote"), dict):
                    _cached["quote"] = _enrich_market_state(dict(_cached["quote"]))
            except Exception:
                pass
            return _cached
    except Exception:
        _ck = None
    try:
        out = svc.get_chart(symbol, timeframe, limit)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="chart failed")) from exc
    try:
        if _ck:
            from backend.cache import get_cache as _get_cache2

            _get_cache2().set(_ck, out, ttl_s=_CHART_TTL_S)
    except Exception:
        pass
    try:
        if isinstance(out.get("quote"), dict):
            out["quote"] = _enrich_market_state(out["quote"])
    except Exception:
        pass
    return out


@router.get("/indicators")
def market_indicators(
    symbol: str = Query(..., min_length=1, max_length=32),
    indicators: str | None = Query(
        default=None,
        description="Comma-separated overlays, e.g. SMA20,EMA12,RSI14,MACD,BB20,VWAP,ATR14",
    ),
    timeframe: str = Query(default="1d"),
    limit: int = Query(default=120, ge=1, le=1000),
    svc: MarketDataService = Depends(get_market_service),
):
    """GET /api/market_data/indicators?symbol=&indicators=&timeframe=&limit=.

    Distinct path from /bars and /quote. Same overlay validation as
    /api/analytics (unknown -> 422 exact detail). Returns the
    {requested, bars, max_points, series} wrapper with provenance, plus
    flat series duplicates for the frontend passthrough.
    """
    symbol = validate_symbol(symbol)
    timeframe = validate_timeframe(timeframe)
    if indicators is None or not str(indicators).strip():
        wanted: list[str] = []
    else:
        try:
            wanted = parse_indicators(indicators)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        payload = svc.get_bars(symbol, timeframe, limit)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=sanitize_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="indicators failed")) from exc
    rows = payload.get("bars", []) if isinstance(payload, dict) else []
    provenance = dict(payload.get("provenance", {})) if isinstance(payload, dict) else {}
    # Drop malformed rows before framing (vendor gaps must degrade, not 502).
    try:
        clean: list[dict] = []
        for r in rows:
            try:
                if not isinstance(r, dict):
                    continue
                import math as _math
                if not _math.isfinite(float(r.get("close"))):  # type: ignore[arg-type]
                    continue
                clean.append(r)
            except (TypeError, ValueError):
                continue
        rows = clean
    except Exception:
        pass
    try:
        import pandas as _pd

        frame = _pd.DataFrame(
            {
                "open": [r.get("open") for r in rows],
                "high": [r.get("high") for r in rows],
                "low": [r.get("low") for r in rows],
                "close": [r.get("close") for r in rows],
                "volume": [float(r.get("volume") or 0) for r in rows],
            },
            index=_pd.to_datetime([r.get("ts") for r in rows]) if rows else [],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"indicators frame failed: {exc}") from exc
    try:
        computed = compute_indicators(frame, wanted, max_points=INDICATOR_MAX_POINTS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    nested: dict = dict(computed)
    nested["provenance"] = provenance
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "requested": list(wanted),
        "bars": int(len(frame)),
        "max_points": INDICATOR_MAX_POINTS,
        "series": dict(computed.get("series", {})),
        "indicators": nested,
        "provenance": provenance,
    }


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
    limit: int = Query(default=30, ge=1, le=1000),
    svc: MarketDataService = Depends(get_market_service),
    registry=Depends(get_registry),
):
    instrument_id = validate_instrument_id(instrument_id)
    timeframe = validate_timeframe(timeframe)
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
