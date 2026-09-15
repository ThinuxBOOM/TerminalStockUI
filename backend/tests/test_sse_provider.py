"""M6 SSE provider tests (no network).

Covers:
- symbol normalization 600519 <-> 600519.SS
- CNY currency (never USD for XSHG)
- stub_mode + missing-akshare fallback (flagged fallback_used=True)
- breaker isolation (yfinance vs akshare independent)
- service SSE fallback chain yfinance(.SS) -> akshare(6-digit) -> stub
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.instruments.calendars import expected_delay_minutes
from backend.market_data.providers.akshare import (
    AKShareProvider,
    shanghai_display,
    to_akshare_code,
    to_yahoo_symbol,
)
from backend.market_data.providers.base import CircuitBreaker, ProviderError, RateLimiter
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point DATABASE_URL at a fresh sqlite file; drop the cached engine."""
    from backend.db.session import reset_engine

    url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


def _liveify_stub(quote: dict, source: str | None = None) -> dict:
    """Convert a stub quote dict into a live-shaped double (fail-closed helper).

    Providers are unchanged (stub_mode still returns fallback-flagged dicts);
    the service refuses those. Tests build live doubles by clearing the
    fallback flags and ensuring a price is present.
    """
    q = dict(quote)
    q["fallback_used"] = False
    q.pop("fallback", None)
    if q.get("price") is None:
        q["price"] = 100.0
    if source is not None:
        q["source"] = source
    return q


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -- symbol normalization -------------------------------------------------


def test_to_akshare_code_strips_ss():
    assert to_akshare_code("600519.SS") == "600519"
    assert to_akshare_code("600519.ss") == "600519"
    assert to_akshare_code("600519", market="XSHG") == "600519"
    assert to_akshare_code(" 600519.SS ", market="XSHG") == "600519"


def test_to_yahoo_symbol_adds_ss():
    assert to_yahoo_symbol("600519") == "600519.SS"
    assert to_yahoo_symbol("600519.SS") == "600519.SS"
    assert to_yahoo_symbol("600519.ss") == "600519.SS"


def test_akshare_get_quote_accepts_both_forms_stub():
    prov = AKShareProvider(stub_mode=True)
    q1 = prov.get_quote("600519.SS")
    q2 = prov.get_quote("600519", market="XSHG")
    assert q1["symbol"] == "600519.SS"
    assert q2["symbol"] == "600519.SS"
    assert q1["price"] == q2["price"]
    assert q1["currency"] == "CNY" == q2["currency"]


# -- currency + fallback flag ----------------------------------------------


def test_akshare_stub_mode_cny_and_fallback_flag():
    prov = AKShareProvider(stub_mode=True)
    q = prov.get_quote("600519.SS")
    assert q["currency"] == "CNY"
    assert q["currency"] != "USD"
    assert q["fallback_used"] is True
    assert q["source"] == "akshare"
    assert q["price"] is not None
    assert isinstance(q["missing_fields"], list)
    as_of = q["as_of"]
    assert isinstance(as_of, datetime)
    # Stored UTC (tz-aware).
    assert as_of.tzinfo is not None


def test_akshare_stub_never_usd_for_unknown_sse_code():
    prov = AKShareProvider(stub_mode=True)
    q = prov.get_quote("600123", market="XSHG")
    assert q["currency"] == "CNY"
    assert q["fallback_used"] is True
    assert q["symbol"] == "600123.SS"


def test_akshare_empty_symbol_raises():
    prov = AKShareProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_quote("")
    with pytest.raises(ProviderError):
        prov.get_quote("   ")


# -- missing akshare / network failure --------------------------------------


def test_missing_akshare_package_falls_back_flagged(monkeypatch):
    import backend.market_data.providers.akshare as akmod

    monkeypatch.setattr(akmod, "ak", None)
    prov = AKShareProvider(stub_mode=False)
    q = prov.get_quote("600519.SS")
    assert q["fallback_used"] is True
    assert q["currency"] == "CNY"
    assert q["source"] == "akshare"
    assert q["price"] is not None


def test_akshare_network_failure_falls_back_flagged(monkeypatch):
    prov = AKShareProvider(stub_mode=False)

    def _boom(symbol, market=None):
        raise ProviderError("akshare", "boom")

    monkeypatch.setattr(prov, "_fetch_raw", _boom)
    q = prov.get_quote("600519.SS")
    assert q["fallback_used"] is True
    assert q["currency"] == "CNY"
    assert q["price"] is not None


def test_akshare_fetch_raw_missing_package_raises():
    import backend.market_data.providers.akshare as akmod

    prov = AKShareProvider(stub_mode=False)
    orig = akmod.ak
    try:
        akmod.ak = None
        with pytest.raises(ProviderError):
            prov._fetch_raw("600519.SS")
    finally:
        akmod.ak = orig


# -- breaker isolation -------------------------------------------------------


def test_breaker_objects_are_isolated():
    yf = YFinanceProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=2))
    ak = AKShareProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=2))
    assert yf.breaker is not ak.breaker
    yf.breaker.record_failure()
    yf.breaker.record_failure()
    assert yf.breaker.state == "open"
    assert ak.breaker.state == "closed"
    assert ak.breaker.allow_request() is True


def test_provider_circuit_open_returns_flagged_stub():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    assert breaker.state == "open"
    prov = AKShareProvider(stub_mode=False, breaker=breaker)
    q = prov.get_quote("600519.SS")
    assert q["fallback_used"] is True
    assert q.get("circuit_open") is True
    assert q["currency"] == "CNY"


def test_service_breaker_isolation_yf_open_akshare_wins(isolated_db):
    # yfinance breaker open -> its get_quote returns stub fallback;
    # akshare live mock must still win the SSE chain.
    yf_breaker = CircuitBreaker(failure_threshold=1)
    yf_breaker.record_failure()
    assert yf_breaker.state == "open"
    yf = YFinanceProvider(stub_mode=False, breaker=yf_breaker)

    ak = AKShareProvider(stub_mode=False, breaker=CircuitBreaker())
    assert ak.breaker.state == "closed"

    def _ak_live(symbol, market=None):
        return {
            "symbol": "600519.SS",
            "price": 1685.0,
            "open": 1678.0,
            "high": 1690.0,
            "low": 1675.0,
            "prev_close": 1680.0,
            "volume": 3_100_000,
            "currency": "CNY",
            "as_of": _utcnow(),
        }

    ak._fetch_raw = _ak_live  # type: ignore[method-assign]
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("600519.SS")
    assert out["provenance"]["source"] == "akshare"
    assert out["currency"] == "CNY"
    assert out["provenance"]["fallback_used"] is False


# -- service fallback chain ---------------------------------------------------


def _live_yf_raw_cny(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "price": 1685.0,
        "open": 1678.0,
        "high": 1690.0,
        "low": 1675.0,
        "prev_close": 1680.0,
        "volume": 3_100_000,
        "currency": "CNY",
        "as_of": _utcnow(),
    }


def _live_ak_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": "600519.SS",
        "price": 1690.0,
        "open": 1682.0,
        "high": 1695.0,
        "low": 1680.0,
        "prev_close": 1685.0,
        "volume": 3_000_000,
        "currency": "CNY",
        "as_of": _utcnow(),
    }


def test_service_sse_yfinance_wins_when_live(isolated_db, monkeypatch):
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=False)
    monkeypatch.setattr(yf, "_fetch_raw", _live_yf_raw_cny)
    monkeypatch.setattr(
        ak, "_fetch_raw", lambda symbol, market=None: (_ for _ in ()).throw(
            ProviderError("akshare", "should not be called live")
        )
        if False else _live_ak_raw(symbol, market),
    )
    # Force yfinance live; akshare would also be live but yfinance is first.
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("600519.SS")
    assert out["provenance"]["source"] == "yfinance"
    assert out["currency"] == "CNY"
    assert out["provenance"]["fallback_used"] is False
    assert out["symbol"] == "600519.SS"


def test_service_sse_akshare_wins_when_yfinance_fails(isolated_db, monkeypatch):
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=False)

    def _yf_boom(symbol):
        raise ProviderError("yfinance", "network down")

    monkeypatch.setattr(yf, "_fetch_raw", _yf_boom)
    monkeypatch.setattr(ak, "_fetch_raw", _live_ak_raw)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("600519.SS")
    assert out["provenance"]["source"] == "akshare"
    assert out["currency"] == "CNY"
    assert out["price"] == 1690.0
    assert out["provenance"]["fallback_used"] is False


def test_service_sse_bare_code_resolves_and_wins(isolated_db, monkeypatch):
    # Bare 600519 (+ market=XSHG) normalizes to 600519.SS via registry/chain.
    # Fail-closed: use live-shaped doubles (stubs are refused by the service).
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=False)
    monkeypatch.setattr(yf, "_fetch_raw", _live_yf_raw_cny)
    monkeypatch.setattr(ak, "_fetch_raw", _live_ak_raw)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out_bare = svc.get_quote("600519", market="XSHG")
    out_full = svc.get_quote("600519.SS")
    assert out_bare["symbol"] == "600519.SS"
    assert out_full["symbol"] == "600519.SS"
    assert out_bare["currency"] == "CNY"
    assert out_full["currency"] == "CNY"
    # yfinance is first in the SSE chain, so it wins when live.
    assert out_bare["provenance"]["source"] == "yfinance"
    assert out_bare["provenance"]["fallback_used"] is False
    assert out_full["provenance"]["fallback_used"] is False


def test_service_sse_both_fail_raises_provider_error(isolated_db):
    # Fail-closed: no live quote from yfinance+akshare -> ProviderError.
    # The old stub/grade-C cover is dead (stubs are refused, never served).
    yf = YFinanceProvider(stub_mode=True)
    ak = AKShareProvider(stub_mode=True)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    with pytest.raises(ProviderError):
        svc.get_quote("600519.SS")


def test_service_refuses_fallback_flagged_quote(isolated_db):
    # A lingering fallback_used=True flag on the winning quote also raises,
    # even when a price is present (stub-shaped doubles are refused).
    yf = YFinanceProvider(stub_mode=True)
    raw_stub = yf.get_quote("600519.SS")
    assert raw_stub["fallback_used"] is True
    assert raw_stub["price"] is not None
    # Sanity: converting the same stub to live-shaped passes the gate.
    live = _liveify_stub(raw_stub, source="yfinance")
    assert live["fallback_used"] is False
    assert live["price"] is not None
    ak = AKShareProvider(stub_mode=True)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    with pytest.raises(ProviderError):
        svc.get_quote("600519.SS")


def test_service_sse_currency_never_usd_even_if_yfinance_says_usd(isolated_db, monkeypatch):
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=True)

    def _yf_usd(symbol):
        raw = _live_yf_raw_cny(symbol)
        raw["currency"] = "USD"  # misbehaving upstream; service must coerce
        return raw

    monkeypatch.setattr(yf, "_fetch_raw", _yf_usd)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("600519.SS")
    assert out["currency"] == "CNY"
    assert out["provenance"]["source"] == "yfinance"


def test_service_sse_delay_from_calendars(isolated_db, monkeypatch):
    # Fail-closed: delay still comes from calendars, verified on a live quote.
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=False)
    monkeypatch.setattr(yf, "_fetch_raw", _live_yf_raw_cny)
    monkeypatch.setattr(ak, "_fetch_raw", _live_ak_raw)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("600519.SS")
    assert out["provenance"]["delay_minutes"] == expected_delay_minutes("XSHG")
    assert out["provenance"]["fallback_used"] is False


def test_service_us_behavior_preserved(isolated_db, monkeypatch):
    # US path serves ONLY live quotes (stubs raise); currency stays USD.
    yf = YFinanceProvider(stub_mode=False)
    ak = AKShareProvider(stub_mode=False)

    def _live_yf_usd(symbol: str) -> dict:
        return {
            "symbol": symbol,
            "price": 232.50,
            "open": 231.0,
            "high": 233.8,
            "low": 230.1,
            "prev_close": 230.75,
            "volume": 54_000_000,
            "currency": "USD",
            "as_of": _utcnow(),
        }

    monkeypatch.setattr(yf, "_fetch_raw", _live_yf_usd)
    monkeypatch.setattr(ak, "_fetch_raw", _live_ak_raw)
    svc = MarketDataService(provider=yf, akshare_provider=ak)
    out = svc.get_quote("AAPL")
    assert out["symbol"] == "AAPL"
    assert out["currency"] == "USD"
    assert out["provenance"]["source"] == "yfinance"
    assert out["provenance"]["fallback_used"] is False


def test_package_exports_akshare():
    from backend.market_data.providers import AKShareProvider as Exported

    assert Exported is AKShareProvider


def test_shanghai_display_utc_storage():
    utc = datetime(2026, 9, 12, 6, 30, tzinfo=timezone.utc)
    disp = shanghai_display(utc)
    # 06:30 UTC == 14:30 Asia/Shanghai (+08:00).
    assert "14:30" in disp
    assert utc.tzinfo is not None
    assert utc.isoformat().endswith("+00:00")
