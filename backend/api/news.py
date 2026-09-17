"""GET /api/news — market news via Alpaca News API (v1beta1).

Fail-closed: no keys -> 423 (never stub news); upstream miss -> 502.
Every item carries source + timestamps; sentiment is a deterministic
keyword score (-1..+1) computed locally — explainable, never AI.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query

from backend.cache import get_cache
from backend.market_data.provenance import build_provenance

router = APIRouter(prefix="/api/news", tags=["news"])

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
_CACHE_TTL_S = 300

BULLISH_WORDS = frozenset({
    "beat", "beats", "record", "surge", "surges", "upgrade", "upgraded",
    "growth", "profit", "rally", "bullish", "buy", "outperform", "strong",
    "gain", "gains", "jump", "soar", "positive", "optimistic", "raise",
    "raised", "guidance", "dividend", "buyback",
})
BEARISH_WORDS = frozenset({
    "miss", "misses", "downgrade", "downgraded", "cut", "loss", "losses",
    "fall", "falls", "drop", "drops", "bearish", "sell", "underperform",
    "weak", "decline", "plunge", "negative", "warning", "lawsuit",
    "investigation", "recall", "layoff", "layoffs",
})

DISCLOSURE = "Not investment advice. For informational purposes only."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_keys() -> tuple[str, str]:
    try:
        from backend.market_data.providers.alpaca import resolve_keys
        return resolve_keys()
    except Exception:
        return "", ""


def news_sentiment_score(title: str, summary: str = "") -> float:
    """Deterministic keyword sentiment in [-1, +1]. Pure function."""
    try:
        text = f"{title or ''} {summary or ''}".lower()
    except Exception:
        return 0.0
    import re as _re
    words = set(_re.findall(r"[a-z]+", text))
    if not words:
        return 0.0
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    if bull == 0 and bear == 0:
        return 0.0
    return round((bull - bear) / max(1, bull + bear), 3)


def _cache_key(symbols: str, limit: int) -> str:
    return f"news:{symbols or 'ALL'}:{int(limit)}"


@router.get("")
def get_news(
    symbols: str | None = Query(default=None, description="Comma-list e.g. AAPL,MSFT (US only; empty = market-wide)"),
    limit: int = Query(default=20, ge=1, le=50, description="Max articles (cap 50)"),
) -> dict:
    key_id, secret = _resolve_keys()
    if not (key_id and secret):
        raise HTTPException(
            status_code=423,
            detail="news unavailable — set ALPACA_API_KEY_ID + ALPACA_API_SECRET_KEY on the backend",
        )
    sym_list: list[str] = []
    if symbols:
        for part in str(symbols).split(","):
            s = part.strip().upper().replace("-", ".")
            if not s:
                continue
            # Alpaca news is US-only; skip suffixed symbols silently.
            if any(s.endswith(suf) for suf in (".SS", ".PA", ".AS", ".BR", ".L")):
                continue
            sym_list.append(s)
        sym_list = sym_list[:10]
    ck = _cache_key(",".join(sym_list), int(limit))
    try:
        cached = get_cache().get(ck)
        if isinstance(cached, dict) and isinstance(cached.get("articles"), list):
            return cached
    except Exception:
        pass
    params: dict = {"limit": int(limit), "sort": "desc", "include_content": "false"}
    if sym_list:
        params["symbols"] = ",".join(sym_list)
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.get(NEWS_URL, params=params, headers=headers)
        if resp.status_code in (401, 403):
            raise HTTPException(status_code=423, detail="news unavailable — Alpaca keys rejected (401/403)")
        resp.raise_for_status()
        payload = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"news fetch failed: {type(exc).__name__}") from exc
    raw_items = payload.get("news", []) if isinstance(payload, dict) else []
    articles: list[dict] = []
    for item in raw_items[: int(limit)]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("headline") or item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        url = str(item.get("url") or "").strip()
        created = str(item.get("created_at") or item.get("updated_at") or "").strip()
        item_syms = [str(s).upper() for s in (item.get("symbols") or []) if str(s).strip()]
        author = str(item.get("author") or item.get("source") or "").strip()
        score = news_sentiment_score(title, summary)
        articles.append({
            "title": title or "(untitled)",
            "summary": summary[: 400],
            "url": url,
            "created_at": created,
            "symbols": item_syms,
            "author": author,
            "sentiment": score,
            "sentiment_label": "bullish" if score > 0.2 else ("bearish" if score < -0.2 else "neutral"),
        })
    try:
        prov = build_provenance(
            "alpaca-news", as_of=_utcnow(), delay_minutes=0,
            quality_grade="B", fallback_used=False, missing_fields=[],
        ).model_dump(mode="json")
    except Exception:
        prov = {}
    out = {
        "articles": articles,
        "count": len(articles),
        "symbols": sym_list,
        "provenance": prov,
        "disclosure": DISCLOSURE,
    }
    try:
        get_cache().set(ck, out, ttl_s=_CACHE_TTL_S)
    except Exception:
        pass
    return out


@router.get("/symbol/{symbol}")
def get_symbol_news(
    symbol: str,
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    return get_news(symbols=symbol, limit=limit)
