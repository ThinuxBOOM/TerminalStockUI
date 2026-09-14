"""Shared request validation + error sanitization (no new deps).

Symbols flow into upstream provider calls (``yf.Ticker(symbol)``) and cache
keys; unbounded free-form input is a DoS/amplification vector. All routers
validate through here:

  * ``validate_symbol`` — 1..32 chars, ``^[A-Z0-9.\\-/=]{1,32}$`` (covers
    US/SSE/Euronext suffix forms, FX pairs, index futures). Returns UPPER.
  * ``validate_timeframe`` — Literal ``1d|1wk|1mo`` (bars are daily; other
    values previously flowed into ``price_bars.timeframe`` unchecked).
  * ``sanitize_error`` — truncate reflected exception text to 200 chars,
    strip newlines/control chars so error envelopes cannot reflect raw
    input or connection strings.
"""

from __future__ import annotations

import re

_SYMBOL_RE = re.compile(r"^[A-Z0-9._\-/=]{1,32}$")
_VALID_TIMEFRAMES = ("1d", "1wk", "1mo")
_MAX_ERROR_LEN = 200


def validate_symbol(value: str, *, field: str = "symbol") -> str:
    try:
        text = str(value or "").strip().upper()
    except Exception:
        raise ValueError(f"{field} must be a non-empty string")
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    if len(text) > 32 or not _SYMBOL_RE.match(text):
        raise ValueError(
            f"{field} contains unsupported characters "
            "(want 1-32 of A-Z 0-9 . _ - / =)"
        )
    return text


def validate_symbols(values: list[str], *, field: str = "symbols", max_items: int = 100) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a non-empty list")
    if len(values) > max_items:
        raise ValueError(f"{field} capped at {max_items} items, got {len(values)}")
    return [validate_symbol(v, field=field) for v in values]


def validate_timeframe(value: str) -> str:
    try:
        text = str(value or "").strip()
    except Exception:
        raise ValueError(f"timeframe must be one of {list(_VALID_TIMEFRAMES)}")
    if text not in _VALID_TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {list(_VALID_TIMEFRAMES)}, got {value!r}")
    return text


def sanitize_error(exc: BaseException | object, *, prefix: str = "") -> str:
    """Single-line, truncated error detail (safe for wire envelopes)."""
    try:
        text = str(exc)
    except Exception:
        text = type(exc).__name__
    try:
        text = "".join(c if c.isprintable() else " " for c in text)
        text = " ".join(text.split())
    except Exception:
        text = type(exc).__name__
    if len(text) > _MAX_ERROR_LEN:
        text = text[:_MAX_ERROR_LEN].rstrip() + "…"
    if prefix:
        return f"{prefix}: {text}" if text else prefix
    return text or type(exc).__name__


__all__ = [
    "sanitize_error",
    "validate_symbol",
    "validate_symbols",
    "validate_timeframe",
]
