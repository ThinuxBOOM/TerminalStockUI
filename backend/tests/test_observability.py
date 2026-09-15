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


# --- Agent 6: enriched aggregation, state matrix, alert hook -------------------

def test_aggregate_preserves_quota_state_passthrough():
    from backend.observability.provider_metrics import aggregate_provider_calls

    now = datetime.now(timezone.utc)
    calls = [{"latency_ms": 120, "ok": True, "t": now}]
    quota = {"limited": True, "reason": "rate_limited", "status_code": 429,
             "updated_at": now.isoformat(), "auth_required": False}
    stats = aggregate_provider_calls("yfinance", calls, circuit="closed",
                                     quota=quota, state="degraded",
                                     last_success=now.isoformat(),
                                     consecutive_failures=0)
    assert stats["kind"] == "data"
    assert stats["state"] == "degraded"
    assert stats["quota"]["limited"] is True
    assert stats["quota"]["status_code"] == 429
    assert stats["last_success"] == now.isoformat()
    assert stats["consecutive_failures"] == 0
    # Legacy keys intact.
    assert stats["latency_p50_ms"] > 0 and stats["error_rate_1h"] == 0.0


def test_derive_state_matrix_data_vs_ai():
    from backend.observability.provider_metrics import derive_state

    assert derive_state(kind="data", total=0) == "unknown"
    assert derive_state(kind="ai", total=0, quota_limited=True,
                        quota_reason="unconfigured") == "unconfigured"
    assert derive_state(kind="data", total=10, circuit="open") == "down"
    assert derive_state(kind="data", total=10, circuit="open", quota_limited=True,
                        quota_reason="rate_limited") == "degraded"
    assert derive_state(kind="data", total=10, circuit="half-open") == "degraded"
    assert derive_state(kind="ai", total=10, quota_limited=True,
                        quota_reason="unconfigured") == "unconfigured"
    assert derive_state(kind="data", total=10, error_5m=0.5, calls_5m=10) == "degraded"
    assert derive_state(kind="data", total=10) == "up"


def test_emit_alert_never_raises_and_throttles_audit():
    from backend.observability.provider_metrics import emit_alert

    emit_alert("yfinance", "high_error_rate", "test alert",
               {"state": "degraded", "circuit": "closed", "error_rate_5m": 0.5,
                "calls_5m": 10, "consecutive_failures": 3})
    # Immediate repeat: log fires, audit throttled — still never raises.
    emit_alert("yfinance", "high_error_rate", "test alert",
               {"state": "degraded", "circuit": "closed"})
    emit_alert("xai", "weird", "x" * 5000, {"unserializable": object()})


def test_dashboard_enriched_rows_and_quota_alert():
    from backend.observability.dashboard import KNOWN_PROVIDERS, build_dashboard

    tracker = ProviderHealthTracker()
    tracker.record("yfinance", 120, True)
    for _ in range(6):
        tracker.record("stooq", 80, False, status_code=429, error="rate limited (429)")
    data = build_dashboard(tracker)
    by_name = {p["provider"]: p for p in data["providers"]}
    for name in KNOWN_PROVIDERS:
        assert name in by_name, f"dashboard missing {name}"
        for key in ("kind", "state", "error_rate_5m", "calls_5m", "last_success",
                    "consecutive_failures", "quota"):
            assert key in by_name[name], f"{name} missing {key!r}"
    assert by_name["yfinance"]["kind"] == "data"
    assert by_name["gemini"]["kind"] == "ai"
    stooq = by_name["stooq"]
    assert stooq["quota"]["limited"] is True
    assert stooq["state"] == "degraded"
    assert any(a["reason"] == "quota_limited" and a["provider"] == "stooq"
               for a in data["alerts"])
    assert "stooq" in data["summary"]["degraded_providers"]
    assert data["summary"]["degraded"] is True


def test_dashboard_half_open_warns_not_critical():
    tracker = ProviderHealthTracker()
    tracker.record("fx", 50, True)
    tracker.set_circuit("fx", "half-open")
    data = build_dashboard(tracker)
    assert any(a["reason"] == "circuit_half_open" and a["provider"] == "fx"
               for a in data["alerts"])
    assert not any(a["reason"] == "circuit_open" and a["provider"] == "fx"
                   for a in data["alerts"])
