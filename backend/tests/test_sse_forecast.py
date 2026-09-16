"""SSE market-specific forecasting tests (Milestone 6).

Synthetic bars only, no network, no AI. Covers:
  * SSE feature formulas + limit-up proximity flag fires
  * CNY scale invariance + past-invariance (no leakage)
  * SSE drift winsorization + wider bands
  * Service routing (.SS suffix or XSHG MIC) -> blended versions
  * Confidence penalized one notch on limit proximity
  * Horizons 5/21/63 only + determinism
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import FEATURE_VERSION
from backend.forecasting.features.sse import (
    SSE_FEATURE_COLUMNS,
    SSE_FEATURE_VERSION,
    build_sse_features,
    limit_proximity_triggered,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.quantile_bands import return_quantiles
from backend.forecasting.models.sse_drift import (
    MODEL_VERSION as SSE_DRIFT_VERSION,
    SSE_DEFAULT_Z,
    SseDriftBaseline,
    winsorize_returns,
)
from backend.forecasting.registry import ENSEMBLE_VERSION, list_models
from backend.forecasting.service import (
    SSE_BLEND_VERSION,
    ForecastService,
    _confidence,
    _is_sse,
    _penalize_confidence,
)
from backend.tests.fixtures import FIXED_AS_OF, make_ohlcv


# --- synthetic SSE bars ----------------------------------------------------

def _force_last_bar(frame: pd.DataFrame, close_mult: float, vol_mult: float) -> pd.DataFrame:
    out = frame.copy()
    prev = float(out["close"].iloc[-2])
    close = round(prev * float(close_mult), 2)
    open_ = round(prev * (1.0 + (float(close_mult) - 1.0) / 2.0), 2)
    high = round(max(open_, close) * 1.001, 2)
    low = round(min(open_, close) * 0.999, 2)
    vol_avg = float(out["volume"].iloc[-6:-1].mean())
    out.iloc[-1, out.columns.get_loc("open")] = open_
    out.iloc[-1, out.columns.get_loc("high")] = high
    out.iloc[-1, out.columns.get_loc("low")] = low
    out.iloc[-1, out.columns.get_loc("close")] = close
    out.iloc[-1, out.columns.get_loc("volume")] = vol_avg * float(vol_mult)
    return out


def make_sse_limit_up(n: int = 252, seed: int = 7) -> pd.DataFrame:
    """Deterministic bars whose final close sits ~9.9% above prev_close."""
    return _force_last_bar(make_ohlcv(n=n, seed=seed), close_mult=1.099, vol_mult=2.5)


def make_sse_normal(n: int = 252, seed: int = 7) -> pd.DataFrame:
    """Same seed, final close ~0.1% above prev_close (no proximity)."""
    return _force_last_bar(make_ohlcv(n=n, seed=seed), close_mult=1.001, vol_mult=1.0)


class _FakeMarket:
    """Minimal MarketDataService double returning a fixed frame."""

    def __init__(
        self,
        frame: pd.DataFrame,
        instrument_id: str = "XSHG-600519.SS",
        quality_grade: str = "B",
    ) -> None:
        self._frame = frame
        self._instrument_id = instrument_id
        self._grade = quality_grade

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
                "missing_fields": [],
            }
            for ts, r in frame.iterrows()
        ]
        return {
            "symbol": symbol.strip().upper(),
            "instrument_id": self._instrument_id,
            "timeframe": timeframe,
            "bars": rows,
            "provenance": {
                "source": "yfinance",
                "as_of": FIXED_AS_OF,
                "delay_minutes": 15,
                "quality_grade": self._grade,
                "fallback_used": False,
                "missing_fields": [],
            },
        }


# --- SSE features ----------------------------------------------------------

def test_sse_feature_columns_and_limit_up_proximity_fires():
    frame = make_sse_limit_up()
    feats = build_sse_features(frame)
    assert list(feats.columns) == list(SSE_FEATURE_COLUMNS)
    assert np.isfinite(feats.to_numpy()).all()
    last = feats.iloc[-1]
    assert last["limit_proximity"] == 1.0
    assert last["limit_up_dist"] <= 0.02
    assert last["limit_down_dist"] > 0.02
    assert last["turnover_5d"] > 1.0  # volume spike vs 5d average
    assert limit_proximity_triggered(feats) is True
    normal = build_sse_features(make_sse_normal())
    assert normal.iloc[-1]["limit_proximity"] == 0.0
    assert limit_proximity_triggered(normal) is False


def test_sse_features_scale_invariant_cny():
    frame = make_sse_limit_up()
    scaled = frame.copy()
    for col in ("open", "high", "low", "close"):
        scaled[col] = scaled[col] * 10.0  # CNY x10 repricing; volume untouched
    base = build_sse_features(frame)
    resc = build_sse_features(scaled)
    assert base.shape == resc.shape
    # Ratios only: x10 repricing leaves features unchanged up to float noise.
    assert np.allclose(base.to_numpy(), resc.to_numpy(), rtol=1e-9, atol=1e-12)


def test_sse_features_past_invariance_no_leakage():
    frame = make_sse_limit_up()
    short = build_sse_features(frame.iloc[:100])
    long_ = build_sse_features(frame.iloc[:150])
    pd.testing.assert_frame_equal(short, long_.loc[short.index])


# --- SSE drift -------------------------------------------------------------

def test_sse_drift_winsorizes_and_wider_bands():
    raw = pd.Series([0.001] * 100 + [0.50] + [-0.40] + [0.001] * 50)
    clipped = winsorize_returns(raw)
    assert float(clipped.max()) <= math.log(1.10) + 1e-12
    assert float(clipped.min()) >= math.log(0.90) - 1e-12
    lret = pd.Series(np.log(make_sse_limit_up()["close"] / make_sse_limit_up()["close"].shift(1))).dropna()
    first = SseDriftBaseline().fit(lret).direction_probability(21, as_of=FIXED_AS_OF)
    second = SseDriftBaseline().fit(lret).direction_probability(21, as_of=FIXED_AS_OF)
    assert first.value == pytest.approx(second.value)
    assert 0.0 <= first.value <= 1.0
    assert first.model_version == "sse-drift-v1"
    assert first.feature_version == SSE_FEATURE_VERSION == "sse-features-v1"
    # Wider bands than the US drift baseline on identical inputs.
    sse_band = SseDriftBaseline().fit(lret).expected_return_range(21).value
    us_band = HistoricalDriftBaseline().fit(lret).expected_return_range(21, z=1.0).value
    assert sse_band["z"] == pytest.approx(SSE_DEFAULT_Z)
    assert sse_band["z"] > 1.0
    assert (sse_band["high"] - sse_band["low"]) > (us_band["high"] - us_band["low"])
    assert sse_band["low"] < sse_band["mid"] < sse_band["high"]


# --- routing / versions / blend --------------------------------------------

def test_sse_routing_versions_and_direction_blend():
    from backend.forecasting.service import _calibrate_prob, _weighted_mean

    svc = ForecastService(market_service=_FakeMarket(make_sse_limit_up()))
    res = svc.forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    assert res["model_version"] == SSE_BLEND_VERSION == "ensemble-v2+sse-drift-v1"
    assert res["feature_version"] == SSE_FEATURE_VERSION == "sse-features-v1"
    assert "sse" in res["data_version"]
    assert res["record"]["model_version"] == SSE_BLEND_VERSION
    assert res["record"]["feature_version"] == SSE_FEATURE_VERSION
    assert "sse" in res["record"]["data_version"]
    assert set(res["components"]) >= {"historical-drift", "momentum", "sse-drift"}
    assert SSE_DRIFT_VERSION in res["model_members"]
    # ensemble-v2: weighted US mean (raw) blended 50/50 with sse-drift on RAW,
    # then shrinkage-calibrated.
    us_window = {k: v for k, v in res["components"].items() if k != "sse-drift"}
    us_raw = _weighted_mean(us_window)[0]
    assert res["direction_probability"] == pytest.approx(
        _calibrate_prob((us_raw + res["components"]["sse-drift"]) / 2.0)
    )
    assert res["direction_probability_raw"] == pytest.approx(
        (us_raw + res["components"]["sse-drift"]) / 2.0
    )
    rng = res["expected_return_range"]
    assert rng["low"] <= rng["mid"] <= rng["high"]
    # Union envelope covers the US-only empirical band.
    closes = make_sse_limit_up()["close"]
    us_band = return_quantiles(closes, horizons=[21], as_of=FIXED_AS_OF, data_version="t")[21].value
    assert rng["low"] <= us_band["low"] + 1e-12
    assert rng["high"] >= us_band["high"] - 1e-12


def test_sse_routing_via_mic_without_suffix_and_us_preserved():
    assert _is_sse("600519.SS", {"instrument_id": "XSHG-600519.SS"}) is True
    assert _is_sse("600519", {"instrument_id": "XSHG-600519.SS"}) is True
    assert _is_sse("AAPL", {"instrument_id": "XNAS-AAPL"}) is False
    # MIC-only routing (no .SS suffix on the user symbol).
    svc = ForecastService(
        market_service=_FakeMarket(make_sse_limit_up(), instrument_id="XSHG-600519.SS")
    )
    assert svc.forecast("600519", 21, as_of=FIXED_AS_OF)["model_version"] == SSE_BLEND_VERSION
    # US behavior exactly preserved.
    us = ForecastService(
        market_service=_FakeMarket(make_ohlcv(), instrument_id="XNAS-AAPL")
    ).forecast("AAPL", 21, as_of=FIXED_AS_OF)
    assert us["model_version"] == ENSEMBLE_VERSION == "ensemble-v2"
    assert us["feature_version"] == "features-v2"
    assert "sse" not in us["data_version"]
    assert "sse-drift" not in us["components"]


def test_sse_confidence_penalized_one_notch_on_limit_proximity():
    # Unit mapping first.
    assert _penalize_confidence("high") == "moderate"
    assert _penalize_confidence("moderate") == "low"
    assert _penalize_confidence("low") == "low"
    # Integration: limit-up day applies the penalty; normal day does not.
    svc_lim = ForecastService(market_service=_FakeMarket(make_sse_limit_up()))
    res = svc_lim.forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    comp = res["components"]
    spread = max(comp.values()) - min(comp.values())
    base = _confidence(spread, len(comp), str(res["provenance"].get("quality_grade", "B")),
                       direction_prob=res["direction_probability"])
    assert res["confidence"] == _penalize_confidence(base)
    assert res["confidence"] in ("low", "moderate") or base == "low"
    svc_ok = ForecastService(market_service=_FakeMarket(make_sse_normal()))
    res_ok = svc_ok.forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    comp_ok = res_ok["components"]
    spread_ok = max(comp_ok.values()) - min(comp_ok.values())
    base_ok = _confidence(spread_ok, len(comp_ok), str(res_ok["provenance"].get("quality_grade", "B")),
                          direction_prob=res_ok["direction_probability"])
    assert res_ok["confidence"] == base_ok  # no proximity -> no penalty


def test_sse_horizons_only_and_deterministic():
    svc = ForecastService(market_service=_FakeMarket(make_sse_limit_up()))
    all_h = svc.forecast_all("600519.SS", as_of=FIXED_AS_OF)
    assert set(all_h) == set(FORECAST_HORIZONS) == {5, 21, 63}
    for horizon, res in all_h.items():
        assert res["horizon_days"] == horizon
        assert 0.0 <= res["direction_probability"] <= 1.0
        assert res["model_version"] == SSE_BLEND_VERSION
        assert res["feature_version"] == SSE_FEATURE_VERSION
    first = svc.forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    second = svc.forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    assert first["direction_probability"] == pytest.approx(second["direction_probability"])
    for _k in ("low", "mid", "high", "lower_q", "upper_q"):
        assert first["expected_return_range"][_k] == pytest.approx(
            second["expected_return_range"][_k]
        )
    assert first["model_version"] == second["model_version"]
    with pytest.raises(ValueError):
        svc.forecast("600519.SS", 7, as_of=FIXED_AS_OF)


def test_registry_exposes_both_models():
    models = {(m["name"], m["version"]) for m in list_models()}
    assert ("ensemble", "ensemble-v2") in models
    assert ("ensemble", "ensemble-v1") in models  # legacy retained
    assert ("sse-drift", "sse-drift-v1") in models
    by_key = {(m["name"], m["version"]): m for m in list_models()}
    assert by_key[("sse-drift", "sse-drift-v1")]["feature_version"] == "sse-features-v1"
