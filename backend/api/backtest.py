"""Walk-forward backtest API: POST /api/backtest/run + GET /api/backtest/{symbol}.

Each run fits drift/momentum/logistic baselines on train folds only
(point-in-time labels, WalkForwardSplitter + assert_no_leakage guard) and
scores the mean-ensemble with Brier/ECE + a reliability table. No AI, no
network (deterministic stub bars). Runs are kept in an in-memory history;
GET returns lightweight summaries without the full reliability tables.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from backend.api.deps import get_market_service
from backend.forecasting.backtesting import WalkForwardSplitter, assert_no_leakage
from backend.forecasting.calibration import (
    brier_score,
    calibration_error,
    reliability_table,
)
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import (
    FEATURE_VERSION,
    build_features,
    direction_label,
    log_returns,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

DISCLOSURE = "Not investment advice. For informational purposes only."

_HISTORY: dict[str, list[dict]] = {}


def reset_backtest_history() -> None:  # test hook
    _HISTORY.clear()


class BacktestRunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, description="e.g. AAPL")
    horizons: list[int] = Field(default_factory=lambda: [5, 21, 63], max_length=3)
    train_size: int = Field(default=100, ge=20, le=1000)
    test_size: int = Field(default=21, ge=1, le=500)
    gap: int = Field(default=63, ge=0, le=500)
    n_bins: int = Field(default=10, ge=2, le=20)
    limit: int = Field(default=250, ge=100, le=250)

    @field_validator("horizons")
    @classmethod
    def _check_horizons(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("horizons must be non-empty")
        bad = [h for h in value if int(h) not in FORECAST_HORIZONS]
        if bad:
            raise ValueError(
                f"horizons must be a subset of {list(FORECAST_HORIZONS)}, got {value}"
            )
        return [int(h) for h in value]

    @field_validator("symbol")
    @classmethod
    def _strip_symbol(cls, value: str) -> str:
        text = value.strip().upper()
        if not text:
            raise ValueError("symbol must be non-empty")
        return text

    def validate_gap(self) -> None:
        """Require gap >= max(horizons) so train labels cannot straddle tests."""
        worst = max(int(h) for h in self.horizons)
        if int(self.gap) < worst:
            raise ValueError(
                f"gap ({self.gap}) must be >= max(horizons) ({worst}) to purge "
                f"forward-label overlap; raise gap or shrink horizons"
            )


def _reliability_records(table: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    try:
        records = table.to_dict(orient="records")
    except Exception:
        return out
    for row in records:
        try:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "bin_low": float(row["bin_low"]),
                    "bin_high": float(row["bin_high"]),
                    "count": int(row["count"]),
                    "mean_predicted": None
                    if pd.isna(row["mean_predicted"])
                    else float(row["mean_predicted"]),
                    "fraction_positive": None
                    if pd.isna(row["fraction_positive"])
                    else float(row["fraction_positive"]),
                }
            )
        except Exception:
            continue
    return out


def _evaluate_horizon(
    features: pd.DataFrame,
    closes_feat: pd.Series,
    horizon: int,
    train_size: int,
    test_size: int,
    gap: int,
    n_bins: int,
) -> dict:
    labels_full = direction_label(closes_feat, int(horizon))
    # Expanding origin: train blocks are always contiguous history prefixes
    # (0:origin), so globally precomputed trailing features carry the same
    # warmup as live inference. Rolling mid-series slices would reuse
    # warmup smoothed with out-of-fold past (see features warmup caveat).
    splitter = WalkForwardSplitter(
        train_size=train_size, test_size=test_size, gap=gap, expanding=True
    )
    try:
        folds = list(splitter.splits(len(features)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    y_true: list[float] = []
    y_prob: list[float] = []
    n_folds_used = 0
    for train_idx, test_idx in folds:
        assert_no_leakage(train_idx, test_idx, gap)
        train_feat = features.iloc[train_idx]
        train_close = closes_feat.iloc[train_idx]
        try:
            drift_p = float(
                HistoricalDriftBaseline()
                .fit(log_returns(train_close).dropna())
                .direction_probability(int(horizon))
                .value
            )
        except ValueError:
            drift_p = None  # type: ignore[assignment]
        try:
            mom_p = float(
                MomentumBaseline()
                .fit(train_close)
                .direction_probability(int(horizon))
                .value
            )
        except ValueError:
            mom_p = None  # type: ignore[assignment]
        try:
            logreg = LogisticDirectionModel(horizons=[int(horizon)]).fit(
                train_feat, train_close
            )
        except ValueError:
            logreg = None
        if drift_p is None and mom_p is None and logreg is None:
            continue
        n_folds_used += 1
        # Batched logistic predict: one predict_proba per fold (not per row).
        logreg_probs: dict[int, float] = {}
        if logreg is not None:
            try:
                batch = logreg.predict_proba_batch(features.iloc[test_idx])
                arr = batch.get(int(horizon))
                if arr is not None:
                    for pos, proba in zip(
                        (int(p) for p in test_idx), (float(v) for v in arr)
                    ):
                        logreg_probs[pos] = proba
            except (ValueError, IndexError, KeyError):
                logreg_probs = {}
        for pos in test_idx:
            pos = int(pos)
            label = labels_full.iloc[pos]
            if pd.isna(label):
                continue  # horizon unobservable at the tail: skip, never impute
            parts: list[float] = []
            if drift_p is not None:
                parts.append(float(drift_p))
            if mom_p is not None:
                parts.append(float(mom_p))
            if pos in logreg_probs:
                parts.append(float(logreg_probs[pos]))
            elif logreg is not None:
                try:
                    parts.append(
                        float(
                            logreg.predict_direction_proba(
                                features.iloc[[int(pos)]]
                            )[int(horizon)].value
                        )
                    )
                except (ValueError, IndexError):
                    pass
            if not parts:
                continue
            y_true.append(float(label))
            y_prob.append(float(sum(parts) / len(parts)))
    if not y_true:
        raise HTTPException(
            status_code=422,
            detail=f"horizon {horizon}: no observable labels for these splits "
            "(increase limit / shrink horizon / gap)",
        )
    try:
        table = reliability_table(y_true, y_prob, n_bins=n_bins)
        brier = float(brier_score(y_true, y_prob))
        ece = float(calibration_error(y_true, y_prob, n_bins=n_bins))
        reliability = _reliability_records(table)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backtest scoring failed: {exc}") from exc
    return {
        "n_folds": int(n_folds_used),
        "n_points": int(len(y_true)),
        "n_warning": (
            "insufficient windows (n<10): scores unreliable"
            if len(y_true) < 10
            else ("small sample (n<30): wide uncertainty" if len(y_true) < 30 else None)
        ),
        "brier": brier,
        "brier_formula": "Brier = mean((p_i - y_i)^2); 0 = perfect, 0.25 = coin-flip baseline",
        "ece": ece,
        "ece_formula": "ECE = sum_b (|bin_b|/n * |mean_p_b - frac_pos_b|) over equal-width bins",
        "reliability": reliability,
    }


def _run_backtest(req: BacktestRunRequest, market: MarketDataService) -> dict:
    # Purge guard: default gap=5 leaks for h=21/63. Fail fast with 422
    # instead of silently scoring leaky folds.
    try:
        req.validate_gap()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        bars = market.get_bars(req.symbol, timeframe="1d", limit=req.limit)
    except HTTPException:
        raise
    except Exception as exc:
        from backend.market_data.providers.base import ProviderError as _PE

        if isinstance(exc, _PE):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=f"backtest bars failed: {exc}") from exc
    rows = bars.get("bars", []) if isinstance(bars, dict) else []
    if not rows:
        raise HTTPException(status_code=422, detail=f"insufficient history for {req.symbol!r}: 0 bars")
    try:
        # Single-pass frame build (one loop, not five list comps).
        recs = [
            (r["open"], r["high"], r["low"], r["close"],
             float(r["volume"] or 0), r["ts"])
            for r in rows
        ]
        frame = pd.DataFrame(
            {
                "open": [o for o, _, _, _, _, _ in recs],
                "high": [h for _, h, _, _, _, _ in recs],
                "low": [lo for _, _, lo, _, _, _ in recs],
                "close": [c for _, _, _, c, _, _ in recs],
                "volume": [v for _, _, _, _, v, _ in recs],
            },
            index=pd.to_datetime([t for _, _, _, _, _, t in recs]),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backtest frame failed: {exc}") from exc
    try:
        features = build_features(frame)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"backtest features failed: {exc}") from exc
    closes_feat = frame["close"].loc[features.index]
    provenance = dict(bars.get("provenance", {})) if isinstance(bars, dict) else {}
    stamp = str(provenance.get("as_of", ""))
    day = stamp[:10] if len(stamp) >= 10 else (stamp or "unknown")
    data_version = f"{provenance.get('source', 'unknown')}-bars-{day}"
    horizons = sorted(set(req.horizons))
    results: dict[str, dict] = {}
    for h in horizons:
        try:
            results[str(h)] = _evaluate_horizon(
                features, closes_feat, h, req.train_size, req.test_size, req.gap, req.n_bins
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"backtest horizon {h} failed: {exc}") from exc
    material = "|".join(
        [req.symbol, ",".join(map(str, horizons)),
         str(req.train_size), str(req.test_size), str(req.gap), stamp]
    )
    run_id = hashlib.sha256(material.encode()).hexdigest()[:16]
    run = {
        "run_id": run_id,
        "symbol": req.symbol,
        "horizons": horizons,
        "params": {
            "train_size": req.train_size,
            "test_size": req.test_size,
            "gap": req.gap,
            "n_bins": req.n_bins,
            "limit": req.limit,
        },
        "results": results,
        "model_version": ENSEMBLE_VERSION,
        "feature_version": FEATURE_VERSION,
        "data_version": data_version,
        "as_of": stamp,
        "provenance": provenance,
        "disclosure": DISCLOSURE,
    }
    _HISTORY.setdefault(req.symbol, []).append(run)
    return run


@router.post("/run")
def run_backtest(
    req: BacktestRunRequest,
    market: MarketDataService = Depends(get_market_service),
) -> dict:
    """Run a walk-forward calibration backtest (no leakage by construction)."""
    return _run_backtest(req, market)


@router.get("/{symbol}")
def backtest_history(
    symbol: str,
    include_reliability: bool = Query(
        default=False,
        description="Include reliability tables (+ brier/ece) per horizon",
    ),
    market: MarketDataService = Depends(get_market_service),
) -> dict:
    """Lightweight calibration history for one symbol (summaries only).

    ``include_reliability=true`` adds the full reliability table per horizon
    (additive; default false keeps the lightweight summary shape).
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol must be a non-empty string")
    if len(sym) > 32 or not all(c.isalnum() or c in "._-/=" for c in sym):
        raise HTTPException(status_code=422, detail="symbol contains unsupported characters")
    try:
        bars = market.get_bars(sym, timeframe="1d", limit=5)
        provenance = dict(bars.get("provenance", {})) if isinstance(bars, dict) else {}
        if not provenance:
            raise ValueError("empty provenance")
    except HTTPException:
        raise
    except Exception as exc:
        # Fail-closed: no bars, no honest provenance — raise instead of
        # fabricating an envelope.
        from backend.market_data.providers.base import ProviderError as _PE

        if isinstance(exc, _PE):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=f"backtest history failed: {exc}") from exc
    runs = _HISTORY.get(sym, [])
    summaries = []
    for r in runs:
        metrics: dict[str, dict] = {}
        for h, m in r["results"].items():
            entry: dict = {
                "n_folds": m["n_folds"],
                "n_points": m["n_points"],
                "brier": m["brier"],
                "ece": m["ece"],
            }
            if include_reliability:
                entry["reliability"] = list(m.get("reliability", []))
            metrics[h] = entry
        summaries.append(
            {
                "run_id": r["run_id"],
                "as_of": r["as_of"],
                "horizons": r["horizons"],
                "params": r["params"],
                "metrics": metrics,
                "model_version": r["model_version"],
                "feature_version": r["feature_version"],
                "data_version": r["data_version"],
            }
        )
    return {
        "symbol": sym,
        "as_of": str(provenance.get("as_of")),
        "provenance": provenance,
        "runs": summaries,
        "disclosure": DISCLOSURE,
    }
