"""Bars payloads as pandas frames indexed by exchange-local session day.

Indexing by session day (not the UTC timestamp) keeps a Shanghai or Paris
stock aligned with its own market index and with US series by calendar day.
"""

from __future__ import annotations

import pandas as pd

from backend.market_data.service import bar_session_day

#: Market series used for beta and the v4 "market" features, per venue.
#: US names use SPY (the series the v4 model is trained on).
MARKET_SYMBOL = {
    "XNYS": "SPY", "XNAS": "SPY", "XSHG": "000001.SS",
    "XPAR": "^FCHI", "XAMS": "^AEX", "XBRU": "^BFX",
}


def bars_frame(payload: dict) -> pd.DataFrame:
    """OHLCV floats indexed by session day; rows without a close are dropped."""
    rows = [r for r in (payload or {}).get("bars", []) or [] if isinstance(r, dict) and r.get("close") is not None]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(
        {
            "open": [r.get("open") for r in rows],
            "high": [r.get("high") for r in rows],
            "low": [r.get("low") for r in rows],
            "close": [r.get("close") for r in rows],
            "volume": [r.get("volume") or 0 for r in rows],
        },
        index=pd.DatetimeIndex([bar_session_day(r) for r in rows]),
    ).astype(float)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    # Missing OHLC on an otherwise valid close: fall back to the close.
    for col in ("open", "high", "low"):
        frame[col] = frame[col].fillna(frame["close"])
    return frame


def venue_of(payload: dict, symbol: str) -> str:
    """Exchange MIC from a bars/quote payload, guessing from the suffix if absent."""
    inst = (payload or {}).get("instrument") or {}
    mic = str(inst.get("exchange_mic") or "").upper()
    if mic:
        return mic
    s = symbol.upper()
    if s.endswith(".SS"):
        return "XSHG"
    if s.endswith(".PA"):
        return "XPAR"
    if s.endswith(".AS"):
        return "XAMS"
    if s.endswith(".BR"):
        return "XBRU"
    return "XNAS"


def market_symbol_for(mic: str) -> str:
    return MARKET_SYMBOL.get((mic or "").upper(), "SPY")
