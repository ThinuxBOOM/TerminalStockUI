"""Normalization: provider raw payloads -> canonical quote/bars schema.

Missing data is flagged in missing_fields ("unavailable", never zero-filled).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

QUOTE_FIELDS = ("price", "open", "high", "low", "prev_close", "volume", "currency")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_number(value: object) -> object:
    """Pass through finite numerics; map NaN/inf to None (JSON-safe).

    Non-numeric values (None, strings) pass through untouched so missing-
    field detection below keeps its existing semantics. Decimal/numpy
    numerics are coerced to float (finite) or None (non-finite) so no
    non-JSON numeric leaks through.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(number) else None
    if value is None or isinstance(value, str):
        return value
    # Decimal / numpy / other __float__ numerics: coerce safely.
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return value
    if not math.isfinite(number):
        return None
    return float(number)


def normalize_quote(raw: dict, *, source: str, delay_minutes: int = 15) -> dict:
    """Normalize a raw provider quote dict to the canonical schema."""
    raw = dict(raw or {})
    symbol = str(raw.get("symbol", "")).upper()
    currency = raw.get("currency")

    quote: dict = {
        "symbol": symbol,
        "price": _clean_number(raw.get("price")),
        "open": _clean_number(raw.get("open")),
        "high": _clean_number(raw.get("high")),
        "low": _clean_number(raw.get("low")),
        "prev_close": _clean_number(raw.get("prev_close")),
        "volume": _clean_number(raw.get("volume")),
        "currency": currency,
        "as_of": raw.get("as_of") or _utcnow(),
        "source": source,
    }
    missing = [f for f in QUOTE_FIELDS if quote.get(f) is None]
    quote["missing_fields"] = missing
    quote["delay_minutes"] = delay_minutes
    if quote["price"] is not None and quote.get("prev_close"):
        try:
            change = quote["price"] - quote["prev_close"]
            change_pct = change / quote["prev_close"] * 100
            if not (math.isfinite(float(change)) and math.isfinite(float(change_pct))):
                raise ValueError("non-finite change")
            quote["change"] = change
            quote["change_pct"] = change_pct
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            quote["change"] = None
            quote["change_pct"] = None
            if "change" not in missing:
                missing.append("change")
    else:
        quote["change"] = None
        quote["change_pct"] = None
    return quote
