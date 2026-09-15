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
    """Singleton FX provider (override in tests via dependency_overrides).

    Passive FX health: wired to the shared ProviderHealthTracker so live
    /api/fx calls feed the same per-provider latency/error/breaker stats
    as the equity chain (quota-aware; never raises).
    """
    global _fx_provider
    if _fx_provider is None:
        try:
            from backend.api.deps import get_health_tracker as _get_ht

            _tracker = _get_ht()
        except Exception:
            _tracker = None

        def _hook(p, ms, ok, **kw):  # type: ignore[no-untyped-def]
            if _tracker is None:
                return
            try:
                _tracker.record(p, ms, ok, status_code=kw.get("status_code"),
                                error=kw.get("error"))
            except TypeError:
                try:
                    _tracker.record(p, ms, ok)
                except Exception:
                    pass
            except Exception:
                pass

        try:
            _fx_provider = FXProvider(on_call=_hook if _tracker is not None else None)
        except Exception:
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
    symbols: list[str] = Field(min_length=1, max_length=20)
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
            raw = entry.get("as_of") if isinstance(entry, dict) else None
            if isinstance(raw, datetime):
                stamp = raw
            else:
                stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamps.append(stamp)
        except Exception:
            stamps.append(_utcnow())
    oldest = min(stamps)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    try:
        sources = sorted({str(e.get("source", "fx")) for e in entries if isinstance(e, dict)})
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
        sources, fallback, missing, max_delay, grade = ["fx"], True, [], 15, "C"
    return {
        "source": "+".join(sources),
        "as_of": oldest,
        "delay_minutes": max_delay,
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
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(
            status_code=400 if not exc.retryable else 502, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fx rate failed: {exc}") from exc
    try:
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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fx rate failed: {exc}") from exc


@router.post("/convert")
def fx_convert(
    body: ConvertRequest,
    fx: FXProvider = Depends(get_fx_provider),
) -> dict:
    """POST /api/fx/convert {amount, from, to} -> converted + provenance."""
    import math as _math

    _check_ccy(body.from_ccy)
    _check_ccy(body.to_ccy)
    try:
        amount = float(body.amount)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="amount must be a finite number") from exc
    if isinstance(body.amount, bool) or not _math.isfinite(amount):
        raise HTTPException(status_code=400, detail="amount must be a finite number")
    try:
        entry = fx.get_rate(body.from_ccy, body.to_ccy)
    except HTTPException:
        raise
    except ProviderError as exc:
        raise HTTPException(
            status_code=400 if not exc.retryable else 502, detail=str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fx convert failed: {exc}") from exc
    try:
        rate = float(entry["rate"])
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=f"fx convert failed: {exc}") from exc
    if not _math.isfinite(rate):
        raise HTTPException(status_code=502, detail="fx convert failed: non-finite rate")
    converted = amount * rate
    if not _math.isfinite(converted):
        raise HTTPException(status_code=502, detail="fx convert failed: non-finite result")
    try:
        provenance = fx.provenance_for(entry).model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fx convert failed: {exc}") from exc
    return {
        "amount": body.amount,
        "from": entry["base"],
        "to": entry["quote"],
        "converted": converted,
        "rate": entry["rate"],
        "inverse": entry["inverse"],
        "as_of": entry["as_of"],
        "source": entry["source"],
        "fallback_used": entry["fallback_used"],
        "provenance": provenance,
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
    skipped: list[dict] = []
    seen: set[str] = set()
    quote_cache: dict[str, dict] = {}
    for sym in symbols:
        # Avoid duplicate registry/market-service work per request: one
        # quote per unique symbol (case-insensitive), results reused.
        key = sym.upper()
        if key in seen:
            cached = quote_cache.get(key)
            if cached is not None:
                items.append(dict(cached))
            continue
        seen.add(key)
        try:
            quote_out = svc.get_quote(sym)
        except HTTPException:
            raise
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            skipped.append({"symbol": key, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        try:
            item = {
                "symbol": quote_out.get("symbol", sym.upper()),
                "price": quote_out.get("price"),
                "currency": (quote_out.get("currency") or "USD").upper(),
            }
        except Exception as exc:
            skipped.append({"symbol": key, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        quote_cache[key] = dict(item)
        items.append(item)
    if not items:
        raise HTTPException(status_code=502, detail="fx rank failed: no quotable symbols")

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
        except HTTPException:
            raise
        except ProviderError as exc:
            raise HTTPException(
                status_code=400 if not exc.retryable else 502, detail=str(exc)
            ) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"fx rate failed: {exc}") from exc
        try:
            rates[entry["pair"]] = entry["rate"]
            prov_entries.append(fx.provenance_for(entry).model_dump(mode="json"))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"fx rate failed: {exc}") from exc

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
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"fx rank failed: {exc}") from exc
    if skipped:
        # Additive degrade channel (mirrors screener/markets skipped[]).
        result["skipped"] = skipped
    return jsonable_encoder(result)
