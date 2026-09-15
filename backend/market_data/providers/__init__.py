"""Provider package (yfinance default free source; AKShare SSE fallback).

Milestone 0 live-data extension (opt-in, additive):
- ``AlpacaProvider`` — free real-time US (IEX feed, ``delay_minutes=0``).
  Requires ``ALPACA_API_KEY_ID`` + ``ALPACA_API_SECRET_KEY``; unconfigured
  instances degrade to flagged stubs, never raise into the chain.
- ``StooqProvider`` — free delayed gap-filler (US + Euronext, no key).
- ``FinnhubProvider`` — free-tier real-time US (``delay_minutes=0``).
  Requires ``FINNHUB_API_KEY``; unconfigured instances degrade to flagged
  stubs. US-only on the free tier (international is EOD-only).
- ``TwelveDataProvider`` — free Basic-tier real-time US
  (``delay_minutes=0``, 8 credits/min + 800/day). Requires
  ``TWELVEDATA_API_KEY``; unconfigured/quota-exhausted instances degrade
  to flagged stubs. US-only on the free tier.
"""

from backend.market_data.providers.yfinance import YFinanceProvider

try:
    from backend.market_data.providers.akshare import AKShareProvider
except Exception:  # pragma: no cover - optional dep / partial checkout
    AKShareProvider = None  # type: ignore[assignment]

try:
    from backend.market_data.providers.alpaca import AlpacaProvider
except Exception:  # pragma: no cover - partial checkout
    AlpacaProvider = None  # type: ignore[assignment]

try:
    from backend.market_data.providers.stooq import StooqProvider
except Exception:  # pragma: no cover - partial checkout
    StooqProvider = None  # type: ignore[assignment]

try:
    from backend.market_data.providers.finnhub_free import FinnhubProvider
except Exception:  # pragma: no cover - partial checkout
    FinnhubProvider = None  # type: ignore[assignment]

try:
    from backend.market_data.providers.twelvedata_free import TwelveDataProvider
except Exception:  # pragma: no cover - partial checkout
    TwelveDataProvider = None  # type: ignore[assignment]

__all__ = [
    "YFinanceProvider",
    "AKShareProvider",
    "AlpacaProvider",
    "StooqProvider",
    "FinnhubProvider",
    "TwelveDataProvider",
]
