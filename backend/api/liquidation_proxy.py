"""Liquidation-PROXY router (deterministic heuristic, NOT exchange data).

True exchange liquidation feeds (forced closes / leverage wipes) require paid
derivatives APIs. This endpoint exposes a clearly-labelled PROXY built only
from existing deterministic primitives:

  intensity = max(0, |vol_z| - 2.0) * range_atr * (1 + |body| / range)

where vol_z is the 20-bar volume-anomaly z-score, range_atr is
(high-low)/ATR14, and body is |close-open|. Side is a display heuristic
(close>=open -> short_proxy, else long_proxy).

Response ALWAYS carries disclosure + methodology + provenance with
missing_fields += ["liquidation-feed"]. Per-symbol failures degrade to
``skipped`` rows; the batch never 500s.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.analytics.technical.indicators import atr, is_volume_anomaly
from backend.api.deps import get_market_service
from backend.market_data.service import MarketDataService
from backend.security.validation import sanitize_error, validate_symbol

router = APIRouter(prefix="/api/markets", tags=["markets-liquidation"])

#: Envelope cache: the full-MIC quote scan + up to 2x limit second-pass
#: 60-bar fetches dominate wall time (same pattern as screener/markets
#: overview). Anonymous deterministic response (varies only by mic/limit/
#: sort), so the key is unscoped. TTL 60s mirrors the quote cache.
_LIQ_PROXY_TTL_S = 60


def _liq_proxy_cache_key(mic: str, limit: int, sort: str) -> str:
    try:
        return f"markets:liq-proxy:{str(mic).upper()}:{int(limit)}:{str(sort).lower()}"
    except Exception:
        return f"markets:liq-proxy:{mic}:{limit}:{sort}"

DISCLOSURE = (
    "PROXY — volume-anomaly x ATR-range heuristic, NOT exchange liquidation data. "
    "Not investment advice."
)
METHODOLOGY = (
    "intensity = max(0, |vol_z(20)| - 2.0) * ((high-low)/ATR14) * "
    "(1 + |close-open|/(high-low)); side = short_proxy when close>=open "
    "else long_proxy (display heuristic only)."
)


def _proxy_for_bars(bars: list[dict]) -> dict | None:
    try:
        if not bars or len(bars) < 25:
            return None
        closes = [float(b.get("close")) for b in bars]
        highs = [float(b.get("high")) for b in bars]
        lows = [float(b.get("low")) for b in bars]
        vols = [float(b.get("volume") or 0) for b in bars]
        last = bars[-1]
        o = float(last.get("open"))
        h = float(last.get("high"))
        lo = float(last.get("low"))
        c = float(last.get("close"))
        # Volume z over trailing 20 (excl. last) vs last.
        import statistics as _st

        window = vols[-21:-1] if len(vols) >= 21 else vols[:-1]
        mean = _st.fmean(window) if window else 0.0
        sd = _st.pstdev(window) if len(window) >= 2 else 0.0
        vol_z = ((vols[-1] - mean) / sd) if sd > 0 else 0.0
        atr_vals = atr(highs, lows, closes, window=14)
        atr_last = float(atr_vals[-1]) if atr_vals and atr_vals[-1] is not None else 0.0
        rng = max(0.0, h - lo)
        range_atr = (rng / atr_last) if atr_last > 0 else 0.0
        body_frac = (abs(c - o) / rng) if rng > 0 else 0.0
        intensity = max(0.0, abs(vol_z) - 2.0) * range_atr * (1.0 + body_frac)
        side = "short_proxy" if c >= o else "long_proxy"
        # is_volume_anomaly flag for parity with the scalar bundle.
        try:
            anomaly = bool(is_volume_anomaly(vols, window=20))
        except Exception:
            anomaly = abs(vol_z) > 2.0
        return {
            "intensity": round(float(intensity), 4),
            "side": side,
            "vol_z": round(float(vol_z), 3),
            "range_atr": round(float(range_atr), 3),
            "volume_anomaly": anomaly,
            "price": c,
        }
    except Exception:
        return None


@router.get("/{mic}/liquidation-proxy")
def market_liquidation_proxy(
    mic: str,
    limit: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default="intensity", description="intensity|symbol"),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """GET /api/markets/{mic}/liquidation-proxy?limit=50&sort=intensity."""
    from backend.api.deps import get_registry
    from backend.api.markets import _combine_provenance, _scan_mic, _validate_mic

    try:
        clean_mic = _validate_mic(mic)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=sanitize_error(exc)) from exc
    sort_key = str(sort or "intensity").strip().lower()
    if sort_key not in ("intensity", "symbol"):
        raise HTTPException(status_code=422, detail="sort must be intensity|symbol")
    # Envelope cache: identical scans within TTL skip the quote fan-out +
    # second-pass bar fetches entirely.
    _ck: str | None = None
    try:
        from backend.cache import get_cache as _get_cache

        _ck = _liq_proxy_cache_key(clean_mic, int(limit), sort_key)
        _cached = _get_cache().get(_ck)
        if isinstance(_cached, dict) and isinstance(_cached.get("rows"), list):
            return _cached
    except Exception:
        _ck = None
    try:
        registry = get_registry()
        skipped_rows: list[dict] = []
        scan_rows, _universe_n = _scan_mic(clean_mic, registry, svc, skipped_rows, limit=None, sort="turnover")
        scan = {"rows": scan_rows}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="liquidation scan failed")) from exc
    symbols = [str(r.get("symbol") or "") for r in (scan.get("rows") or []) if isinstance(r, dict)]
    rows: list[dict] = []
    skipped = 0
    for sym in symbols[: max(1, int(limit * 2))]:
        if not sym:
            skipped += 1
            continue
        try:
            payload = svc.get_bars(sym, "1d", 60)
            bars = payload.get("bars", []) if isinstance(payload, dict) else []
            proxy = _proxy_for_bars(bars)
            if proxy is None:
                skipped += 1
                continue
            # Enrich display fields from the scan row when present.
            base = next((r for r in (scan.get("rows") or []) if str(r.get("symbol")) == sym), {})
            rows.append({
                "symbol": sym,
                "company_name": base.get("company_name"),
                "price": proxy["price"],
                "change_pct": base.get("change_pct"),
                "volume": base.get("volume"),
                **proxy,
            })
        except Exception:
            skipped += 1
            continue
    if sort_key == "symbol":
        rows.sort(key=lambda r: str(r.get("symbol")))
    else:
        rows.sort(key=lambda r: float(r.get("intensity") or 0.0), reverse=True)
    rows = rows[: max(1, int(limit))]
    try:
        provenance = _combine_provenance(scan.get("rows") or [])
    except Exception as exc:
        # Fail-closed: no honest provenance envelope, no response.
        raise HTTPException(
            status_code=502, detail=sanitize_error(exc, prefix="liquidation scan failed")
        ) from exc
    try:
        missing = list(provenance.get("missing_fields") or [])
        if "liquidation-feed" not in missing:
            missing.append("liquidation-feed")
        provenance["missing_fields"] = missing
    except Exception:
        pass
    long_n = sum(1 for r in rows if r.get("side") == "long_proxy")
    short_n = sum(1 for r in rows if r.get("side") == "short_proxy")
    intensities = [float(r.get("intensity") or 0.0) for r in rows]
    out = {
        "mic": clean_mic,
        "rows": rows,
        "aggregates": {
            "n": len(rows),
            "skipped": skipped,
            "long_proxy_n": long_n,
            "short_proxy_n": short_n,
            "mean_intensity": round(sum(intensities) / len(intensities), 4) if intensities else 0.0,
            "max_intensity": round(max(intensities), 4) if intensities else 0.0,
        },
        "methodology": METHODOLOGY,
        "disclosure": DISCLOSURE,
        "provenance": provenance,
    }
    try:
        if _ck:
            from backend.cache import get_cache as _get_cache2

            _get_cache2().set(_ck, out, ttl_s=_LIQ_PROXY_TTL_S)
    except Exception:
        pass
    return out


# Keep validate_symbol import used (contract parity with markets router).
_ = validate_symbol
