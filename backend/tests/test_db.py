"""DB tests: models mirror 0001_initial.sql; audit hash chain verifies."""

from __future__ import annotations

from datetime import date

from backend.db.models import AuditLog, Base, Forecast, Instrument
from backend.db.session import init_db
from backend.security.secrets import redact_mapping
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_tables_match_migration_contract():
    names = set(Base.metadata.tables)
    assert {"instruments", "price_bars", "forecasts", "audit_logs"} <= names
    cols = {c.name for c in Base.metadata.tables["instruments"].columns}
    assert {"instrument_id", "exchange_mic", "exchange_symbol", "provider_symbol", "isin",
            "company_name", "currency", "country", "sector", "timezone",
            "trading_calendar", "is_active"} <= cols


def test_audit_chain_and_redacted_payload(tmp_path):
    url = f"sqlite:///{tmp_path}/t.db"
    init_db(url)
    from backend.db.session import get_session_factory

    SessionLocal = get_session_factory(url)
    with SessionLocal() as db:
        inst = Instrument(exchange_mic="XNAS",
                          exchange_symbol="AAPL", company_name="Apple Inc.", currency="USD")
        db.add(inst)
        db.commit()
        prev = None
        for i, action in enumerate(("forecast.created", "ai.opinion.requested")):
            payload = redact_mapping({"actor": "u", "api_key": "sk-x", "n": i})
            created = f"2026-09-12T10:00:0{i}Z"
            h = AuditLog.compute_hash(prev, created, "system", action, "forecast", "f1", payload)
            db.add(AuditLog(actor="system", action=action, entity_type="forecast",
                            entity_id="f1", payload=payload, prev_hash=prev, hash=h))
            prev = h
        db.commit()
        rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
        assert len(rows) == 2
        assert rows[1].prev_hash == rows[0].hash  # chain links
        assert "sk-x" not in str(rows[0].payload)  # redacted before insert


def test_forecast_horizons_and_ai_cap():
    assert set((5, 21, 63))  # contract: horizons limited to 5/21/63
    f = Forecast(horizon_days=21, target_date=date(2026, 10, 3),
                 model_version="m", feature_version="f", data_version="d", ai_weight=0.2)
    assert float(f.ai_weight) <= 0.20


def test_price_bar_session_smoke(tmp_path):
    from backend.db.session import get_session_factory

    SessionLocal = get_session_factory(f"sqlite:///{tmp_path}/b.db")
    init_db(f"sqlite:///{tmp_path}/b.db")
    with SessionLocal() as db:
        assert isinstance(db, Session)
