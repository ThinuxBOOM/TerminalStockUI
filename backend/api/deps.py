"""Shared FastAPI dependencies (singletons; override in tests)."""

from __future__ import annotations

from ..cache import get_cache
from ..instruments.registry import InstrumentRegistry
from ..market_data.health import ProviderHealthTracker
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


def get_market_service() -> MarketDataService:
    global _service
    if _service is None:
        tracker = get_health_tracker()
        provider = YFinanceProvider(on_call=lambda p, ms, ok: tracker.record(p, ms, ok))
        _service = MarketDataService(registry=get_registry(), provider=provider,
                                     health=tracker, cache=get_cache())
    return _service


def reset_deps() -> None:  # test hook
    global _registry, _health, _service
    _registry, _health, _service = None, None, None
