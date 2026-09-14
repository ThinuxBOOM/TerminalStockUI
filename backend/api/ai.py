"""AI API router (Milestone 4-5). Prefix /api/ai.

Endpoints:
    POST /insight            {symbol, profile} -> validated AIOpinion
    POST /forecast_opinion   {symbol, horizon[, profile, quant_prob,
                             ai_weight, ai_enabled]} -> opinion + blend preview
    GET  /providers/performance[?exchange&horizon] -> tracker scoreboard
    POST /providers/health/test [{provider}] -> safe health (never key material)

Security: keys are encrypted at rest, decrypted only inside provider
insight() at call time, never returned in responses, redacted in logs.
No key material ever reaches the browser.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.ai.blend import blend_forecast, resolve_ai_weight
from backend.ai.evidence import build_evidence_packet
from backend.ai.prompts import PROFILES
from backend.ai.router import AIRouter
from backend.ai.schemas import DISCLAIMER, EvidencePacket
from backend.security.secrets import redact_mapping

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

_ai_router: AIRouter | None = None


def get_ai_router() -> AIRouter:
    global _ai_router
    if _ai_router is None:
        _ai_router = AIRouter()
    return _ai_router


def reset_ai_router() -> None:  # test hook
    global _ai_router
    _ai_router = None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class InsightBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$")
    profile: str = Field(default="quick_insight", max_length=64)


class ForecastOpinionBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$")
    horizon: int = Field(default=21)
    profile: str = Field(default="forecast_assist", max_length=64)
    quant_prob: float | None = Field(default=None)
    ai_weight: float | None = Field(default=None)
    ai_enabled: bool = True
    quant_confidence_label: str | None = Field(default=None, max_length=16)


class ProviderHealthTestBody(BaseModel):
    provider: str | None = Field(default=None, max_length=64)


def _check_profile(profile: str) -> str:
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in PROFILES:
        raise HTTPException(status_code=422, detail=f"unknown AI profile: {profile!r}; expected one of {list(PROFILES)}")
    return key


def _check_horizon(horizon: Any) -> int:
    if isinstance(horizon, bool):
        raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
    if isinstance(horizon, float):
        if not horizon.is_integer():
            raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
        horizon = int(horizon)
    elif isinstance(horizon, str):
        text = horizon.strip()
        if not text.lstrip("+-").isdigit():
            raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
        horizon = int(text)
    try:
        value = int(horizon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
    if value not in (5, 21, 63):
        raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
    return value


def _build_packet(symbol: str) -> EvidencePacket:
    """Assemble a bounded evidence packet from deterministic services.

    Only summary-level fields enter the packet — raw bars/candles are never
    included (see backend/ai/evidence.py forbidden keys). The market-data
    lookup is blocking (yfinance is sync I/O); async callers below run this
    helper in a worker thread via :func:`asyncio.to_thread`.
    """
    clean = (symbol or "").strip().upper()
    if not clean:
        raise HTTPException(status_code=422, detail="symbol must be a non-empty string")
    if len(clean) > 32 or re.match(r"^[A-Z0-9][A-Z0-9.\-:]{0,31}$", clean) is None:
        raise HTTPException(status_code=422, detail="symbol must match ^[A-Z0-9][A-Z0-9.\\-:]{0,31}$")
    try:
        from backend.api.deps import get_market_service

        service = get_market_service()
        quote = service.get_quote(clean)
    except HTTPException:
        raise
    except Exception as exc:  # market data unavailable -> minimal degraded packet
        logger.warning("ai packet degraded for %s: %s", clean, redact_mapping({"error": str(exc)}))
        return build_evidence_packet(
            clean,
            {"quote_unavailable": True, "limitations": ["market data unavailable; AI opinion is low-confidence stub-grade"]},
            {"source": "unknown", "quality_grade": "F", "delay_minutes": 15, "fallback_used": True},
        )
    try:
        return _build_packet_from_quote(clean, quote)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ai packet build failed for %s: %s", clean, redact_mapping({"error": str(exc)}))
        raise HTTPException(status_code=502, detail=f"evidence packet failed: {exc}") from exc


def _build_packet_from_quote(clean: str, quote: dict) -> EvidencePacket:
    if not isinstance(quote, dict):
        raise ValueError("quote unavailable")
    provenance = quote.get("provenance") or {}
    if not isinstance(provenance, dict):
        provenance = {}
    price = quote.get("price")
    change_pct = quote.get("change_pct")
    deterministic: dict[str, Any] = {
        "quote": {
            "price": price, "change_pct": change_pct,
            "volume": quote.get("volume"), "currency": quote.get("currency"),
            "market_state": quote.get("market_state"),
        },
        "exchange_mic": (quote.get("instrument") or {}).get("exchange_mic", "") if isinstance(quote.get("instrument"), dict) else "",
    }
    bullish: list[dict[str, str]] = []
    risks: list[dict[str, str]] = []
    try:
        if isinstance(change_pct, (int, float)):
            if change_pct > 0:
                bullish.append({
                    "label": f"Positive session momentum {change_pct:+.2f}%",
                    "detail": f"price {price}, market_state {quote.get('market_state')}",
                    "source": str(provenance.get("source", "market-data")),
                })
            elif change_pct < 0:
                risks.append({
                    "label": f"Negative session momentum {change_pct:+.2f}%",
                    "detail": f"price {price}, market_state {quote.get('market_state')}",
                    "source": str(provenance.get("source", "market-data")),
                })
    except Exception:
        pass
    if provenance.get("fallback_used"):
        risks.append({
            "label": "Fallback/cached data in use",
            "detail": "quote served from cache after provider issue; freshness reduced",
            "source": str(provenance.get("source", "market-data")),
        })
    deterministic["top_bullish"] = bullish
    deterministic["top_risks"] = risks
    deterministic["events"] = [{
        "label": f"Quote {clean} @ {price} {quote.get('currency', 'USD')}",
        "detail": f"state={quote.get('market_state')} quality={provenance.get('quality_grade', '?')}",
        "source": str(provenance.get("source", "market-data")),
    }]
    instrument = quote.get("instrument")
    return build_evidence_packet(clean, deterministic, provenance, instrument=instrument)


@router.post("/insight")
async def post_insight(body: InsightBody, ai: AIRouter = Depends(get_ai_router)) -> dict[str, Any]:
    profile = _check_profile(body.profile)
    # get_quote/yfinance are blocking sync I/O — keep the event loop free.
    packet = await asyncio.to_thread(_build_packet, body.symbol)
    try:
        opinion, cached = await ai.get_insight(packet, profile=profile)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai insight failed: {exc}") from exc
    logger.info("ai insight %s", redact_mapping({"symbol": packet.symbol, "profile": profile, "cached": cached}))
    return {
        "symbol": packet.symbol,
        "profile": profile,
        "provider": opinion.provider,
        "model": opinion.model,
        "opinion": opinion.model_dump(mode="json"),
        "packet_id": packet.packet_id,
        "evidence_hash": packet.evidence_hash,
        "cached": cached,
        "provenance": packet.freshness.model_dump(mode="json"),
        "disclaimer": DISCLAIMER,
    }


@router.post("/forecast_opinion")
async def post_forecast_opinion(
    body: ForecastOpinionBody, ai: AIRouter = Depends(get_ai_router)
) -> dict[str, Any]:
    profile = _check_profile(body.profile)
    horizon = _check_horizon(body.horizon)
    try:
        quant_check = float(body.quant_prob) if body.quant_prob is not None else None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="quant_prob must be in [0, 1]") from exc
    if quant_check is not None and not 0.0 <= quant_check <= 1.0:
        raise HTTPException(status_code=422, detail="quant_prob must be in [0, 1]")
    try:
        weight_preview = resolve_ai_weight(body.ai_weight, ai_enabled=body.ai_enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai weight failed: {exc}") from exc
    # Blocking market-data lookup — run off the event loop (see post_insight).
    packet = await asyncio.to_thread(_build_packet, body.symbol)
    try:
        opinion, cached = await ai.get_insight(packet, profile=profile, horizon=horizon)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai insight failed: {exc}") from exc
    quant_prob = float(body.quant_prob) if body.quant_prob is not None else 0.5
    _lbl = body.quant_confidence_label
    if _lbl is not None:
        _lbl = str(_lbl).strip().lower()
        if _lbl not in ("low", "moderate", "high"):
            raise HTTPException(status_code=422, detail="quant_confidence_label must be low|moderate|high")
    try:
        blend = blend_forecast(
            quant_prob, opinion, ai_weight=body.ai_weight, ai_enabled=body.ai_enabled,
            quant_confidence_label=_lbl,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai blend failed: {exc}") from exc
    logger.info(
        "ai forecast_opinion %s",
        redact_mapping({"symbol": packet.symbol, "horizon": horizon, "cached": cached}),
    )
    try:
        provenance = packet.freshness.model_dump(mode="json")
    except Exception:
        provenance = {}
    return {
        "symbol": packet.symbol,
        "profile": profile,
        "provider": opinion.provider,
        "model": opinion.model,
        "opinion": opinion.model_dump(mode="json"),
        "blend": blend,
        "quant_source": "request" if body.quant_prob is not None else "unspecified-placeholder",
        "ai_weight_requested": body.ai_weight,
        "ai_weight_applied": weight_preview if body.ai_enabled else 0.0,
        "packet_id": packet.packet_id,
        "evidence_hash": packet.evidence_hash,
        "cached": cached,
        "provenance": provenance,
        "disclaimer": DISCLAIMER,
    }


@router.get("/providers/performance")
def providers_performance(
    exchange: str | None = None, horizon: int | None = None,
    ai: AIRouter = Depends(get_ai_router),
) -> dict[str, Any]:
    if horizon is not None and horizon not in (5, 21, 63):
        raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
    try:
        rows = ai.performance.summary(exchange=exchange, horizon=horizon)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"performance summary failed: {exc}") from exc
    return {"rows": redact_mapping({"rows": rows})["rows"], "disclaimer": DISCLAIMER}


@router.post("/providers/health/test")
def providers_health_test(
    body: ProviderHealthTestBody | None = None, ai: AIRouter = Depends(get_ai_router)
) -> dict[str, Any]:
    try:
        providers_map = ai.providers
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"provider health failed: {exc}") from exc
    name = (body.provider if body and body.provider else "").strip().lower() or None
    if name is not None and name not in providers_map:
        raise HTTPException(status_code=422, detail=f"unknown provider: {name!r}")
    targets = [name] if name else sorted(providers_map)
    # health() exposes configuration only — never key material.
    results: list[dict] = []
    for target in targets:
        try:
            results.append(redact_mapping(providers_map[target].health()))
        except Exception as exc:
            results.append({"provider": target, "error": f"{type(exc).__name__}: {exc}"})
    return {"providers": results}
