"""Forecast endpoints (engine v4).

GET /api/forecast/model               model card: version, data, measured record
GET /api/forecast/model/{symbol}      that symbol's walk-forward record
GET /api/forecast/{symbol}?horizon=   one horizon (1, 7, 14, 21)
GET /api/forecast/{symbol}/all        every horizon in one response

Every forecast carries the provenance envelope, model/feature/data versions,
its measured record, plain-English reasons and the disclosure.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from backend.auth.guards import get_current_user
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.persist import persist_forecast_record
from backend.forecasting.service import ForecastService, ForecastUnavailable, get_forecast_service
from backend.forecasting.v4.store import get_bundle
from backend.market_data.providers.base import ProviderError
from backend.security.validation import sanitize_error, validate_symbol

router = APIRouter(prefix="/api/forecast", tags=["forecast"], dependencies=[Depends(get_current_user)])


def direction_label(probability: float) -> str:
    """Neutral wording for the chance of rising (kept close to 50/50 by design)."""
    p = float(probability)
    if p >= 0.6:
        return "leans up"
    if p <= 0.4:
        return "leans down"
    return "no clear direction"


def _pct(x: float | None, digits: int = 0) -> str:
    return "n/a" if x is None else f"{x * 100:+.{digits}f}%"


def explain(f: dict) -> dict:
    """Plain-English summary, reasons for and against, and limitations."""
    h = f["horizon_days"]
    rng = f["expected_return_range"]
    m = f.get("measured") or {}
    lines = [
        f"Over the next {h} trading day{'s' if h != 1 else ''}, {f['symbol']} has typically moved "
        f"between {_pct(rng['low'], 1)} and {_pct(rng['high'], 1)} (80% range).",
        f"Chance of a 10%+ drop at some point in that time: {f['drawdown_probability'] * 100:.0f}%.",
    ]
    if f.get("relative_available") and f.get("outperform_rank") is not None:
        rank = f["outperform_rank"]
        side = f"top {max(1, round((1 - rank) * 100))}%" if rank >= 0.5 else f"bottom {max(1, round(rank * 100))}%"
        lines.append(
            f"On the model's {h}-day outperformance score it ranks in the {side} of S&P 500 stocks "
            f"({f['outperform_probability'] * 100:.1f}% chance of beating the median stock).")
    why, risks = [], []
    for d in (f.get("drivers") or {}).get("for", []):
        why.append(_driver_text(d, supports=True))
    for d in (f.get("drivers") or {}).get("against", []):
        risks.append(_driver_text(d, supports=False))
    if f.get("volatility_regime") == "high":
        risks.append("Volatility is running well above its 1-year norm, so the range is wide.")
    limitations = [
        f"The 80% range contained {m['range_coverage_80'] * 100:.1f}% of outcomes in walk-forward tests."
        if m.get("range_coverage_80") is not None else "Range coverage not yet measured.",
        "The chance of rising stays close to the historical base rate "
        f"({f['base_rate'] * 100:.1f}%) because no model beat it reliably in testing.",
    ]
    if m.get("out_ic") is not None:
        limitations.append(
            f"The outperformance ranking had a rank correlation of {m['out_ic']:.3f} with later returns "
            f"(t = {m['out_ic_t']}): a small edge across many stocks, not a call on any one.")
    if not f.get("relative_available"):
        limitations.append("Outperformance ranking covers US listings only (the model is trained on S&P 500 stocks).")
    limitations.append("Daily data: forecasts update after each close, not intraday.")
    return {"summary": " ".join(lines), "why": why, "risks": risks, "limitations": limitations}


def _driver_text(d: dict, *, supports: bool) -> str:
    label = d.get("label") or d.get("feature")
    pct = d.get("percentile")
    where = ""
    if pct is not None:
        where = f" (higher than {pct * 100:.0f}% of S&P 500 stocks)" if pct >= 0.5 else f" (lower than {(1 - pct) * 100:.0f}% of S&P 500 stocks)"
    verb = "supports" if supports else "weighs on"
    return f"{label[0].upper()}{label[1:]}{where} {verb} the outperformance score."


def _decorate(f: dict) -> dict:
    payload = {k: v for k, v in f.items() if k not in ("record", "features")}
    payload.update(explain(f))
    payload["label"] = direction_label(f["direction_probability"])
    payload["quality_grade"] = str((f.get("provenance") or {}).get("quality_grade") or "U").upper()
    payload["provider"] = "forecast-engine-v4"
    return payload


def _run(svc: ForecastService, symbol: str) -> dict[int, dict]:
    try:
        return svc.forecast_all(symbol)
    except ForecastUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix="forecast failed")) from exc


@router.get("/model")
def model_card() -> dict:
    """The active model and its walk-forward record (no per-symbol detail)."""
    bundle = get_bundle()
    if bundle is None:
        raise HTTPException(status_code=423, detail="no forecast model is installed")
    report = {k: v for k, v in bundle.report.items() if k != "per_symbol"}
    return {
        "version": bundle.version, "trained_at": bundle.trained_at,
        "data_start": bundle.data_start, "data_end": bundle.data_end,
        "universe_size": len(bundle.universe), "horizons": bundle.horizons,
        "report": report,
    }


@router.get("/model/{symbol}")
def model_symbol(symbol: str) -> dict:
    sym = validate_symbol(symbol)
    bundle = get_bundle()
    if bundle is None:
        raise HTTPException(status_code=423, detail="no forecast model is installed")
    key = sym.replace(".", "-")
    stats = (bundle.report.get("per_symbol") or {}).get(key) or (bundle.report.get("per_symbol") or {}).get(sym)
    return {"symbol": sym, "in_universe": stats is not None, "horizons": stats or {}, "model_version": bundle.version}


@router.get("/{symbol}/all")
def get_forecast_all(
    symbol: str, background_tasks: BackgroundTasks, svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    sym = validate_symbol(symbol)
    results = _run(svc, sym)
    for res in results.values():
        background_tasks.add_task(persist_forecast_record, res, sym, market_service=getattr(svc, "market", None))
    return {"symbol": sym, "horizons": {str(h): _decorate(f) for h, f in results.items()}}


@router.get("/{symbol}")
def get_forecast(
    symbol: str,
    background_tasks: BackgroundTasks,
    horizon: int = Query(default=21, description="Trading-day horizon: 1, 7, 14 or 21"),
    svc: ForecastService = Depends(get_forecast_service),
) -> dict:
    if int(horizon) not in FORECAST_HORIZONS:
        raise HTTPException(status_code=422, detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
    sym = validate_symbol(symbol)
    result = _run(svc, sym)[int(horizon)]
    background_tasks.add_task(persist_forecast_record, result, sym, market_service=getattr(svc, "market", None))
    return _decorate(result)
