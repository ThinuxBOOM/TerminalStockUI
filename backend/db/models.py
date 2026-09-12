"""SQLAlchemy models mirroring infra/migrations/0001_initial.sql.

Tables: instruments, price_bars, forecasts, audit_logs (append-only,
hash-chained). Portable types (Uuid/String/JSON) so unit tests run on
SQLite while Postgres stays the deployment target.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:  # SQLAlchemy 2.0 portable UUID (CHAR(32) on SQLite, UUID on Postgres)
    from sqlalchemy import Uuid as _Uuid

    ID_TYPE = _Uuid(native_uuid=False)
except Exception:  # pragma: no cover
    ID_TYPE = String(36)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange_mic", "exchange_symbol", name="uq_instruments_mic_symbol"),)

    instrument_id: Mapped[uuid.UUID] = mapped_column(ID_TYPE, primary_key=True, default=uuid.uuid4)
    exchange_mic: Mapped[str] = mapped_column(String(8), nullable=False)
    exchange_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_symbol: Mapped[str | None] = mapped_column(String(32))
    isin: Mapped[str | None] = mapped_column(String(16))
    company_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    country: Mapped[str | None] = mapped_column(String(2))
    sector: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    trading_calendar: Mapped[str] = mapped_column(Text, nullable=False, default="XNYS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PriceBar(Base):
    __tablename__ = "price_bars"

    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.instrument_id", ondelete="CASCADE"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True, default="1d")
    open: Mapped[float | None] = mapped_column(Numeric(20, 6))
    high: Mapped[float | None] = mapped_column(Numeric(20, 6))
    low: Mapped[float | None] = mapped_column(Numeric(20, 6))
    close: Mapped[float | None] = mapped_column(Numeric(20, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="yfinance")
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    quality_grade: Mapped[str] = mapped_column(String(1), nullable=False, default="C")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Forecast(Base):
    __tablename__ = "forecasts"

    forecast_id: Mapped[uuid.UUID] = mapped_column(ID_TYPE, primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instruments.instrument_id", ondelete="CASCADE"))
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    target_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    direction_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    expected_ret_low: Mapped[float | None] = mapped_column(Numeric(10, 6))
    expected_ret_high: Mapped[float | None] = mapped_column(Numeric(10, 6))
    volatility_regime: Mapped[str | None] = mapped_column(Text)
    drawdown_prob: Mapped[float | None] = mapped_column(Numeric(6, 5))
    confidence: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    data_version: Mapped[str] = mapped_column(Text, nullable=False)
    ai_provider: Mapped[str | None] = mapped_column(Text)
    ai_model: Mapped[str | None] = mapped_column(Text)
    ai_weight: Mapped[float] = mapped_column(Numeric(4, 3), default=0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    """Append-only audit log. hash = sha256(prev_hash||created_at||actor||action||entity||payload)."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str | None] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(Text, nullable=False, default="")

    @staticmethod
    def compute_hash(prev_hash: str | None, created_at: str, actor: str, action: str,
                     entity_type: str, entity_id: str, payload: dict) -> str:
        material = "|".join([
            prev_hash or "",
            created_at,
            actor,
            action,
            entity_type,
            entity_id,
            json.dumps(payload or {}, sort_keys=True, default=str),
        ])
        return hashlib.sha256(material.encode()).hexdigest()


Index("ix_audit_logs_entity", AuditLog.entity_type, AuditLog.entity_id, AuditLog.id.desc())
Index("ix_price_bars_ts", PriceBar.ts.desc())

_ = PG_UUID  # keep linter honest: Postgres UUID is the deploy target
