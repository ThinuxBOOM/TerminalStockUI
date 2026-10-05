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
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.ai.blend import blend_forecast, resolve_ai_weight
from backend.ai.evidence import build_evidence_packet
from backend.ai.prompts import PROFILES
from backend.ai.quota import enforce_ai_quota
from backend.ai.router import AIRouter
from backend.ai.schemas import DISCLAIMER, EvidencePacket
from backend.security.secrets import redact_mapping

from backend.auth.guards import get_current_user, require_admin, user_id_of
from backend.db.models import User


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"], dependencies=[Depends(get_current_user)])

_ai_router: AIRouter | None = None

#: Deep-research job store (distributed when Redis is configured, else
#: process-local). Jobs are best-effort: missing cache degrades to memory.
_jobs_memory: dict[str, dict[str, Any]] = {}


def _jobs_store() -> Any | None:
    try:
        from backend.cache import get_cache as _get_cache

        return _get_cache()
    except Exception:
        return None


def _job_get(job_id: str) -> dict[str, Any] | None:
    key = f"ai:job:{job_id}"
    try:
        store = _jobs_store()
        if store is not None:
            raw = store.get(key)
            if isinstance(raw, dict):
                return raw
    except Exception:
        pass
    return _jobs_memory.get(job_id)


def _job_put(job_id: str, doc: dict[str, Any], *, owner: str, ttl_s: int = 3600) -> None:
    doc = {**doc, "owner": owner}
    key = f"ai:job:{job_id}"
    try:
        store = _jobs_store()
        if store is not None:
            store.set(key, doc, ttl_s=ttl_s)
    except Exception:
        pass
    try:
        if len(_jobs_memory) > 500:
            _jobs_memory.pop(next(iter(_jobs_memory)))
        _jobs_memory[job_id] = doc
    except Exception:
        pass


def _persist_ledger(
    ai: AIRouter,
    *,
    provider: str,
    model: str,
    profile: str,
    call_type: str,
    packet: EvidencePacket,
    cached: bool,
    user_id: Any = None,
) -> None:
    """Best-effort ai_token_ledger persist from the router's last entry."""
    try:
        entry = ai.token_log[-1] if getattr(ai, "token_log", None) else None
        prompt_tok = int((entry or {}).get("prompt_tokens") or 0)
        comp_tok = int((entry or {}).get("completion_tokens") or 0)
        lat_raw = (entry or {}).get("latency_ms") or 0
        try:
            lat_ms = max(0, int(float(lat_raw)))
        except (TypeError, ValueError):
            lat_ms = None
        from backend.db.session import get_session_factory
        from backend.db.writers import log_ai_tokens

        Session = get_session_factory()
        db = Session()
        try:
            log_ai_tokens(
                db, provider=provider, model=model, profile=profile,
                call_type=call_type, input_tokens=0 if cached else prompt_tok,
                output_tokens=0 if cached else comp_tok, latency_ms=lat_ms,
                evidence_hash=getattr(packet, "evidence_hash", None),
                user_id=user_id,
            )
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception as exc:
        # Best-effort ledger must never be silent: DB-down loss is logged so
        # the audit gap is visible instead of vanishing.
        try:
            logger.warning("ai ledger persist missed provider=%s profile=%s: %s", provider, profile, type(exc).__name__)
        except Exception:
            pass


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
        raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
    if isinstance(horizon, float):
        if not horizon.is_integer():
            raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
        horizon = int(horizon)
    elif isinstance(horizon, str):
        text = horizon.strip()
        if not text.lstrip("+-").isdigit():
            raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
        horizon = int(text)
    try:
        value = int(horizon)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
    if value not in (1, 7, 14, 21):
        raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
    return value


def _require_live_provider(ai: AIRouter, profile: str) -> tuple[str, str]:
    """Fail-closed AI gate: the profile's provider must hold a usable key.

    No key -> HTTP 423 (AI disabled: explicit, honest, never a fake
    opinion). Deterministic forecasting is unaffected.
    """
    try:
        provider_name, model = ai.resolve(profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    configured = False
    try:
        provider = ai.providers.get(provider_name)
        health = provider.health() if provider is not None else {}
        configured = bool(health.get("configured", False))
    except Exception:
        configured = False
    if not configured:
        raise HTTPException(
            status_code=423,
            detail=(
                f"AI disabled: no API key configured for {provider_name} ({model}). "
                "Add a key in Provider Settings to enable AI opinions; "
                "the deterministic forecast is unaffected."
            ),
        )
    return provider_name, model


def _refuse_stub_opinion(opinion: Any, *, context: str) -> None:
    """Wire guard: a stub opinion must never reach the API response.

    With a configured provider, a stub means the live call failed
    (timeout/network/validation) — that is a 502, not a 200 with
    fabricated content.
    """
    try:
        is_stub = bool(getattr(opinion, "stub", False))
    except Exception:
        is_stub = True
    if not is_stub:
        return
    try:
        reasons = " ".join(getattr(opinion, "limitations", []) or [])
    except Exception:
        reasons = ""
    detail = f"AI {context} failed: live model call unsuccessful"
    if reasons:
        detail += f" ({str(reasons)[:200]})"
    raise HTTPException(status_code=502, detail=detail)


def _fetch_news_context(symbol: str, limit: int = 10, profile: str | None = None) -> dict[str, Any] | None:
    """Phase 6: best-effort news sentiment aggregate for AI evidence.

    Calls backend.api.news.get_news(symbols=symbol, limit) + the optional
    Investopedia scrape (when INVESTOPEDIA_ENABLED). Never raises: any
    failure (423 no-keys / 502 upstream / disabled flag) returns None so
    the evidence packet still builds from deterministic market data.

    Returns {mean_sentiment, bullish_count, bearish_count, article_count,
    articles:[{title, summary, url, sentiment, sentiment_label, created_at,
    source}]} or None when no articles are available.
    """
    clean = (symbol or "").strip().upper()
    if not clean:
        return None
    try:
        lim = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        lim = 10
    merged: list[dict[str, Any]] = []

    def _clip_text(value: Any, n: int) -> str:
        try:
            text = "" if value is None else str(value)
        except Exception:
            return ""
        return text.strip()[:n]

    # -- Alpaca news (best-effort) --
    try:
        from backend.api.news import get_news as _get_news

        payload = _get_news(symbols=clean, limit=lim)
        raw_arts = (payload.get("articles") or []) if isinstance(payload, dict) else []
        for art in raw_arts[:lim]:
            if not isinstance(art, dict):
                continue
            try:
                score = float(art.get("sentiment") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            score = max(-1.0, min(1.0, score))
            label = str(art.get("sentiment_label") or ("bullish" if score > 0.2 else ("bearish" if score < -0.2 else "neutral")))
            merged.append({
                "title": _clip_text(art.get("title"), 200),
                "summary": _clip_text(art.get("summary"), 280),
                "url": _clip_text(art.get("url"), 280),
                "sentiment": round(score, 3),
                "sentiment_label": (label.strip().lower()[:16] or "neutral"),
                "created_at": _clip_text(art.get("created_at"), 64),
                "source": _clip_text(art.get("author") or art.get("source") or "alpaca-news", 120) or "alpaca-news",
            })
    except Exception:
        pass

    # -- Investopedia scrape (flag-gated, best-effort; append + dedup) --
    try:
        from backend.market_data.providers import investopedia as _inves  # type: ignore

        fetch_fn = getattr(_inves, "fetch_symbol_sentiment", None)
        if callable(fetch_fn):
            try:
                inv = fetch_fn(clean)
            except Exception:
                inv = None
            inv_arts = []
            if isinstance(inv, dict):
                inv_arts = inv.get("articles") or []
            if isinstance(inv_arts, list):
                for art in inv_arts:
                    if not isinstance(art, dict):
                        continue
                    try:
                        raw_score = art.get("sentiment", 0.0)
                        inv_score = float(raw_score) if raw_score is not None else 0.0
                    except (TypeError, ValueError):
                        inv_score = 0.0
                    inv_score = max(-1.0, min(1.0, inv_score))
                    try:
                        merged.append({
                            "title": _clip_text(art.get("title"), 200),
                            "summary": _clip_text(art.get("summary"), 280),
                            "url": _clip_text(art.get("url"), 280),
                            "sentiment": round(inv_score, 3),
                            "sentiment_label": _clip_text(art.get("sentiment_label") or ("bullish" if inv_score > 0.2 else ("bearish" if inv_score < -0.2 else "neutral")), 16) or "neutral",
                            "created_at": _clip_text(art.get("created_at"), 64),
                            "source": _clip_text(art.get("source") or "investopedia", 120) or "investopedia",
                        })
                    except Exception:
                        continue
    except Exception:
        pass

    if not merged:
        return None
    # Deduplicate by url (fallback: title lower).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for art in merged:
        key = (art.get("url") or "").strip().lower() or (art.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        if not (art.get("title") or "").strip():
            continue
        seen.add(key)
        deduped.append(art)
    if not deduped:
        return None
    try:
        scores = [float(a.get("sentiment") or 0.0) for a in deduped]
    except (TypeError, ValueError):
        scores = [0.0]
    mean_sent = round(sum(scores) / len(scores), 3) if scores else 0.0
    bull_n = sum(1 for s in scores if s > 0.2)
    bear_n = sum(1 for s in scores if s < -0.2)
    ctx: dict[str, Any] = {
        "mean_sentiment": mean_sent,
        "bullish_count": int(bull_n),
        "bearish_count": int(bear_n),
        "article_count": len(deduped),
        "articles": deduped,
    }
    return _truncate_news_for_profile(ctx, profile)


def _truncate_news_for_profile(
    news_context: dict[str, Any] | None, profile: str | None
) -> dict[str, Any] | None:
    """Trim news articles to fit PROFILE_TOKEN_BUDGETS (best-effort).

    Caps (articles): quick 600->3, forecast 1000->5, report 2000->8,
    deep 4000->10. Aggregate counts are preserved (they reflect the full
    fetch); only the articles list is sliced so prompt JSON stays in
    budget (enforced again in evidence.summarize_packet_for_profile).
    """
    if not isinstance(news_context, dict) or not news_context:
        return None
    arts = news_context.get("articles")
    if not isinstance(arts, list) or not arts:
        return None
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    cap_map = {"quick_insight": 3, "forecast_assist": 5, "report": 8, "deep_research": 10}
    cap = cap_map.get(key, 10 if key in ("", "none") else 5)
    # Default fetch path (no profile) keeps up to 10.
    if profile is None:
        cap = min(len(arts), 10)
    try:
        trimmed = list(arts[: max(1, int(cap))])
    except (TypeError, ValueError):
        trimmed = list(arts[:5])
    out = dict(news_context)
    out["articles"] = trimmed
    return out


async def _packet_for(symbol: str, profile: str | None = None) -> EvidencePacket:
    """Mock-tolerant packet builder: passes profile when supported.

    Existing tests monkeypatch _build_packet(symbol) with a 1-arg lambda;
    production _build_packet(symbol, profile) takes 2. Inspect the arity
    so both work without editing tests.
    """
    import inspect as _inspect

    func = _build_packet
    try:
        n_params = len(_inspect.signature(func).parameters)
    except (TypeError, ValueError):
        n_params = 1
    if n_params >= 2:
        return await asyncio.to_thread(func, symbol, profile)
    return await asyncio.to_thread(func, symbol)


def _build_packet(symbol: str, profile: str | None = None) -> EvidencePacket:
    """Assemble a bounded evidence packet from deterministic services.

    Only summary-level fields enter the packet — raw bars/candles are never
    included (see backend/ai/evidence.py forbidden keys). The market-data
    lookup is blocking (yfinance is sync I/O); async callers below run this
    helper in a worker thread via :func:`asyncio.to_thread`.

    Phase 6: best-effort news sentiment (Alpaca + Investopedia when
    enabled) is merged via news_context — failures never fail the packet.

    Fail-closed: when market data is unavailable there is no honest
    evidence to ground an opinion on, so the request raises instead of
    assembling a degraded stub packet.
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
    except Exception as exc:
        from backend.market_data.providers.base import ProviderError as _PE

        logger.warning("ai packet failed for %s: %s", clean, redact_mapping({"error": str(exc)}))
        if isinstance(exc, _PE):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=f"evidence packet failed: {exc}") from exc
    try:
        return _build_packet_from_quote(clean, quote, profile=profile)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ai packet build failed for %s: %s", clean, redact_mapping({"error": str(exc)}))
        raise HTTPException(status_code=502, detail=f"evidence packet failed: {exc}") from exc


def _build_packet_from_quote(
    clean: str, quote: dict, news_context: dict[str, Any] | None = None, profile: str | None = None
) -> EvidencePacket:
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
    deterministic["top_bullish"] = bullish
    deterministic["top_risks"] = risks
    deterministic["events"] = [{
        "label": f"Quote {clean} @ {price} {quote.get('currency', 'USD')}",
        "detail": f"state={quote.get('market_state')} quality={provenance.get('quality_grade', '?')}",
        "source": str(provenance.get("source", "market-data")),
    }]
    instrument = quote.get("instrument")
    # Phase 6: resolve news context best-effort (never fails the packet).
    # An explicit news_context (tests/callers) wins; otherwise fetch live.
    resolved_news = news_context
    if resolved_news is None:
        try:
            # Limit by profile budget: quick 3 / forecast 5 / report 8 / deep 10.
            _cap_map = {"quick_insight": 4, "forecast_assist": 6, "report": 8, "deep_research": 10}
            _key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
            _limit = _cap_map.get(_key, 10)
            resolved_news = _fetch_news_context(clean, limit=_limit, profile=profile)
        except Exception:
            resolved_news = None
    else:
        try:
            resolved_news = _truncate_news_for_profile(resolved_news, profile)
        except Exception:
            pass
    return build_evidence_packet(clean, deterministic, provenance, instrument=instrument, news_context=resolved_news)


@router.post("/insight")
async def post_insight(
    body: InsightBody,
    ai: AIRouter = Depends(get_ai_router),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    profile = _check_profile(body.profile)
    _uid = user_id_of(user)
    enforce_ai_quota(_uid)
    # Fail-closed gate first: no key -> 423 before any evidence work.
    _require_live_provider(ai, profile)
    # get_quote/yfinance are blocking sync I/O — keep the event loop free.
    # Phase 6: profile-aware news context (best-effort, never fails packet).
    packet = await _packet_for(body.symbol, profile)
    try:
        opinion, cached = await ai.get_insight(
            packet, profile=profile,
            call_type="insight", user_id=_uid,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai insight failed: {exc}") from exc
    _refuse_stub_opinion(opinion, context="insight")
    logger.info("ai insight %s", redact_mapping({"symbol": packet.symbol, "profile": profile, "cached": cached}))
    _persist_ledger(
        ai, provider=opinion.provider, model=opinion.model, profile=profile,
        call_type="opinion", packet=packet, cached=cached,
        user_id=_uid,
    )
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
    body: ForecastOpinionBody,
    ai: AIRouter = Depends(get_ai_router),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    profile = _check_profile(body.profile)
    _uid = user_id_of(user)
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
    _lbl = body.quant_confidence_label
    if _lbl is not None:
        _lbl = str(_lbl).strip().lower()
        if _lbl not in ("low", "moderate", "high"):
            raise HTTPException(status_code=422, detail="quant_confidence_label must be low|moderate|high")
    # AI-disabled mode skips the model call entirely: the deterministic
    # forecast passes through intact (blend handles opinion=None). No fake
    # opinion is requested or served.
    if not body.ai_enabled:
        packet = await _packet_for(body.symbol, profile)
        quant_prob = float(body.quant_prob) if body.quant_prob is not None else 0.5
        try:
            blend = blend_forecast(
                quant_prob, None, ai_weight=body.ai_weight, ai_enabled=False,
                quant_confidence_label=_lbl,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ai blend failed: {exc}") from exc
        provider_name, model = ai.resolve(profile)
        try:
            provenance = packet.freshness.model_dump(mode="json")
        except Exception:
            provenance = {}
        return {
            "symbol": packet.symbol,
            "profile": profile,
            "provider": provider_name,
            "model": model,
            "opinion": None,
            "blend": blend,
            "quant_source": "request" if body.quant_prob is not None else "unspecified-placeholder",
            "ai_weight_requested": body.ai_weight,
            "ai_weight_applied": 0.0,
            "packet_id": packet.packet_id,
            "evidence_hash": packet.evidence_hash,
            "cached": False,
            "provenance": provenance,
            "disclaimer": DISCLAIMER,
        }
    # Fail-closed gate: no key -> 423 before any evidence work.
    _require_live_provider(ai, profile)
    # Blocking market-data lookup — run off the event loop (see post_insight).
    # Phase 6: profile-aware news context (best-effort).
    packet = await _packet_for(body.symbol, profile)
    try:
        opinion, cached = await ai.get_insight(
            packet, profile=profile, horizon=horizon,
            call_type="forecast_opinion", user_id=_uid,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ai insight failed: {exc}") from exc
    _refuse_stub_opinion(opinion, context="forecast opinion")
    quant_prob = float(body.quant_prob) if body.quant_prob is not None else 0.5
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
    _persist_ledger(
        ai, provider=opinion.provider, model=opinion.model, profile=profile,
        call_type="forecast", packet=packet, cached=cached,
        user_id=_uid,
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
    if horizon is not None and horizon not in (1, 7, 14, 21):
        raise HTTPException(status_code=422, detail="horizon must be one of 1, 7, 14, 21")
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


class DeepResearchJobBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$")
    horizon: int = Field(default=21)


#: Strong references to in-flight deep-research tasks (asyncio keeps only weak
#: ones, so an unreferenced task can be garbage-collected mid-run).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _run_deep_job(job_id: str, symbol: str, horizon: int, user_id: str) -> None:
    """Background deep_research worker (never raises; writes job doc).

    Fail-closed throughout: no key -> error doc (never a stub opinion);
    live-call failure -> error doc (never a fabricated opinion).
    """
    try:
        ai = get_ai_router()
        _require_live_provider(ai, "deep_research")
    except HTTPException as exc:
        _job_put(job_id, {"job_id": job_id, "status": "error",
                          "error": str(exc.detail)[:280],
                          "symbol": symbol, "horizon": horizon}, owner=user_id)
        return
    try:
        packet = await _packet_for(symbol, "deep_research")
    except Exception as exc:
        _job_put(job_id, {"job_id": job_id, "status": "error",
                          "error": f"packet failed: {type(exc).__name__}",
                          "symbol": symbol, "horizon": horizon}, owner=user_id)
        return
    try:
        opinion, cached = await ai.get_insight(
            packet, profile="deep_research", horizon=horizon,
            call_type="deep_research", user_id=user_id,
        )
        try:
            _refuse_stub_opinion(opinion, context="deep research")
        except HTTPException as exc:
            _job_put(job_id, {"job_id": job_id, "status": "error",
                              "error": str(exc.detail)[:280],
                              "symbol": symbol, "horizon": horizon}, owner=user_id)
            return
        _persist_ledger(ai, provider=opinion.provider, model=opinion.model,
                        profile="deep_research", call_type="evidence",
                        packet=packet, cached=cached, user_id=user_id)
        _job_put(job_id, {"job_id": job_id, "status": "done",
                          "symbol": packet.symbol, "horizon": horizon,
                          "provider": opinion.provider, "model": opinion.model,
                          "opinion": opinion.model_dump(mode="json"),
                          "packet_id": packet.packet_id,
                          "evidence_hash": packet.evidence_hash,
                          "cached": cached, "disclaimer": DISCLAIMER}, owner=user_id)
    except Exception as exc:
        _job_put(job_id, {"job_id": job_id, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"[:280],
                          "symbol": symbol, "horizon": horizon}, owner=user_id)


@router.post("/deep_research_job", status_code=202)
async def post_deep_research_job(
    body: DeepResearchJobBody,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Enqueue a deep_research call; poll ``GET /api/ai/jobs/{id}``.

    Returns immediately with a job id while a background task fills the job
    document. Jobs are visible only to the user who created them.
    """
    horizon = _check_horizon(body.horizon)
    clean = (body.symbol or "").strip().upper()
    owner = user_id_of(user)
    enforce_ai_quota(owner)
    job_id = uuid.uuid4().hex
    _job_put(job_id, {"job_id": job_id, "status": "pending",
                      "symbol": clean, "horizon": horizon}, owner=owner)
    task = asyncio.create_task(_run_deep_job(job_id, clean, horizon, owner))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return JSONResponse(status_code=202, content={
        "job_id": job_id, "status": "pending",
        "status_url": f"/api/ai/jobs/{job_id}",
        "symbol": clean, "horizon": horizon,
    })


@router.get("/jobs/{job_id}")
def get_ai_job(job_id: str, user: User = Depends(get_current_user)) -> dict[str, Any]:
    doc = _job_get((job_id or "").strip())
    # Someone else's job is indistinguishable from a missing one.
    if not doc or doc.get("owner") != user_id_of(user):
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {k: v for k, v in doc.items() if k != "owner"}
