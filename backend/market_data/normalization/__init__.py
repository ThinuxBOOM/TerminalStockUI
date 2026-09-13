"""Normalization: provider raw payloads -> canonical quote/bars schema.

Missing data is flagged in missing_fields ("unavailable", never zero-filled).
"""

from __future__ import annotations

from datetime import datetime, timezone

QUOTE_FIELDS = ("price", "open", "high", "low", "prev_close", "volume", "currency")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_quote(raw: dict, *, source: str, delay_minutes: int = 15) -> dict:
    """Normalize a raw provider quote dict to the canonical schema."""
    raw = dict(raw or {})
    symbol = str(raw.get("symbol", "")).upper()
    currency = raw.get("currency")

    quote: dict = {
        "symbol": symbol,
        "price": raw.get("price"),
        "open": raw.get("open"),
        "high": raw.get("high"),
        "low": raw.get("low"),
        "prev_close": raw.get("prev_close"),
        "volume": raw.get("volume"),
        "currency": currency,
        "as_of": raw.get("as_of") or _utcnow(),
        "source": source,
    }
    missing = [f for f in QUOTE_FIELDS if quote.get(f) is None]
    quote["missing_fields"] = missing
    quote["delay_minutes"] = delay_minutes
    if quote["price"] is not None and quote.get("prev_close"):
        try:
            quote["change"] = quote["price"] - quote["prev_close"]
            quote["change_pct"] = quote["change"] / quote["prev_close"] * 100
        except (TypeError, ZeroDivisionError):
            quote["change"] = None
            quote["change_pct"] = None
            if "change" not in missing:
                missing.append("change")
    else:
        quote["change"] = None
        quote["change_pct"] = None
    return quote
