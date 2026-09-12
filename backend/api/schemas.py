"""Shared API schemas: provenance envelope + quote/search/health payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..market_data.provenance import Provenance

QualityGrade = Literal["A", "B", "C", "D", "F"]
MarketState = Literal["open", "closed", "delayed", "stale"]


class InstrumentOut(BaseModel):
    instrument_id: str
    exchange_mic: str
    exchange_symbol: str
    provider_symbol: str = ""
    isin: str | None = None
    company_name: str = ""
    currency: str = "USD"
    country: str | None = None
    sector: str | None = None
    timezone: str = "UTC"
    trading_calendar: str = "XNYS"
    is_active: bool = True


class SearchResponse(BaseModel):
    query: str
    market: str | None = None
    results: list[InstrumentOut]
    provenance: Provenance


class QuoteResponse(BaseModel):
    symbol: str
    instrument: InstrumentOut | None = None
    candidates: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    prev_close: float | None = None
    volume: int | None = None
    currency: str = "USD"
    change: float | None = None
    change_pct: float | None = None
    market_state: str = "delayed"
    provenance: Provenance


class BarOut(BaseModel):
    ts: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    missing_fields: list[str] = Field(default_factory=list)


class BarsResponse(BaseModel):
    symbol: str
    instrument_id: str | None = None
    timeframe: str = "1d"
    bars: list[BarOut]
    provenance: Provenance


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    postgres: str = "unknown"
    redis: str = "unknown"
    version: str = "0.1.0"
    providers: list[dict] = Field(default_factory=list)


class ProviderHealthOut(BaseModel):
    provider: str
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    error_rate_1h: float = 0.0
    calls_1h: int = 0
    total_calls: int = 0
    circuit: str = "closed"
    last_check: datetime | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = True
    provenance: Provenance | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
