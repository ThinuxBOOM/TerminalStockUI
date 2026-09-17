"""GET /api/signals/top — Top-5 BUY / Top-5 SHORT per market.

Deterministic: ensemble-v2 direction_probability (weight 0.8) blended with
local news sentiment (weight 0.2, mapped 0..1). No AI, no recommendations —
ranked probabilities with full provenance + disclosure. Non-trader friendly
labels included (plain-English verdict + what-it-means).
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_market_service, get_registry
from backend.api.news import news_sentiment_score
from backend.cache import get_cache
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/signals", tags=["signals"])

DISCLOSURE = "Not investment advice. For informational purposes only."
_CACHE_TTL_S = 60
_MAX_WORKERS = 8

NEWS_WEIGHT = 0.2
ENSEMBLE_WEIGHT = 0.8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _plain_verdict(prob: float) -> tuple[str, str]:
    """Non-trader verdict + explanation for a direction probability."""
    try:
        p = float(prob)
    except Exception:
        return "No signal", "Not enough data to form a view."
    if p >= 0.65:
        return "Likely to rise", "Models + recent news lean positive. Still risky — prices can fall."
    if p >= 0.55:
        return "Slightly positive", "A gentle upward lean. Treat as watch, not a buy order."
    if p > 0.45:
        return "Mixed / flat", "Signals conflict. No clear direction — waiting is fine."
    if p > 0.35:
        return "Slightly negative", "A gentle downward lean. Extra caution if holding."
    return "Likely to fall", "Models + recent news lean negative. Avoid chasing dips."


def _blend(ensemble_p: float, sentiment: float) -> float:
    """Blend ensemble prob with news sentiment (-1..+1 -> 0..1)."""
    try:
        e = max(0.0, min(1.0, float(ensemble_p)))
    except Exception:
        e = 0.5
    try:
        s01 = max(0.0, min(1.0, (float(sentiment) + 1.0) / 2.0))
    except Exception:
        s01 = 0.5
    return round(ENSEMBLE_WEIGHT * e + NEWS_WEIGHT * s01, 4)


def _fetch_news_map(symbols: list[str]) -> dict[str, float]:
    """Per-symbol mean sentiment via Alpaca news (best-effort, {} on miss)."""
    out: dict[str, float] = {}
    try:
        from backend.api.news import get_news as _get_news
        # Batch in groups of 10 (API cap).
        for i in range(0, len(symbols), 10):
            chunk = [s for s in symbols[i: i + 10] if s and not any(
                s.upper().endswith(suf) for suf in (".SS", ".PA", ".AS", ".BR"))]
            if not chunk:
                continue
            try:
                payload = _get_news(symbols=",".join(chunk), limit=50)
            except Exception:
                continue
            acc: dict[str, list[float]] = {}
            for a in (payload.get("articles") or []):
                try:
                    sc = float(a.get("sentiment") or 0.0)
                except Exception:
                    sc = 0.0
                for sym in (a.get("symbols") or []):
                    acc.setdefault(str(sym).upper(), []).append(sc)
            for sym, scores in acc.items():
                if scores:
                    out[sym] = round(sum(scores) / len(scores), 3)
    except Exception:
        pass
    return out


#: Minimum cached 1d bars to score a symbol (mirrors the get_bars serve
#: gate): uncached symbols skip fast with an honest reason instead of each
#: paying a live vendor fetch inside the fan-out. Single-symbol views still
#: fetch on demand — this gate is scan-only.
_SIGNALS_MIN_WARM_BARS = 100


def _score_one(inst, svc: MarketDataService, horizon: int, news_map: dict,
               warm: dict | None = None) -> tuple[dict | None, dict | None]:
    try:
        key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
    except Exception:
        return None, {"symbol": "UNKNOWN", "reason": "bad registry entry"}
    if isinstance(warm, dict):
        try:
            have = int(warm.get(str(key).upper(), 0))
        except Exception:
            have = 0
        if have < _SIGNALS_MIN_WARM_BARS:
            return None, {"symbol": str(key),
                          "reason": "no cached bars yet (daily warming covers it)"}
    try:
        quote = svc.get_quote(key, getattr(inst, "exchange_mic", None))
        fc = ForecastService(market_service=svc).forecast(key, horizon)
        ensemble_p = float(fc["direction_probability"])
        symbol_up = str(key).upper()
        # News lookup: bare US form + provider form.
        sentiment = news_map.get(symbol_up, 0.0)
        if sentiment == 0.0:
            try:
                alt = str(getattr(inst, "exchange_symbol", "") or "").upper()
                if alt and alt in news_map:
                    sentiment = news_map[alt]
            except Exception:
                pass
        signal = _blend(ensemble_p, sentiment)
        verdict, meaning = _plain_verdict(signal)
        row = {
            "symbol": key,
            "company_name": getattr(inst, "company_name", key),
            "exchange_mic": getattr(inst, "exchange_mic", None),
            "currency": (quote.get("currency") if isinstance(quote, dict) else None) or getattr(inst, "currency", "USD"),
            "price": quote.get("price") if isinstance(quote, dict) else None,
            "change_pct": quote.get("change_pct") if isinstance(quote, dict) else None,
            "ensemble_probability": round(ensemble_p, 4),
            "news_sentiment": round(float(sentiment), 3),
            "signal_probability": signal,
            "verdict": verdict,
            "what_it_means": meaning,
            "confidence": fc.get("confidence"),
            "horizon": horizon,
            "provenance": quote.get("provenance") if isinstance(quote, dict) else {},
        }
        return row, None
    except HTTPException as exc:
        d = getattr(exc, "detail", exc)
        reason = str(d)[:180] if isinstance(d, str) else str(d)[:180]
        return None, {"symbol": str(key), "reason": reason}
    except Exception as exc:
        return None, {"symbol": str(key), "reason": f"{type(exc).__name__}: {str(exc)[:180]}"}


@router.get("/top")
def top_signals(
    horizon: int = Query(default=21, description="Trading-day horizon: 1, 7, 14 or 21"),
    per_market: int = Query(default=5, ge=1, le=10, description="Top-N per side per market"),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    try:
        horizon_int = int(horizon)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}") from None
    if horizon_int not in FORECAST_HORIZONS:
        raise HTTPException(status_code=422, detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
    ck = f"signals:top:{horizon_int}:{int(per_market)}"
    try:
        cached = get_cache().get(ck)
        if isinstance(cached, dict) and isinstance(cached.get("markets"), dict):
            return cached
    except Exception:
        pass
    try:
        universe = [i for i in registry.all() if getattr(i, "sector", "") not in ("ETF", "Index")]
    except Exception as exc:
        raise HTTPException(status_code=502, detail="signals universe failed") from exc
    # Cost guard: the 500-symbol S&P 500 bulk would blow the serverless
    # budget on a full quote+forecast fan-out (8 workers x ~2s x 500 >> 60s).
    # Seeds (incl. SP500 names like AAPL/TSLA) still scan; SP500-only bulk
    # belongs to the paginated screener (?market=SP500), and any single
    # ticker resolves on demand with the same blended signal math.
    try:
        from backend.instruments.registry import SEED_INSTRUMENTS as _SEEDS
        from backend.instruments.sp500 import SP500_SYMBOLS as _SP

        _seed_syms = {str(getattr(s, "provider_symbol", "") or "").upper()
                      for s in _SEEDS}
        _sp_only = frozenset(s for s in (str(x).upper() for x in _SP)
                             if s and s not in _seed_syms)
        if _sp_only:
            universe = [i for i in universe
                        if str(getattr(i, "provider_symbol", "") or "").upper() not in _sp_only]
    except Exception:
        pass
    # Group by MIC.
    by_mic: dict[str, list] = {}
    for inst in universe:
        try:
            mic = str(getattr(inst, "exchange_mic", "") or "").upper()
        except Exception:
            continue
        if not mic:
            continue
        by_mic.setdefault(mic, []).append(inst)
    us_syms: list[str] = []
    for mic, insts in by_mic.items():
        if mic in ("XNYS", "XNAS"):
            for inst in insts:
                try:
                    us_syms.append(str(getattr(inst, "provider_symbol", "") or getattr(inst, "exchange_symbol", "")))
                except Exception:
                    pass
    news_map = _fetch_news_map(us_syms) if us_syms else {}
    markets: dict = {}
    skipped: list[dict] = []
    # Warm-bars gate (one cheap COUNT query, no network); DB unreachable
    # (None) fails open to the legacy per-symbol path.
    try:
        _all_insts = [i for insts in by_mic.values() for i in insts]
        _keys = [str(getattr(i, "provider_symbol", None)
                     or getattr(i, "exchange_symbol", "") or "")
                 for i in _all_insts]
        warm = svc.warm_bar_counts(_keys, timeframe="1d")
    except Exception:
        warm = None
    for mic, insts in sorted(by_mic.items()):
        rows: list[dict] = []
        workers = max(1, min(_MAX_WORKERS, len(insts)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_score_one, inst, svc, horizon_int, news_map, warm): inst for inst in insts}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    row, skip = fut.result(timeout=60)
                except Exception as exc:
                    skipped.append({"symbol": "UNKNOWN", "reason": f"{type(exc).__name__}"})
                    continue
                if row is not None:
                    rows.append(row)
                elif skip is not None:
                    skipped.append(skip)
        ranked = sorted(rows, key=lambda r: r["signal_probability"], reverse=True)
        n = max(1, min(int(per_market), 10))
        markets[mic] = {
            "top_buy": ranked[:n],
            "top_short": list(reversed(ranked[-n:])) if ranked else [],
            "count": len(ranked),
        }
    try:
        provs = []
        for mic_data in markets.values():
            for r in (mic_data.get("top_buy", []) + mic_data.get("top_short", [])):
                if isinstance(r.get("provenance"), dict):
                    provs.append(r["provenance"])
        if provs:
            stamps = []
            for e in provs:
                try:
                    from datetime import datetime as _dt
                    raw = e.get("as_of")
                    st = raw if isinstance(raw, _dt) else _dt.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                    stamps.append(st)
                except Exception:
                    pass
            oldest = min(stamps) if stamps else _utcnow()
            sources = sorted({str(e.get("source", "unknown")) for e in provs})
            prov = build_provenance(
                "+".join(sources), as_of=oldest, delay_minutes=15,
                quality_grade="B", fallback_used=False, missing_fields=["news-blend-20pct"],
            ).model_dump(mode="json")
        else:
            prov = build_provenance(
                "signals", as_of=_utcnow(), delay_minutes=15,
                quality_grade="C", fallback_used=False, missing_fields=["universe-empty"],
            ).model_dump(mode="json")
    except Exception:
        prov = {}
    out = {
        "markets": markets,
        "horizon": horizon_int,
        "formula": "signal = 0.8 * ensemble_direction_probability + 0.2 * news_sentiment_mapped_0_1",
        "skipped": skipped,
        "provenance": prov,
        "disclosure": DISCLOSURE,
    }
    try:
        get_cache().set(ck, out, ttl_s=_CACHE_TTL_S)
    except Exception:
        pass
    return out
