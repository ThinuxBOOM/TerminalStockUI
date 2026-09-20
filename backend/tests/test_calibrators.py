"""Ensemble-v3 calibrator unit tests (offline, deterministic, no network)."""

import pytest

from backend.forecasting.calibration.calibrators import (
    apply_calibrator,
    brier_to_weights,
    fit_isotonic,
)


def test_fit_returns_none_on_small_or_single_class():
    assert fit_isotonic([0.6, 0.7], [1, 0]) is None  # n < 30
    assert fit_isotonic([0.6] * 40, [1] * 40) is None  # single class
    assert fit_isotonic([], []) is None


def test_fit_auto_selects_platt_or_isotonic():
    import numpy as np

    rng = np.random.RandomState(3)
    raw_40 = list(rng.rand(40))
    y_40 = [1 if r > 0.5 else 0 for r in raw_40]
    cal_40 = fit_isotonic(raw_40, y_40, kind="auto")
    assert cal_40 is not None and cal_40["kind"] == "platt"

    raw_80 = list(rng.rand(80))
    y_80 = [1 if r > 0.5 else 0 for r in raw_80]
    cal_80 = fit_isotonic(raw_80, y_80, kind="auto")
    assert cal_80 is not None and cal_80["kind"] == "isotonic"
    assert len(cal_80["xs"]) == len(cal_80["ys"]) > 0


def test_apply_isotonic_monotone_and_clipped():
    cal = {"kind": "isotonic", "xs": [0.0, 0.5, 1.0], "ys": [0.2, 0.5, 0.8], "n": 60}
    assert apply_calibrator(0.25, cal) == pytest.approx(0.35)
    assert apply_calibrator(0.0, cal) == pytest.approx(0.2)
    # Out-of-range clips via interp edges, then floor/cap.
    assert 0.05 <= apply_calibrator(-5.0, cal) <= 0.95
    assert 0.05 <= apply_calibrator(99.0, cal) <= 0.95
    # Bad dicts fall back to shrinkage, never raise.
    assert apply_calibrator(0.7, None) == pytest.approx(0.5 + (0.7 - 0.5) * 0.8)
    assert apply_calibrator(0.7, {"kind": "nope"}) == pytest.approx(
        0.5 + (0.7 - 0.5) * 0.8
    )
    assert apply_calibrator(float("nan"), cal) == 0.5


def test_apply_platt_bounded():
    cal = {"kind": "platt", "a": 2.0, "b": -1.0, "n": 40}
    out = apply_calibrator(0.7, cal)
    assert 0.05 <= out <= 0.95


def test_brier_to_weights_inverse_and_fallback():
    w = brier_to_weights({"a": 0.10, "b": 0.20, "c": 0.30})
    assert w is not None
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"] > w["c"]  # lower Brier -> higher weight
    # Missing/non-finite/bad values -> None (caller keeps fixed weights).
    assert brier_to_weights({}) is None
    assert brier_to_weights(None) is None
    assert brier_to_weights({"a": None, "b": float("nan")}) is None
    assert brier_to_weights({"a": 0.0, "b": -1.0}) is None


def test_cross_fitted_scores_honest_and_bounded():
    import numpy as np

    from backend.forecasting.calibration.calibrators import cross_fitted_scores

    rng = np.random.RandomState(11)
    raw = list(rng.rand(80))
    y = [1 if r > 0.5 else 0 for r in raw]
    cal, brier, ece = cross_fitted_scores(raw, y, n_bins=10)
    assert cal is not None and cal["kind"] in ("isotonic", "platt")
    assert brier is not None and 0.0 <= brier <= 1.0
    assert ece is not None and 0.0 <= ece <= 1.0
    # Too few pairs -> honest Nones (full fit may still return for use).
    cal2, b2, e2 = cross_fitted_scores(raw[:20], y[:20])
    assert b2 is None and e2 is None
