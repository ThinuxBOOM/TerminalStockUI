"""Instrument routers: search / resolve / detail (exchange-aware, never bare ticker)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.instruments.calendars import SUPPORTED_MICS, market_state_at
from backend.instruments.registry import InstrumentRegistry
from backend.instruments.search import search_instruments
from backend.market_data.provenance import build_provenance
from backend.api.deps import get_registry
from backend.api.schemas import InstrumentOut, SearchResponse

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


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
    q: str = Query(..., min_length=1, description="Symbol fragment or company name"),
    market: str | None = Query(default=None, description="Filter by MIC, e.g. XNAS"),
    limit: int = Query(default=10, ge=1, le=50),
    registry: InstrumentRegistry = Depends(get_registry),
):
    """GET /api/instruments/search?q=AAPL&market=XNAS&limit=10"""
    if market and market.upper() not in SUPPORTED_MICS:
        raise HTTPException(status_code=422, detail=f"unsupported market {market!r}")
    results = search_instruments(registry.all(), q, market=market, limit=limit)
    provenance = build_provenance(
        "instrument-registry", as_of=datetime.now(timezone.utc),
        delay_minutes=0, quality_grade="A", fallback_used=False, missing_fields=[],
    )
    return {"query": q, "market": market.upper() if market else None,
            "results": [_out(r) for r in results],
            "provenance": provenance.model_dump(mode="json")}


@router.get("/resolve")
def resolve(
    symbol: str = Query(..., min_length=1),
    market: str | None = Query(default=None),
    registry: InstrumentRegistry = Depends(get_registry),
):
    """Resolve raw input incl. .SS/.PA/.AS/.BR suffixes; surfaces ambiguity."""
    inst, candidates, ambiguous = registry.resolve(symbol, market)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"no instrument for {symbol!r}")
    provenance = build_provenance("instrument-registry", delay_minutes=0, quality_grade="A")
    return {"instrument": _out(inst),
            "candidates": [_out(c) for c in candidates],
            "ambiguous": ambiguous,
            "market_state": _market_state_for(inst),
            "currency": inst.currency,
            "exchange_mic": inst.exchange_mic,
            "provenance": provenance.model_dump(mode="json")}


@router.get("/{instrument_id}")
def detail(instrument_id: str, registry: InstrumentRegistry = Depends(get_registry)):
    inst = registry.get_by_id(instrument_id)
    if inst is None:
        raise HTTPException(status_code=404, detail="unknown instrument_id")
    provenance = build_provenance("instrument-registry", delay_minutes=0, quality_grade="A")
    return {"instrument": _out(inst),
            "market_state": _market_state_for(inst),
            "currency": inst.currency,
            "exchange_mic": inst.exchange_mic,
            "provenance": provenance.model_dump(mode="json")}
