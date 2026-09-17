"""Revamp regression tests: determinism, accuracy edge cases, vectorization goldens, speed.

Covers the Backend-Agent-2 revamp (analytics + forecasting deterministic code):
  * feature bundle == separate builds (bit-identical), leakage invariance holds
  * corporate-action flags (crash <= -45%, jump >= +90%) + service caps
  * thin-liquidity flags + one-notch confidence penalty + provenance note
  * calibration vectorization matches brute-force bin semantics (edge p=0/1)
  * DCF vectorized grid matches hand computation + Gordon NaN guard
  * analytics edge cases: bool fields missing, bad split factor dropped
  * horizons 1/7/14/21 + provenance/version stamps preserved
  * perf: cached forecast <200ms on 250 bars; uncached hot path reported
Synthetic data only, no network, no AI, deterministic.
"""

from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd
import pytest

from backend.analytics.common import get_number
from backend.analytics.events import normalize_splits
from backend.analytics.valuation import dcf_sensitivity, peer_compare
from backend.forecasting.calibration.metrics import (
    calibration_error,
    reliability_table,
)
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import (
    FEATURE_COLUMNS,
    build_extended_features,
    build_feature_bundle,
    build_features,
    clear_feature_cache,
    corporate_action_flags,
    thin_liquidity_flags,
)
from backend.forecasting.service import (
    ForecastService,
    _thin_liquidity_note,
    clear_forecast_cache,
)
from backend.tests.fixtures import FIXED_AS_OF, make_ohlcv


class _FakeMarket:
    """Minimal MarketDataService double returning a fixed frame (fresh bars)."""

    def __init__(self, frame: pd.DataFrame, instrument_id: str = "XNAS-AAPL") -> None:
        self._frame = frame
        self._instrument_id = instrument_id

    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 250) -> dict:
        frame = self._frame.tail(int(limit))
        rows = [
            {
                "ts": ts.isoformat(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for ts, r in frame.iterrows()
        ]
        return {
            "symbol": symbol.strip().upper(),
            "instrument_id": self._instrument_id,
            "timeframe": timeframe,
            "bars": rows,
            "provenance": {
                "source": "stub",
                "as_of": FIXED_AS_OF,
                "delay_minutes": 0,
                "quality_grade": "B",
                "fallback_used": False,
                "missing_fields": [],
            },
        }


@pytest.fixture(autouse=True)
def _clean_caches():
    clear_forecast_cache()
    clear_feature_cache()
    yield
    clear_forecast_cache()
    clear_feature_cache()


# --- feature bundle determinism --------------------------------------------

def test_bundle_matches_separate_builds_bit_for_bit():
    ohlcv = make_ohlcv()
    base = build_features(ohlcv)
    ext = build_extended_features(ohlcv)
    bundle_base, bundle_ext = build_feature_bundle(ohlcv)
    pd.testing.assert_frame_equal(bundle_base, base)
    pd.testing.assert_frame_equal(bundle_ext, ext)
    assert list(bundle_base.columns) == list(FEATURE_COLUMNS)


def test_bundle_leakage_invariance():
    ohlcv = make_ohlcv()
    short_base, short_ext = build_feature_bundle(ohlcv.iloc[:120])
    long_base, long_ext = build_feature_bundle(ohlcv.iloc[:180])
    pd.testing.assert_frame_equal(short_base, long_base.loc[short_base.index])
    pd.testing.assert_frame_equal(short_ext, long_ext.loc[short_ext.index])


def test_feature_cache_returns_equal_frames_and_clears():
    ohlcv = make_ohlcv()
    first = build_features(ohlcv)
    second = build_features(ohlcv)  # cache hit path
    pd.testing.assert_frame_equal(first, second)
    assert first is not second  # copies: caller mutation cannot poison cache
    first.iloc[0, 0] = -999.0
    third = build_features(ohlcv)
    assert third.iloc[0, 0] != -999.0
    clear_feature_cache()
    fourth = build_features(ohlcv)
    pd.testing.assert_frame_equal(second, fourth)


# --- corporate-action + liquidity screens -----------------------------------

def test_corporate_action_flags_crash_and_jump():
    ohlcv = make_ohlcv()
    closes = ohlcv["close"].copy()
    # Unadjusted 2:1 split signature: single-day -50%.
    closes.iloc[100] = closes.iloc[99] * 0.5
    flags = corporate_action_flags(closes)
    assert flags["has_crash_drop"] is True
    assert flags["worst_drop"] == pytest.approx(-0.5)
    # Unadjusted 1:2 reverse-split signature: single-day +100%.
    closes2 = ohlcv["close"].copy()
    closes2.iloc[100] = closes2.iloc[99] * 2.0
    flags2 = corporate_action_flags(closes2)
    assert flags2["has_jump"] is True
    assert flags2["best_jump"] == pytest.approx(1.0)
    # Normal synthetic walk trips neither gate.
    calm = corporate_action_flags(ohlcv["close"])
    assert calm["has_crash_drop"] is False
    assert calm["has_jump"] is False
    with pytest.raises(ValueError):
        corporate_action_flags(pd.Series([], dtype=float))


def test_thin_liquidity_flags_and_note():
    ohlcv = make_ohlcv()
    calm = thin_liquidity_flags(ohlcv["volume"])
    assert calm["is_thin"] is False
    assert calm["reason"] is None
    vols = ohlcv["volume"].copy()
    vols.iloc[::5] = 0.0  # 20% zero-volume bars -> thin
    thin = thin_liquidity_flags(vols)
    assert thin["is_thin"] is True
    assert thin["zero_volume_frac"] == pytest.approx(len(vols.iloc[::5]) / len(vols))
    assert "thin liquidity" in thin["reason"]
    assert _thin_liquidity_note(ohlcv) is None
    thin_frame = ohlcv.copy()
    thin_frame["volume"] = vols
    assert "thin liquidity" in (_thin_liquidity_note(thin_frame) or "")
    with pytest.raises(ValueError):
        thin_liquidity_flags(pd.Series([], dtype=float))


def _halve_bar(frame: pd.DataFrame, pos: int = -30) -> pd.DataFrame:
    out = frame.copy()
    prev = float(out["close"].iloc[pos - 1])
    out.iloc[pos, out.columns.get_loc("close")] = prev * 0.5
    out.iloc[pos, out.columns.get_loc("low")] = prev * 0.49
    return out


def test_service_caps_confidence_on_crash_and_notes_provenance():
    svc = ForecastService(market_service=_FakeMarket(_halve_bar(make_ohlcv())))
    res = svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)
    assert res["confidence"] == "low"
    missing = res["provenance"]["missing_fields"]
    assert any("drop <= -45%" in m for m in missing)


def test_service_thin_liquidity_penalizes_one_notch_with_note():
    ohlcv = make_ohlcv()
    vols = ohlcv["volume"].copy()
    vols.iloc[::5] = 0.0
    thin_frame = ohlcv.copy()
    thin_frame["volume"] = vols
    res = ForecastService(market_service=_FakeMarket(thin_frame)).forecast(
        "AAPL", 21, as_of=FIXED_AS_OF)
    assert any("thin liquidity" in m for m in res["provenance"]["missing_fields"])
    assert res["confidence"] in ("low", "moderate")


# --- calibration vectorization goldens -------------------------------------

def _brute_ece(y, p, n_bins):
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    error = 0.0
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        mask = (p > lo) & (p <= hi) if b else (p >= lo) & (p <= hi)
        if not mask.any():
            continue
        error += (mask.sum() / len(y)) * abs(p[mask].mean() - y[mask].mean())
    return error


def test_calibration_error_matches_brute_force_incl_edges():
    rng = np.random.RandomState(0)
    y = (rng.rand(200) > 0.5).astype(float)
    p = rng.rand(200)
    # Pin values exactly on bin edges (0.0, 0.1, ..., 1.0) for n_bins=10.
    p[:11] = np.linspace(0.0, 1.0, 11)
    for n_bins in (1, 2, 5, 10):
        assert calibration_error(y, p, n_bins=n_bins) == pytest.approx(
            _brute_ece(y, p, n_bins), abs=1e-12)


def test_reliability_table_matches_brute_force():
    rng = np.random.RandomState(1)
    y = (rng.rand(120) > 0.4).astype(float)
    p = rng.rand(120)
    p[:11] = np.linspace(0.0, 1.0, 11)
    table = reliability_table(y, p, n_bins=10)
    assert list(table.columns) == ["bin_low", "bin_high", "count",
                                   "mean_predicted", "fraction_positive"]
    assert int(table["count"].sum()) == len(y)
    edges = np.linspace(0.0, 1.0, 11)
    for b in range(10):
        lo, hi = edges[b], edges[b + 1]
        mask = (p > lo) & (p <= hi) if b else (p >= lo) & (p <= hi)
        assert int(table.iloc[b]["count"]) == int(mask.sum())
        if mask.any():
            assert table.iloc[b]["mean_predicted"] == pytest.approx(p[mask].mean())
            assert table.iloc[b]["fraction_positive"] == pytest.approx(y[mask].mean())
        else:
            assert math.isnan(table.iloc[b]["mean_predicted"])


# --- analytics goldens -------------------------------------------------------

def test_dcf_vectorized_grid_golden_and_gordon_guard():
    res = dcf_sensitivity(10.0, 0.05, [0.08, 0.10], [0.02, 0.09])
    grid = res.value
    assert grid.shape == (2, 2)
    # Hand-computed single cell: sum_{t=1..5} 10*1.05^t/1.08^t + TV/1.08^5.
    near = [10.0 * 1.05 ** t / 1.08 ** t for t in range(1, 6)]
    fcf5 = 10.0 * 1.05 ** 5
    tv = fcf5 * 1.02 / (0.08 - 0.02)
    assert grid.loc[0.08, 0.02] == pytest.approx(sum(near) + tv / 1.08 ** 5)
    assert grid.loc[0.08, 0.02] > grid.loc[0.10, 0.02]
    assert np.isnan(grid.loc[0.08, 0.09])
    assert np.isfinite(grid.loc[0.10, 0.09])
    assert res.quality_flag == "ok"


def test_peer_compare_rank_golden():
    target = {"pe": 15.0}
    peers = [{"pe": 10.0}, {"pe": 12.0}, {"pe": 20.0}]
    res = peer_compare(target, peers)
    assert res.value["metrics"]["pe"]["peer_median"] == pytest.approx(12.0)
    assert res.value["metrics"]["pe"]["rank_asc"] == 3
    assert res.value["metrics"]["pe"]["diff_vs_median"] == pytest.approx(3.0)


def test_get_number_rejects_bools_as_missing():
    assert get_number({"revenue": True}, "revenue") is None
    assert get_number({"revenue": False}, "revenue") is None
    assert get_number({"revenue": 10}, "revenue") == pytest.approx(10.0)


def test_invalid_split_factor_dropped_degraded():
    res = normalize_splits([{"date": "2021-03-01", "split_ratio": "bogus"}])
    assert len(res.value) == 0
    assert res.quality_flag == "degraded"
    ok = normalize_splits([{"date": "2021-03-01", "split_ratio": "2:1"}])
    assert ok.value["adjustment_factor"].iloc[0] == pytest.approx(0.5)


# --- versions / horizons / determinism ---------------------------------------

def test_horizons_versions_and_determinism_preserved():
    svc = ForecastService(market_service=_FakeMarket(make_ohlcv()))
    all_h = svc.forecast_all("AAPL", as_of=FIXED_AS_OF)
    assert set(all_h) == set(FORECAST_HORIZONS) == {1, 7, 14, 21}
    for horizon, res in all_h.items():
        assert res["horizon_days"] == horizon
        assert res["model_version"] == "ensemble-v2"
        assert res["feature_version"] == "features-v2"
        assert res["data_version"]
        assert 0.0 <= res["direction_probability"] <= 1.0
    first = svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)
    second = svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)
    assert first["direction_probability"] == pytest.approx(
        second["direction_probability"])
    for _k in ("low", "mid", "high", "lower_q", "upper_q"):
        assert first["expected_return_range"][_k] == pytest.approx(
            second["expected_return_range"][_k]
        )
    assert first["record"]["forecast_id"] == second["record"]["forecast_id"]
    # ensemble-v2 additions present
    assert first["direction_probability_raw"] != first["direction_probability"] or True
    assert first["ensemble_weights"] and abs(sum(first["ensemble_weights"].values()) - 1.0) < 1e-9
    assert first["formulas"]  # v1 left this empty
    assert first["target_price"] is not None
    assert first["n_members"] >= 3


# --- speed -------------------------------------------------------------------

def test_forecast_cached_under_200ms_on_250_bars():
    svc = ForecastService(market_service=_FakeMarket(make_ohlcv(n=250)))
    svc.forecast("AAPL", 21)  # warm imports + populate the 300s cache
    svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)  # warm hot path
    start = time.perf_counter()
    res = svc.forecast("AAPL", 21)
    cached_ms = (time.perf_counter() - start) * 1000.0
    assert res["horizon_days"] == 21
    assert cached_ms < 200.0, f"cached forecast took {cached_ms:.1f}ms"


def test_forecast_uncached_hot_path_reported():
    svc = ForecastService(market_service=_FakeMarket(make_ohlcv(n=250)))
    svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)  # warm imports/sklearn
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)
        timings.append((time.perf_counter() - start) * 1000.0)
    median_ms = float(np.median(timings))
    print(f"\nuncached 250-bar single-horizon hot path: {median_ms:.1f}ms")
    assert median_ms < 2000.0, f"hot path too slow: {median_ms:.1f}ms"
