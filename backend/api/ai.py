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

import logging
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
    symbol: str = Field(min_length=1, max_length=32)
    profile: str = Field(default="quick_insight", max_length=64)


class ForecastOpinionBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    horizon: int = Field(default=21)
    profile: str = Field(default="forecast_assist", max_length=64)
    quant_prob: float | None = Field(default=None)
    ai_weight: float | None = Field(default=None)
    ai_enabled: bool = True


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
    included (see backend/ai/evidence.py forbidden keys).
    """
    clean = (symbol or "").strip().upper()
    if not clean:
        raise HTTPException(status_code=422, detail="symbol must be a non-empty string")
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
    provenance = quote.get("provenance") or {}
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
    packet = _build_packet(body.symbol)
    opinion, cached = await ai.get_insight(packet, profile=profile)
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
    if body.quant_prob is not None and not 0.0 <= float(body.quant_prob) <= 1.0:
        raise HTTPException(status_code=422, detail="quant_prob must be in [0, 1]")
    try:
        weight_preview = resolve_ai_weight(body.ai_weight, ai_enabled=body.ai_enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    packet = _build_packet(body.symbol)
    opinion, cached = await ai.get_insight(packet, profile=profile, horizon=horizon)
    quant_prob = float(body.quant_prob) if body.quant_prob is not None else 0.5
    blend = blend_forecast(
        quant_prob, opinion, ai_weight=body.ai_weight, ai_enabled=body.ai_enabled
    )
    logger.info(
        "ai forecast_opinion %s",
        redact_mapping({"symbol": packet.symbol, "horizon": horizon, "cached": cached}),
    )
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
        "disclaimer": DISCLAIMER,
    }


@router.get("/providers/performance")
def providers_performance(
    exchange: str | None = None, horizon: int | None = None,
    ai: AIRouter = Depends(get_ai_router),
) -> dict[str, Any]:
    if horizon is not None and horizon not in (5, 21, 63):
        raise HTTPException(status_code=422, detail="horizon must be one of 5, 21, 63")
    rows = ai.performance.summary(exchange=exchange, horizon=horizon)
    return {"rows": redact_mapping({"rows": rows})["rows"], "disclaimer": DISCLAIMER}


@router.post("/providers/health/test")
def providers_health_test(
    body: ProviderHealthTestBody | None = None, ai: AIRouter = Depends(get_ai_router)
) -> dict[str, Any]:
    name = (body.provider if body and body.provider else "").strip().lower() or None
    if name is not None and name not in ai.providers:
        raise HTTPException(status_code=422, detail=f"unknown provider: {name!r}")
    targets = [name] if name else sorted(ai.providers)
    # health() exposes configuration only — never key material.
    results = [redact_mapping(ai.providers[target].health()) for target in targets]
    return {"providers": results}
