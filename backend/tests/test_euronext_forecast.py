"""Euronext market-specific forecasting tests (Milestone 7).

Synthetic bars only, no network, no AI. Covers:
  * Euronext feature formulas (mom/vol/rsi/turnover/gap) + EUR invariance
  * Past-invariance (no leakage) + gap-proxy recomputation
  * Eux drift winsorization at +/-15% + z=1.15 band
  * Service routing (.PA/.AS/.BR suffix or XPAR/XAMS/XBRU MIC) -> blend
  * Horizons 1/7/14/21 only + determinism
  * US + SSE paths exactly preserved + registry exposes all three models
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import FEATURE_VERSION
from backend.forecasting.features.euronext import (
    EUX_FEATURE_COLUMNS,
    EUX_FEATURE_VERSION,
    build_euronext_features,
)
from backend.forecasting.features.sse import SSE_FEATURE_VERSION
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.quantile_bands import return_quantiles
from backend.forecasting.models.euronext_drift import (
    MODEL_VERSION as EUX_DRIFT_VERSION,
    EUX_DEFAULT_Z,
    WINSOR_CAP_PCT,
    EuxDriftBaseline,
    winsorize_returns,
)
from backend.forecasting.models.sse_drift import (
    MODEL_VERSION as SSE_DRIFT_VERSION,
)
from backend.forecasting.registry import ENSEMBLE_VERSION, list_models
from backend.forecasting.service import (
    EUX_BLEND_VERSION,
    SSE_BLEND_VERSION,
    ForecastService,
    _is_euronext,
    _is_sse,
)
from backend.tests.fixtures import FIXED_AS_OF, make_ohlcv


# --- synthetic EUR bars ------------------------------------------------------

def make_eux_bars(n: int = 252, seed: int = 11) -> pd.DataFrame:
    """Deterministic synthetic Euronext-style OHLCV bars."""
    return make_ohlcv(n=n, seed=seed)


class _FakeMarket:
    """Minimal MarketDataService double returning a fixed frame."""

    def __init__(
        self,
        frame: pd.DataFrame,
        instrument_id: str = "XPAR-MC.PA",
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


# --- Euronext features -------------------------------------------------------

def test_eux_feature_columns_and_formulas():
    frame = make_eux_bars()
    feats = build_euronext_features(frame)
    assert list(feats.columns) == list(EUX_FEATURE_COLUMNS)
    assert np.isfinite(feats.to_numpy()).all()
    assert len(feats) < len(frame)  # warmup rows dropped
    # Manual recomputation on the last row (past-only formulas).
    last_idx = feats.index[-1]
    pos = frame.index.get_loc(last_idx)
    close = frame["close"]
    assert feats.loc[last_idx, "mom_5"] == pytest.approx(
        close.iloc[pos] / close.iloc[pos - 5] - 1.0
    )
    assert feats.loc[last_idx, "mom_10"] == pytest.approx(
        close.iloc[pos] / close.iloc[pos - 10] - 1.0
    )
    assert feats.loc[last_idx, "mom_20"] == pytest.approx(
        close.iloc[pos] / close.iloc[pos - 20] - 1.0
    )
    expected_gap = (
        float(frame["open"].iloc[pos]) - float(close.iloc[pos - 1])
    ) / float(close.iloc[pos - 1])
    assert feats.loc[last_idx, "gap_proxy"] == pytest.approx(expected_gap)
    # Ex-current trailing mean: spike bar excluded from its own denominator.
    vol5 = frame["volume"].iloc[pos - 5:pos].mean()
    assert feats.loc[last_idx, "turnover_5d"] == pytest.approx(
        float(frame["volume"].iloc[pos]) / float(vol5)
    )
    assert feats.loc[last_idx, "vol_21"] > 0
    assert 0.0 <= feats.loc[last_idx, "rsi_14"] <= 100.0


def test_eux_features_scale_invariant_eur():
    frame = make_eux_bars()
    scaled = frame.copy()
    for col in ("open", "high", "low", "close"):
        scaled[col] = scaled[col] * 10.0  # EUR x10 repricing; volume untouched
    base = build_euronext_features(frame)
    resc = build_euronext_features(scaled)
    assert base.shape == resc.shape
    assert np.allclose(base.to_numpy(), resc.to_numpy(), rtol=1e-9, atol=1e-12)


def test_eux_features_past_invariance_no_leakage():
    frame = make_eux_bars()
    short = build_euronext_features(frame.iloc[:100])
    long_ = build_euronext_features(frame.iloc[:150])
    pd.testing.assert_frame_equal(short, long_.loc[short.index])


# --- Eux drift ---------------------------------------------------------------

def test_eux_drift_winsorizes_at_15pct_and_z_115():
    assert WINSOR_CAP_PCT == pytest.approx(0.15)
    assert EUX_DEFAULT_Z == pytest.approx(1.15)
    raw = pd.Series([0.001] * 100 + [0.50] + [-0.40] + [0.001] * 50)
    clipped = winsorize_returns(raw)
    assert float(clipped.max()) <= math.log(1.15) + 1e-12
    assert float(clipped.min()) >= math.log(0.85) - 1e-12
    # +/-15% cap is wider than the SSE +/-10% limit-implied band.
    assert math.log(1.15) > math.log(1.10)
    assert math.log(0.85) < math.log(0.90)
    lret = pd.Series(
        np.log(make_eux_bars()["close"] / make_eux_bars()["close"].shift(1))
    ).dropna()
    first = EuxDriftBaseline().fit(lret).direction_probability(21, as_of=FIXED_AS_OF)
    second = EuxDriftBaseline().fit(lret).direction_probability(21, as_of=FIXED_AS_OF)
    assert first.value == pytest.approx(second.value)
    assert 0.0 <= first.value <= 1.0
    assert first.model_version == "eux-drift-v1"
    assert first.feature_version == EUX_FEATURE_VERSION == "eux-features-v1"
    # z=1.15 band is wider than the US z=1.0 band on identical inputs.
    eux_band = EuxDriftBaseline().fit(lret).expected_return_range(21).value
    us_band = HistoricalDriftBaseline().fit(lret).expected_return_range(21, z=1.0).value
    assert eux_band["z"] == pytest.approx(EUX_DEFAULT_Z)
    assert eux_band["z"] > 1.0
    assert (eux_band["high"] - eux_band["low"]) > (us_band["high"] - us_band["low"])
    assert eux_band["low"] < eux_band["mid"] < eux_band["high"]


# --- routing / versions / blend ----------------------------------------------

def test_eux_routing_helpers():
    assert _is_euronext("MC.PA", {"instrument_id": "XPAR-MC.PA"}) is True
    assert _is_euronext("ASML.AS", {"instrument_id": "XAMS-ASML.AS"}) is True
    assert _is_euronext("UCB.BR", {"instrument_id": "XBRU-UCB.BR"}) is True
    assert _is_euronext("MC", {"instrument_id": "XPAR-MC.PA"}) is True
    assert _is_euronext("ASML", {"instrument_id": "XAMS-ASML.AS"}) is True
    assert _is_euronext("UCB", {"instrument_id": "XBRU-UCB.BR"}) is True
    assert _is_euronext("AAPL", {"instrument_id": "XNAS-AAPL"}) is False
    assert _is_euronext("600519.SS", {"instrument_id": "XSHG-600519.SS"}) is False
    # SSE helper untouched by Euronext symbols.
    assert _is_sse("MC.PA", {"instrument_id": "XPAR-MC.PA"}) is False
    assert _is_sse("600519.SS", {"instrument_id": "XSHG-600519.SS"}) is True


def test_eux_routing_versions_and_direction_blend():
    from backend.forecasting.service import _calibrate_prob, _weighted_mean

    svc = ForecastService(market_service=_FakeMarket(make_eux_bars()))
    res = svc.forecast("MC.PA", 21, as_of=FIXED_AS_OF)
    assert res["model_version"] == EUX_BLEND_VERSION == "ensemble-v3+eux-drift-v1"
    assert res["feature_version"] == EUX_FEATURE_VERSION == "eux-features-v1"
    assert "eux" in res["data_version"]
    assert "sse" not in res["data_version"]
    assert res["record"]["model_version"] == EUX_BLEND_VERSION
    assert res["record"]["feature_version"] == EUX_FEATURE_VERSION
    assert "eux" in res["record"]["data_version"]
    assert set(res["components"]) >= {"historical-drift", "momentum", "eux-drift"}
    assert "sse-drift" not in res["components"]
    assert EUX_DRIFT_VERSION in res["model_members"]
    # ensemble-v3: weighted US mean (raw) blended 50/50 with eux-drift on RAW,
    # then shrinkage-calibrated.
    us_window = {k: v for k, v in res["components"].items() if k != "eux-drift"}
    us_raw = _weighted_mean(us_window)[0]
    assert res["direction_probability"] == pytest.approx(
        _calibrate_prob((us_raw + res["components"]["eux-drift"]) / 2.0)
    )
    assert res["direction_probability_raw"] == pytest.approx(
        (us_raw + res["components"]["eux-drift"]) / 2.0
    )
    rng = res["expected_return_range"]
    assert rng["low"] <= rng["mid"] <= rng["high"]
    # Union envelope covers the US-only empirical band.
    closes = make_eux_bars()["close"]
    us_band = return_quantiles(
        closes, horizons=[21], as_of=FIXED_AS_OF, data_version="t"
    )[21].value
    assert rng["low"] <= us_band["low"] + 1e-12
    assert rng["high"] >= us_band["high"] - 1e-12


def test_eux_routing_all_venues_suffix_and_mic():
    for symbol, mic in (
        ("MC.PA", "XPAR-MC.PA"),
        ("ASML.AS", "XAMS-ASML.AS"),
        ("UCB.BR", "XBRU-UCB.BR"),
    ):
        svc = ForecastService(
            market_service=_FakeMarket(make_eux_bars(), instrument_id=mic)
        )
        res = svc.forecast(symbol, 21, as_of=FIXED_AS_OF)
        assert res["model_version"] == EUX_BLEND_VERSION
        assert res["feature_version"] == EUX_FEATURE_VERSION
    # MIC-only routing (no suffix on the user symbol).
    for symbol, mic in (
        ("MC", "XPAR-MC.PA"),
        ("ASML", "XAMS-ASML.AS"),
        ("UCB", "XBRU-UCB.BR"),
    ):
        svc = ForecastService(
            market_service=_FakeMarket(make_eux_bars(), instrument_id=mic)
        )
        assert (
            svc.forecast(symbol, 21, as_of=FIXED_AS_OF)["model_version"]
            == EUX_BLEND_VERSION
        )


def test_eux_horizons_only_and_deterministic():
    svc = ForecastService(market_service=_FakeMarket(make_eux_bars()))
    all_h = svc.forecast_all("MC.PA", as_of=FIXED_AS_OF)
    assert set(all_h) == set(FORECAST_HORIZONS) == {1, 7, 14, 21}
    for horizon, res in all_h.items():
        assert res["horizon_days"] == horizon
        assert 0.0 <= res["direction_probability"] <= 1.0
        assert res["model_version"] == EUX_BLEND_VERSION
        assert res["feature_version"] == EUX_FEATURE_VERSION
        assert "eux" in res["data_version"]
    first = svc.forecast("MC.PA", 21, as_of=FIXED_AS_OF)
    second = svc.forecast("MC.PA", 21, as_of=FIXED_AS_OF)
    assert first["direction_probability"] == pytest.approx(second["direction_probability"])
    for _k in ("low", "mid", "high", "lower_q", "upper_q"):
        assert first["expected_return_range"][_k] == pytest.approx(
            second["expected_return_range"][_k]
        )
    assert first["model_version"] == second["model_version"]
    with pytest.raises(ValueError):
        svc.forecast("MC.PA", 5, as_of=FIXED_AS_OF)


def test_us_and_sse_paths_unaffected():
    # US behavior exactly preserved.
    us = ForecastService(
        market_service=_FakeMarket(make_ohlcv(), instrument_id="XNAS-AAPL")
    ).forecast("AAPL", 21, as_of=FIXED_AS_OF)
    assert us["model_version"] == ENSEMBLE_VERSION == "ensemble-v3"
    assert us["feature_version"] == "features-v2"
    assert "sse" not in us["data_version"]
    assert "eux" not in us["data_version"]
    assert "sse-drift" not in us["components"]
    assert "eux-drift" not in us["components"]
    # SSE behavior exactly preserved.
    sse = ForecastService(
        market_service=_FakeMarket(make_ohlcv(), instrument_id="XSHG-600519.SS")
    ).forecast("600519.SS", 21, as_of=FIXED_AS_OF)
    assert sse["model_version"] == SSE_BLEND_VERSION == "ensemble-v3+sse-drift-v1"
    assert sse["feature_version"] == SSE_FEATURE_VERSION == "sse-features-v1"
    assert "sse" in sse["data_version"]
    assert "eux" not in sse["data_version"]
    assert "sse-drift" in sse["components"]
    assert "eux-drift" not in sse["components"]
    assert SSE_DRIFT_VERSION in sse["model_members"]
    assert EUX_DRIFT_VERSION not in sse["model_members"]


def test_registry_exposes_all_three_models():
    models = {(m["name"], m["version"]) for m in list_models()}
    assert ("ensemble", "ensemble-v3") in models
    assert ("ensemble", "ensemble-v1") in models  # legacy retained
    assert ("sse-drift", "sse-drift-v1") in models
    assert ("eux-drift", "eux-drift-v1") in models
    by_key = {(m["name"], m["version"]): m for m in list_models()}
    assert by_key[("eux-drift", "eux-drift-v1")]["feature_version"] == "eux-features-v1"
    assert by_key[("sse-drift", "sse-drift-v1")]["feature_version"] == "sse-features-v1"
