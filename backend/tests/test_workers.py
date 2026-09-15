"""Worker tests (fail-closed, spec Sec 2 + Sec 6).

In-memory only, no Redis. Covers: registry lists 6 jobs, honest ok flags
per job (ingest/refresh real, report/alerts honestly False without wiring),
provenance fail-closed (fallback_used False, grade B), --once without
broker/DB.
"""

from __future__ import annotations

import json
import sys
import tempfile
import os
from datetime import datetime, timezone

from backend.workers import jobs
from backend.workers.jobs import (
    JOB_NAMES,
    JOB_REGISTRY,
    REPORT_PROFILES,
    build_provenance,
    capture_snapshot,
    evaluate_alerts,
    generate_report,
    ingest_bars,
    refresh_forecast,
    run_once,
    score_forecasts,
)


def test_job_registry_lists_four_jobs():
    # Original four jobs are always registered; Agent 3 adds snapshot/scoring
    # jobs alongside (superset, never a rename/removal).
    assert {
        "ingest_bars",
        "refresh_forecast",
        "evaluate_alerts",
        "generate_report",
    } <= set(JOB_REGISTRY)
    assert {"capture_snapshot", "score_forecasts"} <= set(JOB_REGISTRY)
    assert sorted(JOB_NAMES) == sorted(JOB_REGISTRY)
    assert len(JOB_REGISTRY) == 6


def _assert_envelope(result: dict, job: str):
    """Job envelopes describe the RUN: fallback False, grade B, versions."""
    assert result["job"] == job
    prov = result["provenance"]
    for key in ("source", "as_of", "delay_minutes", "quality_grade",
                "fallback_used", "missing_fields"):
        assert key in prov, f"{job} provenance missing {key!r}"
    assert prov["fallback_used"] is False, f"{job} envelope must be fail-closed"
    assert prov["quality_grade"] == "B", f"{job} envelope grade must be B"
    versions = prov["versions"]
    for key in ("backend", "worker", "model", "feature", "data"):
        assert versions.get(key), f"{job} versions missing {key!r}"
    json.dumps(result, default=str)  # JSON-serializable


def test_build_provenance_fail_closed():
    prov = build_provenance("ingest_bars")
    assert prov["fallback_used"] is False
    assert prov["quality_grade"] == "B"
    assert prov["source"] == "worker:ingest_bars"
    assert prov["versions"]["worker"] == jobs.WORKER_VERSION


def _fake_bars(n: int = 5):
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    return [
        {"ts": now - timedelta(days=(n - 1 - i)), "open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i,
         "close": 100.5 + i, "volume": 1000}
        for i in range(n)
    ]


def test_ingest_bars_real_ok_only_when_ingested(tmp_path):
    """ingest_bars is REAL: ok True only if bars ingested, else ok False."""
    db_url = f"sqlite:///{tmp_path}/ingest_ok.db"

    def _fetch_ok(symbol: str):
        return list(_fake_bars(5)), "yfinance"

    ok = ingest_bars("AAPL", fetch_fn=_fetch_ok, db_url=db_url)
    assert ok["job"] == "ingest_bars"
    assert ok["ok"] is True
    assert ok["bars_ingested"] > 0
    assert ok["errors"] == {}
    _assert_envelope(ok, "ingest_bars")

    def _fetch_boom(symbol: str):
        raise RuntimeError("fetch down")

    bad = ingest_bars("AAPL", fetch_fn=_fetch_boom, db_url=f"sqlite:///{tmp_path}/ingest_bad.db")
    assert bad["ok"] is False
    assert bad["bars_ingested"] == 0
    assert bad["errors"]
    _assert_envelope(bad, "ingest_bars")


def test_ingest_bars_accepts_registry_fetch_fn_db_url(tmp_path):
    """ingest_bars accepts registry/fetch_fn/db_url kwargs (test seam)."""
    from backend.instruments.registry import InstrumentRegistry

    seen: dict = {}

    def _fetch(symbol: str):
        seen["symbol"] = symbol
        return list(_fake_bars(3)), "yfinance"

    out = ingest_bars(
        "aapl",
        registry=InstrumentRegistry(),
        fetch_fn=_fetch,
        db_url=f"sqlite:///{tmp_path}/ingest_seam.db",
    )
    assert out["symbol"] == "AAPL"
    assert seen["symbol"] == "AAPL"
    assert out["ok"] is True
    _assert_envelope(out, "ingest_bars")


def test_refresh_forecast_real_ok_only_when_all_horizons_complete():
    """refresh_forecast is REAL via ForecastService: ok only if all 3 complete."""

    class _Full:
        def forecast(self, symbol: str, horizon: int) -> dict:
            return {"symbol": symbol, "horizon_days": horizon}

    full = refresh_forecast("AAPL", forecast=_Full())
    assert full["job"] == "refresh_forecast"
    assert full["ok"] is True
    assert full["horizons_completed"] == [5, 21, 63]
    assert full["errors"] == {}
    _assert_envelope(full, "refresh_forecast")

    class _Partial:
        def forecast(self, symbol: str, horizon: int) -> dict:
            if horizon == 63:
                raise ValueError("no bars")
            return {"symbol": symbol, "horizon_days": horizon}

    partial = refresh_forecast("AAPL", forecast=_Partial())
    assert partial["ok"] is False
    assert partial["horizons_completed"] == [5, 21]
    assert "63" in partial["errors"]
    _assert_envelope(partial, "refresh_forecast")


def test_refresh_forecast_accepts_forecast_market_kwargs():
    class _Svc:
        def forecast(self, symbol: str, horizon: int) -> dict:
            return {"symbol": symbol, "horizon_days": horizon}

    out = refresh_forecast("msft", forecast=_Svc(), market=object())
    assert out["symbol"] == "MSFT"
    assert out["ok"] is True
    _assert_envelope(out, "refresh_forecast")


def test_generate_report_honestly_not_implemented():
    """generate_report is NOT wired: ok False with errors.report set."""
    out = generate_report("AAPL", "quick_insight")
    assert out["job"] == "generate_report"
    assert out["ok"] is False
    assert "report" in out["errors"]
    assert "Not investment advice" in out["disclosure"]
    _assert_envelope(out, "generate_report")


def test_generate_report_profile_fallback_and_disclosure():
    out = generate_report("AAPL", "nope-not-a-profile")
    assert out["profile"] == "quick_insight"
    assert out["ok"] is False
    assert "Not investment advice" in out["disclosure"]
    for profile in REPORT_PROFILES:
        got = generate_report("AAPL", profile)
        assert got["profile"] == profile
        assert got["ok"] is False


def test_evaluate_alerts_db_none_honest_false():
    """evaluate_alerts with db=None honestly reports ok False (needs rules)."""
    out = evaluate_alerts()
    assert out["job"] == "evaluate_alerts"
    assert out["ok"] is False
    assert out["alerts_checked"] == 0 and out["alerts_fired"] == 0
    assert "db" in out["errors"]
    _assert_envelope(out, "evaluate_alerts")


def test_capture_snapshot_honest_shape():
    """capture_snapshot: ok True with bars, ok False without — envelope honest."""

    class _LiveMarket:
        def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 250) -> dict:
            return {
                "bars": [
                    {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
                     "volume": 100, "ts": "2026-09-11T00:00:00+00:00"}
                    for _ in range(10)
                ],
                "provenance": {
                    "source": "yfinance",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "delay_minutes": 15, "quality_grade": "B",
                    "fallback_used": False, "missing_fields": [],
                },
            }

    ok = capture_snapshot("AAPL", market=_LiveMarket())
    assert ok["job"] == "capture_snapshot"
    assert ok["ok"] is True
    _assert_envelope(ok, "capture_snapshot")

    class _DeadMarket:
        def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 250) -> dict:
            from backend.market_data.providers.base import ProviderError

            raise ProviderError("yfinance", "no live bars")

    bad = capture_snapshot("AAPL", market=_DeadMarket())
    assert bad["ok"] is False
    assert bad["errors"]
    _assert_envelope(bad, "capture_snapshot")


def test_score_forecasts_honest_shape():
    out = score_forecasts()
    assert out["job"] == "score_forecasts"
    assert out["ok"] is True
    assert out["scored"] == 0
    _assert_envelope(out, "score_forecasts")


def test_run_once_honest_mixed(monkeypatch):
    """run_once is honestly NOT all-ok: report/alerts are False by design."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_USE_REDIS", raising=False)
    outcome = run_once(symbols=("AAPL",), profile="quick_insight")
    # generate_report + evaluate_alerts(db=None) are honestly False.
    assert outcome["ok"] is False
    assert "evaluate_alerts" in outcome["results"]
    assert "ingest_bars:AAPL" in outcome["results"]
    for name, res in outcome["results"].items():
        assert "provenance" in res, name
        assert res["provenance"]["fallback_used"] is False, name


def test_cli_once_and_list(capsys, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_USE_REDIS", raising=False)
    assert jobs.main(["--list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in listed} == set(JOB_REGISTRY)

    # --once is honestly non-zero: report/alerts fail closed by design.
    assert jobs.main(["--once", "--symbol", "AAPL"]) == 1
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"] is False

    # Single honest-false job exits non-zero with its envelope.
    assert jobs.main(["--job", "evaluate_alerts"]) == 1
    single = json.loads(capsys.readouterr().out)
    assert single["job"] == "evaluate_alerts" and single["ok"] is False

    # Honest-true job exits zero.
    assert jobs.main(["--job", "score_forecasts"]) == 0
    scored = json.loads(capsys.readouterr().out)
    assert scored["job"] == "score_forecasts" and scored["ok"] is True


def test_no_hard_redis_dependency():
    # Importing workers must never require redis/rq/arq at module scope.
    assert "redis" not in sys.modules or sys.modules["redis"] is not None
    import ast
    from pathlib import Path

    tree = ast.parse((Path(jobs.__file__)).read_text())
    top_imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            top_imports.add((node.module or "").split(".")[0])
    assert top_imports.isdisjoint({"redis", "rq", "arq"})
