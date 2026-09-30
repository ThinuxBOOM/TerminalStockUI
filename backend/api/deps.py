"""Shared FastAPI dependencies (singletons; override in tests)."""

from __future__ import annotations

from ..cache import get_cache
from ..instruments.registry import InstrumentRegistry
from ..market_data.health import ProviderHealthTracker
from ..market_data.providers.alpaca import AlpacaProvider
from ..market_data.providers.finnhub_free import FinnhubProvider
from ..market_data.providers.twelvedata_free import TwelveDataProvider
from ..market_data.providers.yfinance import YFinanceProvider
from ..market_data.service import MarketDataService

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


def _configured_or_none(factory, hook):  # type: ignore[no-untyped-def]
    """Instantiate an optional key-based provider only when its keys are set.

    An unconfigured provider in the chain would be called (and fail) on every
    quote, costing latency and showing as "degraded" instead of simply absent.
    """
    if factory is None:
        return None
    provider = factory(on_call=hook)
    return provider if bool(getattr(provider, "configured", False)) else None


def get_market_service() -> MarketDataService:
    """Quote chain: Alpaca (US, if keyed) -> yfinance -> Finnhub -> TwelveData
    (each optional provider only when its API key is configured)."""
    global _service
    if _service is None:
        tracker = get_health_tracker()
        hook = _tracker_hook(tracker)
        _service = MarketDataService(
            registry=get_registry(),
            provider=YFinanceProvider(on_call=hook, timeout_s=10.0),
            health=tracker,
            cache=get_cache(),
            alpaca_provider=_configured_or_none(AlpacaProvider, hook),
            finnhub_provider=_configured_or_none(FinnhubProvider, hook),
            twelvedata_provider=_configured_or_none(TwelveDataProvider, hook),
        )
    return _service


def reset_deps() -> None:  # test hook
    global _registry, _health, _service
    _registry, _health, _service = None, None, None
