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

try:
    from ..market_data.providers.stooq import StooqProvider
except Exception:  # pragma: no cover
    StooqProvider = None  # type: ignore[assignment]

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
        # Milestone 0 chain: Alpaca live US (keys via env; unconfigured ->
        # flagged stubs) + Stooq delayed gap-filler (no key). Each has an
        # independent breaker; failures never take down yfinance/AKShare.
        alpaca = None
        if AlpacaProvider is not None:
            try:
                alpaca = AlpacaProvider(
                    on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
                )
            except Exception:
                alpaca = None
        stooq = None
        if StooqProvider is not None:
            try:
                stooq = StooqProvider(
                    on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
                )
            except Exception:
                stooq = None
        _service = MarketDataService(registry=get_registry(), provider=provider,
                                     health=tracker, cache=get_cache(),
                                     alpaca_provider=alpaca, stooq_provider=stooq)
    return _service


def reset_deps() -> None:  # test hook
    global _registry, _health, _service
    _registry, _health, _service = None, None, None
