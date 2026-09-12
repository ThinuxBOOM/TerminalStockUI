"""Retention tests (M8 hardening, spec Milestone 0 + Sec 6).

In-memory SQLite, no Redis. Covers: retention rules table, dry-run counts
without deleting, purge deletes expired rows, audit hash-chain head preserved.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.audit import append_audit_log
from backend.db.models import AuditLog, Base, Forecast, Instrument, PriceBar
from backend.observability.retention import (
    RETENTION_DAYS,
    RETENTION_RULES,
    get_retention_days,
    purge,
    purge_dry_run,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _seed_instrument(db, symbol="AAPL", mic="XNAS"):
    inst = Instrument(exchange_mic=mic, exchange_symbol=symbol,
                      company_name=f"{symbol} Inc.", currency="USD")
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def _old(dt_days_ago: int) -> datetime:
    return _utcnow() - timedelta(days=dt_days_ago)


# --- rules table ------------------------------------------------------------

def test_retention_rules_table():
    by_dataset = {r["dataset"]: r for r in RETENTION_RULES}
    assert by_dataset["ticks"]["retention_days"] == 90
    assert by_dataset["bars"]["retention_days"] == 5 * 365
    assert by_dataset["forecasts"]["retention_days"] == 3 * 365
    assert by_dataset["audit"]["retention_days"] == 7 * 365
    assert by_dataset["ai_token_logs"]["retention_days"] == 365
    assert RETENTION_DAYS == {r["dataset"]: r["retention_days"] for r in RETENTION_RULES}
    # Env overrides win over defaults (restore afterwards).
    import os

    os.environ["RETENTION_BARS_DAYS"] = "10"
    try:
        assert get_retention_days()["bars"] == 10
        assert get_retention_days({"bars": 7})["bars"] == 7
    finally:
        del os.environ["RETENTION_BARS_DAYS"]


def test_missing_optional_tables_count_zero():
    db = _session()
    try:
        result = purge_dry_run(db)
        assert result["counts"]["ticks"] == 0
        assert result["counts"]["ai_token_logs"] == 0
        assert result["deleted"] is False
    finally:
        db.close()


# --- dry-run counts without deleting ----------------------------------------

def test_purge_dry_run_counts_expired_without_deleting():
    db = _session()
    try:
        inst = _seed_instrument(db)
        now = _utcnow()
        # Bars: one expired (>5y on ts), one fresh.
        db.add(PriceBar(instrument_id=inst.instrument_id, ts=now - timedelta(days=6 * 365),
                        timeframe="1d", close=100.0, source="yfinance", as_of=now))
        db.add(PriceBar(instrument_id=inst.instrument_id, ts=now - timedelta(days=10),
                        timeframe="1d", close=101.0, source="yfinance", as_of=now))
        # Forecasts: one expired (>3y), one fresh.
        db.add(Forecast(instrument_id=inst.instrument_id, horizon_days=21,
                        target_date=date(2019, 1, 1), direction_prob=0.6,
                        model_version="m", feature_version="f", data_version="d",
                        provenance={}, created_at=now - timedelta(days=4 * 365)))
        db.add(Forecast(instrument_id=inst.instrument_id, horizon_days=21,
                        target_date=date(2027, 1, 1), direction_prob=0.6,
                        model_version="m2", feature_version="f", data_version="d",
                        provenance={}, created_at=now))
        db.commit()

        dry = purge_dry_run(db, now=now)
        assert dry["counts"]["bars"] == 1
        assert dry["counts"]["forecasts"] == 1
        assert dry["total"] >= 2
        # Dry-run must not delete.
        assert db.execute(select(PriceBar)).scalars().all().__len__() == 2
        assert db.execute(select(Forecast)).scalars().all().__len__() == 2

        # Purge removes exactly the expired rows.
        done = purge(db, now=now)
        assert done["counts"]["bars"] == 1
        assert done["counts"]["forecasts"] == 1
        assert done["deleted"] is True
        assert len(db.execute(select(PriceBar)).scalars().all()) == 1
        assert len(db.execute(select(Forecast)).scalars().all()) == 1
    finally:
        db.close()


# --- audit head preserved ----------------------------------------------------

def test_purge_preserves_audit_head():
    db = _session()
    try:
        now = _utcnow()
        ancient = now - timedelta(days=8 * 365)
        # Two expired audit rows + one fresh row (head).
        append_audit_log(db, actor="system", action="a", entity_type="t",
                         entity_id="1", payload={})
        append_audit_log(db, actor="system", action="b", entity_type="t",
                         entity_id="1", payload={})
        append_audit_log(db, actor="system", action="c", entity_type="t",
                         entity_id="1", payload={})
        rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
        assert len(rows) == 3
        head_id = rows[-1].id
        # Backdate the first two rows past the 7y retention window.
        for row in rows[:2]:
            row.created_at = ancient
        db.commit()

        dry = purge_dry_run(db, now=now)
        # Both expired rows are deletable, but the head is fresh so it stays anyway.
        assert dry["counts"]["audit"] == 2
        assert dry["audit_head_id"] == head_id

        # Now also expire the head: purge must still keep it.
        head = db.execute(select(AuditLog).where(AuditLog.id == head_id)).scalars().one()
        head.created_at = ancient
        db.commit()

        dry2 = purge_dry_run(db, now=now)
        assert dry2["counts"]["audit"] == 2  # 3 expired minus the preserved head
        done = purge(db, now=now)
        assert done["counts"]["audit"] == 2
        remaining = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
        assert [r.id for r in remaining] == [head_id]
    finally:
        db.close()
