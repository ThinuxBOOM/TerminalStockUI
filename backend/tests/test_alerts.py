"""Phase 3b alert tests (offline only, no network).

Covers: model/migration shape on sqlite, CRUD validation (unknown symbol
422, bad condition 422, NaN threshold 422), evaluation fires /
respects-cooldown / skips-thin-history (fake market+forecast services
injected — never real network), cron auth reuse (401/200), webhook no-URL
no-op + failure swallowed (monkeypatched httpx), and delete cascades
events.
"""

from __future__ import annotations

import logging
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.alerts_notify import ConsoleNotifier, WebhookNotifier
from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.db.models import Alert, AlertEvent, AuditLog, Base
from backend.db.session import get_session_factory, init_db, reset_engine
from backend.forecasting.service import get_forecast_service, reset_forecast_service
from backend.instruments.registry import InstrumentRegistry

PROVENANCE_KEYS = {
    "source", "as_of", "delay_minutes", "quality_grade",
    "fallback_used", "missing_fields",
}


class FakeMarket:
    """Deterministic quotes (no network)."""

    def __init__(self, price: float = 150.0, change_pct: float = -6.0) -> None:
        self.registry = InstrumentRegistry()
        self.price = float(price)
        self.change_pct = float(change_pct)

    def get_quote(self, symbol: str, market: str | None = None) -> dict:
        sym = (symbol or "").strip().upper()
        return {
            "symbol": sym,
            "price": float(self.price),
            "change_pct": float(self.change_pct),
            "market_state": "closed",
            "provenance": {
                "source": "fake",
                "as_of": "2026-09-12T00:00:00+00:00",
                "delay_minutes": 15,
                "quality_grade": "B",
                "fallback_used": False,
                "missing_fields": [],
            },
        }


class FakeForecast:
    """Deterministic direction probabilities (no network)."""

    def __init__(self, prob: float = 0.80, thin_symbols: tuple = ()) -> None:
        self.prob = float(prob)
        self.thin = {(s or "").strip().upper() for s in thin_symbols}

    def forecast(self, symbol: str, horizon: int, as_of: str | None = None) -> dict:
        sym = (symbol or "").strip().upper()
        if sym in self.thin:
            raise ValueError(f"insufficient history for {sym!r}: 12 bars")
        return {
            "symbol": sym,
            "horizon_days": int(horizon),
            "direction_probability": float(self.prob),
            "provenance": {
                "source": "fake-forecast",
                "as_of": "2026-09-12T00:00:00+00:00",
                "delay_minutes": 15,
                "quality_grade": "B",
                "fallback_used": False,
                "missing_fields": [],
            },
            "disclosure": "Not investment advice",
        }


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/alerts.db"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db(url)
    try:
        yield url
    finally:
        reset_engine()


def _teardown() -> None:
    reset_deps()
    reset_engine()
    reset_forecast_service()


def _client(market=None, forecast=None) -> TestClient:
    reset_deps()
    reset_forecast_service()
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: market or FakeMarket()
    app.dependency_overrides[get_forecast_service] = lambda: forecast or FakeForecast()
    return TestClient(app)


def _session():
    return get_session_factory()()


def _create(client: TestClient, **overrides) -> dict:
    body = {"symbol": "AAPL", "condition": "price_above", "threshold": 100.0}
    body.update(overrides)
    resp = client.post("/api/alerts/", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["alert"]


# --- model / migration shape ------------------------------------------------


def test_model_and_migration_contract(isolated_db):
    from pathlib import Path

    try:
        assert {"alerts", "alert_events"} <= set(Base.metadata.tables)
        alert_cols = {c.name for c in Base.metadata.tables["alerts"].columns}
        assert {"alert_id", "symbol", "exchange_mic", "condition", "threshold",
                "horizon_days", "target_ccy", "is_active", "cooldown_hours",
                "last_fired_at", "created_at"} <= alert_cols
        event_cols = {c.name for c in Base.metadata.tables["alert_events"].columns}
        assert {"event_id", "alert_id", "symbol", "observed", "threshold",
                "provenance", "created_at"} <= event_cols

        root = Path(__file__).resolve().parents[2]
        infra = (root / "infra" / "migrations" / "0003_alerts.sql").read_text()
        supa = (root / "supabase" / "migrations" / "0003_alerts.sql").read_text()
        for text in (infra, supa):
            lowered = text.lower()
            assert "create table if not exists alerts" in lowered
            assert "create table if not exists alert_events" in lowered
            assert "gen_random_uuid()" in lowered
            assert "condition in ('price_above', 'price_below', 'direction_above', " \
                "'direction_below', 'change_pct_below')" in lowered
            assert "check (horizon_days in (1, 7, 14, 21))" in lowered
            assert "on delete cascade" in lowered
            assert "ix_alerts_symbol_active" in lowered
            assert "ix_alert_events_alert" in lowered
        assert "enable row level security" in supa.lower()

        # sqlite round-trip via init_db.
        db = _session()
        try:
            row = Alert(symbol="AAPL", exchange_mic="XNAS",
                        condition="price_above", threshold=100.0)
            db.add(row)
            db.commit()
            db.refresh(row)
            db.add(AlertEvent(alert_id=row.alert_id, symbol="AAPL",
                              observed=150.0, threshold=100.0,
                              provenance={"source": "fake"}))
            db.commit()
            assert db.query(AlertEvent).count() == 1
        finally:
            db.close()
    finally:
        _teardown()


# --- CRUD -------------------------------------------------------------------


def test_create_alert_happy_path(isolated_db):
    client = _client()
    try:
        resp = client.post("/api/alerts/", json={
            "symbol": "aapl", "condition": "price_above", "threshold": 100.0,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        alert = body["alert"]
        assert alert["symbol"] == "AAPL"
        assert alert["exchange_mic"] == "XNAS"
        assert alert["condition"] == "price_above"
        assert alert["threshold"] == 100.0
        assert alert["horizon_days"] == 21
        assert alert["target_ccy"] == "USD"
        assert alert["is_active"] is True
        assert alert["cooldown_hours"] == 24
        assert alert["last_fired_at"] is None
        assert alert["created_at"]
        assert uuid.UUID(alert["alert_id"])  # parses
        assert PROVENANCE_KEYS <= set(body["provenance"])
        assert "Not investment advice" in body["disclosure"]
    finally:
        _teardown()


def test_create_unknown_symbol_422(isolated_db):
    client = _client()
    try:
        resp = client.post("/api/alerts/", json={
            "symbol": "ZZZ_NOPE_123", "condition": "price_above",
            "threshold": 10.0,
        })
        assert resp.status_code == 422, resp.text
    finally:
        _teardown()


def test_create_bad_condition_422(isolated_db):
    client = _client()
    try:
        resp = client.post("/api/alerts/", json={
            "symbol": "AAPL", "condition": "price_sideways",
            "threshold": 10.0,
        })
        assert resp.status_code == 422, resp.text
    finally:
        _teardown()


def test_create_nan_threshold_422(isolated_db):
    client = _client()
    try:
        # Raw bodies: httpx refuses to serialize NaN/Inf itself, so send the
        # literals verbatim; the server must still reject them with 422.
        raw_thresholds = ("NaN", "Infinity", "-Infinity", '"not-a-number"')
        for raw in raw_thresholds:
            resp = client.post(
                "/api/alerts/",
                content='{"symbol": "AAPL", "condition": "price_above", '
                        f'"threshold": {raw}}}',
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 422, (raw, resp.text)
    finally:
        _teardown()


def test_create_bad_horizon_422(isolated_db):
    client = _client()
    try:
        resp = client.post("/api/alerts/", json={
            "symbol": "AAPL", "condition": "direction_above",
            "threshold": 0.6, "horizon_days": 5,
        })
        assert resp.status_code == 422, resp.text
    finally:
        _teardown()


def test_list_and_active_only_filter(isolated_db):
    client = _client()
    try:
        first = _create(client, symbol="AAPL")
        _create(client, symbol="MSFT", condition="price_below",
                threshold=500.0)
        body = client.get("/api/alerts/").json()
        assert body["count"] == 2
        assert PROVENANCE_KEYS <= set(body["provenance"])
        assert "Not investment advice" in body["disclosure"]

        resp = client.patch(f"/api/alerts/{first['alert_id']}",
                            json={"is_active": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["alert"]["is_active"] is False

        filtered = client.get("/api/alerts/", params={"active_only": "true"}).json()
        assert filtered["count"] == 1
        assert filtered["alerts"][0]["symbol"] == "MSFT"
        unfiltered = client.get("/api/alerts/").json()
        assert unfiltered["count"] == 2
    finally:
        _teardown()


def test_patch_subset_and_errors(isolated_db):
    client = _client()
    try:
        alert = _create(client)
        aid = alert["alert_id"]
        resp = client.patch(f"/api/alerts/{aid}", json={
            "threshold": 123.5, "cooldown_hours": 6, "is_active": True,
        })
        assert resp.status_code == 200, resp.text
        patched = resp.json()["alert"]
        assert patched["threshold"] == 123.5
        assert patched["cooldown_hours"] == 6
        assert PROVENANCE_KEYS <= set(resp.json()["provenance"])

        assert client.patch(f"/api/alerts/{aid}", json={}).status_code == 422
        assert client.patch(f"/api/alerts/{aid}",
                            json={"cooldown_hours": -1}).status_code == 422
        nan_patch = client.patch(
            f"/api/alerts/{aid}",
            content='{"threshold": NaN}',
            headers={"Content-Type": "application/json"},
        )
        assert nan_patch.status_code == 422, nan_patch.text
        assert client.patch(
            f"/api/alerts/{uuid.uuid4()}", json={"is_active": False}
        ).status_code == 404
        assert client.patch("/api/alerts/not-a-uuid",
                            json={"is_active": False}).status_code == 404
    finally:
        _teardown()


def test_delete_cascades_events(isolated_db):
    from backend.api.alerts import evaluate_due_alerts

    client = _client(market=FakeMarket(price=150.0))
    try:
        alert = _create(client, threshold=100.0)
        db = _session()
        try:
            out = evaluate_due_alerts(
                db, market=FakeMarket(price=150.0),
                forecast=FakeForecast(), notifier=ConsoleNotifier(),
            )
            assert len(out["fired"]) == 1
            assert db.query(AlertEvent).count() == 1
        finally:
            db.close()

        resp = client.delete(f"/api/alerts/{alert['alert_id']}")
        assert resp.status_code == 204, resp.text

        db = _session()
        try:
            assert db.query(Alert).count() == 0
            assert db.query(AlertEvent).count() == 0  # cascaded
        finally:
            db.close()
        assert client.get("/api/alerts/").json()["count"] == 0
        assert client.delete(
            f"/api/alerts/{alert['alert_id']}"
        ).status_code == 404
    finally:
        _teardown()


# --- evaluation -------------------------------------------------------------


def test_evaluate_fires_and_audits(isolated_db):
    from backend.api.alerts import evaluate_due_alerts

    client = _client(market=FakeMarket(price=150.0))
    try:
        alert = _create(client, threshold=100.0)
        db = _session()
        try:
            out = evaluate_due_alerts(
                db, market=FakeMarket(price=150.0),
                forecast=FakeForecast(), notifier=ConsoleNotifier(),
            )
            assert out["checked"] == 1
            assert out["errors"] == {}
            assert len(out["fired"]) == 1
            fired = out["fired"][0]
            assert fired["alert_id"] == alert["alert_id"]
            assert fired["symbol"] == "AAPL"
            assert fired["observed"] == 150.0
            assert PROVENANCE_KEYS <= set(out["provenance"])
            assert "Not investment advice" in out["disclosure"]

            events = db.query(AlertEvent).all()
            assert len(events) == 1
            assert float(events[0].observed) == 150.0
            assert float(events[0].threshold) == 100.0

            row = db.query(Alert).first()
            assert row.last_fired_at is not None

            audits = db.query(AuditLog).filter(
                AuditLog.action == "alert.fired").all()
            assert len(audits) == 1
            assert audits[0].entity_id == alert["alert_id"]
        finally:
            db.close()
    finally:
        _teardown()


def test_evaluate_respects_cooldown(isolated_db):
    from backend.api.alerts import evaluate_due_alerts

    client = _client(market=FakeMarket(price=150.0))
    try:
        alert = _create(client, threshold=100.0)
        kwargs = {"market": FakeMarket(price=150.0),
                  "forecast": FakeForecast(),
                  "notifier": ConsoleNotifier()}
        db = _session()
        try:
            assert len(evaluate_due_alerts(db, **kwargs)["fired"]) == 1
            # Immediate re-run: within the 24h cooldown -> skipped.
            second = evaluate_due_alerts(db, **kwargs)
            assert second["fired"] == [] and second["checked"] == 0
            assert db.query(AlertEvent).count() == 1
        finally:
            db.close()

        # Zero cooldown re-arms the alert.
        resp = client.patch(f"/api/alerts/{alert['alert_id']}",
                            json={"cooldown_hours": 0})
        assert resp.status_code == 200, resp.text
        db = _session()
        try:
            third = evaluate_due_alerts(db, **kwargs)
            assert len(third["fired"]) == 1
            assert db.query(AlertEvent).count() == 2
        finally:
            db.close()
    finally:
        _teardown()


def test_evaluate_below_conditions_and_miss(isolated_db):
    from backend.api.alerts import evaluate_due_alerts

    market = FakeMarket(price=150.0, change_pct=-6.0)
    client = _client(market=market)
    try:
        _create(client, symbol="AAPL", condition="price_below",
                threshold=200.0)
        _create(client, symbol="MSFT", condition="change_pct_below",
                threshold=-5.0)
        _create(client, symbol="JPM", condition="price_above",
                threshold=999999.0)
        db = _session()
        try:
            out = evaluate_due_alerts(
                db, market=FakeMarket(price=150.0, change_pct=-6.0),
                forecast=FakeForecast(), notifier=ConsoleNotifier(),
            )
            assert out["checked"] == 3
            assert out["errors"] == {}
            by_symbol = {f["symbol"]: f for f in out["fired"]}
            assert set(by_symbol) == {"AAPL", "MSFT"}  # JPM misses
            assert by_symbol["AAPL"]["observed"] == 150.0
            assert by_symbol["MSFT"]["observed"] == -6.0
        finally:
            db.close()
    finally:
        _teardown()


def test_evaluate_direction_uses_forecast(isolated_db):
    from backend.api.alerts import evaluate_due_alerts

    forecast = FakeForecast(prob=0.80)
    client = _client(forecast=forecast)
    try:
        _create(client, symbol="AAPL", condition="direction_above",
                threshold=0.60)
        _create(client, symbol="MSFT", condition="direction_below",
                threshold=0.90)
        _create(client, symbol="JPM", condition="direction_above",
                threshold=0.95)
        db = _session()
        try:
            out = evaluate_due_alerts(
                db, market=FakeMarket(), forecast=FakeForecast(prob=0.80),
                notifier=ConsoleNotifier(),
            )
            assert out["checked"] == 3
            assert out["errors"] == {}
            by_symbol = {f["symbol"]: f for f in out["fired"]}
            assert set(by_symbol) == {"AAPL", "MSFT"}
            assert by_symbol["AAPL"]["observed"] == 0.80
            assert "Not investment advice" in out["disclosure"]
        finally:
            db.close()
    finally:
        _teardown()


def test_evaluate_skips_insufficient_history(isolated_db):
    client = _client(forecast=FakeForecast(thin_symbols={"AAPL"}))
    try:
        alert = _create(client, symbol="AAPL", condition="direction_above",
                        threshold=0.60)
        resp = client.get("/api/cron/evaluate")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["fired"] == []
        assert body["checked"] == 0
        assert alert["alert_id"] in body["errors"]
        assert "insufficient history" in body["errors"][alert["alert_id"]]
        assert PROVENANCE_KEYS <= set(body["provenance"])
    finally:
        _teardown()


# --- cron -------------------------------------------------------------------


def test_cron_evaluate_auth_reuse_401(isolated_db, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cr3t")
    client = _client()
    try:
        assert client.get("/api/cron/evaluate").status_code == 401
        assert client.get(
            "/api/cron/evaluate",
            headers={"Authorization": "Bearer wrong"},
        ).status_code == 401
        assert client.post("/api/cron/evaluate").status_code == 401
        ok = client.get("/api/cron/evaluate",
                        headers={"Authorization": "Bearer s3cr3t"})
        assert ok.status_code == 200, ok.text
        assert PROVENANCE_KEYS <= set(ok.json()["provenance"])
    finally:
        _teardown()


def test_cron_evaluate_end_to_end(isolated_db, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    client = _client(market=FakeMarket(price=150.0))
    try:
        alert = _create(client, threshold=100.0)
        first = client.get("/api/cron/evaluate")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["checked"] == 1
        assert len(body["fired"]) == 1
        assert body["fired"][0]["alert_id"] == alert["alert_id"]
        assert body["errors"] == {}

        # POST honors the same cooldown gate (no double-fire).
        second = client.post("/api/cron/evaluate")
        assert second.status_code == 200, second.text
        assert second.json()["fired"] == []
    finally:
        _teardown()


# --- delivery ---------------------------------------------------------------


def test_webhook_no_url_noop(monkeypatch):
    monkeypatch.delenv("ALERTS_WEBHOOK_URL", raising=False)
    monkeypatch.setitem(sys.modules, "httpx", None)  # proves no import/call
    try:
        assert WebhookNotifier().notify({"alert_id": "x"}) is False
        assert isinstance(
            __import__("backend.api.alerts_notify", fromlist=["get_notifier"])
            .get_notifier(),
            ConsoleNotifier,
        )
    finally:
        monkeypatch.undo()


def test_webhook_failure_swallowed_and_firing_stands(isolated_db, monkeypatch):
    import types

    from backend.api.alerts import evaluate_due_alerts

    monkeypatch.setenv("ALERTS_WEBHOOK_URL", "http://127.0.0.1:9/hook")

    def _boom(*args, **kwargs):
        raise ConnectionError("down")

    fake_httpx = types.SimpleNamespace(post=_boom)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    try:
        assert WebhookNotifier().notify({"alert_id": "x"}) is False

        client = _client(market=FakeMarket(price=150.0))
        try:
            _create(client, threshold=100.0)
            db = _session()
            try:
                out = evaluate_due_alerts(
                    db, market=FakeMarket(price=150.0),
                    forecast=FakeForecast(),
                    notifier=WebhookNotifier(),
                )
                assert len(out["fired"]) == 1  # delivery failure never blocks
                assert db.query(AlertEvent).count() == 1
            finally:
                db.close()
        finally:
            _teardown()
    finally:
        monkeypatch.undo()


def test_console_notifier_redacts(caplog):
    notifier = ConsoleNotifier()
    with caplog.at_level(logging.INFO, logger="backend.api.alerts_notify"):
        assert notifier.notify({
            "alert_id": "x", "api_key": "sk-secret-live",
            "nested": {"token": "tok-secret"},
        }) is True
    assert "[REDACTED]" in caplog.text
    assert "sk-secret-live" not in caplog.text
    assert "tok-secret" not in caplog.text


# --- worker -----------------------------------------------------------------


def test_worker_evaluate_alerts_with_db(isolated_db):
    from backend.workers.jobs import evaluate_alerts

    client = _client(market=FakeMarket(price=150.0))
    try:
        _create(client, threshold=100.0)
        db = _session()
        try:
            out = evaluate_alerts(
                db, market=FakeMarket(price=150.0),
                forecast=FakeForecast(), notifier=ConsoleNotifier(),
            )
            assert out["job"] == "evaluate_alerts"
            assert out["ok"] is True
            assert out["alerts_checked"] == 1
            assert out["alerts_fired"] == 1
            versions = out["provenance"]["versions"]
            for key in ("backend", "worker", "model", "feature", "data"):
                assert versions.get(key)
        finally:
            db.close()
    finally:
        _teardown()
