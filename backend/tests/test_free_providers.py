"""Free-tier redundancy tests: Finnhub + TwelveData US chain (no network).

Style mirrors test_live_providers.py: network is stubbed (monkeypatched
``_fetch_raw`` / fake ``httpx``), DB is isolated per test, and every
assertion checks the provenance contract
(source/as_of/delay_minutes/quality_grade/fallback_used/missing_fields).

Covers:
- Finnhub symbol mapping (US-only), /quote parse, zero-as-no-data rule,
  missing-keys fallback, secret hygiene, stub/breaker/empty contracts
- TwelveData symbol mapping (US-only, BRK-B -> BRK/B), /quote parse,
  200-with-error-body handling (quota/unauthorized/unknown), key
  resolution, stub/breaker/empty contracts
- Service chain: yfinance live -> finnhub live -> twelvedata live;
  full outage preserves yfinance fallback; SSE/Euronext never
  touch the US-only free providers; short-circuit (one live feed, one call)
  (stooq/akshare dropped: not in the chain anymore)
- Future tier hooks: user_id/tier accepted and inert
- Package exports + dashboard rows
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

from backend.market_data.providers.base import CircuitBreaker, ProviderError
from backend.market_data.providers.finnhub_free import (
    FinnhubProvider,
    parse_quote as parse_finnhub_quote,
    resolve_key as resolve_finnhub_key,
    to_finnhub_symbol,
)
from backend.market_data.providers.stooq import StooqProvider
from backend.market_data.providers.twelvedata_free import (
    TwelveDataProvider,
    parse_quote as parse_twelvedata_quote,
    resolve_key as resolve_twelvedata_key,
    to_twelvedata_symbol,
)
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


# -- Finnhub symbol mapping ----------------------------------------------------


def test_finnhub_symbol_us_only():
    assert to_finnhub_symbol("AAPL") == "AAPL"
    assert to_finnhub_symbol("brk-b") == "BRK.B"
    for bad in ("600519.SS", "MC.PA", "ASML.AS", "UCB.BR"):
        with pytest.raises(ProviderError):
            to_finnhub_symbol(bad)
    with pytest.raises(ProviderError):
        to_finnhub_symbol("")
    with pytest.raises(ProviderError):
        to_finnhub_symbol("   ")


def test_finnhub_key_resolution(monkeypatch):
    for var in ("FINNHUB_API_KEY", "FINNHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_finnhub_key(None) == ""
    assert FinnhubProvider(stub_mode=False).configured is False
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-key")
    assert resolve_finnhub_key(None) == "fh-key"
    assert FinnhubProvider(stub_mode=False).configured is True


# -- Finnhub /quote parse ------------------------------------------------------


def test_finnhub_parse_quote():
    payload = {"c": 233.10, "d": 2.35, "dp": 1.02, "h": 233.80,
               "l": 230.10, "o": 231.00, "pc": 230.75, "t": 1757683800}
    raw = parse_finnhub_quote("AAPL", payload)
    assert raw["symbol"] == "AAPL"
    assert raw["price"] == 233.10
    assert raw["open"] == 231.00
    assert raw["high"] == 233.80
    assert raw["low"] == 230.10
    assert raw["prev_close"] == 230.75
    assert raw["volume"] is None  # /quote carries none; flagged missing downstream
    assert raw["currency"] == "USD"
    assert raw["as_of"] == datetime.fromtimestamp(1757683800, tz=timezone.utc)


def test_finnhub_parse_quote_zero_is_no_data():
    assert parse_finnhub_quote("AAPL", {"c": 100.0, "o": 0, "h": 0})["open"] is None
    with pytest.raises(ProviderError):
        parse_finnhub_quote("AAPL", {"c": 0, "o": 1.0})
    with pytest.raises(ProviderError):
        parse_finnhub_quote("AAPL", {"o": 1.0})  # no current price
    with pytest.raises(ProviderError):
        parse_finnhub_quote("AAPL", "not-a-dict")  # type: ignore[arg-type]


# -- Finnhub provider contract -------------------------------------------------


def test_finnhub_missing_keys_degrades_to_stub(monkeypatch):
    for var in ("FINNHUB_API_KEY", "FINNHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    prov = FinnhubProvider(stub_mode=False)
    assert prov.configured is False
    q = prov.get_quote("AAPL")
    assert q["source"] == "finnhub"
    assert q["fallback_used"] is True
    assert q["delay_minutes"] == 0
    assert q["currency"] == "USD"


def test_finnhub_stub_never_leaks_key(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-secret-123")
    prov = FinnhubProvider(stub_mode=True)
    q = prov.get_quote("AAPL")
    assert "fh-secret-123" not in repr(q)
    assert q["currency"] == "USD"
    assert q["fallback_used"] is True


def test_finnhub_stub_mode_flagged():
    prov = FinnhubProvider(stub_mode=True, api_key="k")
    q = prov.get_quote("AAPL")
    assert q["source"] == "finnhub"
    assert q["fallback_used"] is True
    assert q["price"] is not None


def test_finnhub_empty_symbol_raises():
    prov = FinnhubProvider(stub_mode=True, api_key="k")
    with pytest.raises(ProviderError):
        prov.get_quote("")
    with pytest.raises(ProviderError):
        prov.get_quote("   ")


def test_finnhub_non_us_falls_back_not_live():
    prov = FinnhubProvider(stub_mode=False, api_key="k")
    q = prov.get_quote("MC.PA")
    assert q["fallback_used"] is True
    assert q["currency"] == "USD"  # US-only feed never serves EUR


def test_finnhub_breaker_open_returns_flagged_stub():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    prov = FinnhubProvider(stub_mode=False, api_key="k", breaker=breaker)
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q.get("circuit_open") is True


def test_finnhub_network_failure_falls_back_flagged():
    prov = FinnhubProvider(stub_mode=False, api_key="k")

    def _boom(symbol, market=None):
        raise ProviderError("finnhub", "boom")

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q["source"] == "finnhub"


def test_finnhub_live_quote_marks_volume_missing():
    prov = FinnhubProvider(stub_mode=False, api_key="k")
    prov._fetch_raw = lambda symbol, market=None: {  # type: ignore[method-assign]
        "symbol": symbol, "price": 233.10, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": None,
        "currency": "USD", "as_of": _utcnow(),
    }
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is False
    assert "volume" in (q.get("missing_fields") or [])


# -- TwelveData symbol mapping -------------------------------------------------


def test_twelvedata_symbol_us_only():
    assert to_twelvedata_symbol("AAPL") == "AAPL"
    assert to_twelvedata_symbol("brk-b") == "BRK/B"
    for bad in ("600519.SS", "MC.PA", "ASML.AS", "UCB.BR"):
        with pytest.raises(ProviderError):
            to_twelvedata_symbol(bad)
    with pytest.raises(ProviderError):
        to_twelvedata_symbol("")
    with pytest.raises(ProviderError):
        to_twelvedata_symbol("   ")


def test_twelvedata_key_resolution(monkeypatch):
    for var in ("TWELVEDATA_API_KEY", "TWELVE_DATA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_twelvedata_key(None) == ""
    assert TwelveDataProvider(stub_mode=False).configured is False
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "td-key")
    assert resolve_twelvedata_key(None) == "td-key"
    monkeypatch.setenv("TWELVEDATA_API_KEY", "td-key-2")
    assert resolve_twelvedata_key(None) == "td-key-2"  # primary name wins


# -- TwelveData /quote parse ---------------------------------------------------


def test_twelvedata_parse_quote():
    payload = {"symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD",
               "datetime": "2026-09-12 15:59:00",
               "open": "229.10", "high": "233.80", "low": "228.50",
               "close": "233.10", "volume": "54000000",
               "previous_close": "230.75"}
    raw = parse_twelvedata_quote("AAPL", payload)
    assert raw["symbol"] == "AAPL"
    assert raw["price"] == 233.10
    assert raw["volume"] == 54_000_000
    assert raw["prev_close"] == 230.75
    assert raw["currency"] == "USD"
    # 15:59 US/Eastern (EDT, UTC-4) -> 19:59 UTC.
    assert raw["as_of"].tzinfo is not None
    assert (raw["as_of"].hour, raw["as_of"].minute) == (19, 59)


def test_twelvedata_parse_error_body():
    with pytest.raises(ProviderError, match="rate limited"):
        parse_twelvedata_quote("AAPL", {"status": "error", "code": 429,
                                       "message": "API credits exceeded"})
    with pytest.raises(ProviderError):
        parse_twelvedata_quote("AAPL", {"status": "error", "code": 401,
                                       "message": "Invalid API key"})
    with pytest.raises(ProviderError):
        parse_twelvedata_quote("AAPL", {"status": "error", "code": 404,
                                       "message": "symbol not found"})
    with pytest.raises(ProviderError):
        parse_twelvedata_quote("AAPL", {"open": "1.0"})  # no close


def test_twelvedata_200_error_body_degrades_flagged(monkeypatch):
    """HTTP 200 + {"status": "error"} must degrade, never leak or raise."""

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "error", "code": 429,
                    "message": "API credits exceeded. Consider upgrading."}

    class _FakeHttpx:
        @staticmethod
        def get(url, params=None, timeout=None):
            return _Resp()

    monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx())
    prov = TwelveDataProvider(stub_mode=False, api_key="k")
    q = prov.get_quote("AAPL")
    assert q["source"] == "twelvedata"
    assert q["fallback_used"] is True


# -- TwelveData provider contract ----------------------------------------------


def test_twelvedata_missing_keys_degrades_to_stub(monkeypatch):
    for var in ("TWELVEDATA_API_KEY", "TWELVE_DATA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    prov = TwelveDataProvider(stub_mode=False)
    assert prov.configured is False
    q = prov.get_quote("AAPL")
    assert q["source"] == "twelvedata"
    assert q["fallback_used"] is True
    assert q["delay_minutes"] == 0
    assert q["currency"] == "USD"


def test_twelvedata_stub_never_leaks_key(monkeypatch):
    monkeypatch.setenv("TWELVEDATA_API_KEY", "td-secret-456")
    prov = TwelveDataProvider(stub_mode=True)
    q = prov.get_quote("AAPL")
    assert "td-secret-456" not in repr(q)
    assert q["currency"] == "USD"


def test_twelvedata_stub_mode_flagged():
    prov = TwelveDataProvider(stub_mode=True, api_key="k")
    q = prov.get_quote("AAPL")
    assert q["source"] == "twelvedata"
    assert q["fallback_used"] is True
    assert q["price"] is not None


def test_twelvedata_empty_symbol_raises():
    prov = TwelveDataProvider(stub_mode=True, api_key="k")
    with pytest.raises(ProviderError):
        prov.get_quote("")
    with pytest.raises(ProviderError):
        prov.get_quote("   ")


def test_twelvedata_non_us_falls_back_not_live():
    prov = TwelveDataProvider(stub_mode=False, api_key="k")
    q = prov.get_quote("ASML.AS")
    assert q["fallback_used"] is True
    assert q["currency"] == "USD"


def test_twelvedata_breaker_open_returns_flagged_stub():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    prov = TwelveDataProvider(stub_mode=False, api_key="k", breaker=breaker)
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q.get("circuit_open") is True


def test_twelvedata_network_failure_falls_back_flagged():
    prov = TwelveDataProvider(stub_mode=False, api_key="k")

    def _boom(symbol, market=None):
        raise ProviderError("twelvedata", "boom")

    prov._fetch_raw = _boom  # type: ignore[method-assign]
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q["source"] == "twelvedata"


# -- Stooq daily-quota body ----------------------------------------------------


def test_stooq_daily_quota_body_degrades_flagged(monkeypatch):
    """HTTP 200 + 'Exceeded the daily hits limit' reads as rate-limited."""

    class _Resp:
        status_code = 200
        text = "Exceeded the daily hits limit."

    class _FakeHttpx:
        @staticmethod
        def get(url, timeout=None, follow_redirects=True):
            return _Resp()

    monkeypatch.setitem(sys.modules, "httpx", _FakeHttpx())
    prov = StooqProvider(stub_mode=False)
    q = prov.get_quote("AAPL")
    assert q["source"] == "stooq"
    assert q["fallback_used"] is True


# -- service chain -------------------------------------------------------------


def _live_yf_raw(symbol: str) -> dict:
    return {
        "symbol": symbol, "price": 232.50, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 54_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _live_finnhub_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 233.20, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": None,
        "currency": "USD", "as_of": _utcnow(),
    }


def _live_twelvedata_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 233.30, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 12_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _boom_yf(symbol: str) -> dict:
    raise ProviderError("yfinance", "down")


def _all_stub_service() -> MarketDataService:
    from backend.market_data.providers.alpaca import AlpacaProvider

    return MarketDataService(
        provider=YFinanceProvider(stub_mode=True),
        alpaca_provider=AlpacaProvider(stub_mode=True),
        finnhub_provider=FinnhubProvider(stub_mode=True),
        twelvedata_provider=TwelveDataProvider(stub_mode=True),
        cache=None,
    )


def test_service_finnhub_live_wins_when_yfinance_fails(isolated_db):
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _boom_yf  # type: ignore[method-assign]
    finnhub = FinnhubProvider(stub_mode=False, api_key="k")
    finnhub._fetch_raw = _live_finnhub_raw  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        finnhub_provider=finnhub,
        twelvedata_provider=TwelveDataProvider(stub_mode=True),
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "finnhub"
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 0
    assert out["price"] == 233.20


def test_service_twelvedata_live_wins_when_yfinance_and_finnhub_fail(isolated_db):
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _boom_yf  # type: ignore[method-assign]
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _live_twelvedata_raw  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        finnhub_provider=FinnhubProvider(stub_mode=True),
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "twelvedata"
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 0
    assert out["price"] == 233.30


def test_service_full_outage_with_new_stubs_raises_provider_error(isolated_db):
    # Fail-closed: all fallback-flagged stubs are refused -> ProviderError.
    with pytest.raises(ProviderError):
        _all_stub_service().get_quote("AAPL")


def test_service_sse_never_touches_finnhub_or_twelvedata(isolated_db):
    calls: list[str] = []

    def _spy(symbol: str, market=None) -> dict:
        calls.append(symbol)
        raise ProviderError("unreachable", "must not be called for SSE")

    def _live_yf_cny(symbol: str) -> dict:
        return {
            "symbol": symbol, "price": 1685.0, "open": 1678.0, "high": 1690.0,
            "low": 1675.0, "prev_close": 1680.0, "volume": 3_100_000,
            "currency": "CNY", "as_of": _utcnow(),
        }

    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_cny  # type: ignore[method-assign]
    finnhub = FinnhubProvider(stub_mode=False, api_key="k")
    finnhub._fetch_raw = _spy  # type: ignore[method-assign]
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _spy  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("600519.SS")
    assert out["currency"] == "CNY"
    assert calls == []
    assert out["provenance"]["source"] == "yfinance"
    assert out["provenance"]["fallback_used"] is False


def _live_wrap_get_quote(prov, source: str, price: float):
    """Wrap provider.get_quote to return live-shaped dicts with a distinct source.

    Keeps chain-precedence coverage explicit under fail-closed: each double
    is live (fallback_used False, price present) with its own source marker.
    """
    def _live(symbol: str, market=None) -> dict:
        upper = (symbol or "").strip().upper() or "AAPL"
        return {
            "symbol": upper,
            "price": price,
            "open": price - 1.0,
            "high": price + 1.0,
            "low": price - 2.0,
            "prev_close": price - 2.5,
            "volume": 1_000_000,
            "currency": "USD",
            "as_of": _utcnow(),
            "source": source,
            "missing_fields": [],
            "fallback_used": False,
        }

    prov.get_quote = _live  # type: ignore[method-assign]
    return prov


def test_service_full_chain_precedence_alpaca_first(isolated_db):
    """alpaca > yfinance > finnhub > twelvedata when all live."""
    from backend.market_data.providers.alpaca import AlpacaProvider

    yf = _live_wrap_get_quote(YFinanceProvider(stub_mode=False), "yfinance", 232.50)
    alpaca = _live_wrap_get_quote(
        AlpacaProvider(stub_mode=False, api_key="k", api_secret="s"), "alpaca", 233.10
    )
    finnhub = _live_wrap_get_quote(
        FinnhubProvider(stub_mode=False, api_key="k"), "finnhub", 233.20
    )
    td = _live_wrap_get_quote(
        TwelveDataProvider(stub_mode=False, api_key="k"), "twelvedata", 233.30
    )
    svc = MarketDataService(
        provider=yf, alpaca_provider=alpaca, finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "alpaca"
    assert out["price"] == 233.10
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 0


def test_service_chain_precedence_yfinance_over_free_tiers(isolated_db):
    """yfinance wins over finnhub/twelvedata when alpaca is out."""
    from backend.market_data.providers.alpaca import AlpacaProvider

    yf = _live_wrap_get_quote(YFinanceProvider(stub_mode=False), "yfinance", 232.50)
    finnhub = _live_wrap_get_quote(
        FinnhubProvider(stub_mode=False, api_key="k"), "finnhub", 233.20
    )
    td = _live_wrap_get_quote(
        TwelveDataProvider(stub_mode=False, api_key="k"), "twelvedata", 233.30
    )
    svc = MarketDataService(
        provider=yf,
        alpaca_provider=AlpacaProvider(stub_mode=True),
        finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "yfinance"
    assert out["price"] == 232.50
    assert out["provenance"]["fallback_used"] is False


def test_service_chain_precedence_finnhub_over_twelvedata(isolated_db):
    """finnhub wins over twelvedata when alpaca+yfinance miss."""
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _boom_yf  # type: ignore[method-assign]
    # yfinance failure degrades to a flagged stub internally; the service
    # refuses it and moves down-chain, so finnhub (live) must win over a
    # live twelvedata leg.
    finnhub = _live_wrap_get_quote(
        FinnhubProvider(stub_mode=False, api_key="k"), "finnhub", 233.20
    )
    td = _live_wrap_get_quote(
        TwelveDataProvider(stub_mode=False, api_key="k"), "twelvedata", 233.30
    )
    svc = MarketDataService(
        provider=yf, finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "finnhub"
    assert out["price"] == 233.20
    assert out["provenance"]["fallback_used"] is False


def test_service_chain_precedence_twelvedata_terminal(isolated_db):
    """twelvedata wins as the terminal leg when alpaca+yfinance+finnhub miss."""
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _boom_yf  # type: ignore[method-assign]
    td = _live_wrap_get_quote(
        TwelveDataProvider(stub_mode=False, api_key="k"), "twelvedata", 233.30
    )
    svc = MarketDataService(
        provider=yf,
        finnhub_provider=FinnhubProvider(stub_mode=True),
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "twelvedata"
    assert out["price"] == 233.30
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 0


def test_service_euronext_skips_us_only_free_providers(isolated_db):
    """Euronext is yfinance-served; US-only free tiers are never queried."""
    yf = YFinanceProvider(stub_mode=False)

    def _live_yf_euronext(symbol: str) -> dict:
        return {
            "symbol": symbol, "price": 715.50, "open": 712.0, "high": 718.2,
            "low": 710.4, "prev_close": 711.30, "volume": 480_000,
            "currency": "EUR", "as_of": _utcnow(),
        }

    yf._fetch_raw = _live_yf_euronext  # type: ignore[method-assign]
    calls: list[str] = []

    def _spy(symbol: str, market=None) -> dict:
        calls.append(symbol)
        return _live_finnhub_raw(symbol, market)

    finnhub = FinnhubProvider(stub_mode=False, api_key="k")
    finnhub._fetch_raw = _spy  # type: ignore[method-assign]
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _spy  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf, finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("MC.PA")
    assert out["provenance"]["source"] == "yfinance"
    assert out["currency"] == "EUR"
    assert calls == []  # US-only free tiers never queried for Euronext


def test_service_chain_short_circuits_new_providers_on_yfinance_live(isolated_db):
    """One live feed costs one call: finnhub/td untouched when yf is live."""
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    calls: list[str] = []

    def _spy(name: str):
        def _inner(symbol: str, market=None) -> dict:
            calls.append(f"{name}:{symbol}")
            return _live_finnhub_raw(symbol, market)

        return _inner

    finnhub = FinnhubProvider(stub_mode=False, api_key="k")
    finnhub._fetch_raw = _spy("finnhub")  # type: ignore[method-assign]
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _spy("twelvedata")  # type: ignore[method-assign]
    from backend.market_data.providers.alpaca import AlpacaProvider

    svc = MarketDataService(
        provider=yf,
        alpaca_provider=AlpacaProvider(stub_mode=True),
        finnhub_provider=finnhub,
        twelvedata_provider=td,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "yfinance"
    assert calls == []


def test_service_tier_params_accepted_and_inert(isolated_db):
    """Future per-user tier routing hooks: accepted, no behavior change today."""
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    svc = MarketDataService(provider=yf, cache=None)
    out = svc.get_quote("AAPL", user_id="user-123", tier="Silver")
    assert out["provenance"]["source"] == "yfinance"
    assert out["provenance"]["fallback_used"] is False
    many = svc.get_quotes_many(["AAPL"], user_id="user-123", tier="Free")
    assert set(many) == {"AAPL"}


def test_service_breaker_isolation_across_four_providers():
    from backend.market_data.providers.alpaca import AlpacaProvider

    providers = [
        YFinanceProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1)),
        AlpacaProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1)),
        FinnhubProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1)),
        TwelveDataProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1)),
    ]
    breakers = {p.breaker for p in providers}
    assert len(breakers) == 4  # independent breakers
    providers[2].breaker.record_failure()
    assert providers[2].breaker.state == "open"
    assert all(p.breaker.state == "closed" for i, p in enumerate(providers) if i != 2)


# -- wiring --------------------------------------------------------------------


def test_package_exports_free_providers():
    from backend.market_data.providers import FinnhubProvider as F
    from backend.market_data.providers import TwelveDataProvider as T

    assert F is FinnhubProvider
    assert T is TwelveDataProvider


def test_dashboard_knows_free_providers():
    from backend.observability.dashboard import KNOWN_PROVIDERS, build_dashboard

    assert "finnhub" in KNOWN_PROVIDERS
    assert "twelvedata" in KNOWN_PROVIDERS
    body = build_dashboard()
    names = {p["provider"] for p in body["providers"]}
    assert {"finnhub", "twelvedata"} <= names


def test_probe_allow_list_dropped_providers():
    """akshare/stooq dropped from the probe allow-list with the chain."""
    from backend.api.providers import MARKET_DATA_PROBE_PROVIDERS

    assert set(MARKET_DATA_PROBE_PROVIDERS) == {"yfinance", "alpaca"}
