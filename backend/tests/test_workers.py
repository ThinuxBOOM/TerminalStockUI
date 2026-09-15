"""Worker tests (M8 hardening, spec Sec 2 + Sec 6).

In-memory only, no Redis. Covers: registry lists 4 jobs, each job returns
provenance + versions, --once runs without broker/DB.
"""

from __future__ import annotations

import json
import sys

from backend.workers import jobs
from backend.workers.jobs import (
    JOB_NAMES,
    JOB_REGISTRY,
    REPORT_PROFILES,
    evaluate_alerts,
    generate_report,
    ingest_bars,
    refresh_forecast,
    run_once,
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


def _assert_provenance(result: dict, job: str):
    assert result["job"] == job and result["ok"] is True
    prov = result["provenance"]
    for key in ("source", "as_of", "delay_minutes", "quality_grade",
                "fallback_used", "missing_fields"):
        assert key in prov, f"{job} provenance missing {key!r}"
    versions = prov["versions"]
    for key in ("backend", "worker", "model", "feature", "data"):
        assert versions.get(key), f"{job} versions missing {key!r}"
    json.dumps(result, default=str)  # JSON-serializable


def test_jobs_log_provenance_and_versions_in_memory():
    _assert_provenance(ingest_bars("AAPL"), "ingest_bars")
    _assert_provenance(refresh_forecast("aapl"), "refresh_forecast")
    _assert_provenance(evaluate_alerts(), "evaluate_alerts")
    _assert_provenance(generate_report("MC.PA", "deep_research"), "generate_report")


def test_generate_report_profile_fallback_and_disclosure():
    out = generate_report("AAPL", "nope-not-a-profile")
    assert out["profile"] == "quick_insight"
    assert "Not investment advice" in out["disclosure"]
    for profile in REPORT_PROFILES:
        assert generate_report("AAPL", profile)["profile"] == profile


def test_run_once_in_memory_without_redis_or_db(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("WORKER_USE_REDIS", raising=False)
    outcome = run_once(symbols=("AAPL",), profile="quick_insight")
    assert outcome["ok"] is True
    assert "evaluate_alerts" in outcome["results"]
    assert "ingest_bars:AAPL" in outcome["results"]


def test_cli_once_and_list(capsys):
    assert jobs.main(["--list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in listed} == set(JOB_REGISTRY)

    assert jobs.main(["--once", "--symbol", "AAPL"]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"] is True

    assert jobs.main(["--job", "evaluate_alerts"]) == 0
    single = json.loads(capsys.readouterr().out)
    assert single["job"] == "evaluate_alerts" and single["ok"] is True


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
