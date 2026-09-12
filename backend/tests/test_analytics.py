"""Unit tests for deterministic analytics (Milestone 2 + spec section 6).

Covers: envelope contract, every technical indicator, fundamentals,
valuation, quality scores, event timeline. Synthetic fixtures only.
"""

import numpy as np
import pandas as pd
import pytest

from backend.analytics.common import MetricResult
from backend.analytics.events import (
    build_timeline,
    normalize_dividends,
    normalize_earnings,
    normalize_splits,
)
from backend.analytics.fundamentals import (
    debt_to_assets,
    debt_to_equity,
    earnings_growth,
    gross_margin,
    interest_coverage,
    net_margin,
    operating_margin,
    revenue_growth,
    roe,
    roic,
)
from backend.analytics.quality import altman_z, beneish_m, dupont, piotroski_score
from backend.analytics.technical import (
    atr,
    bollinger,
    ema,
    is_volume_anomaly,
    macd,
    rsi,
    sma,
    volatility,
    volume_anomaly,
)
from backend.analytics.valuation import dcf_sensitivity, peer_compare, wacc
from backend.tests.fixtures import make_events, make_ohlcv, make_statements

OHLCV = make_ohlcv()
FIN = make_statements(strong=True)


# --- envelope contract -----------------------------------------------------

def test_envelope_tuple_shape():
    result = sma(OHLCV["close"], window=20)
    assert isinstance(result, MetricResult)
    value, formula, sources, quality = result.as_tuple()
    assert isinstance(value, pd.Series)
    assert isinstance(formula, str) and formula
    assert sources == ["close"]
    assert quality == "ok"


def test_missing_data_is_unavailable_with_reason():
    result = sma(None, window=20)
    assert result.quality_flag == "unavailable"
    assert result.value is None and result.reason


def test_short_series_is_unavailable():
    result = sma([1.0, 2.0], window=20)
    assert result.quality_flag == "unavailable"
    assert "need >=" in result.reason


def test_nan_input_is_degraded_not_lost():
    close = OHLCV["close"].copy()
    close.iloc[10] = np.nan
    result = sma(close, window=20)
    assert result.quality_flag == "degraded"
    assert result.value.notna().sum() > 0


# --- technical -------------------------------------------------------------

def test_sma_hand_computed():
    result = sma(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), window=3)
    assert result.value.iloc[-1] == pytest.approx(4.0)
    assert result.value.iloc[:2].isna().all()


def test_ema_deterministic_and_seeded():
    first = ema(OHLCV["close"], window=12).value
    second = ema(OHLCV["close"], window=12).value
    pd.testing.assert_series_equal(first, second)
    expected = OHLCV["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    pd.testing.assert_series_equal(first, expected, check_names=False)


def test_rsi_bounds_and_uptrend():
    rising = pd.Series(np.arange(1.0, 60.0))
    result = rsi(rising, window=14)
    assert result.quality_flag == "ok"
    assert float(result.value.iloc[-1]) == pytest.approx(100.0)
    assert result.value.dropna().between(0, 100).all()


def test_macd_frame_columns_and_histogram_identity():
    result = macd(OHLCV["close"])
    frame = result.value
    assert list(frame.columns) == ["macd", "signal", "histogram"]
    pd.testing.assert_series_equal(frame["histogram"],
                                   frame["macd"] - frame["signal"],
                                   check_names=False)


def test_bollinger_band_ordering():
    bands = bollinger(OHLCV["close"], window=20, num_std=2.0).value.dropna()
    assert ((bands["upper"] >= bands["middle"])
            & (bands["middle"] >= bands["lower"])).all()
    assert (bands["bandwidth"] >= 0).all()


def test_atr_nonnegative():
    result = atr(OHLCV["high"], OHLCV["low"], OHLCV["close"], window=14)
    assert (result.value.dropna() >= 0).all()


def test_volatility_nonnegative_and_annualized():
    result = volatility(OHLCV["close"], window=21, annualization=252)
    assert (result.value.dropna() >= 0).all()
    assert result.value.dropna().iloc[-1] > 0


def test_volume_anomaly_flags_spike():
    volume = OHLCV["volume"].copy()
    volume.iloc[-1] = volume.mean() * 10
    flag = is_volume_anomaly(volume, window=20, z_threshold=2.0)
    assert flag.value is True
    calm = is_volume_anomaly(OHLCV["volume"], window=20, z_threshold=100.0)
    assert calm.value is False


def test_technical_rejects_bad_params():
    with pytest.raises(ValueError):
        sma(OHLCV["close"], window=1)
    with pytest.raises(ValueError):
        macd(OHLCV["close"], fast=26, slow=12)


# --- fundamentals ----------------------------------------------------------

def test_fundamental_values():
    assert revenue_growth(FIN).value == pytest.approx(100 / 900)
    assert gross_margin(FIN).value == pytest.approx(0.6)
    assert operating_margin(FIN).value == pytest.approx(0.3)
    assert net_margin(FIN).value == pytest.approx(0.2)
    assert debt_to_equity(FIN).value == pytest.approx(400 / 1200)
    assert debt_to_assets(FIN).value == pytest.approx(400 / 2000)
    assert interest_coverage(FIN).value == pytest.approx(280 / 20)
    assert earnings_growth(FIN).value == pytest.approx(50 / 150)
    assert roe(FIN).value == pytest.approx(200 / 1100)
    assert roe(FIN).quality_flag == "ok"
    assert roic(FIN).value == pytest.approx(280 * 0.79 / 1600)


def test_fundamentals_missing_field_unavailable():
    thin = {"revenue": 100.0}
    result = gross_margin(thin)
    assert result.quality_flag == "unavailable"
    assert "gross_profit" in result.reason


def test_fundamentals_zero_denominator_unavailable():
    result = gross_margin({"gross_profit": 1.0, "revenue": 0.0})
    assert result.quality_flag == "unavailable"
    assert "zero" in result.reason


def test_roe_fallback_is_degraded():
    fin = dict(FIN)
    del fin["total_equity_prior"]
    result = roe(fin)
    assert result.quality_flag == "degraded"
    assert result.value == pytest.approx(200 / 1200)
    assert "ending equity" in result.reason


def test_roic_derives_capital_degraded():
    fin = dict(FIN)
    del fin["invested_capital"]
    result = roic(fin)
    assert result.quality_flag == "degraded"
    assert result.value == pytest.approx(280 * 0.79 / 1600)


# --- valuation -------------------------------------------------------------

def test_wacc_hand_computed():
    result = wacc({"market_value_equity": 600.0, "market_value_debt": 400.0,
                   "cost_of_equity": 0.10, "cost_of_debt": 0.05, "tax_rate": 0.2})
    assert result.value == pytest.approx(0.6 * 0.10 + 0.4 * 0.05 * 0.8)
    bad = wacc({"market_value_equity": 0.0, "market_value_debt": 0.0,
                "cost_of_equity": 0.1, "cost_of_debt": 0.05, "tax_rate": 0.2})
    assert bad.quality_flag == "unavailable"


def test_dcf_sensitivity_grid_and_gordon_guard():
    result = dcf_sensitivity(10.0, 0.05, [0.08, 0.10], [0.02, 0.09])
    grid = result.value
    assert grid.shape == (2, 2)
    assert grid.loc[0.08, 0.02] > grid.loc[0.10, 0.02]  # higher discount -> lower value
    assert np.isnan(grid.loc[0.08, 0.09])  # r <= g_terminal -> NaN
    assert np.isfinite(grid.loc[0.10, 0.09])


def test_peer_compare_median_and_rank():
    target = {"pe": 15.0, "ev_ebitda": 8.0}
    peers = [{"pe": 10.0, "ev_ebitda": 7.0}, {"pe": 12.0, "ev_ebitda": 9.0},
             {"pe": 20.0, "ev_ebitda": 8.0}]
    result = peer_compare(target, peers)
    assert result.quality_flag == "ok"
    pe = result.value["metrics"]["pe"]
    assert pe["peer_median"] == pytest.approx(12.0)
    assert pe["rank_asc"] == 3
    assert peer_compare(target, []).quality_flag == "unavailable"


# --- quality ---------------------------------------------------------------

def test_piotroski_strong_scores_high():
    result = piotroski_score(FIN)
    assert result.value["score"] >= 7
    assert result.value["grade"] == "strong"
    assert sum(result.value["breakdown"].values()) == result.value["score"]
    weak = piotroski_score(make_statements(strong=False))
    assert weak.value["score"] < result.value["score"]
    missing = piotroski_score({})
    assert missing.quality_flag == "unavailable"


def test_altman_zone_and_formula():
    result = altman_z(FIN)
    x = result.value["components"]
    expected = 1.2 * x["X1"] + 1.4 * x["X2"] + 3.3 * x["X3"] + 0.6 * x["X4"] + x["X5"]
    assert result.value["z"] == pytest.approx(expected)
    assert result.value["zone"] == "safe"
    distressed = dict(make_statements(strong=False))
    distressed.update({"market_value_equity": 200.0, "total_liabilities": 1800.0})
    distress = altman_z(distressed)
    assert distress.value["zone"] == "distress"


def test_beneish_components_and_flag():
    result = beneish_m(FIN)
    comps = result.value["components"]
    assert len(comps) == 8
    expected = (-4.84 + 0.92 * comps["DSRI"] + 0.58 * comps["GMI"]
                + 0.464 * comps["AQI"] + 0.404 * comps["SGI"]
                + 0.115 * comps["DEPI"] - 0.172 * comps["SGAI"]
                + 4.679 * comps["TATA"] - 0.327 * comps["LVGI"])
    assert result.value["m"] == pytest.approx(expected)
    assert isinstance(result.value["likely_manipulator"], bool)


def test_dupont_identity():
    result = dupont(FIN)
    v = result.value
    assert v["roe"] == pytest.approx(v["net_margin"] * v["asset_turnover"]
                                     * v["equity_multiplier"])
    assert v["roe"] == pytest.approx(200 / 1100)
    fin = dict(FIN)
    del fin["total_assets_prior"]
    assert dupont(fin).quality_flag == "degraded"


# --- events ----------------------------------------------------------------

def test_events_normalize_sort_dedupe():
    feeds = make_events()
    earnings = normalize_earnings(feeds["earnings"])
    assert earnings.quality_flag == "degraded"  # one bad-date row dropped
    dividends = normalize_dividends(feeds["dividends"])
    splits = normalize_splits(feeds["splits"])
    assert splits.value["adjustment_factor"].iloc[0] == pytest.approx(0.5)
    timeline = build_timeline(earnings, dividends, splits)
    dates = timeline.value["date"]
    assert dates.is_monotonic_increasing
    # exact-duplicate dividend row removed
    assert (timeline.value["event_type"] == "dividend").sum() == 1
    assert timeline.quality_flag == "degraded"
