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
