"""Shared input validation (single place for symbol-shaped input rules).

Symbols arrive from query params, path-adjacent lookups, and JSON bodies.
Downstream they feed the instrument registry, yfinance ticker names, and DB
filters — so reject anything outside the known ticker alphabet early with a
clean 422 instead of letting path-traversal / injection-shaped text travel
further. Allowed: letters, digits, dot, dash, colon (``600519.SS``,
``BRK-B``, ``XSHG:600519``); 1..32 chars after stripping.
"""

from __future__ import annotations

import re

from fastapi import HTTPException

SYMBOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-:]{0,31}$")
SYMBOL_MAX_LEN = 32


def validate_symbol(value: object, *, field: str = "symbol") -> str:
    """Strip/uppercase + alphabet check. Returns canonical UPPER form."""
    text = str(value or "").strip().upper() if isinstance(value, (str, int, float)) else ""
    if not text or len(text) > SYMBOL_MAX_LEN or SYMBOL_RE.match(text) is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must match {SYMBOL_RE.pattern} (1-{SYMBOL_MAX_LEN} chars)",
        )
    if ".." in text:
        raise HTTPException(status_code=422, detail=f"{field} is not a valid symbol")
    return text


def validate_instrument_id(value: object) -> str:
    """Instrument ids look like ``MIC-SYMBOL``; bound length, reject controls."""
    text = str(value or "").strip()
    if not text or len(text) > 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise HTTPException(status_code=422, detail="invalid instrument_id")
    if "/" in text or "\\" in text or ".." in text:
        raise HTTPException(status_code=422, detail="invalid instrument_id")
    return text


#: Bars timeframes the ``price_bars`` store serves. Anything else previously
#: flowed into ``PriceBar.timeframe`` unchecked (mixed-granularity rows).
VALID_TIMEFRAMES = ("1d", "1wk", "1mo")

#: Truncation bound for reflected error details (see :func:`sanitize_error`).
MAX_ERROR_LEN = 200


def validate_timeframe(value: object) -> str:
    """Accept only ``VALID_TIMEFRAMES`` (422 otherwise)."""
    try:
        text = str(value or "").strip()
    except Exception:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {list(VALID_TIMEFRAMES)}",
        ) from None
    if text not in VALID_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {list(VALID_TIMEFRAMES)}, got {value!r}",
        )
    return text


def validate_symbols(
    values: list[str], *, field: str = "symbols", max_items: int = 100
) -> list[str]:
    """Validate a batch body (cron ingest, FX rank): length cap + per-symbol."""
    if not isinstance(values, list) or not values:
        raise HTTPException(status_code=422, detail=f"{field} must be a non-empty list")
    if len(values) > max_items:
        raise HTTPException(
            status_code=422,
            detail=f"{field} capped at {max_items} items, got {len(values)}",
        )
    return [validate_symbol(v, field=field) for v in values]


def sanitize_error(exc: BaseException | object, *, prefix: str = "") -> str:
    """Single-line, truncated error detail (safe for wire envelopes).

    Strips control chars/newlines and truncates to ``MAX_ERROR_LEN`` so
    error envelopes cannot reflect raw input or connection strings.
    """
    try:
        text = str(exc)
    except Exception:
        text = type(exc).__name__
    try:
        text = "".join(c if c.isprintable() else " " for c in text)
        text = " ".join(text.split())
    except Exception:
        text = type(exc).__name__
    if len(text) > MAX_ERROR_LEN:
        text = text[:MAX_ERROR_LEN].rstrip() + "…"
    if prefix:
        return f"{prefix}: {text}" if text else prefix
    return text or type(exc).__name__
