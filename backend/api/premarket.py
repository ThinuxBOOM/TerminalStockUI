"""GET /api/sentiment/premarket — merged premarket sentiment.

Merges Alpaca market news (when keys configured) with the optional
Investopedia scrape (behind INVESTOPEDIA_ENABLED). Best-effort throughout:
missing sources degrade to None, the endpoint never 423/502 for a source
miss — it returns what it has plus an honest aggregate + disclosure.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from backend.auth.guards import get_current_user

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"], dependencies=[Depends(get_current_user)])

DISCLOSURE = (
    "Not investment advice. For informational purposes only. "
    "Investopedia data is a best-effort web scrape (quality grade C), "
    "not an official API; verify figures from primary sources."
)
_CACHE_TTL_S = 60


def _utcnow_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return ""


@router.get("/premarket")
def get_premarket() -> dict[str, Any]:
    # Merged-response cache (best-effort, 60s).
    ck = "sentiment:premarket"
    try:
        from backend.cache import get_cache as _get_cache

        cached = _get_cache().get(ck)
        if isinstance(cached, dict) and "aggregate_sentiment" in cached:
            return cached
    except Exception:
        pass

    alpaca: dict[str, Any] | None = None
    try:
        from backend.api.news import get_news as _get_news

        try:
            payload = _get_news(symbols=None, limit=20)
            if isinstance(payload, dict) and isinstance(payload.get("articles"), list):
                alpaca = payload
        except Exception:
            alpaca = None
    except Exception:
        alpaca = None

    investopedia: dict[str, Any] | None = None
    try:
        from backend.market_data.providers import investopedia as _inves

        fetch_fn = getattr(_inves, "fetch_premarket_snapshot", None)
        if callable(fetch_fn):
            try:
                snap = fetch_fn()
            except Exception:
                snap = None
            if isinstance(snap, dict):
                investopedia = snap
    except Exception:
        investopedia = None

    scores: list[float] = []
    try:
        for art in (alpaca.get("articles") or []) if isinstance(alpaca, dict) else []:
            if isinstance(art, dict):
                try:
                    scores.append(float(art.get("sentiment") or 0.0))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    try:
        for art in (investopedia.get("articles") or []) if isinstance(investopedia, dict) else []:
            if isinstance(art, dict):
                try:
                    scores.append(float(art.get("sentiment") or 0.0))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    mean_sent = round(sum(scores) / len(scores), 3) if scores else 0.0
    aggregate = {
        "mean_sentiment": mean_sent,
        "bullish_count": sum(1 for s in scores if s > 0.2),
        "bearish_count": sum(1 for s in scores if s < -0.2),
        "article_count": len(scores),
        "sources": [
            s for s, present in (("alpaca", alpaca is not None), ("investopedia", investopedia is not None))
            if present
        ],
        "fetched_at": _utcnow_iso(),
    }
    out: dict[str, Any] = {
        "alpaca": alpaca,
        "investopedia": investopedia,
        "aggregate_sentiment": aggregate,
        "disclosure": DISCLOSURE,
    }
    try:
        from backend.cache import get_cache as _get_cache

        _get_cache().set(ck, out, ttl_s=_CACHE_TTL_S)
    except Exception:
        pass
    return out
