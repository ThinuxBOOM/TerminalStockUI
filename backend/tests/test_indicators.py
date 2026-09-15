"""Overlay indicator engine tests (Backend Agent 5 repair).

Deterministic, no network. Covers: hand-computed SMA/EMA/RSI fixtures,
422 validation, unavailable on insufficient history, determinism,
warmup-null (never 0), nested BB/MACD shapes, and perf (median-of-3
<250ms for 500 bars; idle cost ~18ms, bound has 14x headroom for suite load).
"""

from __future__ import annotations

import time

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analytics.technical.overlays import (
    SUPPORTED_INDICATORS,
    compute_indicators,
    parse_indicators,
)
from backend.api.analytics_api import router as analytics_router
from backend.api.market_data import router as market_router
from backend.tests.fixtures import make_ohlcv


def _frame(n: int = 120, seed: int = 42) -> pd.DataFrame:
    return make_ohlcv(n=n, seed=seed)


def _analytics_client() -> TestClient:
    app = FastAPI()
    app.include_router(analytics_router)
    return TestClient(app)


def _market_client() -> TestClient:
    app = FastAPI()
    app.include_router(market_router)
    return TestClient(app)


# --- contract ---------------------------------------------------------------

def test_supported_indicators_superset():
    for name in ("SMA20", "SMA50", "SMA200", "EMA12", "EMA26", "RSI14",
                 "MACD", "BB20", "VWAP", "ATR14", "VOLUME_SMA20"):
        assert name in SUPPORTED_INDICATORS


def test_parse_aliases_and_dedupe():
    assert parse_indicators("sma20,SMA_20, sma-20") == ["SMA20"]
    assert parse_indicators("rsi") == ["RSI14"]
    assert parse_indicators("RSI14") == ["RSI14"]
    assert parse_indicators("bb") == ["BB20"]
    assert parse_indicators("Bollinger") == ["BB20"]
    assert parse_indicators("atr") == ["ATR14"]
    assert parse_indicators("vol") == ["VOLUME_SMA20"]
    assert parse_indicators("VOLUME_SMA20") == ["VOLUME_SMA20"]
    assert parse_indicators(None) == []
    assert parse_indicators("") == []
    assert parse_indicators(["EMA12", "ema12", "SMA20"]) == ["EMA12", "SMA20"]


def test_parse_unknown_raises_exact_detail():
    with pytest.raises(ValueError) as exc:
        parse_indicators("SMA20,FOO,BAR")
    msg = str(exc.value)
    assert msg.startswith("unknown indicator(s):")
    assert "FOO" in msg and "BAR" in msg
    assert f"expected one of {SUPPORTED_INDICATORS}" in msg


# --- hand-computed fixtures --------------------------------------------------

def test_sma20_hand_computed():
    close = pd.Series([float(i) for i in range(1, 31)],
                      index=pd.bdate_range("2020-01-01", periods=30))
    frame = pd.DataFrame({"open": close, "high": close + 1, "low": close - 1,
                          "close": close, "volume": 1000.0}, index=close.index)
    out = compute_indicators(frame, ["SMA20"])
    pts = out["series"]["SMA20"]
    assert isinstance(pts, list) and len(pts) == 30
    # Warmup: first 19 are null, never 0.
    assert all(p["value"] is None for p in pts[:19])
    assert all(p["value"] != 0 for p in pts[:19])
    # Last 20 of 1..30 are 11..30 -> mean 20.5
    assert pts[-1]["value"] == pytest.approx(20.5)
    # Time format YYYY-MM-DD
    assert pts[-1]["time"] == str(close.index[-1].date().isoformat())


def test_ema12_matches_pandas_and_warmup_null():
    frame = _frame(60)
    out = compute_indicators(frame, ["EMA12"])
    pts = out["series"]["EMA12"]
    expected = frame["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    assert pts[10]["value"] is None  # warmup null, never 0
    last = [p for p in pts if p["value"] is not None][-1]
    assert last["value"] == pytest.approx(float(expected.dropna().iloc[-1]))


def test_rsi14_rising_is_100_and_flat_is_50():
    rising = pd.Series([float(i) for i in range(1, 60)],
                       index=pd.bdate_range("2020-01-01", periods=59))
    frame = pd.DataFrame({"open": rising, "high": rising + 0.5, "low": rising - 0.5,
                          "close": rising, "volume": 1000.0}, index=rising.index)
    pts = compute_indicators(frame, ["RSI14"])["series"]["RSI14"]
    valid = [p["value"] for p in pts if p["value"] is not None]
    assert valid and valid[-1] == pytest.approx(100.0)
    assert all(0 <= v <= 100 for v in valid)
    flat = pd.Series([100.0] * 40, index=pd.bdate_range("2020-01-01", periods=40))
    f2 = pd.DataFrame({"open": flat, "high": flat, "low": flat,
                       "close": flat, "volume": 1000.0}, index=flat.index)
    pts2 = compute_indicators(f2, ["RSI14"])["series"]["RSI14"]
    valid2 = [p["value"] for p in pts2 if p["value"] is not None]
    assert valid2 and valid2[-1] == pytest.approx(50.0)


def test_bb20_ordering_and_macd_identity():
    frame = _frame(120)
    out = compute_indicators(frame, ["BB20", "MACD"])
    bb = out["series"]["BB20"]
    assert set(bb.keys()) == {"upper", "middle", "lower"}
    for u, m, lo in zip(bb["upper"], bb["middle"], bb["lower"]):
        if None in (u["value"], m["value"], lo["value"]):
            continue
        assert u["value"] >= m["value"] >= lo["value"]
    macd = out["series"]["MACD"]
    assert set(macd.keys()) == {"macd", "signal", "histogram"}
    for a, s, h in zip(macd["macd"], macd["signal"], macd["histogram"]):
        if None in (a["value"], s["value"], h["value"]):
            continue
        assert h["value"] == pytest.approx(a["value"] - s["value"])


def test_vwap_atr_volume_sma_available():
    frame = _frame(120)
    out = compute_indicators(frame, ["VWAP", "ATR14", "VOLUME_SMA20"])
    vwap = [p["value"] for p in out["series"]["VWAP"] if p["value"] is not None]
    assert vwap and all(v > 0 for v in vwap)
    atr = [p["value"] for p in out["series"]["ATR14"] if p["value"] is not None]
    assert atr and all(v >= 0 for v in atr)
    vsma = [p["value"] for p in out["series"]["VOLUME_SMA20"] if p["value"] is not None]
    assert vsma and all(v > 0 for v in vsma)


# --- unavailable / determinism / perf ----------------------------------------

def test_insufficient_history_is_unavailable_with_reason():
    frame = _frame(5)
    out = compute_indicators(frame, ["SMA20", "RSI14", "MACD"])
    for name in ("SMA20", "RSI14", "MACD"):
        entry = out["series"][name]
        assert isinstance(entry, dict)
        assert entry.get("status") == "unavailable"
        assert entry.get("reason")
    assert out["requested"] == ["SMA20", "RSI14", "MACD"]
    assert out["bars"] == 5


def test_determinism():
    frame = _frame(120)
    wanted = ["SMA20", "EMA12", "RSI14", "MACD", "BB20", "VWAP", "ATR14"]
    first = compute_indicators(frame, wanted)
    second = compute_indicators(frame, wanted)
    assert first == second


def test_max_points_truncates_to_last_n():
    frame = _frame(120)
    out = compute_indicators(frame, ["SMA20"], max_points=10)
    assert out["max_points"] == 10
    assert len(out["series"]["SMA20"]) == 10


def test_perf_500_bars_under_250ms():
    frame = _frame(500)
    wanted = list(SUPPORTED_INDICATORS)
    # Warmup (import/pandas init) outside the timed section.
    compute_indicators(frame, wanted)
    # Median of 3: a single wall-clock sample flakes under full-suite load
    # (GC pauses, CPU contention) even though the idle cost is ~18ms. The
    # 250ms bound (~14x idle) still catches real algorithmic regressions —
    # an accidental O(n^2) would land in the seconds, not milliseconds.
    samples = []
    for _ in range(3):
        start = time.perf_counter()
        compute_indicators(frame, wanted)
        samples.append((time.perf_counter() - start) * 1000.0)
    elapsed_ms = sorted(samples)[1]
    assert elapsed_ms < 250.0, f"overlay engine too slow: {elapsed_ms:.1f}ms"


# --- API ---------------------------------------------------------------------

def test_analytics_indicators_wrapper_and_provenance():
    client = _analytics_client()
    resp = client.get("/api/analytics/AAPL?indicators=SMA20,RSI14,MACD")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "indicators" in body
    ind = body["indicators"]
    for key in ("requested", "bars", "max_points", "series"):
        assert key in ind, f"indicators missing {key}"
    assert ind["requested"] == ["SMA20", "RSI14", "MACD"]
    assert ind["bars"] >= 2
    assert "provenance" in ind and "provenance" in body
    assert isinstance(ind["series"]["SMA20"], list)
    assert set(ind["series"]["MACD"].keys()) == {"macd", "signal", "histogram"}
    # Flat duplicates for the frontend passthrough.
    assert isinstance(ind["SMA20"], list)


def test_analytics_backward_compat_no_indicators_key():
    client = _analytics_client()
    body = client.get("/api/analytics/AAPL").json()
    assert body.get("indicators", {}) == {} or "indicators" not in body
    assert "technical" in body and "provenance" in body


def test_analytics_unknown_indicator_422_exact():
    client = _analytics_client()
    resp = client.get("/api/analytics/AAPL?indicators=SMA20,FOO")
    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail", "")
    assert detail.startswith("unknown indicator(s):")
    assert "FOO" in detail
    assert f"expected one of {SUPPORTED_INDICATORS}" in detail


def test_market_data_indicators_endpoint():
    client = _market_client()
    resp = client.get("/api/market_data/indicators?symbol=AAPL&indicators=SMA20,BB20&limit=60")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert "provenance" in body
    nested = body.get("indicators", {})
    assert nested.get("requested") == ["SMA20", "BB20"]
    assert "series" in nested
    assert isinstance(nested["series"]["SMA20"], list)
    assert set(nested["series"]["BB20"].keys()) == {"upper", "middle", "lower"}


def test_market_data_indicators_422_and_unavailable():
    client = _market_client()
    bad = client.get("/api/market_data/indicators?symbol=AAPL&indicators=NOPE")
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"].startswith("unknown indicator(s):")
    thin = client.get("/api/market_data/indicators?symbol=AAPL&indicators=SMA200&limit=5")
    assert thin.status_code == 200, thin.text
    entry = thin.json()["indicators"]["series"]["SMA200"]
    assert entry.get("status") == "unavailable"
    assert entry.get("reason")
