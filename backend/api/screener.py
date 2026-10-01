"""GET /api/screener — filter and sort the daily forecast scores.

Reads ``forecast_scores`` (written by the daily ``predict`` job), so a scan
of the whole universe is one indexed query. When no scores exist yet the
job is started and the response says ``pending: true``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth.guards import get_current_user
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import measured_for, signal_strength
from backend.forecasting.v4.store import get_bundle, read_scores
from backend.market_data.provenance import build_provenance

router = APIRouter(prefix="/api/screener", tags=["screener"], dependencies=[Depends(get_current_user)])

MARKETS = {"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"}
US = ["XNYS", "XNAS"]
SORTS = {
    "out_rank": lambda r: r.get("out_rank"),
    "p_up": lambda r: r.get("p_up"),
    "drawdown": lambda r: r.get("drawdown_prob"),
    "volatility": lambda r: (r.get("payload") or {}).get("vol_annual_forecast"),
    "range_width": lambda r: (r["q90"] - r["q10"]) if r.get("q90") is not None and r.get("q10") is not None else None,
    "change": lambda r: (r.get("payload") or {}).get("change_pct"),
    "symbol": lambda r: r.get("symbol"),
}
DISCLOSURE = ("Ranked by model scores updated after each close. The outperformance rank has a small "
              "measured edge across many stocks; it is not a recommendation on any one of them.")


def _row(r: dict) -> dict:
    p = r.get("payload") or {}
    return {
        "symbol": r["symbol"], "company_name": p.get("company_name") or r["symbol"],
        "exchange_mic": r["exchange_mic"], "sector": p.get("sector"), "currency": p.get("currency"),
        "last_close": r.get("last_close"), "change_pct": p.get("change_pct"),
        "p_up": r.get("p_up"), "p_out": r.get("p_out"), "out_rank": r.get("out_rank"),
        "signal_strength": signal_strength(r.get("out_rank")),
        "q10": r.get("q10"), "q50": r.get("q50"), "q90": r.get("q90"),
        "drawdown_prob": r.get("drawdown_prob"), "vol_regime": r.get("vol_regime"),
        "vol_annual_forecast": p.get("vol_annual_forecast"), "as_of": r.get("as_of"),
        "model_version": r.get("model_version"),
    }


@router.get("")
def screen(
    market: str = Query(default="ALL", description="ALL, SP500/US, or a MIC"),
    horizon: int = Query(default=21),
    sort: str = Query(default="out_rank"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    min_rank: float | None = Query(default=None, ge=0, le=1),
    max_drawdown: float | None = Query(default=None, ge=0, le=1),
    regime: str | None = Query(default=None, pattern="^(low|normal|high)$"),
    sector: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=64, description="Symbol or name contains"),
    symbols: str | None = Query(default=None, max_length=1200, description="Comma-separated symbols (watchlists)"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=1000),
) -> dict:
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(status_code=422, detail=f"horizon must be one of {list(FORECAST_HORIZONS)}")
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail=f"sort must be one of {sorted(SORTS)}")
    m = (market or "ALL").strip().upper()
    if m in ("SP500", "US"):
        mics = US
    elif m == "ALL":
        mics = None
    elif m in MARKETS:
        mics = [m]
    else:
        raise HTTPException(status_code=422, detail=f"unknown market {market!r}")

    rows = read_scores(int(horizon), mics=mics, limit=2000)
    pending = False
    if not rows:
        from backend.forecasting.v4.jobs import spawn

        spawn("predict")  # first run on a fresh install; no-op if already running
        pending = True
    items = [_row(r) for r in rows]
    if min_rank is not None:
        items = [r for r in items if r["out_rank"] is not None and r["out_rank"] >= min_rank]
    if max_drawdown is not None:
        items = [r for r in items if r["drawdown_prob"] is not None and r["drawdown_prob"] <= max_drawdown]
    if regime:
        items = [r for r in items if r["vol_regime"] == regime]
    if sector:
        items = [r for r in items if (r["sector"] or "").lower() == sector.lower()]
    if symbols:
        wanted = {s.strip().upper() for s in symbols.split(",") if s.strip()}
        items = [r for r in items if r["symbol"].upper() in wanted]
    if q:
        needle = q.strip().lower()
        items = [r for r in items if needle in r["symbol"].lower() or needle in (r["company_name"] or "").lower()]
    key = SORTS[sort]
    present = [r for r in items if key(r) is not None]
    missing = [r for r in items if key(r) is None]
    present.sort(key=key, reverse=(order == "desc"))
    ordered = present + missing  # unknown values always last
    bundle = get_bundle()
    hr = ((bundle.report.get("horizons") or {}).get(str(horizon), {}) if bundle else {})
    sectors = sorted({r["sector"] for r in (_row(x) for x in rows) if r["sector"]})
    return {
        "horizon": int(horizon), "market": m, "sort": sort, "order": order,
        "total": len(ordered), "offset": offset, "limit": limit,
        "rows": ordered[offset:offset + limit],
        "as_of": max((r["as_of"] for r in items if r.get("as_of")), default=None),
        "model_version": bundle.version if bundle else None,
        "measured": measured_for(hr),
        "sectors": sectors,
        "pending": pending,
        "provenance": build_provenance("forecast-scores", granularity="1d").model_dump(mode="json"),
        "disclosure": DISCLOSURE,
    }
