"""Provider package (yfinance default free source; AKShare SSE fallback)."""

from backend.market_data.providers.yfinance import YFinanceProvider

try:
    from backend.market_data.providers.akshare import AKShareProvider
except Exception:  # pragma: no cover - optional dep / partial checkout
    AKShareProvider = None  # type: ignore[assignment]

__all__ = ["YFinanceProvider", "AKShareProvider"]
