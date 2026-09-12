"""FX router (M7): /api/fx rate/pairs/convert + gated cross-market rank demo.

Every response carries the standard provenance envelope
``{source, as_of, delay_minutes, quality_grade, fallback_used, missing_fields}``.

Gate: POST /rank refuses with HTTP 423 + ``code FX_PROVENANCE_MISSING``
when FX rates are stale (>24h) or fallback without explicit
``allow_fallback=true``. Single-rate lookup and conversion always succeed
(live or flagged stub) and leave the decision to the caller, except on
invalid input (400) or provider validation errors.

Wire-up (backend/api/main.py is NOT edited by the FX build; add one line):

    from backend.api.fx import router as fx_router
    app.include_router(fx_router)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.deps import get_market_service
from backend.market_data.fx.convert import (
    CODE as FX_GATE_CODE,
    FXProvenanceMissing,
    rank_cross_market,
)
from backend.market_data.fx.provider import SUPPORTED_CURRENCIES, FXProvider
from backend.market_data.provenance import build_provenance
from backend.market_data.providers.base import ProviderError
from backend.market_data.quality import grade_quality

router = APIRouter(prefix="/api/fx", tags=["fx"])

_fx_provider: FXProvider | None = None


def get_fx_provider() -> FXProvider:
    """Singleton FX provider (override in tests via dependency_overrides)."""
    global _fx_provider
    if _fx_provider is None:
        _fx_provider = FXProvider()
    return _fx_provider


def reset_fx_provider() -> None:  # test hook
    global _fx_provider
    _fx_provider = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConvertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amount: float
    from_ccy: str = Field(validation_alias="from")
    to_ccy: str = Field(validation_alias="to")


class RankRequest(BaseModel):
    symbols: list[str] = Field(min_length=1)
    target_ccy: str = "USD"
    allow_fallback: bool = False


def _check_ccy(code: str) -> str:
    text = (code or "").strip().upper()
    if text not in SUPPORTED_CURRENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported currency {code!r}: expected one of {list(SUPPORTED_CURRENCIES)}",
        )
    return text


def _combine_provenance(entries: list[dict]) -> dict:
    """Merge per-rate provenance dicts (JSON form) into one gate envelope."""
    if not entries:
        return build_provenance(
            "fx", as_of=_utcnow(), delay_minutes=15,
            quality_grade="C", fallback_used=True,
            missing_fields=["rates"],
        ).model_dump(mode="json")
    stamps: list[datetime] = []
    for entry in entries:
        try:
            stamps.append(datetime.fromisoformat(str(entry["as_of"]).replace("Z", "+00:00")))
        except (KeyError, ValueError):
            stamps.append(_utcnow())
    oldest = min(stamps)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    sources = sorted({str(e.get("source", "fx")) for e in entries})
    fallback = any(bool(e.get("fallback_used")) for e in entries)
    missing = sorted({m for e in entries for m in (e.get("missing_fields") or [])})
    grade, _ = grade_quality(
        delay_minutes=max(int(e.get("delay_minutes", 15) or 0) for e in entries),
        age_minutes=max(0.0, (_utcnow() - oldest).total_seconds() / 60),
        missing_fields=missing,
        fallback_used=fallback,
        reconciled=False,
    )
    return {
        "source": "+".join(sources),
        "as_of": oldest,
        "delay_minutes": max(int(e.get("delay_minutes", 15) or 0) for e in entries),
        "quality_grade": grade,
        "fallback_used": fallback,
        "missing_fields": missing,
    }


@router.get("/pairs")
def fx_pairs() -> dict:
    """GET /api/fx/pairs -> supported currencies + quoted pairs (static)."""
    provenance = build_provenance(
        "fx", as_of=_utcnow(), delay_minutes=0,
        quality_grade="A", fallback_used=False, missing_fields=[],
    )
    return {
        "currencies": list(SUPPORTED_CURRENCIES),
        "pairs": ["EUR/USD", "USD/CNY", "EUR/CNY"],
        "provenance": provenance.model_dump(mode="json"),
    }


@router.get("/rate")
def fx_rate(
    base: str = Query(..., min_length=1, description="e.g. EUR"),
    quote: str = Query(..., min_length=1, description="e.g. USD"),
    fx: FXProvider = Depends(get_fx_provider),
) -> dict:
    """GET /api/fx/rate?base=EUR&quote=USD -> rate + provenance."""
    _check_ccy(base)
    _check_ccy(quote)
    try:
        entry = fx.get_rate(base, quote)
    except ProviderError as exc:
        raise HTTPException(
            status_code=400 if not exc.retryable else 502, detail=str(exc)
        ) from exc
    return {
        "pair": entry["pair"],
        "base": entry["base"],
        "quote": entry["quote"],
        "rate": entry["rate"],
        "inverse": entry["inverse"],
        "as_of": entry["as_of"],
        "source": entry["source"],
        "fallback_used": entry["fallback_used"],
        "provenance": fx.provenance_for(entry).model_dump(mode="json"),
    }


@router.post("/convert")
def fx_convert(
    body: ConvertRequest,
    fx: FXProvider = Depends(get_fx_provider),
) -> dict:
    """POST /api/fx/convert {amount, from, to} -> converted + provenance."""
    _check_ccy(body.from_ccy)
    _check_ccy(body.to_ccy)
    try:
        entry = fx.get_rate(body.from_ccy, body.to_ccy)
    except ProviderError as exc:
        raise HTTPException(
            status_code=400 if not exc.retryable else 502, detail=str(exc)
        ) from exc
    return {
        "amount": body.amount,
        "from": entry["base"],
        "to": entry["quote"],
        "converted": body.amount * entry["rate"],
        "rate": entry["rate"],
        "inverse": entry["inverse"],
        "as_of": entry["as_of"],
        "source": entry["source"],
        "fallback_used": entry["fallback_used"],
        "provenance": fx.provenance_for(entry).model_dump(mode="json"),
    }


@router.post("/rank")
def fx_rank(
    body: RankRequest,
    svc=Depends(get_market_service),
    fx: FXProvider = Depends(get_fx_provider),
):
    """POST /api/fx/rank {symbols, target_ccy} -> gated cross-market ranking.

    Refuses with HTTP 423 + ``code FX_PROVENANCE_MISSING`` when FX rates are
    stale (>24h) or fallback without ``allow_fallback=true``.
    """
    target = _check_ccy(body.target_ccy)
    symbols = [(s or "").strip() for s in body.symbols if (s or "").strip()]
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols must not be empty")

    items: list[dict] = []
    for sym in symbols:
        try:
            quote_out = svc.get_quote(sym)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        items.append({
            "symbol": quote_out.get("symbol", sym.upper()),
            "price": quote_out.get("price"),
            "currency": (quote_out.get("currency") or "USD").upper(),
        })

    rates: dict[str, float] = {}
    prov_entries: list[dict] = []
    for ccy in sorted({i["currency"] for i in items}):
        if ccy not in SUPPORTED_CURRENCIES:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported quote currency {ccy!r}: expected one of "
                       f"{list(SUPPORTED_CURRENCIES)}",
            )
        try:
            entry = fx.get_rate(ccy, target)
        except ProviderError as exc:
            raise HTTPException(
                status_code=400 if not exc.retryable else 502, detail=str(exc)
            ) from exc
        rates[entry["pair"]] = entry["rate"]
        prov_entries.append(fx.provenance_for(entry).model_dump(mode="json"))

    combined = _combine_provenance(prov_entries)
    try:
        # Gate enforced inside rank_cross_market (single refuse-point).
        result = rank_cross_market(
            items, target, rates, combined, allow_fallback=body.allow_fallback
        )
    except FXProvenanceMissing as exc:
        return JSONResponse(
            status_code=423,
            content=jsonable_encoder({
                "error": {
                    "code": FX_GATE_CODE,
                    "message": str(exc),
                    "retryable": exc.retryable,
                    "provenance": combined,
                }
            }),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return jsonable_encoder(result)
