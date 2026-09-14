"""Instrument routers: search / resolve / detail (exchange-aware, never bare ticker)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.instruments.calendars import SUPPORTED_MICS, market_state_at
from backend.instruments.registry import InstrumentRegistry
from backend.instruments.search import search_instruments
from backend.market_data.provenance import build_provenance
from backend.api.deps import get_registry
from backend.api.schemas import InstrumentOut, SearchResponse

router = APIRouter(prefix="/api/instruments", tags=["instruments"])

log = logging.getLogger(__name__)


def _out(inst) -> dict:
    return InstrumentOut(**inst.model_dump_canonical()).model_dump()


def _market_state_for(inst) -> str:
    """Current wall-clock state for an instrument (M6/M7: open|closed|lunch)."""
    try:
        return market_state_at(inst.exchange_mic, datetime.now(timezone.utc))
    except Exception:
        return "closed"


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, max_length=100, description="Symbol fragment or company name"),
    market: str | None = Query(default=None, description="Filter by MIC, e.g. XNAS"),
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=200, description="Skip first N matches"),
    registry: InstrumentRegistry = Depends(get_registry),
):
    """GET /api/instruments/search?q=AAPL&market=XNAS&limit=10"""
    if market and market.upper() not in SUPPORTED_MICS:
        raise HTTPException(status_code=422, detail=f"unsupported market {market!r}")
    try:
        # Fetch one extra page so offset pagination doesn't require a second scan.
        candidates = search_instruments(registry.all(), q, market=market, limit=limit + offset)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("instrument search failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="instrument search failed") from exc
    if offset:
        candidates = candidates[offset:]
    else:
        candidates = candidates[:limit]
    # Cap to limit after offset (search_instruments already caps limit+offset).
    candidates = candidates[:limit]
    results: list[dict] = []
    dropped = 0
    for r in candidates:
        try:
            results.append(_out(r))
        except Exception:
            dropped += 1
            continue
    if dropped:
        log.warning("instrument search dropped %d unserializable rows for q=%r", dropped, q[:50])
    provenance = build_provenance(
        "instrument-registry", as_of=datetime.now(timezone.utc),
        delay_minutes=0, quality_grade="A", fallback_used=False, missing_fields=[],
    )
    return {"query": q, "market": market.upper() if market else None,
            "results": results,
            "provenance": provenance.model_dump(mode="json")}


@router.get("/resolve")
def resolve(
    symbol: str = Query(..., min_length=1, max_length=100),
    market: str | None = Query(default=None),
    registry: InstrumentRegistry = Depends(get_registry),
):
    """Resolve raw input incl. .SS/.PA/.AS/.BR suffixes; surfaces ambiguity."""
    try:
        inst, candidates, ambiguous = registry.resolve(symbol, market)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("instrument resolve failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="instrument resolve failed") from exc
    if inst is None:
        raise HTTPException(status_code=404, detail=f"no instrument for {symbol!r}")
    try:
        instrument_out = _out(inst)
        candidates_out = [_out(c) for c in candidates]
    except Exception as exc:
        log.warning("instrument resolve serialization failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="instrument resolve failed") from exc
    provenance = build_provenance("instrument-registry", delay_minutes=0, quality_grade="A")
    return {"instrument": instrument_out,
            "candidates": candidates_out,
            "ambiguous": ambiguous,
            "market_state": _market_state_for(inst),
            "currency": inst.currency,
            "exchange_mic": inst.exchange_mic,
            "provenance": provenance.model_dump(mode="json")}


@router.get("/{instrument_id}")
def detail(instrument_id: str, registry: InstrumentRegistry = Depends(get_registry)):
    try:
        inst = registry.get_by_id(instrument_id)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("instrument lookup failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="instrument lookup failed") from exc
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown instrument_id")
    try:
        instrument_out = _out(inst)
    except Exception as exc:
        log.warning("instrument lookup serialization failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="instrument lookup failed") from exc
    provenance = build_provenance("instrument-registry", delay_minutes=0, quality_grade="A")
    return {"instrument": instrument_out,
            "market_state": _market_state_for(inst),
            "currency": inst.currency,
            "exchange_mic": inst.exchange_mic,
            "provenance": provenance.model_dump(mode="json")}
