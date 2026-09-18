"""Walk-forward backtest API: POST /api/backtest/run + GET /api/backtest/{symbol}.

Each run fits the ensemble-v2 members (drift/momentum/logistic-v3/
gradient-boost-v1/trend-persistence) on train folds only (point-in-time
labels, WalkForwardSplitter + assert_no_leakage guard) and scores the
weighted + shrinkage-calibrated ensemble with Brier/ECE + a reliability
table. No AI, no network (deterministic stub bars). Runs are kept in an
in-memory history; GET returns lightweight summaries without the full
reliability tables. Folds stay serial: the GB/logistic fits are CPython
GIL-bound, so threads add overhead without speedup (measured on the twin
snapshot path), while processes are off the table for serverless deploys.
"""

from __future__ import annotations

import hashlib
from typing import Any

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
    EXTENDED_FEATURE_VERSION,
    build_feature_bundle,
    build_features,
    direction_label,
    log_returns,
)
from backend.forecasting.models.gradient_boost import GradientBoostDirectionModel
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.forecasting.service import (
    _calibrate_prob,
    _trend_persistence_signal,
    _weighted_mean,
)
from backend.market_data.service import MarketDataService

try:  # V2 Phase 2 canonical guards
    from backend.auth.guards import require_tier  # type: ignore
except ImportError:  # pragma: no cover - fallback until Phase 2 lands
    from typing import Any as _Any

    from fastapi import Request as _Request

    from backend.auth.tiers import _TIER_RANK as _RANK
    from backend.auth.tiers import normalize_tier as _norm

    _TEST_TOKENS: dict[str, dict[str, _Any]] = {
        "test-free": {"user_id": "user-free", "tier": "free", "is_admin": False},
        "test-silver": {"user_id": "user-silver", "tier": "silver", "is_admin": False},
        "test-gold": {"user_id": "user-gold", "tier": "gold", "is_admin": False},
        "test-platinum": {"user_id": "user-platinum", "tier": "platinum", "is_admin": False},
        "test-admin": {"user_id": "admin-1", "tier": "platinum", "is_admin": True},
    }

    def require_tier(min_tier: str):  # type: ignore[no-redef]
        need = _norm(min_tier)

        async def _dep(request: _Request) -> dict[str, _Any]:
            try:
                auth = (request.headers.get("authorization") or "").strip()
            except Exception:
                auth = ""
            token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            user = _TEST_TOKENS.get(token)
            if user is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            if bool(user.get("is_admin")):
                return dict(user)
            if _RANK[_norm(user.get("tier"))] >= _RANK[need]:
                return dict(user)
            raise HTTPException(status_code=402, detail={"message": f"upgrade required: {need} or higher", "upgrade_required": True, "min_tier": need})

        return _dep

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

DISCLOSURE = "Not investment advice. For informational purposes only."

_HISTORY: dict[str, list[dict]] = {}


def reset_backtest_history() -> None:  # test hook
    _HISTORY.clear()


class BacktestRunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32, description="e.g. AAPL")
    horizons: list[int] = Field(default_factory=lambda: [1, 7, 14, 21], max_length=4)
    train_size: int = Field(default=100, ge=20, le=1000)
    test_size: int = Field(default=21, ge=1, le=500)
    gap: int = Field(default=21, ge=0, le=500)
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


def _score_backtest_fold(
    train_idx: Any,
    test_idx: Any,
    features: pd.DataFrame,
    closes_feat: pd.Series,
    labels_full: pd.Series,
    frame: pd.DataFrame | None,
    horizon: int,
    gap: int,
) -> tuple[list[tuple[int, float, float]], bool]:
    """Score one backtest fold; returns ([(pos, label, proba)], fold_used). Rows
    are in pos order.

    Pure function of its slice arguments (shared frames read-only, fixed-seed
    fits), so folds may run in any order/thread. Skipped positions (tail
    unobservable / all members missing) are omitted, never imputed. The merge
    by :func:`_evaluate_horizon` in fold order, making output identical to
    the legacy inline loop. Returns (rows, fold_used) where fold_used
    mirrors the legacy ``n_folds_used`` increment (any member fitted, even
    if every position later skips as unobservable).
    """
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
    except (ValueError, ImportError):
        logreg = None
    try:
        gb_fold = GradientBoostDirectionModel(horizons=[int(horizon)]).fit(
            train_feat, train_close
        )
    except (ValueError, ImportError):
        gb_fold = None
    try:
        trend_fold = _trend_persistence_signal(
            frame if frame is not None else train_feat, _ext=train_feat
        )
        if trend_fold is not None and not 0.0 <= float(trend_fold) <= 1.0:
            trend_fold = None
    except Exception:
        trend_fold = None
    if drift_p is None and mom_p is None and logreg is None and gb_fold is None and trend_fold is None:
        return [], False
    # Batched ML predicts: one predict_proba per fold (not per row).
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
    gb_probs: dict[int, float] = {}
    if gb_fold is not None:
        try:
            batch = gb_fold.predict_proba_batch(features.iloc[test_idx])
            arr = batch.get(int(horizon))
            if arr is not None:
                for pos, proba in zip(
                    (int(p) for p in test_idx), (float(v) for v in arr)
                ):
                    gb_probs[pos] = proba
        except (ValueError, IndexError, KeyError):
            gb_probs = {}
    scored: list[tuple[int, float, float]] = []
    for pos in test_idx:
        pos = int(pos)
        label = labels_full.iloc[pos]
        if pd.isna(label):
            continue  # horizon unobservable at the tail: skip, never impute
        window: dict[str, float] = {}
        if drift_p is not None:
            window["historical-drift"] = float(drift_p)
        if mom_p is not None:
            window["momentum"] = float(mom_p)
        if pos in logreg_probs:
            window["logistic-direction"] = float(logreg_probs[pos])
        elif logreg is not None:
            try:
                window["logistic-direction"] = float(
                    logreg.predict_direction_proba(
                        features.iloc[[int(pos)]]
                    )[int(horizon)].value
                )
            except (ValueError, IndexError):
                pass
        if pos in gb_probs:
            window["gradient-boost-direction"] = float(gb_probs[pos])
        elif gb_fold is not None:
            try:
                window["gradient-boost-direction"] = float(
                    gb_fold.predict_direction_proba(
                        features.iloc[[int(pos)]]
                    )[int(horizon)].value
                )
            except (ValueError, IndexError):
                pass
        if trend_fold is not None:
            window["trend-persistence"] = float(trend_fold)
        if not window:
            continue
        raw = _weighted_mean(window)[0]
        scored.append((pos, float(label), float(_calibrate_prob(raw))))
    return scored, True


def _evaluate_horizon(
    features: pd.DataFrame,
    closes_feat: pd.Series,
    horizon: int,
    train_size: int,
    test_size: int,
    gap: int,
    n_bins: int,
    frame: pd.DataFrame | None = None,
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
        scored, fold_used = _score_backtest_fold(
            train_idx, test_idx, features, closes_feat,
            labels_full, frame, int(horizon), gap,
        )
        if fold_used:
            n_folds_used += 1
        for pos, label_f, proba in scored:
            y_true.append(float(label_f))
            y_prob.append(float(proba))
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
    # Purge guard: gap must be >= max(horizons) (V2: max 21). Fail fast with 422
    # instead of silently scoring leaky folds.
    try:
        req.validate_gap()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Alphabet consistency with quote/bars/forecast (validate_symbol rejects
    # ^, /, _ etc. with 422 instead of a downstream 502).
    try:
        from backend.security.validation import validate_symbol as _vsym

        req.symbol = _vsym(req.symbol, field="symbol")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        bars = market.get_bars(req.symbol, timeframe="1d", limit=req.limit)
    except HTTPException:
        raise
    except Exception as exc:
        from backend.market_data.providers.base import ProviderError as _PE

        if isinstance(exc, _PE):
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=f"backtest bars failed: {exc}") from exc
    rows = bars.get("bars", []) if isinstance(bars, dict) else []
    if not rows:
        raise HTTPException(status_code=422, detail=f"insufficient history for {req.symbol!r}: 0 bars (required_bars>=100 for walk-forward)")
    try:
        # Single-pass frame build (one loop, not five list comps).
        # Drop malformed rows (vendor gaps must degrade, not KeyError->502).
        recs = []
        for r in rows:
            try:
                if not isinstance(r, dict):
                    continue
                import math as _math
                if not _math.isfinite(float(r.get("close"))):  # type: ignore[arg-type]
                    continue
                recs.append((r.get("open"), r.get("high"), r.get("low"), r.get("close"),
                             float(r.get("volume") or 0), r.get("ts")))
            except (TypeError, ValueError):
                continue
        if not recs:
            raise HTTPException(status_code=422, detail=f"insufficient history for {req.symbol!r}: 0 usable bars (required_bars>=100 for walk-forward)")
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
        raise HTTPException(status_code=422, detail=f"backtest frame failed: {exc}") from exc
    try:
        # ensemble-v2: ML members train on the extended frame (v2).
        _, features = build_feature_bundle(frame)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"backtest features failed: {exc}") from exc
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
                features, closes_feat, h, req.train_size, req.test_size, req.gap, req.n_bins,
                frame=frame,
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
        "feature_version": EXTENDED_FEATURE_VERSION,
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
    user: dict = Depends(require_tier("gold")),
) -> dict:
    """Run a walk-forward calibration backtest (no leakage by construction).

    V2 HARD gate: gold+ (free/silver -> 402, admin bypasses).
    """
    return _run_backtest(req, market)


@router.get("/{symbol}")
def backtest_history(
    symbol: str,
    include_reliability: bool = Query(
        default=False,
        description="Include reliability tables (+ brier/ece) per horizon",
    ),
    market: MarketDataService = Depends(get_market_service),
    user: dict = Depends(require_tier("gold")),
) -> dict:
    """Lightweight calibration history for one symbol (summaries only).

    ``include_reliability=true`` adds the full reliability table per horizon
    (additive; default false keeps the lightweight summary shape).
    """
    from backend.security.validation import validate_symbol as _vsym

    try:
        sym = _vsym(symbol, field="symbol")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
