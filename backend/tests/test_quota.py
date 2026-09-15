"""Client-side quota tests: TwelveData 6/min + 600/day, Alpaca 150/min.

Fail-fast 429 (never wait out the window): over-cap providers raise
``ProviderError("... HTTP 429")`` so the service chain falls through to the
next live source. Offline only (``_fetch_raw`` faked, clocks patched).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.db.session import reset_engine
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.alpaca import AlpacaProvider
from backend.market_data.providers.base import ProviderError, QuotaLimiter
from backend.market_data.providers.twelvedata_free import TwelveDataProvider
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/quota.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    try:
        yield url
    finally:
        reset_engine()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _live_td_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 233.30, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 12_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _live_alpaca_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 233.10, "open": 232.0, "high": 233.8,
        "low": 231.5, "prev_close": 232.75, "volume": 9_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _live_yf_raw(symbol: str, market=None) -> dict:
    return {
        "symbol": symbol, "price": 232.50, "open": 231.0, "high": 233.8,
        "low": 230.1, "prev_close": 230.75, "volume": 54_000_000,
        "currency": "USD", "as_of": _utcnow(),
    }


def _teardown() -> None:
    reset_engine()


# -- limiter units ------------------------------------------------------------


def test_quota_minute_cap_and_refill(monkeypatch):
    import backend.market_data.providers.base as base_module

    now = [1000.0]
    monkeypatch.setattr(base_module.time, "monotonic", lambda: now[0])
    lim = QuotaLimiter(calls_per_minute=2, name="t")
    assert lim.acquire() is True
    assert lim.acquire() is True
    assert lim.acquire() is False
    assert lim.rejected == 1
    now[0] += 61.0  # window slides: budget back
    assert lim.acquire() is True
    assert lim.status()["minute_used"] == 1


def test_quota_day_cap_counts_utc_days():
    lim = QuotaLimiter(calls_per_minute=None, calls_per_day=2, name="t")
    assert lim.acquire() is True
    assert lim.acquire() is True
    assert lim.acquire() is False
    assert lim.rejected_day == 1
    # Stale day buckets never block today.
    lim2 = QuotaLimiter(calls_per_minute=None, calls_per_day=1, name="t2")
    lim2._day_counts["2000-01-01"] = 99
    assert lim2.acquire() is True


def test_quota_invalid_caps_disable_not_crash():
    lim = QuotaLimiter(calls_per_minute=0, calls_per_day=-3, name="t")
    assert lim.acquire() is True
    assert lim.status()["calls_per_minute"] is None
    assert lim.status()["calls_per_day"] is None


# -- provider enforcement -----------------------------------------------------


def test_twelvedata_sixth_call_ok_seventh_raises_429():
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _live_td_raw  # type: ignore[method-assign]
    for _ in range(6):
        q = td.get_quote("AAPL")
        assert q["fallback_used"] is False
    assert td.limiter.status()["minute_used"] == 6
    with pytest.raises(ProviderError) as exc:
        td.get_quote("AAPL")
    assert "429" in str(exc.value)
    assert exc.value.retryable is True
    assert td.limiter.status()["rejected"] == 1


def test_twelvedata_day_cap_enforced():
    td = TwelveDataProvider(
        stub_mode=False, api_key="k",
        limiter=QuotaLimiter(calls_per_minute=1000, calls_per_day=2,
                             name="twelvedata"),
    )
    td._fetch_raw = _live_td_raw  # type: ignore[method-assign]
    assert td.get_quote("AAPL")["fallback_used"] is False
    assert td.get_quote("MSFT")["fallback_used"] is False
    with pytest.raises(ProviderError) as exc:
        td.get_quote("NVDA")
    assert "429" in str(exc.value)


def test_alpaca_cap_150_per_minute():
    alpaca = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    assert alpaca.limiter.status()["calls_per_minute"] == 150
    assert alpaca.limiter.status()["calls_per_day"] is None
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    for _ in range(150):
        assert alpaca.get_quote("AAPL")["fallback_used"] is False
    with pytest.raises(ProviderError) as exc:
        alpaca.get_quote("AAPL")
    assert "429" in str(exc.value)


def test_quota_rejection_emits_no_fake_zero_sample():
    """Throttling is flow-control, not provider illness: no 0.0ms sample."""
    tracker = ProviderHealthTracker()
    td = TwelveDataProvider(
        stub_mode=False, api_key="k", on_call=lambda p, ms, ok, **kw: tracker.record(p, ms, ok),
        limiter=QuotaLimiter(calls_per_minute=1, name="twelvedata"),
    )
    td._fetch_raw = _live_td_raw  # type: ignore[method-assign]
    assert td.get_quote("AAPL")["fallback_used"] is False
    with pytest.raises(ProviderError):
        td.get_quote("AAPL")
    stats = tracker.stats("twelvedata")
    assert stats["total_calls"] == 1  # only the real call was sampled
    # The one sample is real (mocked fetch is instant, so it rounds to 0.0
    # at 1dp — but it exists, unlike a fabricated zero with no call behind it).
    assert stats["latency_p50_ms"] is not None


# -- chain behavior -----------------------------------------------------------


def _service(*, alpaca=None, yf=None, td=None) -> MarketDataService:
    tracker = ProviderHealthTracker()
    base_yf = yf or YFinanceProvider(stub_mode=True)
    return MarketDataService(
        registry=InstrumentRegistry(), provider=base_yf,
        health=tracker, cache=None,
        alpaca_provider=alpaca, twelvedata_provider=td,
    )


def test_capped_twelvedata_falls_through_to_yfinance(isolated_db):
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    td = TwelveDataProvider(stub_mode=False, api_key="k")
    td._fetch_raw = _live_td_raw  # type: ignore[method-assign]
    for _ in range(6):
        td.get_quote("AAPL")  # exhaust the minute budget
    try:
        svc = _service(yf=yf, td=td)
        out = svc.get_quote("AAPL")
        assert out["provenance"]["source"] == "yfinance"
        assert out["price"] == 232.50
        assert out["provenance"]["fallback_used"] is False
    finally:
        _teardown()


def test_alpaca_preferred_while_under_quota(isolated_db):
    """Alpaca-first order preserved: live alpaca wins, yfinance untouched."""
    yf_calls: list[str] = []

    def _yf_spy(symbol: str, market=None) -> dict:
        yf_calls.append(symbol)
        return _live_yf_raw(symbol, market)

    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _yf_spy  # type: ignore[method-assign]
    alpaca = AlpacaProvider(stub_mode=False, api_key="k", api_secret="s")
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    try:
        svc = _service(yf=yf, alpaca=alpaca)
        out = svc.get_quote("AAPL")
        assert out["provenance"]["source"] == "alpaca"
        assert out["price"] == 233.10
        assert yf_calls == []
    finally:
        _teardown()


def test_capped_alpaca_falls_through_to_yfinance(isolated_db):
    """150/min hit mid-session: quotes keep flowing via yfinance (delayed)."""
    yf = YFinanceProvider(stub_mode=False)
    yf._fetch_raw = _live_yf_raw  # type: ignore[method-assign]
    alpaca = AlpacaProvider(
        stub_mode=False, api_key="k", api_secret="s",
        limiter=QuotaLimiter(calls_per_minute=1, name="alpaca"),
    )
    alpaca._fetch_raw = _live_alpaca_raw  # type: ignore[method-assign]
    assert alpaca.get_quote("AAPL")["fallback_used"] is False
    try:
        svc = _service(yf=yf, alpaca=alpaca)
        out = svc.get_quote("AAPL")
        assert out["provenance"]["source"] == "yfinance"
        assert out["provenance"]["fallback_used"] is False
    finally:
        _teardown()
