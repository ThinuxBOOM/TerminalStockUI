"""SQLAlchemy models mirroring infra/migrations/0001_initial.sql + 0002_calibration.sql.

Tables: instruments, price_bars, forecasts, calibration_snapshots, audit_logs
(append-only, hash-chained). Portable types (Uuid/String/JSON) so unit tests
run on SQLite while Postgres stays the deployment target.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:  # SQLAlchemy 2.0 portable UUID: native UUID on Postgres (psycopg
    # returns uuid.UUID objects; native mode passes them through), CHAR(32)
    # fallback on SQLite. native_uuid=False MUST NOT be used here: its result
    # processor assumes string values and crashes on Postgres-native UUIDs
    # (AttributeError: 'UUID' object has no attribute 'replace').
    from sqlalchemy import Uuid as _Uuid

    ID_TYPE = _Uuid()
except Exception:  # pragma: no cover
    ID_TYPE = String(36)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("exchange_mic", "exchange_symbol", name="uq_instruments_mic_symbol"),
        Index("ix_instruments_provider_symbol", "provider_symbol"),
        Index("ix_instruments_isin", "isin"),
        Index("ix_instruments_exchange_symbol", "exchange_symbol"),
        Index("ix_instruments_mic_active", "exchange_mic", "is_active"),
    )

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


class CalibrationSnapshot(Base):
    """Walk-forward calibration snapshot (Phase 2b).

    One row per (symbol, horizon_days, model_version, feature_version,
    data_version): Brier/ECE over the trailing ensemble direction
    probabilities, a JSON reliability table, and per-member hit rates.
    Mirrors infra/migrations/0002_calibration.sql (Postgres JSONB/NUMERIC
    map to portable JSON/Numeric here so SQLite tests stay green).
    """

    __tablename__ = "calibration_snapshots"
    __table_args__ = (UniqueConstraint(
        "symbol", "horizon_days", "model_version", "feature_version",
        "data_version", name="uq_calibration_snapshot"),)

    snapshot_id: Mapped[uuid.UUID] = mapped_column(ID_TYPE, primary_key=True, default=uuid.uuid4)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    feature_version: Mapped[str] = mapped_column(Text, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_mic: Mapped[str] = mapped_column(String(8), nullable=False)
    brier: Mapped[float | None] = mapped_column(Numeric(6, 5))
    ece: Mapped[float | None] = mapped_column(Numeric(6, 5))
    n_windows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reliability: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    members: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_version: Mapped[str] = mapped_column(Text, nullable=False)
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
Index("ix_calibration_snapshots_symbol_horizon",
      CalibrationSnapshot.symbol, CalibrationSnapshot.horizon_days,
      CalibrationSnapshot.created_at.desc())

_ = PG_UUID  # keep linter honest: Postgres UUID is the deploy target


# --- Phase 3b: alerts (APPENDED at end-of-file by design) --------------------
# Parallel agents append other models to this same file: do not move this block
# above existing models and do not edit any line above it. Mirrors
# infra/migrations/0003_alerts.sql (Postgres JSONB/NUMERIC map to portable
# JSON/Numeric here so SQLite tests stay green).
import sqlalchemy as _sa  # local alias for the appended alert models only


class Alert(Base):
    """User-defined alert rule (Phase 3b).

    ``condition`` is one of price_above / price_below / direction_above /
    direction_below / change_pct_below. ``horizon_days`` is consumed by the
    direction_* conditions only (forecast direction_probability horizon).
    ``last_fired_at`` + ``cooldown_hours`` gate re-fires.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        _sa.CheckConstraint(
            "condition IN ('price_above', 'price_below', 'direction_above', "
            "'direction_below', 'change_pct_below')",
            name="ck_alerts_condition",
        ),
        _sa.CheckConstraint(
            "horizon_days IN (5, 21, 63)",
            name="ck_alerts_horizon_days",
        ),
    )

    alert_id: Mapped[uuid.UUID] = mapped_column(ID_TYPE, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange_mic: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    threshold: Mapped[float] = mapped_column(Numeric, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=21)
    target_ccy: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AlertEvent(Base):
    """Fired-alert event (append-only; cascade-deleted with its alert)."""

    __tablename__ = "alert_events"

    event_id: Mapped[uuid.UUID] = mapped_column(ID_TYPE, primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("alerts.alert_id", ondelete="CASCADE"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    observed: Mapped[float] = mapped_column(Numeric, nullable=False)
    threshold: Mapped[float] = mapped_column(Numeric, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


Index("ix_alerts_symbol_active", Alert.symbol, Alert.is_active)
Index("ix_alert_events_alert", AlertEvent.alert_id, AlertEvent.created_at.desc())


# --- Last-fetched market data (APPENDED at end-of-file by design) --------------
# Parallel agents append other models to this same file: do not move this block
# above existing models and do not edit any line above it. Mirrors
# infra/migrations/0005_quote_snapshots.sql (Postgres NUMERIC/BIGINT map to
# portable Numeric/BigInteger here so SQLite tests stay green).
class QuoteSnapshot(Base):
    """Last LIVE quote per provider symbol (last-fetched fallback store).

    One row per canonical provider symbol (e.g. ``AAPL``, ``600519.SS``):
    every live (non-fallback) ``get_quote`` upserts it, so a later provider
    outage serves the last real market data instead of a deterministic
    placeholder. Stub/fallback quotes are NEVER persisted (that would poison
    the well). ``instrument_id`` is lineage-only and nullable so symbols
    outside the registry still get coverage.
    """

    __tablename__ = "quote_snapshots"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instruments.instrument_id", ondelete="CASCADE"), nullable=True)
    exchange_mic: Mapped[str | None] = mapped_column(String(8))
    price: Mapped[float | None] = mapped_column(Numeric(20, 6))
    open: Mapped[float | None] = mapped_column(Numeric(20, 6))
    high: Mapped[float | None] = mapped_column(Numeric(20, 6))
    low: Mapped[float | None] = mapped_column(Numeric(20, 6))
    prev_close: Mapped[float | None] = mapped_column(Numeric(20, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    change: Mapped[float | None] = mapped_column(Numeric(20, 6))
    change_pct: Mapped[float | None] = mapped_column(Numeric(10, 6))
    source: Mapped[str] = mapped_column(Text, nullable=False, default="yfinance")
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    quality_grade: Mapped[str] = mapped_column(String(1), nullable=False, default="C")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


Index("ix_quote_snapshots_updated", QuoteSnapshot.updated_at.desc())
