"""Canonical instrument record (spec section 4).

Never rely solely on a ticker: identity is (exchange_mic, exchange_symbol).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Instrument(BaseModel):
    """Canonical instrument. Field names mirror infra/migrations/0001_initial.sql."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    instrument_id: str = Field(description="Stable canonical ID, e.g. XNAS-AAPL")
    exchange_mic: str = Field(description="XNYS / XNAS / XSHG / XPAR / XAMS / XBRU ...")
    exchange_symbol: str = Field(description="Exchange-level symbol, e.g. AAPL, 600519.SS, MC.PA")
    provider_symbol: str = Field(
        default="",
        description="Raw symbol at the upstream provider (e.g. Yahoo suffix form)",
    )
    isin: str | None = Field(default=None)
    company_name: str = ""
    currency: str = Field(default="USD", min_length=3, max_length=3)
    country: str | None = Field(default=None)
    sector: str | None = Field(default=None)
    timezone: str = "UTC"
    trading_calendar: str = Field(default="XNYS")
    is_active: bool = True

    def model_dump_canonical(self) -> dict:
        data = self.model_dump()
        if not data.get("provider_symbol"):
            data["provider_symbol"] = data["exchange_symbol"]
        return data
