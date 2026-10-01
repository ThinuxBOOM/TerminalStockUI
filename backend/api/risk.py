"""Risk analytics endpoints: one symbol, or a portfolio of holdings.

GET  /api/risk/{symbol}       volatility, drawdowns, VaR/CVaR, beta, Sharpe, liquidity
POST /api/risk/portfolio      portfolio volatility, VaR, risk contributions, correlations

Computed from stored daily bars (up to ~4 years); fails closed with 502
when a series is unavailable instead of returning partial numbers silently.
"""

from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.api.deps import get_market_service
from backend.auth.guards import get_current_user
from backend.market_data.frames import bars_frame, market_symbol_for, venue_of
from backend.market_data.provenance import build_provenance
from backend.market_data.service import MarketDataService
from backend.risk import metrics as M
from backend.security.validation import sanitize_error, validate_symbol

router = APIRouter(prefix="/api/risk", tags=["risk"], dependencies=[Depends(get_current_user)])

HISTORY_BARS = 1000
MAX_HOLDINGS = 25


def _frame(svc: MarketDataService, symbol: str, limit: int) -> tuple[pd.DataFrame, dict]:
    try:
        payload = svc.get_bars(symbol, timeframe="1d", limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=sanitize_error(exc, prefix=f"bars for {symbol} failed")) from exc
    frame = bars_frame(payload)
    if len(frame) < M.MIN_OBS:
        raise HTTPException(status_code=502, detail=f"not enough history for {symbol} ({len(frame)} sessions)")
    return frame, payload


def _market_close(svc: MarketDataService, mic: str, limit: int) -> tuple[pd.Series | None, str]:
    sym = market_symbol_for(mic)
    try:
        frame = bars_frame(svc.get_bars(sym, timeframe="1d", limit=limit))
    except Exception:
        return None, sym
    return (frame["close"] if len(frame) >= M.MIN_OBS else None), sym


@router.get("/{symbol}")
def symbol_risk(
    symbol: str,
    lookback: int = Query(default=HISTORY_BARS, ge=120, le=HISTORY_BARS, description="Sessions of history"),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    sym = validate_symbol(symbol)
    frame, payload = _frame(svc, sym, lookback)
    mic = venue_of(payload, sym)
    market, market_symbol = _market_close(svc, mic, lookback)
    out = M.symbol_risk(frame, market)
    return {
        "symbol": sym,
        "exchange_mic": mic,
        "benchmark": market_symbol if market is not None else None,
        "start": str(frame.index[0].date()),
        "end": str(frame.index[-1].date()),
        "last_close": float(frame["close"].iloc[-1]),
        **out,
        "provenance": {**(payload.get("provenance") or build_provenance("yfinance").model_dump(mode="json")), "granularity": "1d"},
    }


class Holding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=24)
    weight: float = Field(description="Fraction of capital, or any positive amount (normalized)")

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        return validate_symbol(v)


class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holdings: list[Holding] = Field(min_length=1, max_length=MAX_HOLDINGS)
    lookback: int = Field(default=252, ge=63, le=HISTORY_BARS)


@router.post("/portfolio")
def portfolio_risk(req: PortfolioRequest, svc: MarketDataService = Depends(get_market_service)) -> dict:
    weights: dict[str, float] = {}
    for h in req.holdings:
        weights[h.symbol] = weights.get(h.symbol, 0.0) + float(h.weight)
    if not any(abs(w) > 0 for w in weights.values()):
        raise HTTPException(status_code=422, detail="weights sum to zero")
    returns = {}
    unavailable: dict[str, str] = {}
    first_mic = None
    for sym in weights:
        try:
            frame, payload = _frame(svc, sym, req.lookback + 5)
        except HTTPException as exc:
            unavailable[sym] = str(exc.detail)
            continue
        first_mic = first_mic or venue_of(payload, sym)
        returns[sym] = M.log_returns(frame["close"])
    if not returns:
        raise HTTPException(status_code=502, detail={"message": "no holding has usable history", "unavailable": unavailable})
    market, market_symbol = _market_close(svc, first_mic or "XNAS", req.lookback + 5)
    frame = pd.DataFrame(returns)
    try:
        out = M.portfolio_risk(
            frame, {k: v for k, v in weights.items() if k in returns},
            M.log_returns(market) if market is not None else None, window=req.lookback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        **out,
        "benchmark": market_symbol if market is not None else None,
        "unavailable": unavailable,
        "provenance": build_provenance("risk-portfolio", granularity="1d").model_dump(mode="json"),
    }
