"""M7 FX tests (no network) — FAIL-CLOSED contract.

Covers:
- provider fail-closed: stub_mode raises, open breaker raises, fetch
  failure raises, malformed/non-finite upstream raises; identity pairs
  (b==q) return exact 1.0 with fallback_used False and no upstream call.
  No fallback stub rates; live rates carry fallback_used False.
- provider: empty/unsupported -> ProviderError(retryable=False);
  breaker isolation; 15-min cache
- convert math: direct / inverse / triangle / key-form variants / no-path
- gate: fresh passes; stale >24h refuses; fallback refuses without
  allow_fallback, passes with it; missing provenance refuses
  (gate logic unchanged; provider never serves fallback so the fallback
  branch is only reachable with hand-built envelopes)
- rank/compare: refuse without fresh FX, sort otherwise
- HTTP fail-closed: ProviderError -> 400 when not retryable else 502;
  rank with no quotable symbols -> 502; rank gate 423 unchanged for
  stale/missing rates; live responses always fallback_used False.
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


# -- live doubles (fail-closed) ------------------------------------------------
# MarketDataService serves ONLY live data: build live doubles by flipping a
# stub quote dict to fallback_used False (price present).


def _live_market_service() -> MarketDataService:
    tracker = ProviderHealthTracker()
    base = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )

    _orig = base.get_quote

    def _live_get_quote(symbol: str, *args, **kwargs) -> dict:  # noqa: ANN002, ANN003, ANN202
        q = _orig(symbol)
        q = dict(q)
        q["fallback_used"] = False
        q.pop("fallback", None)
        return q

    base.get_quote = _live_get_quote  # type: ignore[method-assign]
    return MarketDataService(
        registry=InstrumentRegistry(), provider=base,
        health=tracker, cache=InMemoryCache(),
    )


def _make_live_fx(*, stale_hours: float | None = None) -> FXProvider:
    """Live FX double: deterministic stub_rate values served as LIVE."""
    prov = FXProvider(stub_mode=False)

    def _live(base: str, quote: str) -> dict:
        from backend.market_data.fx.provider import stub_rate as _stub

        asof = _utcnow() - timedelta(hours=stale_hours) if stale_hours else _utcnow()
        return {
            "base": base, "quote": quote, "rate": _stub(base, quote),
            "as_of": asof, "source": "frankfurter",
        }

    prov._fetch_raw = _live  # type: ignore[method-assign]
    prov._get_ecb_table = lambda: None  # type: ignore[method-assign]  # hermetic abstain
    return prov


def _live_client(fx_provider: FXProvider, market=None) -> TestClient:
    reset_deps()
    fxapi.reset_fx_provider()
    app = FastAPI()
    app.include_router(fxapi.router)
    app.dependency_overrides[fxapi.get_fx_provider] = lambda: fx_provider
    svc = market if isinstance(market, MarketDataService) else _live_market_service()
    app.dependency_overrides[get_market_service] = lambda: svc
    return TestClient(app)


# -- fail-closed provider ------------------------------------------------------


def test_stub_mode_raises_fail_closed():
    """stub_mode serves nothing: every non-identity pair raises ProviderError."""
    prov = FXProvider(stub_mode=True)
    for base, quote in (("EUR", "USD"), ("USD", "CNY"), ("EUR", "CNY"), ("USD", "EUR")):
        with pytest.raises(ProviderError):
            prov.get_rate(base, quote)
    # Identity still exact 1.0 with fallback False even in stub_mode.
    out = prov.get_rate("USD", "USD")
    assert out["rate"] == 1.0 and out["inverse"] == 1.0
    assert out["fallback_used"] is False


def test_stub_mode_triangle_raises_no_fallback_rates():
    """No fallback stub rates: triangle pairs raise in stub_mode."""
    prov = FXProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "USD")
    with pytest.raises(ProviderError):
        prov.get_rate("USD", "CNY")
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "CNY")
    # stub_mode error is retryable (upstream unavailable) -> HTTP 502.
    try:
        prov.get_rate("EUR", "USD")
    except ProviderError as exc:
        assert exc.retryable is True


def test_same_currency_identity():
    prov = FXProvider(stub_mode=True)
    out = prov.get_rate("USD", "USD")
    assert out["rate"] == 1.0 and out["inverse"] == 1.0
    assert out["fallback_used"] is False


def test_identity_live_no_upstream_call():
    """Identity returns exact 1.0 with no upstream fetch (even live)."""
    prov = FXProvider(stub_mode=False)

    def _boom(base: str, quote: str) -> dict:
        raise AssertionError("upstream must not be called for identity")

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    prov._fetch_yahoo = _boom  # type: ignore[method-assign]
    out = prov.get_rate("EUR", "EUR")
    assert out["rate"] == 1.0 and out["inverse"] == 1.0
    assert out["fallback_used"] is False
    assert out["reconciled"] is True


def test_empty_and_unsupported_currency_raise():
    prov = FXProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_rate("", "USD")
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "   ")
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "JPY")


def test_empty_currency_not_retryable():
    prov = FXProvider(stub_mode=False)
    with pytest.raises(ProviderError) as ei:
        prov.get_rate("", "USD")
    assert ei.value.retryable is False
    with pytest.raises(ProviderError) as ei2:
        prov.get_rate("EUR", "JPY")
    assert ei2.value.retryable is False


def test_provenance_grades_live_and_manual_fallback():
    """Live payloads grade B (single-source) / A when reconciled; a
    hand-built fallback envelope still grades C (gate layer unchanged)."""
    prov = FXProvider(stub_mode=False)
    live = {
        "pair": "EUR/USD", "rate": 1.08, "as_of": _utcnow(),
        "source": "frankfurter", "missing_fields": [], "fallback_used": False,
    }
    live_prov = prov.provenance_for(live)
    assert live_prov.fallback_used is False
    assert live_prov.quality_grade == "B"  # fresh single-source
    assert REQUIRED_KEYS <= set(live_prov.model_dump(mode="json"))
    # Explicit reconciled keyword reaches A from a bare payload.
    bare = dict(live)
    assert prov.provenance_for(bare, reconciled=True).quality_grade == "A"
    # Manual fallback envelope (never served by the provider) still C.
    stub_like = dict(live)
    stub_like["fallback_used"] = True
    stub_like["source"] = "fx"
    assert prov.provenance_for(stub_like).quality_grade == "C"
    assert prov.provenance_for(stub_like).fallback_used is True


def test_daily_fix_grades_against_fix_cadence():
    """Daily reference fixes grade against the 24h fix cadence, not the
    15-minute poll interval (regression: every same-day midnight fix graded C
    after ~00:30 UTC, permanently gating cross-market ranking on weekdays)."""
    from backend.market_data.fx.provider import FX_FIX_DELAY_MINUTES  # noqa: PLC0415

    assert FX_FIX_DELAY_MINUTES == 1440
    prov = FXProvider(stub_mode=False)
    midnight = _utcnow().replace(hour=0, minute=5, second=0, microsecond=0)
    if midnight > _utcnow():
        midnight -= timedelta(days=1)
    payload = {
        "pair": "EUR/USD", "rate": 1.08, "as_of": midnight,
        "source": "frankfurter", "missing_fields": [], "fallback_used": False,
    }
    graded = prov.provenance_for(payload)
    assert graded.delay_minutes == FX_FIX_DELAY_MINUTES
    assert graded.quality_grade in ("A", "B")
    # Genuinely old fixes (>~48h) still grade C and keep the gate closed.
    old = dict(payload, as_of=_utcnow() - timedelta(hours=60))
    assert prov.provenance_for(old).quality_grade == "C"


# -- offline resilience (fail-closed: raise, never fallback) -------------------


def test_network_failure_raises_fail_closed():
    prov = FXProvider(stub_mode=False)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "network down")

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    prov._fetch_yahoo = _boom  # type: ignore[method-assign]
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "USD")


def test_missing_httpx_package_raises_fail_closed(monkeypatch):
    import sys

    prov = FXProvider(stub_mode=False)
    monkeypatch.setitem(sys.modules, "httpx", None)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "yahoo down")

    prov._fetch_yahoo = _boom  # type: ignore[method-assign]  # isolate httpx path
    with pytest.raises(ProviderError):  # raw fetch names the missing dep
        prov._fetch_raw("EUR", "USD")
    with pytest.raises(ProviderError):  # public path raises fail-closed
        prov.get_rate("EUR", "USD")


def test_malformed_upstream_raises():
    prov = FXProvider(stub_mode=False)

    def _bad(base: str, quote: str) -> dict:
        return {
            "base": base, "quote": quote, "rate": "oops-not-a-number",
            "as_of": _utcnow(), "source": "frankfurter",
        }

    prov._fetch_raw = _bad  # type: ignore[method-assign]
    prov._get_ecb_table = lambda: None  # type: ignore[method-assign]
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "USD")


def test_nonfinite_upstream_raises():
    import math

    for bad_rate in (float("nan"), float("inf"), -1.0, 0.0):
        prov = FXProvider(stub_mode=False)

        def _bad(base: str, quote: str, _r=bad_rate) -> dict:
            return {
                "base": base, "quote": quote, "rate": _r,
                "as_of": _utcnow(), "source": "frankfurter",
            }

        prov._fetch_raw = _bad  # type: ignore[method-assign]
        prov._get_ecb_table = lambda: None  # type: ignore[method-assign]
        with pytest.raises(ProviderError):
            prov.get_rate("EUR", "USD")
    assert not math.isfinite(float("nan"))  # sanity


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


def test_breaker_open_raises_fail_closed():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    prov = FXProvider(stub_mode=False, breaker=breaker)
    with pytest.raises(ProviderError) as ei:
        prov.get_rate("EUR", "USD")
    assert "circuit open" in str(ei.value)


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


def test_live_rates_carry_fallback_false():
    prov = _make_live_fx()
    for base, quote in (("EUR", "USD"), ("USD", "CNY"), ("EUR", "CNY")):
        out = prov.get_rate(base, quote)
        assert out["fallback_used"] is False
        assert out["rate"] > 0


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


def test_rate_live_carries_provenance_no_fallback():
    client = _live_client(_make_live_fx())
    resp = client.get("/api/fx/rate", params={"base": "EUR", "quote": "USD"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rate"] == pytest.approx(1.08)
    assert body["inverse"] == pytest.approx(1 / 1.08)
    assert body["fallback_used"] is False
    assert REQUIRED_KEYS <= set(body["provenance"])
    assert body["provenance"]["fallback_used"] is False


def test_rate_stub_mode_is_502():
    client = _live_client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/rate", params={"base": "EUR", "quote": "USD"})
    assert resp.status_code == 502, resp.text


def test_rate_identity_in_stub_mode_is_200():
    """Identity needs no upstream: USD/USD works even in stub_mode."""
    client = _live_client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/rate", params={"base": "USD", "quote": "USD"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rate"] == 1.0
    assert body["fallback_used"] is False


def test_rate_provider_error_mapping_400_vs_502():
    class _BadInput(FXProvider):
        def get_rate(self, base: str, quote: str) -> dict:  # noqa: ANN002, ANN202
            raise ProviderError("fx", "bad input", retryable=False)

    class _Downstream(FXProvider):
        def get_rate(self, base: str, quote: str) -> dict:  # noqa: ANN002, ANN202
            raise ProviderError("fx", "upstream down", retryable=True)

    assert _live_client(_BadInput()).get(
        "/api/fx/rate", params={"base": "EUR", "quote": "USD"}
    ).status_code == 400
    assert _live_client(_Downstream()).get(
        "/api/fx/rate", params={"base": "EUR", "quote": "USD"}
    ).status_code == 502


def test_rate_rejects_unsupported_currency():
    client = _client(FXProvider(stub_mode=True))
    resp = client.get("/api/fx/rate", params={"base": "EUR", "quote": "JPY"})
    assert resp.status_code == 400


def test_convert_live_math_and_provenance():
    client = _live_client(_make_live_fx())
    resp = client.post("/api/fx/convert", json={"amount": 100, "from": "EUR", "to": "USD"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["converted"] == pytest.approx(108.0)
    assert body["from"] == "EUR" and body["to"] == "USD"
    assert body["fallback_used"] is False
    assert REQUIRED_KEYS <= set(body["provenance"])


def test_convert_stub_mode_is_502():
    client = _live_client(FXProvider(stub_mode=True))
    resp = client.post("/api/fx/convert", json={"amount": 100, "from": "EUR", "to": "USD"})
    assert resp.status_code == 502, resp.text


def test_convert_provider_error_mapping_400_vs_502():
    class _BadInput(FXProvider):
        def get_rate(self, base: str, quote: str) -> dict:  # noqa: ANN002, ANN202
            raise ProviderError("fx", "bad input", retryable=False)

    class _Downstream(FXProvider):
        def get_rate(self, base: str, quote: str) -> dict:  # noqa: ANN002, ANN202
            raise ProviderError("fx", "upstream down", retryable=True)

    bad = _live_client(_BadInput()).post(
        "/api/fx/convert", json={"amount": 100, "from": "EUR", "to": "USD"}
    )
    assert bad.status_code == 400
    down = _live_client(_Downstream()).post(
        "/api/fx/convert", json={"amount": 100, "from": "EUR", "to": "USD"}
    )
    assert down.status_code == 502


def test_rank_stale_rates_gate_423():
    """Stale live rates refuse with 423 with or without the fallback flag."""
    for flag in (False, True):
        client = _live_client(_make_live_fx(stale_hours=30.0))
        resp = client.post(
            "/api/fx/rank",
            json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD",
                  "allow_fallback": flag},
        )
        assert resp.status_code == 423, resp.text
        assert resp.json()["error"]["code"] == "FX_PROVENANCE_MISSING"


def test_rank_live_passes_with_and_without_flag_inert():
    """allow_fallback is inert for live rates: both flag values pass identically."""
    for flag in (False, True):
        client = _live_client(_make_live_fx())
        resp = client.post(
            "/api/fx/rank",
            json={
                "symbols": ["AAPL", "MC.PA", "600519.SS"],
                "target_ccy": "USD",
                "allow_fallback": flag,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [r["symbol"] for r in body["ranked"]] == ["MC.PA", "AAPL", "600519.SS"]
        assert REQUIRED_KEYS <= set(body["provenance"])
        assert body["provenance"]["fallback_used"] is False


def test_rank_refuses_stale_rates_even_with_flag():
    client = _live_client(_make_live_fx(stale_hours=30.0))
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
    client = _live_client(_make_live_fx())
    resp = client.post(
        "/api/fx/rank",
        json={"symbols": ["AAPL", "MC.PA", "600519.SS"], "target_ccy": "USD"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["fallback_used"] is False
    assert [r["symbol"] for r in body["ranked"]] == ["MC.PA", "AAPL", "600519.SS"]


def test_rank_no_quotable_symbols_is_502():
    """All quotes failing -> 502, never a flagged 200 or empty 200."""
    dead = _stub_market_service()  # stub provider raises under fail-closed service
    client = _live_client(_make_live_fx(), market=dead)
    resp = client.post(
        "/api/fx/rank",
        json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD"},
    )
    assert resp.status_code == 502, resp.text


def test_rank_stub_fx_multi_ccy_is_502():
    """Stub FX cannot serve cross-currency ranks: 502 (identity-only passes)."""
    client = _live_client(FXProvider(stub_mode=True))
    resp = client.post(
        "/api/fx/rank",
        json={"symbols": ["AAPL", "MC.PA"], "target_ccy": "USD"},
    )
    assert resp.status_code == 502, resp.text
    # Single-USD rank needs only the identity rate, so it still passes.
    ok = client.post(
        "/api/fx/rank", json={"symbols": ["AAPL"], "target_ccy": "USD"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["provenance"]["fallback_used"] is False


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


# -- Phase 1c ECB reconciliation (offline: all network monkeypatched) -------


#: Fake ECB per-EUR table (units per EUR); EUR/USD cross == 1.08 exactly.
ECB_TABLE = {"EUR": 1.0, "USD": 1.08, "CNY": 7.83}

ECB_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <gesmes:Sender><gesmes:name>European Central Bank</gesmes:name></gesmes:Sender>
  <Cube>
    <Cube time="2026-09-11">
      <Cube currency="USD" rate="1.0821"/>
      <Cube currency="JPY" rate="162.34"/>
      <Cube currency="CNY" rate="7.8422"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def _live_reconciled_provider(monkeypatch, *, live_rate, ecb_table=ECB_TABLE):
    """Live frankfurter stub + fake ECB table (no real network either way)."""
    prov = FXProvider(stub_mode=False)

    def _live(base: str, quote: str) -> dict:
        return {
            "base": base, "quote": quote, "rate": live_rate,
            "as_of": _utcnow(), "source": "frankfurter",
        }

    def _ecb() -> dict | None:
        return dict(ecb_table) if ecb_table is not None else None

    monkeypatch.setattr(prov, "_fetch_raw", _live)
    monkeypatch.setattr(prov, "_get_ecb_table", _ecb)
    return prov


def test_reconcile_agree_grades_A(monkeypatch):
    prov = _live_reconciled_provider(monkeypatch, live_rate=1.081)  # ~0.09% off
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is False
    assert out["reconciled"] is True
    assert out["ecb_rate"] == pytest.approx(1.08)
    assert "divergence_pct" not in out  # agree carries no divergence field
    assert prov.provenance_for(out).quality_grade == "A"
    # Explicit keyword also reaches A from a bare (unstamped) payload.
    bare = {
        "pair": "EUR/USD", "rate": 1.081, "as_of": _utcnow(),
        "source": "frankfurter", "missing_fields": [], "fallback_used": False,
    }
    assert prov.provenance_for(bare, reconciled=True).quality_grade == "A"


def test_reconcile_disagree_grades_B_with_divergence(monkeypatch):
    prov = _live_reconciled_provider(monkeypatch, live_rate=1.10)  # ~1.85% off
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is False
    assert out["rate"] == pytest.approx(1.10)
    assert out["reconciled"] is False
    assert out["ecb_rate"] == pytest.approx(1.08)
    assert out["divergence_pct"] == pytest.approx(abs(1.10 / 1.08 - 1) * 100.0)
    assert prov.provenance_for(out).quality_grade == "B"


def test_reconciler_abstains_when_ecb_down(monkeypatch):
    """ECB outage -> live rate served unreconciled, grade B exactly as before.

    Abstain choice: ``reconciled: False`` with NO ``ecb_rate``/
    ``divergence_pct`` keys, so abstention is distinguishable from a voted
    disagreement (which always carries both fields).
    """
    prov = _live_reconciled_provider(monkeypatch, live_rate=1.08, ecb_table=None)
    out = prov.get_rate("EUR", "USD")
    assert out["fallback_used"] is False
    assert out["rate"] == pytest.approx(1.08)
    assert out["reconciled"] is False
    assert "ecb_rate" not in out and "divergence_pct" not in out
    assert prov.provenance_for(out).quality_grade == "B"


def test_yahoo_live_path_also_reconciles(monkeypatch):
    """Frankfurter down + yahoo live + ECB agree -> reconciled True, grade A."""
    prov = FXProvider(stub_mode=False)

    def _boom(base: str, quote: str) -> dict:
        raise ProviderError("fx", "frankfurter down")

    def _live_yahoo(base: str, quote: str) -> dict:
        return {
            "base": base, "quote": quote, "rate": 1.081,
            "as_of": _utcnow(), "source": "yfinance",
        }

    monkeypatch.setattr(prov, "_fetch_raw", _boom)
    monkeypatch.setattr(prov, "_fetch_yahoo", _live_yahoo)
    monkeypatch.setattr(prov, "_get_ecb_table", lambda: dict(ECB_TABLE))
    out = prov.get_rate("EUR", "USD")
    assert out["source"] == "yfinance"
    assert out["fallback_used"] is False
    assert out["reconciled"] is True
    assert prov.provenance_for(out).quality_grade == "A"


def test_identity_skips_ecb_fetch(monkeypatch):
    """base==quote is trivially reconciled without any ECB/live fetch."""
    import backend.market_data.fx.provider as fxprov  # noqa: PLC0415

    calls = {"ecb": 0, "live": 0}

    def _no_ecb():
        calls["ecb"] += 1
        raise AssertionError("ECB must not be fetched for identity pairs")

    def _no_live(base: str, quote: str) -> dict:
        calls["live"] += 1
        raise AssertionError("upstream must not be fetched for identity pairs")

    monkeypatch.setattr(fxprov, "_fetch_ecb_table", _no_ecb)
    prov = FXProvider(stub_mode=False)
    monkeypatch.setattr(prov, "_fetch_raw", _no_live)
    out = prov.get_rate("USD", "USD")
    assert out["rate"] == 1.0 and out["reconciled"] is True
    assert calls == {"ecb": 0, "live": 0}
    assert prov.provenance_for(out).quality_grade == "A"


def test_reconcile_tolerance_boundary(monkeypatch):
    """Tolerance is |live/ecb - 1| <= 0.005 (0.5%), agree side inclusive."""
    from backend.market_data.fx.provider import RECONCILE_TOLERANCE  # noqa: PLC0415

    assert RECONCILE_TOLERANCE == pytest.approx(0.005)
    # Exact-binary ECB cross (USD 2.00/EUR) keeps the edge deterministic.
    table = {"EUR": 1.0, "USD": 2.0, "CNY": 14.5}
    agree = _live_reconciled_provider(
        monkeypatch, live_rate=2.0 * 1.005, ecb_table=table
    ).get_rate("EUR", "USD")
    assert agree["reconciled"] is True  # nominally exactly 0.5% still agrees
    inside = _live_reconciled_provider(
        monkeypatch, live_rate=2.0 * 1.004, ecb_table=table
    ).get_rate("EUR", "USD")
    assert inside["reconciled"] is True
    outside = _live_reconciled_provider(
        monkeypatch, live_rate=2.0 * 1.0051, ecb_table=table
    ).get_rate("EUR", "USD")
    assert outside["reconciled"] is False
    assert outside["divergence_pct"] == pytest.approx(0.51, abs=0.01)
    assert outside["ecb_rate"] == pytest.approx(2.0)


def test_ecb_xml_parsing_per_eur_and_time():
    """eurofxref Cube parsing: per-EUR rates + TIME date, namespace-agnostic."""
    from backend.market_data.fx import provider as fxprov  # noqa: PLC0415

    table, day = fxprov._parse_ecb_xml(ECB_SAMPLE_XML)
    assert table["EUR"] == 1.0
    assert table["USD"] == pytest.approx(1.0821)
    assert table["CNY"] == pytest.approx(7.8422)
    assert day == "2026-09-11"  # as_of comes from the ECB TIME date
    with pytest.raises(ValueError):
        fxprov._parse_ecb_xml(
            "<gesmes:Envelope xmlns:gesmes='http://x'><Cube/></gesmes:Envelope>"
        )


def test_ecb_fetch_never_raises(monkeypatch):
    """Transport failure AND garbage body both abstain (return None)."""
    import sys  # noqa: PLC0415
    import types  # noqa: PLC0415

    from backend.market_data.fx import provider as fxprov  # noqa: PLC0415

    fake_httpx = types.SimpleNamespace()

    def _boom(*args, **kwargs):
        raise RuntimeError("ecb down")

    def _garbage(*args, **kwargs):
        return types.SimpleNamespace(
            text="<not xml", raise_for_status=lambda: None
        )

    fake_httpx.get = _boom
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    assert fxprov._fetch_ecb_table() is None
    fake_httpx.get = _garbage
    assert fxprov._fetch_ecb_table() is None


def test_ecb_table_fetched_once_per_hour(monkeypatch):
    """One cached ECB fetch serves reconciliations across pairs (hot path)."""
    import backend.market_data.fx.provider as fxprov  # noqa: PLC0415

    calls = {"n": 0}

    def _fake_fetch():
        calls["n"] += 1
        return (dict(ECB_TABLE), "2026-09-11")

    def _live(base: str, quote: str) -> dict:
        return {
            "base": base, "quote": quote,
            "rate": ECB_TABLE[quote] / ECB_TABLE[base],
            "as_of": _utcnow(), "source": "frankfurter",
        }

    monkeypatch.setattr(fxprov, "_fetch_ecb_table", _fake_fetch)
    prov = FXProvider(stub_mode=False)
    monkeypatch.setattr(prov, "_fetch_raw", _live)
    first = prov.get_rate("EUR", "USD")
    second = prov.get_rate("USD", "CNY")
    assert calls["n"] == 1
    assert first["reconciled"] is True and second["reconciled"] is True
    assert first["ecb_rate"] == pytest.approx(1.08)
    assert second["ecb_rate"] == pytest.approx(7.25)


def test_stub_path_raises_never_served():
    """Fail-closed: stub_mode never serves a rate, so nothing is reconciled.

    The only stub_mode success is the identity pair (trivially reconciled).
    """
    prov = FXProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_rate("EUR", "USD")
    ident = prov.get_rate("USD", "USD")
    assert ident["reconciled"] is True
    assert prov.provenance_for(ident).quality_grade == "A"
    # A hand-built fallback envelope still grades C at the provenance layer.
    manual = {
        "pair": "EUR/USD", "rate": 1.08, "as_of": _utcnow(),
        "source": "fx", "missing_fields": [], "fallback_used": True,
    }
    assert prov.provenance_for(manual).quality_grade == "C"
    assert prov.provenance_for(manual, reconciled=True).quality_grade == "C"
