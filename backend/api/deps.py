"""Shared FastAPI dependencies (singletons; override in tests)."""

from __future__ import annotations

from ..cache import get_cache
from ..instruments.registry import InstrumentRegistry
from ..market_data.health import ProviderHealthTracker
from ..market_data.providers.yfinance import YFinanceProvider
from ..market_data.service import MarketDataService

try:  # Milestone 0 live-data chain (opt-in; missing -> skipped, never crash)
    from ..market_data.providers.alpaca import AlpacaProvider
except Exception:  # pragma: no cover
    AlpacaProvider = None  # type: ignore[assignment]



try:  # Free-tier US redundancy (opt-in; missing -> skipped, never crash)
    from ..market_data.providers.finnhub_free import FinnhubProvider
except Exception:  # pragma: no cover
    FinnhubProvider = None  # type: ignore[assignment]

try:
    from ..market_data.providers.twelvedata_free import TwelveDataProvider
except Exception:  # pragma: no cover
    TwelveDataProvider = None  # type: ignore[assignment]

_registry: InstrumentRegistry | None = None
_health: ProviderHealthTracker | None = None
_service: MarketDataService | None = None


def get_registry() -> InstrumentRegistry:
    global _registry
    if _registry is None:
        _registry = InstrumentRegistry()
    return _registry


def get_health_tracker() -> ProviderHealthTracker:
    global _health
    if _health is None:
        _health = ProviderHealthTracker()
    return _health


def _tracker_hook(tracker):  # type: ignore[no-untyped-def]
    """Quota-aware passive hook: fn(p, ms, ok, *, status_code, error)."""

    def _record(p, ms, ok, **kw):  # type: ignore[no-untyped-def]
        try:
            tracker.record(p, ms, ok,
                           status_code=kw.get("status_code"), error=kw.get("error"),
                           quota_limited=kw.get("quota_limited"))
        except TypeError:
            try:
                tracker.record(p, ms, ok)
            except Exception:
                pass
        except Exception:
            pass

    return _record


def get_market_service() -> MarketDataService:
    global _service
    if _service is None:
        tracker = get_health_tracker()
        hook = _tracker_hook(tracker)
        provider = YFinanceProvider(on_call=hook, timeout_s=10.0)
        # Milestone 0 chain: Alpaca live US (keys via env; unconfigured ->
        # flagged stubs). Each has an independent breaker; failures never
        # take down yfinance.
        # Free-tier US redundancy: Finnhub (FINNHUB_API_KEY) + TwelveData
        # (TWELVEDATA_API_KEY); unconfigured -> flagged stubs, skipped cost
        # is one stub call each only when yfinance is not live.
        alpaca = None
        if AlpacaProvider is not None:
            try:
                alpaca = AlpacaProvider(on_call=hook)
            except Exception:
                alpaca = None
        finnhub = None
        if FinnhubProvider is not None:
            try:
                finnhub = FinnhubProvider(on_call=hook)
            except Exception:
                finnhub = None
        twelvedata = None
        if TwelveDataProvider is not None:
            try:
                twelvedata = TwelveDataProvider(on_call=hook)
            except Exception:
                twelvedata = None
        _service = MarketDataService(registry=get_registry(), provider=provider,
                                     health=tracker, cache=get_cache(),
                                     alpaca_provider=alpaca,
                                     finnhub_provider=finnhub,
                                     twelvedata_provider=twelvedata)
    return _service


def reset_deps() -> None:  # test hook
    global _registry, _health, _service
    _registry, _health, _service = None, None, None
