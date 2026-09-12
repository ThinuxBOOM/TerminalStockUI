"""M7 FX package: dedicated FX source with full provenance (spec section 4).

Free-first Frankfurter/ECB source (:mod:`backend.market_data.fx.provider`)
with a deterministic ECB reference stub for offline use. Every FX response
carries the standard provenance envelope
``{source, as_of, delay_minutes, quality_grade, fallback_used, missing_fields}``.

Cross-market ranking/comparison is gated on fresh FX provenance — see
:func:`backend.market_data.fx.convert.require_fx_provenance`. No cross-market
rank/compare may run on stale (>24h) rates, or on fallback rates without an
explicit ``allow_fallback`` opt-in.
"""

from backend.market_data.fx.convert import (
    FXProvenanceMissing,
    compare_cross_market,
    convert,
    rank_cross_market,
    require_fx_provenance,
)
from backend.market_data.fx.provider import (
    BASE_URL,
    CACHE_TTL_S,
    DEFAULT_DELAY_MINUTES,
    LIVE_SOURCE,
    NAME,
    STUB_SOURCE,
    SUPPORTED_CURRENCIES,
    FXProvider,
)

__all__ = [
    "BASE_URL",
    "CACHE_TTL_S",
    "DEFAULT_DELAY_MINUTES",
    "FXProvenanceMissing",
    "FXProvider",
    "LIVE_SOURCE",
    "NAME",
    "STUB_SOURCE",
    "SUPPORTED_CURRENCIES",
    "compare_cross_market",
    "convert",
    "rank_cross_market",
    "require_fx_provenance",
]
