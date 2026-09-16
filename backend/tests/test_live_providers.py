"""Milestone 0 live-data chain tests (no network).

Covers:
- Stooq symbol mapping (US/Euronext) + CSV parse + N/D handling
  (dormant module: mapping/parse unit tests only, not in the chain)
- Stooq stub_mode / breaker / empty-symbol contract (dormant module)
- Alpaca key resolution, US-only guard, snapshot parse, missing-keys fallback
- Alpaca secrets never leak into quotes
- Service chain: alpaca live wins (delay 0) -> yfinance;
  full outage raises (fail-closed); SSE never touches alpaca
- Package exports, dashboard rows, probe allow-list
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.market_data.providers.alpaca import (
    AlpacaProvider,
    parse_snapshot,
    resolve_feed,
    to_alpaca_symbol,
)
from backend.market_data.providers.base import CircuitBreaker, ProviderError
from backend.market_data.providers.stooq import (
    StooqProvider,
    currency_for_stooq,
    parse_stooq_csv,
    to_stooq_symbol,
)
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
    """Convert a stub quote dict into a live-shaped double.

    Providers still return fallback-flagged dicts in stub_mode; the service
    refuses them. Tests clear the flags to build live doubles.
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


# -- Stooq symbol mapping ---------------------------------------------------


def test_stooq_symbol_us_and_euronext():
    assert to_stooq_symbol("AAPL") == "aapl.us"
    assert to_stooq_symbol("aapl", market="XNAS") == "aapl.us"
    assert to_stooq_symbol("MC.PA") == "mc.fr"
    assert to_stooq_symbol("ASML.AS") == "asml.nl"
    assert to_stooq_symbol("UCB.BR") == "ucb.be"
    # Explicit suffix wins over mic; bare symbols follow the mic.
    assert to_stooq_symbol("MC.PA", market="XNAS") == "mc.fr"
    assert to_stooq_symbol("AAPL", market="XPAR") == "aapl.fr"


def test_stooq_symbol_empty_raises():
    with pytest.raises(ProviderError):
        to_stooq_symbol("")
    with pytest.raises(ProviderError):
        to_stooq_symbol("   ")


def test_stooq_currency_map():
    assert currency_for_stooq("aapl.us") == "USD"
    assert currency_for_stooq("mc.fr") == "EUR"
    assert currency_for_stooq("asml.nl") == "EUR"
    assert currency_for_stooq("ucb.be") == "EUR"


def test_stooq_csv_parse_live_row():
    text = (
        "Symbol,Date,Time,Open,High,Low,Close,Volume\n"
        "AAPL.US,2026-09-11,22:00:00,231.00,233.80,230.10,232.50,54000000\n"
    )
    raw = parse_stooq_csv(text, symbol="AAPL", stooq_symbol="aapl.us")
    assert raw["symbol"] == "AAPL"
    assert raw["price"] == 232.50
    assert raw["open"] == 231.00
    assert raw["volume"] == 54_000_000
    assert raw["currency"] == "USD"
    assert raw["prev_close"] is None  # never fabricated
    assert isinstance(raw["as_of"], datetime)


def test_stooq_csv_nd_raises():
    text = "Symbol,Date,Time,Open,High,Low,Close,Volume\nAAPL.US,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
    with pytest.raises(ProviderError):
        parse_stooq_csv(text, symbol="AAPL", stooq_symbol="aapl.us")


def test_stooq_csv_empty_raises():
    with pytest.raises(ProviderError):
        parse_stooq_csv("", symbol="AAPL", stooq_symbol="aapl.us")


def test_stooq_stub_mode_flagged():
    prov = StooqProvider(stub_mode=True)
    q = prov.get_quote("AAPL")
    assert q["source"] == "stooq"
    assert q["fallback_used"] is True
    assert q["delay_minutes"] == 15
    assert q["price"] is not None


def test_stooq_euronext_stub_currency():
    prov = StooqProvider(stub_mode=True)
    q = prov.get_quote("MC.PA")
    assert q["symbol"] == "MC.PA"
    assert q["currency"] == "EUR"


def test_stooq_empty_symbol_raises():
    prov = StooqProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_quote("")
    with pytest.raises(ProviderError):
        prov.get_quote("   ")


def test_stooq_breaker_open_returns_flagged_stub():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure()
    prov = StooqProvider(stub_mode=False, breaker=breaker)
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q.get("circuit_open") is True


def test_stooq_network_failure_falls_back_flagged(monkeypatch):
    prov = StooqProvider(stub_mode=False)

    def _boom(symbol, market=None):
        raise ProviderError("stooq", "boom")

    monkeypatch.setattr(prov, "_fetch_raw", _boom)
    q = prov.get_quote("AAPL")
    assert q["fallback_used"] is True
    assert q["source"] == "stooq"


# -- Alpaca ------------------------------------------------------------------


def test_alpaca_symbol_us_only():
    assert to_alpaca_symbol("AAPL") == "AAPL"
    assert to_alpaca_symbol("brk-b") == "BRK.B"
    for bad in ("600519.SS", "MC.PA", "ASML.AS", "UCB.BR"):
        with pytest.raises(ProviderError):
            to_alpaca_symbol(bad)
    with pytest.raises(ProviderError):
        to_alpaca_symbol("")


def test_alpaca_feed_resolution(monkeypatch):
    monkeypatch.delenv("ALPACA_FEED", raising=False)
    assert resolve_feed(None) == "iex"
    assert resolve_feed("sip") == "sip"
    assert resolve_feed("nope") == "iex"
    monkeypatch.setenv("ALPACA_FEED", "sip")
    assert resolve_feed(None) == "sip"


def test_alpaca_missing_keys_degrades_to_stub(monkeypatch):
    for var in (
        "ALPACA_API_KEY_ID", "APCA_API_KEY_ID", "ALPACA_API_KEY",
        "ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    prov = AlpacaProvider(stub_mode=False)
    assert prov.configured is False
    q = prov.get_quote("AAPL")
    assert q["source"] == "alpaca"
    assert q["fallback_used"] is True
    assert q["delay_minutes"] == 0
    assert q["currency"] == "USD"


def test_alpaca_stub_never_leaks_secrets(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key-123")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret-456")
    prov = AlpacaProvider(stub_mode=True)
    q = prov.get_quote("AAPL")
    blob = repr(q)
    assert "key-123" not in blob
    assert "secret-456" not in blob
    assert q["currency"] == "USD"


def test_alpaca_non_us_falls_back_not_live():
    prov = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    q = prov.get_quote("MC.PA")
    assert q["fallback_used"] is True
    assert q["currency"] == "USD"  # US-only feed never serves EUR


def test_alpaca_empty_symbol_raises():
    prov = AlpacaProvider(stub_mode=True, api_key="k", api_secret="s")
    with pytest.raises(ProviderError):
        prov.get_quote("")


def test_alpaca_parse_snapshot():
    payload = {
        "symbol": "AAPL",
        "latestTrade": {"p": 233.10, "t": "2026-09-12T14:30:00.123456789Z"},
        "dailyBar": {"o": 231.0, "h": 233.8, "l": 230.1, "c": 233.05, "v": 54_000_000},
        "prevDailyBar": {"c": 230.75},
    }
    raw = parse_snapshot("AAPL", payload)
    assert raw["price"] == 233.10
    assert raw["open"] == 231.0
    assert raw["prev_close"] == 230.75
    assert raw["volume"] == 54_000_000
    assert raw["currency"] == "USD"
    assert isinstance(raw["as_of"], datetime)


def test_alpaca_parse_snapshot_trade_fallback_to_daily_bar():
    payload = {
        "latestTrade": {},
        "dailyBar": {"o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100},
        "prevDailyBar": {},
    }
    raw = parse_snapshot("MSFT", payload)
    assert raw["price"] == 1.5


def test_alpaca_parse_snapshot_no_price_raises():
    with pytest.raises(ProviderError):
        parse_snapshot("AAPL", {"latestTrade": {}, "dailyBar": {}})


def _live_alpaca_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 233.10, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 54_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _live_yf_raw(symbol: str) -> dict:
    return {
        "symbol": symbol, "price": 232.50, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 54_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


# -- service chain ------------------------------------------------------------


def test_service_alpaca_live_wins_with_delay_zero(isolated_db):
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    alpaca = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    svc = MarketDataService(provider=yf, alpaca_provider=alpaca)
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "alpaca"
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 0
    assert out["price"] == 233.10


def test_service_yfinance_live_wins_when_alpaca_unconfigured(isolated_db, monkeypatch):
    for var in (
        "ALPACA_API_KEY_ID", "APCA_API_KEY_ID", "ALPACA_API_KEY",
        "ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY", "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        alpaca_provider=AlpacaProvider(stub_mode=False),
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "yfinance"
    assert out["provenance"]["fallback_used"] is False
    assert out["provenance"]["delay_minutes"] == 15


def test_service_yfinance_failure_raises_when_no_chain(isolated_db):
    """yfinance down with no live chain left (stooq dropped) -> fail-closed."""
    yf = YFinanceProvider(stub_mode=False)

    def _yf_boom(symbol: str) -> dict:
        raise ProviderError("yfinance", "down")

    yf._fetch_raw = _yf_boom  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        alpaca_provider=AlpacaProvider(stub_mode=True),
    )
    with pytest.raises(ProviderError):
        svc.get_quote("AAPL")


def test_service_euronext_skips_alpaca_uses_yfinance(isolated_db):
    """Euronext is yfinance-served; US-only alpaca is skipped."""
    yf = YFinanceProvider(stub_mode=False)

    def _live_yf_euronext(symbol: str) -> dict:
        return {
            "symbol": symbol, "price": 715.50, "open": 712.0, "high": 718.2,
            "low": 710.4, "prev_close": 711.30, "volume": 480_000,
            "currency": "EUR", "as_of": _utcnow(),
        }

    yf._fetch_raw = _live_yf_euronext  # type: ignore[method-assign]
    alpaca = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    svc = MarketDataService(provider=yf, alpaca_provider=alpaca)
    out = svc.get_quote("MC.PA")
    assert out["provenance"]["source"] == "yfinance"
    assert out["currency"] == "EUR"
    assert out["provenance"]["fallback_used"] is False


def test_service_full_outage_raises_provider_error(isolated_db):
    # Fail-closed: all stubs (fallback-flagged) are refused -> ProviderError.
    svc = MarketDataService(
        provider=YFinanceProvider(stub_mode=True),
        alpaca_provider=AlpacaProvider(stub_mode=True),
    )
    with pytest.raises(ProviderError):
        svc.get_quote("AAPL")


def test_service_without_opt_in_raises_when_only_stub(isolated_db):
    # No opt-in chain + stub-only yfinance -> raises (no fallback cover).
    svc = MarketDataService(provider=YFinanceProvider(stub_mode=True))
    with pytest.raises(ProviderError):
        svc.get_quote("AAPL")


def test_service_refuses_fallback_flagged_quote_with_price(isolated_db):
    # Lingering fallback_used=True on the winning quote also raises, even
    # when a price is present (stub-shaped doubles are never served).
    yf = YFinanceProvider(stub_mode=True)
    stub = yf.get_quote("AAPL")
    assert stub["fallback_used"] is True
    assert stub["price"] is not None
    live = _liveify_stub(stub, source="yfinance")
    assert live["fallback_used"] is False
    svc = MarketDataService(
        provider=YFinanceProvider(stub_mode=True),
        alpaca_provider=AlpacaProvider(stub_mode=True),
    )
    with pytest.raises(ProviderError):
        svc.get_quote("AAPL")


def test_service_sse_never_touches_alpaca(isolated_db):
    # SSE path is yfinance(.SS) only; alpaca is never called even when live.
    # yfinance serves a live CNY-shaped double.
    yf = YFinanceProvider(stub_mode=False)

    def _live_yf_cny(symbol: str) -> dict:
        return {
            "symbol": symbol, "price": 1685.0, "open": 1678.0, "high": 1690.0,
            "low": 1675.0, "prev_close": 1680.0, "volume": 3_100_000,
            "currency": "CNY", "as_of": _utcnow(),
        }

    yf._fetch_raw = _live_yf_cny  # type: ignore[method-assign]
    alpaca = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    alp_calls: list[str] = []
    _orig_alp_get = alpaca.get_quote

    def _alp_spy(symbol: str, market=None) -> dict:
        alp_calls.append(symbol)
        return _orig_alp_get(symbol)

    alpaca.get_quote = _alp_spy  # type: ignore[method-assign]
    svc = MarketDataService(provider=yf, alpaca_provider=alpaca)
    out = svc.get_quote("600519.SS")
    assert out["currency"] == "CNY"
    assert out["provenance"]["source"] == "yfinance"
    assert out["provenance"]["fallback_used"] is False
    assert alp_calls == []


def test_service_chain_short_circuits_on_first_live(isolated_db):
    """One live feed costs one call: alpaca stub is never hit when yfinance is live."""
    from backend.market_data.providers.finnhub_free import FinnhubProvider

    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    finnhub = FinnhubProvider(stub_mode=False, api_key="k")
    calls: list[str] = []

    def _fh_spy(symbol: str, market=None) -> dict:
        calls.append(symbol)
        raise ProviderError("finnhub", "must not be called when yfinance is live")

    finnhub._fetch_raw = _fh_spy  # type: ignore[method-assign]
    svc = MarketDataService(
        provider=yf,
        alpaca_provider=AlpacaProvider(stub_mode=True),
        finnhub_provider=finnhub,
    )
    out = svc.get_quote("AAPL")
    assert out["provenance"]["source"] == "yfinance"
    assert calls == []


def test_service_breaker_isolation_across_two_providers():
    yf = YFinanceProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1))
    alpaca = AlpacaProvider(stub_mode=True, breaker=CircuitBreaker(failure_threshold=1))
    assert yf.breaker is not alpaca.breaker
    yf.breaker.record_failure()
    assert yf.breaker.state == "open"
    assert alpaca.breaker.state == "closed"


# -- wiring --------------------------------------------------------------------


def test_package_exports_new_providers():
    from backend.market_data.providers import AlpacaProvider as A
    from backend.market_data.providers import StooqProvider as S

    assert A is AlpacaProvider
    assert S is StooqProvider


def test_dashboard_knows_new_providers():
    from backend.observability.dashboard import KNOWN_PROVIDERS, build_dashboard

    assert "alpaca" in KNOWN_PROVIDERS
    assert "stooq" not in KNOWN_PROVIDERS
    body = build_dashboard()
    names = {p["provider"] for p in body["providers"]}
    assert "alpaca" in names


def test_probe_allow_list():
    from backend.api.providers import MARKET_DATA_PROBE_PROVIDERS

    assert set(MARKET_DATA_PROBE_PROVIDERS) == {"yfinance", "alpaca"}


def test_probe_rejects_unknown_provider():
    from fastapi.testclient import TestClient

    from backend.api.deps import reset_deps
    from backend.api.main import create_app
    from backend.market_data.service import MarketDataService as _Svc

    reset_deps()
    stub = _Svc(provider=YFinanceProvider(stub_mode=True))
    from backend.api import deps as deps_module
    from backend.api.deps import get_market_service

    deps_module._service = stub
    deps_module._registry = stub.registry
    deps_module._health = stub.health
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: stub
    try:
        resp = TestClient(app).post("/api/providers/health/test", params={"provider": "nope"})
        assert resp.status_code == 422, resp.text
    finally:
        reset_deps()
