"""Audit tests (M3/M4 + spec Sec 6: audit log verification).

Covers: append + hash-chain verify, forecast version storage, AI decision
logging with redaction, and the /api/audit HTTP surface.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api.audit import append_audit_log, log_ai_opinion, log_forecast_created
from backend.api.audit import router as audit_router
from backend.api.main import create_app
from backend.db.models import AuditLog, Base, Forecast, Instrument
from backend.db.session import get_db, init_db
from backend.observability.audit_verify import verify_chain, verify_rows


def _fresh_db(tmp_path, name="audit.db"):
    url = f"sqlite:///{tmp_path}/{name}"
    init_db(url)
    from backend.db.session import get_engine
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    return Session


def _seed_instrument(db, symbol="AAPL", mic="XNAS"):
    inst = Instrument(exchange_mic=mic, exchange_symbol=symbol, company_name=f"{symbol} Inc.", currency="USD")
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def _client_with_db(Session):
    app = create_app()
    app.include_router(audit_router)

    def _override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# --- append + hash chain --------------------------------------------------------

def test_audit_append_links_hash_chain(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        first = append_audit_log(db, actor="system", action="forecast.created",
                                 entity_type="forecast", entity_id="f1", payload={"n": 1})
        second = append_audit_log(db, actor="system", action="ai.opinion.requested",
                                  entity_type="ai_opinion", entity_id="o1",
                                  payload={"provider": "gemini"})
        assert first.prev_hash is None
        assert second.prev_hash == first.hash
        assert first.hash and second.hash and first.hash != second.hash

        result = verify_chain(db)
        assert result["ok"], result
        assert result["checked"] == 2


def test_audit_verify_detects_rewrite(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        append_audit_log(db, actor="system", action="forecast.created",
                         entity_type="forecast", entity_id="f1", payload={"n": 1})
        append_audit_log(db, actor="system", action="forecast.created",
                         entity_type="forecast", entity_id="f2", payload={"n": 2})
        # Tamper: rewrite a stored payload without updating the hash.
        row = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().first()
        row.payload = {"n": 999}
        db.commit()

        result = verify_chain(db)
        assert not result["ok"]
        assert result["failed_id"] == row.id
        assert "mismatch" in (result["error"] or "")


def test_audit_verify_detects_gap(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        a = append_audit_log(db, actor="system", action="a", entity_type="t", entity_id="1", payload={})
        b = append_audit_log(db, actor="system", action="b", entity_type="t", entity_id="1", payload={})
        b.prev_hash = "forged-prev-hash"
        db.commit()
        result = verify_rows([a, b])
        assert not result["ok"]
        assert "gap" in (result["error"] or "")


def test_audit_verify_empty_chain_ok(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        assert Base.metadata.tables.keys() >= {"audit_logs"}
        result = verify_chain(db)
        assert result == {"ok": True, "checked": 0, "failed_id": None, "error": None}


# --- forecast versioning --------------------------------------------------------

def test_forecast_stores_versions_and_timestamp(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        inst = _seed_instrument(db)
        f = Forecast(
            instrument_id=inst.instrument_id,
            horizon_days=21,
            target_date=date(2026, 10, 3),
            direction_prob=0.64,
            expected_ret_low=-0.04,
            expected_ret_high=0.09,
            volatility_regime="elevated",
            drawdown_prob=0.18,
            confidence="moderate",
            model_version="gbm-us-v0.1.0",
            feature_version="feat-us-v0.1.0",
            data_version="bars-2026-09-12",
            provenance={"source": "deterministic-engine"},
        )
        db.add(f)
        db.commit()
        db.refresh(f)
        assert f.model_version and f.feature_version and f.data_version
        assert f.created_at is not None  # every forecast stores a timestamp

        # Second version of the same run target is a distinct row (versioned log).
        f2 = Forecast(
            instrument_id=inst.instrument_id,
            horizon_days=21,
            target_date=date(2026, 10, 3),
            direction_prob=0.61,
            model_version="gbm-us-v0.2.0",
            feature_version="feat-us-v0.1.0",
            data_version="bars-2026-09-12",
            provenance={},
        )
        db.add(f2)
        db.commit()
        rows = db.execute(select(Forecast)).scalars().all()
        assert len(rows) == 2
        assert {r.model_version for r in rows} == {"gbm-us-v0.1.0", "gbm-us-v0.2.0"}


def test_forecasts_endpoint_returns_versioned_log(tmp_path):
    Session = _fresh_db(tmp_path, "api.db")
    with Session() as db:
        inst = _seed_instrument(db)
        db.add(Forecast(
            instrument_id=inst.instrument_id, horizon_days=21, target_date=date(2026, 10, 3),
            direction_prob=0.64, expected_ret_low=-0.04, expected_ret_high=0.09,
            volatility_regime="elevated", drawdown_prob=0.18, confidence="moderate",
            model_version="gbm-us-v0.1.0", feature_version="feat-us-v0.1.0",
            data_version="bars-2026-09-12", provenance={"source": "deterministic-engine"},
        ))
        db.commit()

    resp = _client_with_db(Session).get("/api/audit/forecasts", params={"symbol": "AAPL"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    row = body["forecasts"][0]
    for key in ("model_version", "feature_version", "data_version", "created_at"):
        assert row[key], f"forecast log missing {key}"
    assert row["horizon_days"] == 21
    assert "Not investment advice" in body["disclosure"]

    # Symbol filter is case-insensitive; unknown symbols return an empty log.
    resp2 = _client_with_db(Session).get("/api/audit/forecasts", params={"symbol": "aapl"})
    assert resp2.json()["count"] == 1
    resp3 = _client_with_db(Session).get("/api/audit/forecasts", params={"symbol": "NOPE"})
    assert resp3.json()["count"] == 0


# --- AI decisions + redaction ---------------------------------------------------

def test_ai_decisions_logged_with_redaction(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        row = log_ai_opinion(
            db, opinion_id="op-1", provider="gemini", model="gemini-3.7-flash",
            weight=0.15, extra={"api_key": "sk-live-secret", "evidence_ids": ["ev1"]},
        )
        assert "sk-live-secret" not in str(row.payload)
        assert row.payload["provider"] == "gemini"
        assert row.payload["model"] == "gemini-3.7-flash"

        result = verify_chain(db)
        assert result["ok"]


def test_ai_decisions_endpoint_redacts_and_surfaces_weight(tmp_path):
    Session = _fresh_db(tmp_path, "ai.db")
    with Session() as db:
        log_ai_opinion(db, opinion_id="op-1", provider="gemini", model="gemini-3.7-flash",
                       weight=0.15, extra={"evidence_ids": ["ev1", "ev2"]})
        log_forecast_created(db, forecast_id="f-1", payload={"model_version": "gbm-us-v0.1.0"})
        # A plaintext key smuggled into the payload must never survive the append path.
        append_audit_log(db, actor="user:7", action="ai.opinion.requested",
                         entity_type="ai_opinion", entity_id="op-2",
                         payload={"provider": "openai", "model": "gpt-x", "weight": 0.2,
                                  "api_key": "sk-should-never-appear", "token": "tok-secret"})

    client = _client_with_db(Session)
    resp = client.get("/api/audit/ai_decisions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2  # forecast.created is not an AI decision
    by_id = {d["entity_id"]: d for d in body["decisions"]}
    assert by_id["op-1"]["provider"] == "gemini"
    assert by_id["op-1"]["model"] == "gemini-3.7-flash"
    assert by_id["op-1"]["weight"] == 0.15
    assert by_id["op-1"]["evidence_ids"] == ["ev1", "ev2"]
    assert "sk-should-never-appear" not in resp.text
    assert "tok-secret" not in resp.text

    filtered = client.get("/api/audit/ai_decisions", params={"provider": "gemini"})
    assert filtered.json()["total"] == 1


def test_audit_logs_never_contain_keys_end_to_end(tmp_path):
    Session = _fresh_db(tmp_path)
    secret = "sk-end-to-end-plaintext-must-not-persist"
    with Session() as db:
        append_audit_log(db, actor="system", action="ai.opinion.requested",
                         entity_type="ai_opinion", entity_id="op-9",
                         payload={"provider": "xai", "model": "grok", "weight": 0.1,
                                  "nested": {"password": secret}})
        rows = db.execute(select(AuditLog)).scalars().all()
        assert secret not in str([r.payload for r in rows])
        assert secret not in str([r.hash for r in rows])


def test_forecast_horizon_contract_and_ai_weight_cap(tmp_path):
    Session = _fresh_db(tmp_path)
    with Session() as db:
        inst = _seed_instrument(db, symbol="MSFT")
        for horizon in (5, 21, 63):
            db.add(Forecast(
                instrument_id=inst.instrument_id, horizon_days=horizon,
                target_date=date(2026, 10, 3) + timedelta(days=horizon),
                direction_prob=0.55, model_version="m", feature_version="f",
                data_version="d", ai_weight=0.2, provenance={},
            ))
        db.commit()
        rows = db.execute(select(Forecast)).scalars().all()
        assert {r.horizon_days for r in rows} == {5, 21, 63}
        assert all(float(r.ai_weight) <= 0.20 for r in rows)
