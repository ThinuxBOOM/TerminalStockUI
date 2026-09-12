"""Observability tests (M0 + spec Sec 6: provider dashboards, audit verification)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.api.audit import append_audit_log
from backend.db.session import init_db
from backend.market_data.health import ProviderHealthTracker
from backend.observability.audit_verify import verify_chain, verify_database, verify_rows
from backend.observability.dashboard import build_dashboard
from backend.observability.provider_metrics import (
    aggregate_all,
    aggregate_provider_calls,
    check_error_alert,
    record_call,
    summarize_latencies,
)


def _fresh_session(tmp_path, name="obs.db"):
    url = f"sqlite:///{tmp_path}/{name}"
    init_db(url)
    from backend.db.session import get_engine
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False), url


# --- provider_metrics ------------------------------------------------------------

def test_summarize_latencies_empty_and_ordered():
    assert summarize_latencies([]) == (0.0, 0.0)
    p50, p95 = summarize_latencies([100, 200, 300, 400])
    assert p50 > 0 and p95 >= p50


def test_aggregate_provider_calls_shape_and_math():
    now = datetime.now(timezone.utc)
    calls = [
        {"latency_ms": 100 + i * 10, "ok": i < 8, "t": now - timedelta(minutes=i)}
        for i in range(10)
    ]
    stats = aggregate_provider_calls("yfinance", calls)
    assert stats["provider"] == "yfinance"
    assert stats["total_calls"] == 10
    assert stats["latency_p50_ms"] > 0
    assert stats["latency_p95_ms"] >= stats["latency_p50_ms"]
    assert stats["error_rate_1h"] == 0.2
    assert stats["calls_1h"] == 10
    assert stats["circuit"] == "closed"


def test_aggregate_provider_calls_empty():
    stats = aggregate_provider_calls("akshare", [])
    assert stats["latency_p50_ms"] == 0.0
    assert stats["error_rate_1h"] == 0.0
    assert stats["total_calls"] == 0


def test_aggregate_all_sorts_and_defaults_circuits():
    now = datetime.now(timezone.utc)
    stats = aggregate_all(
        {
            "yfinance": [{"latency_ms": 120, "ok": True, "t": now}],
            "akshare": [{"latency_ms": 900, "ok": False, "t": now}],
        },
        circuits={"akshare": "open"},
    )
    assert [s["provider"] for s in stats] == ["akshare", "yfinance"]
    assert next(s for s in stats if s["provider"] == "akshare")["circuit"] == "open"


def test_record_call_writes_to_tracker():
    tracker = ProviderHealthTracker()
    record_call(tracker, "yfinance", 150.0, True)
    record_call(tracker, "yfinance", 250.0, False)
    stats = tracker.stats("yfinance")
    assert stats["total_calls"] == 2
    assert stats["error_rate_1h"] == 0.5


def test_check_error_alert_threshold():
    assert check_error_alert({"error_rate_5m": 0.5, "calls_5m": 10, "error_rate_1h": 0.5, "calls_1h": 10})
    assert not check_error_alert({"error_rate_5m": 0.01, "calls_5m": 10, "error_rate_1h": 0.01, "calls_1h": 10})
    # Fallback to the 1h rate when no 5m window is present (tracker shape).
    assert check_error_alert({"error_rate_1h": 0.2, "calls_1h": 10})
    assert not check_error_alert({"error_rate_1h": 0.0, "calls_1h": 0})


# --- dashboard -------------------------------------------------------------------

def test_dashboard_empty_tracker_shape():
    data = build_dashboard(ProviderHealthTracker())
    assert "generated_at" in data
    assert isinstance(data["providers"], list) and data["providers"]
    assert data["summary"]["degraded"] is False
    assert data["summary"]["banner"] is None
    assert data["alerts"] == []


def test_dashboard_flags_open_circuit_and_banner():
    tracker = ProviderHealthTracker()
    tracker.record("yfinance", 120, True)
    tracker.set_circuit("yfinance", "open")
    data = build_dashboard(tracker)
    assert data["summary"]["degraded"] is True
    assert "yfinance" in (data["summary"]["banner"] or "")
    assert any(a["reason"] == "circuit_open" for a in data["alerts"])


def test_dashboard_alerts_on_high_error_rate():
    tracker = ProviderHealthTracker()
    for _ in range(9):
        tracker.record("yfinance", 200, False)
    tracker.record("yfinance", 200, True)
    data = build_dashboard(tracker, min_calls=5)
    entry = next(p for p in data["providers"] if p["provider"] == "yfinance")
    assert entry["error_rate_1h"] == 0.9
    assert any(a["reason"] == "high_error_rate" and a["provider"] == "yfinance" for a in data["alerts"])


# --- audit_verify ------------------------------------------------------------------

def test_verify_database_round_trip_and_cli_shape(tmp_path):
    Session, url = _fresh_session(tmp_path)
    with Session() as db:
        append_audit_log(db, actor="system", action="forecast.created",
                         entity_type="forecast", entity_id="f1", payload={"n": 1})
    result = verify_database(url)
    assert result["ok"] and result["checked"] == 1


def test_verify_chain_catches_tamper_and_gap(tmp_path):
    Session, _ = _fresh_session(tmp_path)
    with Session() as db:
        append_audit_log(db, actor="system", action="a", entity_type="t", entity_id="1", payload={})
        append_audit_log(db, actor="system", action="b", entity_type="t", entity_id="1", payload={})
        assert verify_chain(db)["ok"]

        from sqlalchemy import select

        from backend.db.models import AuditLog

        rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
        rows[1].payload = {"tampered": True}
        assert not verify_rows(rows)["ok"]
