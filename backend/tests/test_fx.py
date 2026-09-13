"""M7 FX tests (no network).

Covers:
- stub rates: deterministic ECB reference table, fallback_used=True,
  USD/EUR/CNY triangle consistency, inverse math, same-ccy identity
- provider: empty/unsupported -> ProviderError; outage/httpx-missing ->
  flagged stub (never raises); breaker isolation; 15-min cache
- convert math: direct / inverse / triangle / key-form variants / no-path
- gate: fresh passes; stale >24h refuses; fallback refuses without
  allow_fallback, passes with it; missing provenance refuses
- rank/compare: refuse without fresh FX, sort otherwise
- HTTP: /pairs, /rate, /convert, /rank (200 fresh, 423 gated)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import fx as fxapi
from backend.api.deps import get_market_service, reset_deps
from backend.cache import InMemoryCache
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.fx.convert import (
    CODE,
    FXProvenanceMissing,
    compare_cross_market,
    convert,
    rank_cross_market,
    require_fx_provenance,
)
from backend.market_data.fx.provider import FXProvider
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.base import CircuitBreaker, ProviderError
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

REQUIRED_KEYS = {"source", "as_of", "delay_minutes", "quality_grade", "fallback_used", "missing_fields"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _prov(*, hours_old: float = 0.0, fallback: bool = False, source: str = "frankfurter") -> dict:
    return {
        "source": source,
        "as_of": _utcnow() - timedelta(hours=hours_old),
        "delay_minutes": 15,
        "quality_grade": "C" if fallback else "B",
        "fallback_used": fallback,
        "missing_fields": [],
    }


# -- stub rates ------------------------------------------------------------


def test_stub_rates_deterministic_and_flagged():
    prov = FXProvider(stub_mode=True)
    eur_usd = prov.get_rate("EUR", "USD")
    assert eur_usd["pair"] == "EUR/USD"
    assert eur_usd["rate"] == pytest.approx(1.08)
    assert eur_usd["inverse"] == pytest.approx(1 / 1.08)
    assert eur_usd["fallback_used"] is True
    assert eur_usd["source"] == "fx"
    assert eur_usd["missing_fields"] == []
    assert isinstance(eur_usd["as_of"], datetime)
    assert eur_usd["as_of"].tzinfo is not None
    # Deterministic across calls (rate; as_of refreshes).
    assert prov.get_rate("eur", "usd")["rate"] == pytest.approx(1.08)


def test_stub_triangle_usd_eur_cny():
    prov = FXProvider(stub_mode=True)
    eur_usd = prov.get_rate("EUR", "USD")["rate"]
    usd_cny = prov.get_rate("USD", "CNY")["rate"]
    eur_cny = prov.get_rate("EUR", "CNY")["rate"]
    assert eur_usd == pytest.approx(1.08)
    assert usd_cny == pytest.approx(7.25)
    assert eur_cny == pytest.approx(7.83)
    assert eur_usd * usd_cny == pytest.approx(eur_cny)  # triangle-consistent
    assert prov.get_rate("USD", "EUR")["rate"] == pytest.approx(1 / eur_usd)


def test_same_currency_identity():
    prov = FXProvider(stub_mode=True)
    out = prov.get_rate("USD", "USD")
    assert out["rate"] == 1.0 and out["inverse"] == 1.0
    assert out["fallback_used"] is False


def test_empty_and_unsupported_currency_raise():
    prov = FXProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_rate("", "USD")
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "   ")
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "JPY")


def test_provenance_grades_stub_vs_live():
    prov = FXProvider(stub_mode=True)
    stub_prov = prov.provenance_for(prov.get_rate("EUR", "USD"))
    assert stub_prov.fallback_used is True
    assert stub_prov.quality_grade == "C"
    assert stub_prov.source == "fx"
    live = {
        "pair": "EUR/USD", "rate": 1.08, "as_of": _utcnow(),
        "source": "frankfurter", "missing_fields": [], "fallback_used": False,
    }
    live_prov = prov.provenance_for(live)
    assert live_prov.fallback_used is False
    assert live_prov.quality_grade == "B"  # fresh single-source
    assert REQUIRED_KEYS <= set(live_prov.model_dump(mode="json"))


# -- offline resilience ----------------------------------------------------


def test_network_failure_falls_back_flagged_never_raises():
    prov = FXProvider(stub_mode=False)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "network down")

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    prov._fetch_yahoo = _boom  # type: ignore[method-assign]  # full outage -> stub
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is True
    assert out["rate"] == pytest.approx(1.08)
    assert out["source"] == "fx"


def test_missing_httpx_package_falls_back_flagged(monkeypatch):
    import sys

    prov = FXProvider(stub_mode=False)
    monkeypatch.setitem(sys.modules, "httpx", None)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "yahoo down")

    prov._fetch_yahoo = _boom  # type: ignore[method-assign]  # isolate httpx path
    with pytest.raises(ProviderError):  # raw fetch names the missing dep
        prov._fetch_raw("EUR", "USD")
    out = prov.get_rate("EUR", "USD")  # public path never crashes
    assert out["fallback_used"] is True
    assert out["rate"] == pytest.approx(1.08)


def test_yahoo_secondary_serves_live_when_frankfurter_down():
    """Frankfurter outage + yahoo reachable -> live (gate passes, no stub)."""
    prov = FXProvider(stub_mode=False)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "frankfurter down")

    def _live_yahoo(base: str, quote: str) -> dict:
        return {
            "base": base, "quote": quote, "rate": 1.10,
            "as_of": _utcnow(), "source": "yfinance",
        }

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    prov._fetch_yahoo = _live_yahoo  # type: ignore[method-assign]
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is False
    assert out["source"] == "yfinance"
    assert out["rate"] == pytest.approx(1.10)


def test_yahoo_inverse_pair_inverts_rate(monkeypatch):
    """CNY->USD resolves via USDCNY=X inverted; offline (fake yfinance)."""
    import sys
    import types

    import pandas as pd

    fake_yf = types.SimpleNamespace()

    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self._symbol = symbol

        def history(self, period: str = "2d", auto_adjust: bool = True):
            if self._symbol == "USDCNY=X":
                idx = pd.date_range("2026-09-11", periods=2, freq="D")
                return pd.DataFrame({"Close": [7.24, 7.25]}, index=idx)
            return pd.DataFrame()  # direct CNYUSD=X missing -> inverse branch

    fake_yf.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    prov = FXProvider(stub_mode=False)
    out = prov._fetch_yahoo("CNY", "USD")
    assert out["source"] == "yfinance"
    assert out["rate"] == pytest.approx(1.0 / 7.25)


def test_breaker_open_returns_flagged_stub():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    prov = FXProvider(stub_mode=False, breaker=breaker)
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is True
    assert out.get("circuit_open") is True


def test_breaker_objects_are_isolated():
    fx = FXProvider(breaker=CircuitBreaker(failure_threshold=1))
    yf = YFinanceProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1))
    assert fx.breaker is not yf.breaker
    fx.breaker.record_failure()
    assert fx.breaker.state == "open"
    assert yf.breaker.state == "closed"


def test_15min_cache_avoids_refetch():
    prov = FXProvider(stub_mode=False)
    calls = {"n": 0}

    def _fake(base: str, quote: str) -> dict:
        calls["n"] += 1
        return {
            "base": base, "quote": quote, "rate": 1.08,
            "as_of": _utcnow(), "source": "frankfurter",
        }

    prov._fetch_raw = _fake  # type: ignore[method-assign]
    first = prov.get_rate("EUR", "USD")
    second = prov.get_rate("EUR", "USD")
    assert calls["n"] == 1
    assert first["rate"] == second["rate"] == pytest.approx(1.08)
    assert first["fallback_used"] is False
    prov.get_rate("USD", "CNY")  # uncached pair refetches
    assert calls["n"] == 2


def test_expired_cache_refetches():
    prov = FXProvider(stub_mode=False, cache_ttl_s=0)
    calls = {"n": 0}

    def _fake(base: str, quote: str) -> dict:
        calls["n"] += 1
        return {
            "base": base, "quote": quote, "rate": 1.08,
            "as_of": _utcnow(), "source": "frankfurter",
        }

    prov._fetch_raw = _fake  # type: ignore[method-assign]
    prov.get_rate("EUR", "USD")
    prov.get_rate("EUR", "USD")
    assert calls["n"] == 2


# -- convert math ----------------------------------------------------------


def test_convert_direct_and_identity():
    rates = {"EUR/USD": 1.08}
    assert convert(100.0, "EUR", "USD", rates) == pytest.approx(108.0)
    assert convert(108.0, "USD", "EUR", rates) == pytest.approx(100.0)
    assert convert(50.0, "USD", "USD", rates) == pytest.approx(50.0)


def test_convert_triangle_via_usd():
    rates = {"EUR/USD": 1.08, "USD/CNY": 7.25}
    assert convert(1.0, "EUR", "CNY", rates) == pytest.approx(7.83)
    assert convert(7.83, "CNY", "EUR", rates) == pytest.approx(1.0)


def test_convert_key_forms_and_case():
    assert convert(1.0, "eur", "usd", {"EURUSD": 1.08}) == pytest.approx(1.08)
    assert convert(1.0, "EUR", "USD", {("EUR", "USD"): 1.08}) == pytest.approx(1.08)


def test_convert_no_path_raises():
    with pytest.raises(ValueError):
        convert(1.0, "EUR", "CNY", {"EUR/USD": 1.08})
    with pytest.raises(ValueError):
        convert(1.0, "", "USD", {"EUR/USD": 1.08})


# -- gate ------------------------------------------------------------------


def test_gate_fresh_passes_and_normalizes():
    out = require_fx_provenance(_prov(hours_old=1.0))
    assert out["fallback_used"] is False
    assert isinstance(out["as_of"], datetime)
    # ISO-string as_of also accepted.
    iso = _prov(hours_old=1.0)
    iso["as_of"] = iso["as_of"].isoformat()
    assert require_fx_provenance(iso)["fallback_used"] is False


def test_gate_refuses_stale_over_24h():
    with pytest.raises(FXProvenanceMissing) as exc_info:
        require_fx_provenance(_prov(hours_old=25.0))
    assert exc_info.value.code == "FX_PROVENANCE_MISSING" == CODE
    assert require_fx_provenance(_prov(hours_old=23.9))["fallback_used"] is False


def test_gate_refuses_fallback_without_flag():
    with pytest.raises(FXProvenanceMissing) as exc_info:
        require_fx_provenance(_prov(hours_old=0.5, fallback=True, source="fx"))
    assert exc_info.value.code == CODE
    ok = require_fx_provenance(
        _prov(hours_old=0.5, fallback=True, source="fx"), allow_fallback=True
    )
    assert ok["fallback_used"] is True


def test_gate_refuses_missing_provenance():
    for bad in (None, {}, {"source": "fx"}):
        with pytest.raises(FXProvenanceMissing):
            require_fx_provenance(bad)


# -- rank / compare --------------------------------------------------------


def _items() -> list[dict]:
    return [
        {"symbol": "AAPL", "price": 232.50, "currency": "USD"},
        {"symbol": "MC.PA", "price": 715.50, "currency": "EUR"},
        {"symbol": "600519.SS", "price": 1680.00, "currency": "CNY"},
    ]


def _rates() -> dict:
    return {"EUR/USD": 1.08, "USD/CNY": 7.25, "EUR/CNY": 7.83}


def test_rank_sorts_by_converted_desc():
    result = rank_cross_market(_items(), "USD", _rates(), _prov())
    symbols = [r["symbol"] for r in result["ranked"]]
    # MC.PA 715.50 EUR ~ 772.74 USD > AAPL 232.50 > 600519 1680 CNY ~ 231.72 USD
    assert symbols == ["MC.PA", "AAPL", "600519.SS"]
    assert result["target_ccy"] == "USD"
    assert result["count"] == 3
    mc = result["ranked"][0]
    assert mc["converted"] == pytest.approx(715.50 * 1.08)


def test_rank_refuses_stale_and_fallback():
    with pytest.raises(FXProvenanceMissing):
        rank_cross_market(_items(), "USD", _rates(), _prov(hours_old=30.0))
    with pytest.raises(FXProvenanceMissing):
        rank_cross_market(_items(), "USD", _rates(), _prov(fallback=True))
    ok = rank_cross_market(
        _items(), "USD", _rates(), _prov(fallback=True), allow_fallback=True
    )
    assert ok["count"] == 3


def test_rank_missing_price_sorts_last():
    items = _items() + [{"symbol": "NOPRICE", "price": None, "currency": "USD"}]
    result = rank_cross_market(items, "USD", _rates(), _prov())
    assert result["ranked"][-1]["symbol"] == "NOPRICE"
    assert result["ranked"][-1]["converted"] is None


def test_compare_names_winner_and_refuses():
    out = compare_cross_market(_items()[0], _items()[1], "USD", _rates(), _prov())
    assert out["winner"] == "MC.PA"
    with pytest.raises(FXProvenanceMissing):
        compare_cross_market(_items()[0], _items()[1], "USD", _rates(), _prov(hours_old=48))


# -- HTTP ------------------------------------------------------------------


def _stub_market_service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    return MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )


def _client(fx_provider: FXProvider) -> TestClient:
    reset_deps()
    fxapi.reset_fx_provider()
    app = FastAPI()
    app.include_router(fxapi.router)
    app.dependency_overrides[fxapi.get_fx_provider] = lambda: fx_provider
    app.dependency_overrides[get_market_service] = _stub_market_service
    return TestClient(app)


def test_pairs_supported_currencies():
    client = _client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/pairs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currencies"] == ["USD", "EUR", "CNY"]
    assert "EUR/USD" in body["pairs"]
    assert REQUIRED_KEYS <= set(body["provenance"])


def test_rate_carries_provenance():
    client = _client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/rate", params={"base": "EUR", "quote": "USD"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rate"] == pytest.approx(1.08)
    assert body["inverse"] == pytest.approx(1 / 1.08)
    assert body["fallback_used"] is True
    assert REQUIRED_KEYS <= set(body["provenance"])


def test_rate_rejects_unsupported_currency():
    client = _client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/rate", params={"base": "EUR", "quote": "JPY"})
    assert resp.status_code == 400


def test_convert_math_and_provenance():
    client = _client(FXProvider(stub_mode=True))
    resp = client.post("/api/fx/convert", json={"amount": 100, "from": "EUR", "to": "USD"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["converted"] == pytest.approx(108.0)
    assert body["from"] == "EUR" and body["to"] == "USD"
    assert REQUIRED_KEYS <= set(body["provenance"])


def test_rank_gated_fallback_without_flag_is_423():
    client = _client(FXProvider(stub_mode=True))  # stub => fallback_used=True
    resp = client.post(
        "/api/fx/rank",
        json={"symbols": ["AAPL", "MC.PA", "600519.SS"], "target_ccy": "USD"},
    )
    assert resp.status_code == 423, resp.text
    body = resp.json()
    assert body["error"]["code"] == "FX_PROVENANCE_MISSING"


def test_rank_allows_fallback_with_explicit_flag():
    client = _client(FXProvider(stub_mode=True))
    resp = client.post(
        "/api/fx/rank",
        json={
            "symbols": ["AAPL", "MC.PA", "600519.SS"],
            "target_ccy": "USD",
            "allow_fallback": True,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["symbol"] for r in body["ranked"]] == ["MC.PA", "AAPL", "600519.SS"]
    assert REQUIRED_KEYS <= set(body["provenance"])
    assert body["provenance"]["fallback_used"] is True


def test_rank_refuses_stale_rates_even_with_flag():
    class StaleFX(FXProvider):
        def get_rate(self, base: str, quote: str) -> dict:  # noqa: ANN002, ANN202
            out = super().get_rate(base, quote)
            out["as_of"] = _utcnow() - timedelta(hours=30)
            out["fallback_used"] = False
            return out

    client = _client(StaleFX(stub_mode=True))
    resp = client.post(
        "/api/fx/rank",
        json={
            "symbols": ["AAPL", "MC.PA"],
            "target_ccy": "USD",
            "allow_fallback": True,
        },
    )
    assert resp.status_code == 423, resp.text
    assert resp.json()["error"]["code"] == "FX_PROVENANCE_MISSING"


def test_rank_fresh_live_rates_pass_without_flag():
    prov = FXProvider(stub_mode=False)

    def _live(base: str, quote: str) -> dict:
        from backend.market_data.fx.provider import stub_rate as _stub

        return {
            "base": base, "quote": quote, "rate": _stub(base, quote),
            "as_of": _utcnow(), "source": "frankfurter",
        }

    prov._fetch_raw = _live  # type: ignore[method-assign]
    client = _client(prov)
    resp = client.post(
        "/api/fx/rank",
        json={"symbols": ["AAPL", "MC.PA", "600519.SS"], "target_ccy": "USD"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["fallback_used"] is False
    assert [r["symbol"] for r in body["ranked"]] == ["MC.PA", "AAPL", "600519.SS"]


def test_router_prefix_and_package_exports():
    assert fxapi.router.prefix == "/api/fx"
    from backend.market_data.fx import (  # noqa: PLC0415
        FXProvenanceMissing as _E,
        FXProvider as _P,
        convert as _c,
        require_fx_provenance as _g,
    )

    assert _P is FXProvider and _E is FXProvenanceMissing
    assert callable(_c) and callable(_g)
