"""Investopedia adapter (Phase 7, flag-gated, best-effort scrape).

Behind ``INVESTOPEDIA_ENABLED`` (default "false"). When disabled or on any
failure both public helpers return None — callers must never see an
exception from this module.

Sources (best-effort HTML scrape, stdlib html.parser only — no new deps):
- https://www.investopedia.com/markets-news-4427703 (markets-news hub)
- https://www.investopedia.com/5-things-to-know-before-the-stock-market-opens-*
  (latest premarket brief, discovered via hub links; slug changes daily)

Quality grade is always "C" (unstructured scrape, not an official API) and
every payload carries a disclaimer. Results are cached 5 min via
backend.cache (best-effort) to stay polite.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

INVESTOPEDIA_ENABLED = os.getenv("INVESTOPEDIA_ENABLED", "false")

SOURCE = "investopedia-scrape"
QUALITY_GRADE = "C"
DISCLAIMER = (
    "Not investment advice. For informational purposes only. "
    "Investopedia data is a best-effort web scrape (quality grade C), "
    "not an official API; verify figures from primary sources."
)
_CACHE_TTL_S = 300
_TIMEOUT_S = 10.0

MARKETS_NEWS_URLS: tuple[str, ...] = (
    "https://www.investopedia.com/markets-news-4427703",
    "https://www.investopedia.com/stock-market-news-4427780",
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 OneMarketAnalyzer/1.0"
)


def _enabled() -> bool:
    try:
        return (os.getenv("INVESTOPEDIA_ENABLED", "") or "").strip().lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:
        return False


def _utcnow_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        return ""


class _LinkExtractor(HTMLParser):
    """Collect <a href> + anchor text pairs (stdlib only)."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            try:
                href = dict(attrs).get("href") or ""
            except Exception:
                href = ""
            self._href = href.strip()
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            try:
                self._buf.append(data)
            except Exception:
                pass

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            try:
                text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
                if text:
                    self.links.append({"href": self._href, "text": text})
            except Exception:
                pass
            self._href = None
            self._buf = []


def _score(title: str, summary: str = "") -> tuple[float, str]:
    try:
        from backend.api.news import news_sentiment_score as _s
        score = float(_s(title, summary))
    except Exception:
        score = 0.0
    try:
        score = max(-1.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        score = 0.0
    label = "bullish" if score > 0.2 else ("bearish" if score < -0.2 else "neutral")
    return round(score, 3), label


def _fetch_html(url: str) -> str | None:
    try:
        import httpx
    except Exception:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT_S, headers={"User-Agent": _UA}, follow_redirects=True) as client:
            resp = client.get(url)
        if int(getattr(resp, "status_code", 500) or 500) >= 400:
            return None
        text = resp.text or ""
        return text if len(text.strip()) > 500 else None
    except Exception:
        return None


def _parse_articles(html: str, base_url: str, limit: int = 10) -> list[dict[str, Any]]:
    try:
        lim = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        lim = 10
    if not html or not isinstance(html, str):
        return []
    parser = _LinkExtractor()
    try:
        parser.feed(html[:500_000])
    except Exception:
        return []
    seen: set[str] = set()
    articles: list[dict[str, Any]] = []
    for link in parser.links:
        try:
            href = (link.get("href") or "").strip()
            text = re.sub(r"\s+", " ", (link.get("text") or "")).strip()
        except Exception:
            continue
        if len(text) < 24 or len(text) > 200:
            continue
        # Skip nav/boilerplate + glossary/ask pages.
        low_href = href.lower()
        if any(skip in low_href for skip in ("/terms/", "/ask/", "/advisor/", "/tech/", "#", "javascript:", "mailto:")):
            continue
        # Keep Investopedia article links (absolute or site-relative).
        if href.startswith("/"):
            url = urljoin("https://www.investopedia.com", href)
        elif "investopedia.com" in low_href:
            url = href.split("#")[0].strip()
            if not url.lower().startswith("http"):
                continue
        else:
            continue
        # Article slugs contain dashes; skip bare section fronts.
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if "-" not in slug or len(slug) < 12:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        score, label = _score(text)
        articles.append({
            "title": text[:200],
            "summary": "",
            "url": url[:280],
            "sentiment": score,
            "sentiment_label": label,
            "created_at": "",
            "source": "investopedia",
        })
        if len(articles) >= lim:
            break
    return articles


def _cache_get(key: str) -> Any | None:
    try:
        from backend.cache import get_cache as _get_cache
        return _get_cache().get(key)
    except Exception:
        return None


def _cache_set(key: str, value: Any, ttl_s: int = _CACHE_TTL_S) -> None:
    try:
        from backend.cache import get_cache as _get_cache
        _get_cache().set(key, value, ttl_s=ttl_s)
    except Exception:
        pass


def fetch_premarket_snapshot() -> dict[str, Any] | None:
    """Best-effort premarket scrape. None when disabled or on any failure."""
    if not _enabled():
        return None
    try:
        cached = _cache_get("investopedia:premarket")
        if isinstance(cached, dict) and isinstance(cached.get("articles"), list):
            return cached
    except Exception:
        pass
    try:
        articles: list[dict[str, Any]] = []
        hub_html: str | None = None
        hub_url = ""
        for candidate in MARKETS_NEWS_URLS:
            html = _fetch_html(candidate)
            if html:
                hub_html, hub_url = html, candidate
                break
        if hub_html:
            articles = _parse_articles(hub_html, hub_url, limit=10)
            # Discover the latest "5 things to know before the stock market opens" brief.
            try:
                parser = _LinkExtractor()
                parser.feed(hub_html[:500_000])
                brief_url = ""
                for link in parser.links:
                    href = (link.get("href") or "").lower()
                    if "5-things-to-know-before-the-stock-market-opens" in href:
                        raw = (link.get("href") or "").strip()
                        brief_url = urljoin("https://www.investopedia.com", raw) if raw.startswith("/") else raw.split("#")[0]
                        break
                if brief_url:
                    brief_html = _fetch_html(brief_url)
                    if brief_html:
                        brief_arts = _parse_articles(brief_html, brief_url, limit=5)
                        # Brief page headlines first, then hub headlines.
                        seen_urls = {a.get("url", "").lower() for a in brief_arts}
                        articles = list(brief_arts) + [a for a in articles if a.get("url", "").lower() not in seen_urls]
                        articles = articles[:10]
            except Exception:
                pass
        if not articles:
            return None
        try:
            scores = [float(a.get("sentiment") or 0.0) for a in articles]
        except (TypeError, ValueError):
            scores = [0.0 for _ in articles]
        mean_sent = round(sum(scores) / len(scores), 3) if scores else 0.0
        out: dict[str, Any] = {
            "source": SOURCE,
            "articles": articles,
            "snapshot": {
                "futures": [],
                "movers": [],
                "headline_count": len(articles),
                "mean_sentiment": mean_sent,
                "note": "best-effort scrape; futures/movers not parsed into structured fields",
            },
            "mean_sentiment": mean_sent,
            "fetched_at": _utcnow_iso(),
            "quality_grade": QUALITY_GRADE,
            "disclaimer": DISCLAIMER,
        }
        _cache_set("investopedia:premarket", out)
        return out
    except Exception:
        return None


def fetch_symbol_sentiment(symbol: str) -> dict[str, Any] | None:
    """Filtered premarket articles mentioning *symbol*. None when disabled/miss."""
    if not _enabled():
        return None
    clean = (symbol or "").strip().upper()
    if not clean:
        return None
    try:
        cached = _cache_get(f"investopedia:symbol:{clean}")
        if isinstance(cached, dict) and isinstance(cached.get("articles"), list):
            return cached
    except Exception:
        pass
    try:
        snap = fetch_premarket_snapshot()
        if not isinstance(snap, dict):
            return None
        arts = snap.get("articles") or []
        if not isinstance(arts, list):
            return None
        needle = clean.lower()
        # Bare form for suffixed symbols (e.g. MC.PA -> mc).
        bare = re.split(r"[.\-:]", needle)[0]
        filtered: list[dict[str, Any]] = []
        for art in arts:
            if not isinstance(art, dict):
                continue
            try:
                hay = f"{art.get('title') or ''} {art.get('summary') or ''}".lower()
            except Exception:
                continue
            if needle in hay or (len(bare) >= 2 and bare in hay):
                filtered.append(art)
        if not filtered:
            return None
        filtered = filtered[:5]
        try:
            scores = [float(a.get("sentiment") or 0.0) for a in filtered]
        except (TypeError, ValueError):
            scores = [0.0 for _ in filtered]
        mean_sent = round(sum(scores) / len(scores), 3) if scores else 0.0
        out = {
            "source": SOURCE,
            "symbol": clean,
            "articles": filtered,
            "mean_sentiment": mean_sent,
            "bullish_count": sum(1 for s in scores if s > 0.2),
            "bearish_count": sum(1 for s in scores if s < -0.2),
            "fetched_at": _utcnow_iso(),
            "quality_grade": QUALITY_GRADE,
            "disclaimer": DISCLAIMER,
        }
        _cache_set(f"investopedia:symbol:{clean}", out)
        return out
    except Exception:
        return None
