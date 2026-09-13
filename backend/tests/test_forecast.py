"""Unit + leakage tests for forecasting baselines (Milestone 3 + spec section 6).

Covers: leakage-safe features, all five baselines, calibration metrics,
walk-forward splitter and the no-leakage guard. Synthetic fixtures only.
"""

import numpy as np
import pandas as pd
import pytest

from backend.forecasting.backtesting import (
    LeakageError,
    WalkForwardSplitter,
    assert_no_leakage,
)
from backend.forecasting.calibration import (
    brier_score,
    calibration_error,
    reliability_table,
)
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features import (
    FEATURE_COLUMNS,
    build_features,
    direction_label,
    future_drawdown,
    future_return,
    validate_ohlcv,
)
from backend.forecasting.models import (
    GradientBoostDirectionModel,
    HistoricalDriftBaseline,
    LogisticDirectionModel,
    MomentumBaseline,
    drawdown_probability,
    return_quantiles,
    volatility_regime,
)
from backend.tests.fixtures import FIXED_AS_OF, make_ohlcv

OHLCV = make_ohlcv()
CLOSE = OHLCV["close"]


# --- features --------------------------------------------------------------

def test_feature_columns_and_past_only_shape():
    features = build_features(OHLCV)
    assert list(features.columns) == list(FEATURE_COLUMNS)
    assert len(features) < len(OHLCV)  # warmup rows dropped
    assert np.isfinite(features.to_numpy()).all()


def test_features_appending_future_does_not_change_past():
    """Core leakage test: past feature rows are invariant to future data."""
    short = build_features(OHLCV.iloc[:100])
    long_ = build_features(OHLCV.iloc[:150])
    pd.testing.assert_frame_equal(short, long_.loc[short.index])


def test_features_match_manual_recomputation():
    features = build_features(OHLCV)
    idx = features.index[50]
    pos = OHLCV.index.get_loc(idx)
    window = OHLCV["close"].iloc[pos - 21:pos + 1]  # 21-day trailing return
    assert features.loc[idx, "mom_21"] == pytest.approx(window.iloc[-1] / window.iloc[0] - 1)


def test_validate_ohlcv_rejects_bad_input():
    with pytest.raises(ValueError):
        validate_ohlcv(OHLCV.drop(columns=["volume"]))
    with pytest.raises(ValueError):
        validate_ohlcv(OHLCV.iloc[::-1])  # not sorted oldest-first


def test_direction_labels_use_forward_returns():
    labels = direction_label(CLOSE, 5)
    forward = future_return(CLOSE, 5)
    observable = forward.dropna()
    expected = (observable > 0).astype(float)
    pd.testing.assert_series_equal(labels.dropna(), expected, check_names=False)
    assert labels.iloc[-5:].isna().all()  # horizon unobservable at the tail


def test_future_drawdown_nonpositive_and_bounded():
    ddown = future_drawdown(CLOSE, 21).dropna()
    assert ((ddown <= 0) & (ddown >= -1)).all()


# --- baselines 1-2 ---------------------------------------------------------

def test_drift_baseline_deterministic_and_sane():
    rets = np.log(CLOSE / CLOSE.shift(1)).dropna()
    first = HistoricalDriftBaseline().fit(rets).predict_all_horizons(as_of=FIXED_AS_OF)
    second = HistoricalDriftBaseline().fit(rets).predict_all_horizons(as_of=FIXED_AS_OF)
    assert set(first) == set(FORECAST_HORIZONS)
    for horizon in FORECAST_HORIZONS:
        assert first[horizon].value == pytest.approx(second[horizon].value)
        assert 0.0 <= first[horizon].value <= 1.0
        assert first[horizon].model_version == "historical-drift-v1"
        assert first[horizon].as_of == FIXED_AS_OF
    assert first[21].value > 0.5  # synthetic drift is positive
    band = HistoricalDriftBaseline().fit(rets).expected_return_range(21)
    assert band.value["low"] < band.value["mid"] < band.value["high"]


def test_momentum_uptrend_is_bullish_and_bounded():
    rising = pd.Series(100 * np.exp(0.002 * np.arange(120)))
    proba = MomentumBaseline(trailing_days=63).fit(rising).direction_probability(21).value
    assert proba > 0.5
    falling = pd.Series(100 * np.exp(-0.002 * np.arange(120)))
    assert MomentumBaseline(trailing_days=63).fit(falling).direction_probability(21).value < 0.5
    flat = pd.Series(np.full(120, 100.0))
    assert MomentumBaseline(trailing_days=63).fit(flat).direction_probability(21).value == 0.5


# --- baselines 3-4 ---------------------------------------------------------

def _trained_classifiers():
    features = build_features(OHLCV)
    logreg = LogisticDirectionModel().fit(features, CLOSE)
    gbm = GradientBoostDirectionModel().fit(features, CLOSE)
    return features, logreg, gbm


def test_logistic_probabilities_valid_and_deterministic():
    features, first, _ = _trained_classifiers()
    second = LogisticDirectionModel().fit(features, CLOSE)
    latest = features.iloc[[-1]]
    for horizon in FORECAST_HORIZONS:
        p1 = first.predict_direction_proba(latest, as_of=FIXED_AS_OF)[horizon]
        p2 = second.predict_direction_proba(latest)[horizon]
        assert p1.value == pytest.approx(p2.value)
        assert 0.0 <= p1.value <= 1.0


def test_gradient_boost_stub_probabilities_valid_and_deterministic():
    features, _, first = _trained_classifiers()
    second = GradientBoostDirectionModel().fit(features, CLOSE)
    latest = features.iloc[[-1]]
    for horizon in FORECAST_HORIZONS:
        p1 = first.predict_direction_proba(latest, as_of=FIXED_AS_OF)[horizon]
        p2 = second.predict_direction_proba(latest)[horizon]
        assert p1.value == pytest.approx(p2.value)
        assert 0.0 <= p1.value <= 1.0
        assert "stub" in p1.model_version


def test_classifiers_need_two_classes_and_enough_data():
    features = build_features(OHLCV)
    flat = pd.Series(np.full(len(CLOSE), 100.0), index=CLOSE.index)
    with pytest.raises(ValueError):  # single-class labels on a flat series
        LogisticDirectionModel().fit(features, flat)
    with pytest.raises(ValueError):  # too few label-observable rows
        LogisticDirectionModel().fit(features.iloc[:30], CLOSE.iloc[:30])


# --- baseline 5 ------------------------------------------------------------

def test_quantile_bands_ordered():
    bands = return_quantiles(CLOSE, as_of=FIXED_AS_OF)
    assert set(bands) == set(FORECAST_HORIZONS)
    for horizon, result in bands.items():
        assert result.value["low"] <= result.value["median"] <= result.value["high"]


def test_volatility_regime_labels():
    result = volatility_regime(CLOSE, as_of=FIXED_AS_OF)
    assert result.value["regime"] in ("low", "normal", "high")
    assert result.value["trailing_vol"] > 0


def test_drawdown_probability_bounds():
    result = drawdown_probability(CLOSE, 21, threshold=0.10, as_of=FIXED_AS_OF)
    assert 0.0 <= result.value["probability"] <= 1.0
    assert result.value["n_breaches"] <= result.value["n_windows"]


# --- calibration -----------------------------------------------------------

def test_brier_known_value_and_perfect_zero():
    assert brier_score([0, 1], [0.25, 0.75]) == pytest.approx(0.0625)
    assert brier_score([0, 1, 1, 0], [0, 1, 1, 0]) == pytest.approx(0.0)
    assert brier_score([0, 1], [0.5, 0.5]) == pytest.approx(0.25)


def test_calibration_error_perfect_is_zero():
    assert calibration_error([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9], n_bins=2) == \
        pytest.approx(0.15, abs=0.05)
    assert calibration_error([0, 1], [0.0, 1.0]) == pytest.approx(0.0)


def test_reliability_table_sums_to_n():
    table = reliability_table([0, 1, 1, 0, 1], [0.1, 0.4, 0.6, 0.8, 0.9], n_bins=5)
    assert table["count"].sum() == 5
    assert list(table.columns) == ["bin_low", "bin_high", "count",
                                   "mean_predicted", "fraction_positive"]


def test_calibration_rejects_bad_inputs():
    with pytest.raises(ValueError):
        brier_score([0, 1], [1.5, 0.5])
    with pytest.raises(ValueError):
        brier_score([0, 2], [0.5, 0.5])
    with pytest.raises(ValueError):
        brier_score([0], [0.5, 0.5])


# --- backtesting -----------------------------------------------------------

def test_walk_forward_is_time_ordered_with_gap():
    splitter = WalkForwardSplitter(train_size=100, test_size=20, gap=5)
    folds = list(splitter.splits(252))
    assert len(folds) > 1
    for train, test in folds:
        assert train.max() < test.min()
        assert test.min() - train.max() - 1 >= 5
    starts = [test[0] for _, test in folds]
    assert starts == sorted(starts)


def test_walk_forward_expanding_and_count():
    splitter = WalkForwardSplitter(train_size=50, test_size=10, expanding=True)
    folds = list(splitter.splits(120))
    assert folds[0][0][0] == 0  # expanding train starts at origin
    assert all(len(train) >= 50 for train, _ in folds)
    assert splitter.n_splits(120) == len(folds)


def test_no_leakage_guard_raises():
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [2, 3, 4])  # overlap
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 5], [3, 4], gap=0)  # test inside train span
    with pytest.raises(LeakageError):
        assert_no_leakage([0, 1, 2], [3, 4], gap=2)  # gap violated
    assert_no_leakage([0, 1, 2], [5, 6], gap=2)  # exactly satisfied: OK


def test_walk_forward_models_fit_only_on_past():
    """End-to-end leakage test: fold-train fits never see fold-test rows."""
    features = build_features(OHLCV)
    closes = CLOSE.loc[features.index]
    splitter = WalkForwardSplitter(train_size=100, test_size=21, gap=63)
    probas = []
    for train, test in splitter.splits(len(features)):
        model = LogisticDirectionModel(horizons=[21]).fit(
            features.iloc[train], closes.iloc[train])
        proba = model.predict_direction_proba(features.iloc[test[:1]])[21].value
        assert 0.0 <= proba <= 1.0
        probas.append(proba)
    assert len(probas) >= 1


# --- Phase 2c: regime-aware confidence -------------------------------------

def test_confidence_band_edges_spread_size_quality():
    """Base spread/size/quality matrix (no regime/dd penalties)."""
    from backend.forecasting.service import _confidence

    assert _confidence(0.05, 3, "B") == "high"  # full agreement, 3 members
    assert _confidence(0.08, 3, "B") == "high"  # edge inclusive
    assert _confidence(0.10, 3, "B") == "moderate"
    assert _confidence(0.15, 3, "B") == "moderate"  # edge inclusive
    assert _confidence(0.16, 3, "B") == "low"
    assert _confidence(0.20, 3, "B") == "low"
    assert _confidence(0.05, 2, "B") == "moderate"  # thin ensemble caps high
    assert _confidence(0.05, 1, "B") == "low"
    assert _confidence(0.05, 0, "B") == "low"
    assert _confidence(0.05, 3, "D") == "low"  # poor quality caps at low
    assert _confidence(0.05, 3, "F") == "low"
    assert _confidence(0.05, 3, "f") == "low"  # case-insensitive grade
    assert _confidence(0.10, 3, "A") == "moderate"  # good grade untouched


def test_confidence_regime_penalty():
    """elevated/extreme penalize one notch; others are no-ops."""
    from backend.forecasting.service import _confidence

    # high-spread-agreement + elevated -> moderate (spec example).
    assert _confidence(0.05, 3, "B", regime="elevated") == "moderate"
    # high + extreme -> moderate (extreme caps at most moderate from high).
    assert _confidence(0.05, 3, "B", regime="extreme") == "moderate"
    # moderate + extreme -> low.
    assert _confidence(0.10, 3, "B", regime="extreme") == "low"
    assert _confidence(0.10, 3, "B", regime="elevated") == "low"
    # low stays low (floor).
    assert _confidence(0.20, 3, "B", regime="extreme") == "low"
    assert _confidence(0.20, 3, "B", regime="elevated") == "low"
    # No-ops: None / low / normal leave the base level untouched.
    assert _confidence(0.05, 3, "B", regime=None) == "high"
    assert _confidence(0.05, 3, "B", regime="low") == "high"
    assert _confidence(0.05, 3, "B", regime="normal") == "high"
    assert _confidence(0.10, 3, "B", regime="normal") == "moderate"


def test_confidence_drawdown_penalty():
    """drawdown_probability >= 0.25 penalizes one notch."""
    from backend.forecasting.service import _confidence

    assert _confidence(0.05, 3, "B", drawdown_prob=0.30) == "moderate"
    assert _confidence(0.05, 3, "B", drawdown_prob=0.25) == "moderate"  # edge
    assert _confidence(0.10, 3, "B", drawdown_prob=0.25) == "low"
    assert _confidence(0.20, 3, "B", drawdown_prob=0.90) == "low"  # floor
    # Below threshold / missing are no-ops.
    assert _confidence(0.05, 3, "B", drawdown_prob=0.24) == "high"
    assert _confidence(0.05, 3, "B", drawdown_prob=0.10) == "high"
    assert _confidence(0.05, 3, "B", drawdown_prob=None) == "high"


def test_confidence_stacked_penalties_and_floor():
    """Regime + drawdown stack via _penalize_confidence; never raise."""
    from backend.forecasting.service import _confidence, _penalize_confidence

    # high -> moderate (regime) -> low (drawdown).
    assert (
        _confidence(0.05, 3, "B", regime="extreme", drawdown_prob=0.30)
        == "low"
    )
    assert (
        _confidence(0.05, 3, "B", regime="elevated", drawdown_prob=0.25)
        == "low"
    )
    # moderate + both penalties still floors at low.
    assert (
        _confidence(0.10, 3, "B", regime="extreme", drawdown_prob=0.50)
        == "low"
    )
    # low + everything stays low.
    assert (
        _confidence(0.50, 3, "B", regime="extreme", drawdown_prob=0.99)
        == "low"
    )
    # Penalties apply AFTER quality caps: D-grade base is low, stays low.
    assert (
        _confidence(0.05, 3, "D", regime="extreme", drawdown_prob=0.30)
        == "low"
    )
    # Single-penalty equivalence with _penalize_confidence.
    assert _confidence(0.05, 3, "B", regime="extreme") == _penalize_confidence(
        _confidence(0.05, 3, "B")
    )
    assert _confidence(0.10, 3, "B", drawdown_prob=0.30) == _penalize_confidence(
        _confidence(0.10, 3, "B")
    )


def test_confidence_backward_compatible_and_deterministic():
    """Old 3-arg calls still work; penalties are deterministic."""
    from backend.forecasting.service import _confidence

    assert _confidence(0.05, 3, "B") == "high"  # legacy signature no-op
    first = _confidence(0.05, 3, "B", regime="extreme", drawdown_prob=0.30)
    second = _confidence(0.05, 3, "B", regime="extreme", drawdown_prob=0.30)
    assert first == second == "low"
