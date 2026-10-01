"""Forecast engine v4: features, models, training, serving, API, jobs."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.deps import get_market_service
from backend.forecasting.v4 import evaluate as E
from backend.forecasting.v4 import features as F
from backend.forecasting.v4 import store
from backend.forecasting.v4.model import CS_FEATURES, Bundle, CrossSection, LogitModel, cs_transform_panel
from backend.forecasting.v4.panel import add_labels, build_panel
from backend.forecasting.v4.serve import forecast_frame
from backend.forecasting.v4.volatility import fit as fit_vol
from backend.tests.auth_helpers import inject_admin_auth


def synth(symbol: str, n: int = 1400, start: str = "2016-01-04", vol: float | None = None) -> pd.DataFrame:
    seed = abs(hash(symbol)) % (2**32)
    rng = np.random.default_rng(seed)
    v = vol or rng.uniform(0.01, 0.025)
    # Volatility clustering so the vol model has something to find.
    regime = np.exp(np.convolve(rng.normal(0, 0.3, n), np.ones(20) / 20, mode="same"))
    r = rng.normal(0.0003, v, n) * regime
    close = 100 * np.exp(np.cumsum(r))
    idx = pd.bdate_range(start, periods=n)
    hl = np.abs(rng.normal(0, v, n)) * close
    return pd.DataFrame({"open": close, "high": close + hl, "low": close - hl, "close": close,
                         "volume": rng.uniform(1e6, 2e6, n)}, index=idx)


@pytest.fixture(scope="module")
def universe():
    frames = {f"S{i:02d}": synth(f"S{i:02d}") for i in range(40)}
    return frames, synth("SPY", vol=0.01), {s: ("Tech" if i % 2 else "Energy") for i, s in enumerate(frames)}


@pytest.fixture(scope="module")
def bundle(universe):
    from backend.forecasting.v4.train import train_bundle

    frames, market, sectors = universe
    return train_bundle(frames, market, sectors, horizons=(1, 7, 14, 21),
                        as_of=pd.Timestamp("2030-01-01"), n_boot=20)


def test_features_use_only_the_past():
    f = synth("AAA")
    full = F.build(f, None)
    cut = F.build(f.iloc[:900], None)
    pd.testing.assert_frame_equal(full.iloc[:900], cut, check_exact=False)


def test_feature_values_are_scale_free():
    f = synth("BBB")
    scaled = f.copy()
    scaled[["open", "high", "low", "close"]] *= 37.5
    a, b = F.build(f, None).dropna(), F.build(scaled, None).dropna()
    pd.testing.assert_frame_equal(a.drop(columns=["abn_volume"]), b.drop(columns=["abn_volume"]), atol=1e-6)


def test_forward_drawdown_matches_brute_force():
    close = pd.Series([100, 95, 102, 80, 90, 120], dtype=float)
    dd = F.forward_max_drawdown(close, 2)
    assert dd.iloc[0] == pytest.approx(np.log(95 / 100))
    assert dd.iloc[2] == pytest.approx(np.log(80 / 102))
    assert dd.iloc[3] == 0.0  # no lower close ahead
    assert dd.iloc[4:].isna().all()


def test_relative_labels_split_each_date_in_half(universe):
    frames, market, _ = universe
    panel = add_labels(build_panel(frames, market, (21,)), (21,))
    share = panel["out_21"].dropna().groupby(level="date").mean()
    assert share.mean() == pytest.approx(0.5, abs=0.02)


def test_vol_model_covers_about_80_percent(universe):
    frames, market, _ = universe
    panel = add_labels(build_panel(frames, market, (7,)), (7,))
    oof = E.walk_forward_vol(panel, 7, first_test_year=2019)
    m = E.range_metrics(oof["lo"].to_numpy(), oof["hi"].to_numpy(), oof["y"].to_numpy())
    assert 0.74 < m["coverage_80"] < 0.86


def test_logit_json_round_trip_and_contributions():
    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.normal(size=(500, 3)), columns=["a", "b", "c"])
    y = pd.Series((X["a"] + rng.normal(0, 1, 500) > 0).astype(int))
    m = LogitModel.fit(X, y, C=1.0)
    m2 = LogitModel(**json.loads(json.dumps(m.__dict__)))
    np.testing.assert_allclose(m.raw(X), m2.raw(X))
    contrib = m.contributions(X.iloc[[0]])
    assert contrib.columns.tolist() == ["a", "b", "c"]
    assert m.coef[0] > 0  # learned the true signal
    m2.blend = 0.0
    assert np.allclose(m2.predict(X), m2.base_rate)


def test_cross_section_ranks_are_monotone():
    feats = pd.DataFrame({c: np.linspace(-2, 2, 101) for c in F.FEATURES}, index=[f"X{i}" for i in range(101)])
    cs = CrossSection.from_features(feats, {}, "2026-01-01")
    lo = cs.transform(feats.iloc[5], None)
    hi = cs.transform(feats.iloc[95], None)
    assert lo["cs_mom_21"] < 0 < hi["cs_mom_21"]
    assert cs.transform(pd.Series({c: np.nan for c in F.FEATURES}), None)["cs_mom_21"] == 0.0


def test_training_reports_every_horizon_and_round_trips(bundle):
    rep = bundle.report["horizons"]
    assert set(rep) == {"1", "7", "14", "21"}
    for h, r in rep.items():
        assert 0.6 < r["range"]["coverage_80"] < 0.95
        assert 0.0 <= r["up"]["blend"] <= 1.0
        assert len(r["out"]["deciles"]) == 10
        assert "per_year_ic" in r["out"]
    again = Bundle.from_json(bundle.to_json())
    assert again.version == bundle.version
    assert again.cross_section is not None and set(again.cross_section.score_grids) == {"1", "7", "14", "21"}


def test_serving_gives_ordered_quantiles_and_ranks(bundle, universe):
    frames, market, _ = universe
    res = forecast_frame(frames["S03"], market, bundle, bundle.cross_section, sector="Tech", mic="XNAS")
    for h, r in res.items():
        q = r["quantiles"]
        assert q["0.05"] < q["0.10"] < q["0.50"] < q["0.90"] < q["0.95"]
        assert 0 <= r["drawdown_prob"] <= 1
        assert 0 <= r["out_rank"] <= 1
        assert r["relative_available"] is True
    wide = forecast_frame(frames["S03"], market, bundle, bundle.cross_section, sector=None, mic="XSHG")[21]
    assert wide["p_out"] is None and wide["relative_available"] is False


def test_serving_rejects_short_history(bundle):
    with pytest.raises(ValueError):
        forecast_frame(synth("SHORT", n=200), None, bundle, None, sector=None, mic="XNAS")


def test_default_bundle_is_installed():
    b = store.load_default()
    assert b is not None and b.horizons == [1, 7, 14, 21]
    assert set(b.report["horizons"]["21"]) >= {"range", "drop_risk", "up", "out"}
    assert all(f in CS_FEATURES for f in b.out["21"].features)


# --- API ---------------------------------------------------------------------

class FakeMarket:
    def __init__(self):
        self.frames = {}

    def get_bars(self, symbol, timeframe="1d", limit=30):
        f = self.frames.setdefault(symbol, synth(symbol, n=600, start="2024-01-01"))
        if symbol == "TINY":
            f = f.iloc[-100:]
        f = f.iloc[-limit:]
        return {
            "symbol": symbol,
            "bars": [{"date": str(d.date()), "ts": f"{d.date()}T04:00:00+00:00", **{k: float(r[k]) for k in
                      ("open", "high", "low", "close", "volume")}} for d, r in f.iterrows()],
            "provenance": {"source": "fake", "as_of": datetime.now(timezone.utc).isoformat(), "delay_minutes": 0,
                           "quality_grade": "B", "fallback_used": False, "missing_fields": []},
        }

    def get_bars_many(self, symbols, timeframe="1d", limit=30):
        return {s: self.get_bars(s, timeframe, limit) for s in symbols}


@pytest.fixture
def client():
    from backend.api.forecast import router
    from backend.forecasting.service import ForecastService, get_forecast_service

    store.reset_cache()
    fake = FakeMarket()
    app = FastAPI()
    app.include_router(router)
    inject_admin_auth(app)
    svc = ForecastService(market_service=fake)
    app.dependency_overrides[get_forecast_service] = lambda: svc
    app.dependency_overrides[get_market_service] = lambda: fake
    return TestClient(app)


def test_forecast_endpoint_contract(client):
    r = client.get("/api/forecast/AAPL", params={"horizon": 7})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["horizon_days"] == 7 and b["validation_status"] == "measured"
    assert b["expected_return_range"]["low"] < 0 < b["expected_return_range"]["high"]
    assert b["target_price"]["low"] < b["target_price"]["last_close"] < b["target_price"]["high"]
    assert 0 < b["direction_probability"] < 1 and 0 < b["base_rate"] < 1
    assert b["measured"]["range_coverage_80"] > 0.7
    assert "80% range" in b["summary"] and b["limitations"]
    assert "record" not in b and "features" not in b
    for key in ("source", "as_of", "fallback_used", "missing_fields"):
        assert key in b["provenance"]


def test_forecast_all_and_errors(client):
    allh = client.get("/api/forecast/MSFT/all").json()
    assert set(allh["horizons"]) == {"1", "7", "14", "21"}
    widths = [allh["horizons"][h]["expected_return_range"]["high"] - allh["horizons"][h]["expected_return_range"]["low"]
              for h in ("1", "7", "14", "21")]
    assert widths == sorted(widths)  # ranges widen with the horizon
    assert client.get("/api/forecast/AAPL", params={"horizon": 5}).status_code == 422
    short = client.get("/api/forecast/TINY")
    assert short.status_code == 422 and "daily bars" in short.text


def test_model_card(client):
    card = client.get("/api/forecast/model").json()
    assert card["universe_size"] > 400
    assert "per_symbol" not in card["report"]
    sym = client.get("/api/forecast/model/AAPL").json()
    assert sym["in_universe"] is True and "21" in sym["horizons"]


# --- jobs + screener -------------------------------------------------------------

class FakeRegistry:
    def __init__(self, symbols):
        from types import SimpleNamespace as NS

        self.items = [NS(provider_symbol=s, exchange_symbol=s, exchange_mic="XNAS", company_name=f"{s} Inc",
                         currency="USD", sector="Technology") for s in symbols]

    def all(self):
        return self.items


def test_predict_job_then_screener_and_signals(monkeypatch):
    from backend.api.screener import router as screener_router
    from backend.api.signals import router as signals_router
    from backend.forecasting.v4.jobs import run_predict

    store.reset_cache()
    b = store.load_default()
    syms = [s for s in b.universe[:60]]
    result = run_predict(market=FakeMarket(), registry=FakeRegistry(syms))
    assert result["ok"] and result["rows"] == 4 * len(syms)
    assert result["cross_section"] is not None

    app = FastAPI()
    app.include_router(screener_router)
    app.include_router(signals_router)
    inject_admin_auth(app)
    c = TestClient(app)
    body = c.get("/api/screener", params={"horizon": 21, "limit": 10}).json()
    ranks = [r["out_rank"] for r in body["rows"]]
    assert ranks == sorted(ranks, reverse=True) and body["total"] == len(syms)
    assert body["measured"]["range_coverage_80"] > 0.7
    low_risk = c.get("/api/screener", params={"sort": "drawdown", "order": "asc", "limit": 5}).json()["rows"]
    assert [r["drawdown_prob"] for r in low_risk] == sorted(r["drawdown_prob"] for r in low_risk)
    assert c.get("/api/screener", params={"sort": "nope"}).status_code == 422
    some = c.get("/api/screener", params={"symbols": f"{syms[0]},{syms[1]}, NOPE"}).json()
    assert {r["symbol"] for r in some["rows"]} == {syms[0], syms[1]}
    sig = c.get("/api/signals/top", params={"horizon": 21, "n": 5}).json()
    assert {r["symbol"] for r in sig["top"]}.isdisjoint({r["symbol"] for r in sig["bottom"]})


def test_screener_starts_scoring_when_empty(monkeypatch):
    from backend.api import screener as S
    from backend.forecasting.v4 import jobs

    started = []
    monkeypatch.setattr(jobs, "spawn", lambda job: started.append(job) or {"started": True})
    monkeypatch.setattr(S, "read_scores", lambda *a, **k: [])
    app = FastAPI()
    app.include_router(S.router)
    inject_admin_auth(app)
    body = TestClient(app).get("/api/screener").json()
    assert body["pending"] is True and body["rows"] == [] and started == ["predict"]


# --- data hygiene ---------------------------------------------------------------

def test_index_symbols_map_to_their_venue():
    from backend.instruments.calendars import provider_symbol_for, split_provider_symbol

    assert split_provider_symbol("^FCHI") == ("^FCHI", "XPAR")
    assert split_provider_symbol("^AEX") == ("^AEX", "XAMS")
    assert provider_symbol_for("^FCHI", "XPAR") == "^FCHI"
    assert split_provider_symbol("MC.PA") == ("MC", "XPAR")


def test_daily_bars_are_stored_at_exchange_midnight():
    from backend.market_data.ingest import canonical_session_ts

    utc = timezone.utc
    # Finnhub-style 00:00 UTC and yfinance local midnight land on one key.
    a = canonical_session_ts(datetime(2026, 1, 26, 0, 0, tzinfo=utc), "Asia/Shanghai")
    b = canonical_session_ts(datetime(2026, 1, 25, 16, 0, tzinfo=utc), "Asia/Shanghai")
    assert a == b == datetime(2026, 1, 25, 16, 0, tzinfo=utc)
    us = canonical_session_ts(datetime(2026, 3, 6, 5, 0, tzinfo=utc), "America/New_York")
    assert us == datetime(2026, 3, 6, 5, 0, tzinfo=utc)


def test_public_model_summary_is_aggregate_only():
    from backend.api.public import router as public_router

    app = FastAPI()
    app.include_router(public_router)
    body = TestClient(app).get("/api/public/model").json()  # no auth header
    assert body["universe_size"] > 400 and set(body["horizons"]) == {"1", "7", "14", "21"}
    assert len(body["horizons"]["21"]["out_deciles"]) == 10
    text = json.dumps(body)
    assert "per_symbol" not in text and "AAPL" not in text
