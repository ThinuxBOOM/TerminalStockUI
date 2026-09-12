"""Background job stubs (M8 backend hardening, spec Sec 2 + Sec 6).

Arq/RQ-style registry with **no hard Redis dependency**. Each job logs
provenance + model/feature/data versions and is runnable in-memory via::

    python -m backend.workers.jobs --once
    python -m backend.workers.jobs --list
    python -m backend.workers.jobs --job ingest_bars --symbol AAPL

Jobs (registry of 4):
  - ingest_bars(symbol)            -> fetch/normalize OHLCV bars (stub)
  - refresh_forecast(symbol)       -> deterministic forecast refresh (stub)
  - evaluate_alerts()              -> alert-condition evaluation (stub)
  - generate_report(symbol, profile) -> scheduled report build (stub)

Every job returns a JSON-serializable dict carrying a spec Sec 4 style
provenance envelope (source/as_of/delay_minutes/quality_grade/
fallback_used/missing_fields) plus a ``versions`` block
(backend/model/feature/data/worker). When a SQLAlchemy session is passed
(``db=``), the job also appends a redacted audit event via
``backend.api.audit.append_audit_log``; without ``db`` it stays pure
in-memory (no DB, no Redis, no network).

Queue note: :func:`enqueue` pushes to Redis/RQ/Arq only when both
``REDIS_URL`` and a client library are available; otherwise it runs the
job in-process. Importing this module never requires redis/rq/arq.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Optional

try:  # canonical: python -m backend.workers.jobs (repo root on path)
    from backend import __version__ as BACKEND_VERSION
except Exception:  # pragma: no cover
    BACKEND_VERSION = "0.1.0"

try:
    from backend.forecasting.features.features import FEATURE_VERSION as _FEATURE_VERSION
except Exception:  # pragma: no cover
    _FEATURE_VERSION = "features-v1"

try:
    from backend.forecasting.models.historical_drift import MODEL_VERSION as _MODEL_VERSION
except Exception:  # pragma: no cover
    _MODEL_VERSION = "historical-drift-v1"

WORKER_VERSION = "workers-v1"
QUEUE_NAME = "onemarket"
DATA_VERSION_FALLBACK_PREFIX = "bars-"

logger = logging.getLogger("onemarket.workers")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _data_version(now: Optional[datetime] = None) -> str:
    return f"{DATA_VERSION_FALLBACK_PREFIX}{(now or _utcnow()).date().isoformat()}"


def build_provenance(job: str, **extra: Any) -> dict:
    """Spec Sec 4 style provenance envelope + versions block for a job run."""
    now = _utcnow()
    prov: dict[str, Any] = {
        "source": f"worker:{job}",
        "as_of": now.isoformat(),
        "delay_minutes": 0,
        "quality_grade": "B",
        "fallback_used": False,
        "missing_fields": [],
        "actor": f"worker:{job}",
        "versions": {
            "backend": BACKEND_VERSION,
            "worker": WORKER_VERSION,
            "model": _MODEL_VERSION,
            "feature": _FEATURE_VERSION,
            "data": _data_version(now),
        },
    }
    prov.update(extra)
    return prov


def _audit(db: Any, *, job: str, entity_id: str, payload: dict) -> None:
    """Best-effort audit append; silent no-op when db is None/unusable."""
    if db is None:
        return
    try:
        from backend.api.audit import append_audit_log  # lazy: no hard api dep at import
    except Exception as exc:  # pragma: no cover
        logger.debug("audit append skipped (import): %s", exc)
        return
    try:
        append_audit_log(
            db,
            actor=f"worker:{job}",
            action=f"worker.{job}",
            entity_type="worker_job",
            entity_id=str(entity_id),
            payload=dict(payload or {}),
        )
    except Exception as exc:
        logger.warning("audit append failed for job %s: %s", job, exc)


def _log_result(result: dict) -> dict:
    logger.info("job=%s ok=%s provenance=%s", result.get("job"), result.get("ok"),
                json.dumps(result.get("provenance", {}), default=str))
    return result


# ---------------------------------------------------------------- jobs ---

def ingest_bars(symbol: str, db: Any = None, **kwargs: Any) -> dict:
    """Stub: ingest/normalize OHLCV bars for *symbol* (no network, no Redis)."""
    job = "ingest_bars"
    sym = (symbol or "").strip().upper() or "AAPL"
    provenance = build_provenance(job, symbol=sym)
    result = {
        "job": job, "ok": True, "symbol": sym,
        "bars_ingested": 0, "stub": True,
        "detail": "in-memory stub: no provider call; wire MarketDataService here in prod",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=sym, payload={"symbol": sym, "stub": True})
    return _log_result(result)


def refresh_forecast(symbol: str, db: Any = None, **kwargs: Any) -> dict:
    """Stub: refresh the deterministic forecast for *symbol* (no training)."""
    job = "refresh_forecast"
    sym = (symbol or "").strip().upper() or "AAPL"
    provenance = build_provenance(job, symbol=sym)
    result = {
        "job": job, "ok": True, "symbol": sym,
        "horizons_days": [5, 21, 63], "stub": True,
        "detail": "in-memory stub: deterministic engine owns forecasts; AI disabled",
        "model_version": _MODEL_VERSION,
        "feature_version": _FEATURE_VERSION,
        "data_version": provenance["versions"]["data"],
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=sym,
           payload={"symbol": sym, "model_version": _MODEL_VERSION,
                    "feature_version": _FEATURE_VERSION, "stub": True})
    return _log_result(result)


def evaluate_alerts(db: Any = None, **kwargs: Any) -> dict:
    """Stub: evaluate alert conditions (no notification fan-out here)."""
    job = "evaluate_alerts"
    provenance = build_provenance(job)
    result = {
        "job": job, "ok": True,
        "alerts_checked": 0, "alerts_fired": 0, "stub": True,
        "detail": "in-memory stub: alert rules evaluated; delivery lives outside workers",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id="all", payload={"stub": True})
    return _log_result(result)


REPORT_PROFILES = ("quick_insight", "deep_research", "forecast_assist", "report")


def generate_report(symbol: str, profile: str = "quick_insight",
                    db: Any = None, **kwargs: Any) -> dict:
    """Stub: build a scheduled report for *symbol* under task *profile*."""
    job = "generate_report"
    sym = (symbol or "").strip().upper() or "AAPL"
    prof = (profile or "quick_insight").strip().lower()
    if prof not in REPORT_PROFILES:
        prof = "quick_insight"
    provenance = build_provenance(job, symbol=sym, profile=prof)
    result = {
        "job": job, "ok": True, "symbol": sym, "profile": prof,
        "stub": True,
        "detail": "in-memory stub: evidence packet + bounded AI opinion assembled in prod",
        "disclosure": "Not investment advice. For informational purposes only.",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=f"{sym}:{prof}",
           payload={"symbol": sym, "profile": prof, "stub": True})
    return _log_result(result)


JOB_REGISTRY: dict[str, Callable[..., dict]] = {
    "ingest_bars": ingest_bars,
    "refresh_forecast": refresh_forecast,
    "evaluate_alerts": evaluate_alerts,
    "generate_report": generate_report,
}
JOB_NAMES: list[str] = sorted(JOB_REGISTRY)


def list_jobs() -> list[dict]:
    """Registry listing: [{name, queue}] for health/scheduler introspection."""
    return [{"name": name, "queue": QUEUE_NAME} for name in JOB_NAMES]


def enqueue(job_name: str, *args: Any, **kwargs: Any) -> dict:
    """Run *job_name* in-process; push to Redis only when explicitly enabled.

    Production may set ``WORKER_USE_REDIS=1`` + ``REDIS_URL`` with ``rq`` or
    ``arq`` installed. Every other configuration (default) executes the job
    synchronously in-memory so tests and ``--once`` never need Redis.
    """
    fn = JOB_REGISTRY.get(job_name)
    if fn is None:
        raise KeyError(f"unknown job {job_name!r}; expected one of {JOB_NAMES}")
    if os.getenv("WORKER_USE_REDIS", "").strip().lower() in ("1", "true", "yes"):
        redis_url = os.getenv("REDIS_URL", "").strip()
        if redis_url:
            try:
                import rq  # type: ignore[import-not-found]  # optional broker
                import redis  # type: ignore[import-not-found]  # optional broker

                q = rq.Queue(QUEUE_NAME, connection=redis.Redis.from_url(redis_url))
                rq_job = q.enqueue(fn, *args, **kwargs)
                return {"job": job_name, "ok": True, "queued": True,
                        "rq_id": getattr(rq_job, "id", None)}
            except Exception as exc:
                logger.warning("redis enqueue failed, falling back to in-process: %s", exc)
    return fn(*args, **kwargs)


def run_once(symbols: tuple[str, ...] = ("AAPL",), profile: str = "quick_insight",
             db: Any = None) -> dict:
    """Run the full job set once in-memory (scheduler tick / smoke test)."""
    results: dict[str, Any] = {}
    for sym in symbols:
        results[f"ingest_bars:{sym}"] = ingest_bars(sym, db=db)
        results[f"refresh_forecast:{sym}"] = refresh_forecast(sym, db=db)
        results[f"generate_report:{sym}:{profile}"] = generate_report(sym, profile, db=db)
    results["evaluate_alerts"] = evaluate_alerts(db=db)
    return {"ok": all(r.get("ok") for r in results.values()), "results": results}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OneMarket worker jobs (in-memory by default).")
    parser.add_argument("--once", action="store_true", help="Run all jobs once in-memory and exit.")
    parser.add_argument("--list", action="store_true", help="Print the job registry and exit.")
    parser.add_argument("--job", default=None, help="Run a single job by name.")
    parser.add_argument("--symbol", default="AAPL", help="Symbol for single-job runs.")
    parser.add_argument("--profile", default="quick_insight", help="Report profile for generate_report.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.list:
        print(json.dumps(list_jobs(), indent=2))
        return 0
    if args.job:
        result = enqueue(args.job, **({"symbol": args.symbol, "profile": args.profile}
                                      if args.job == "generate_report"
                                      else ({} if args.job == "evaluate_alerts"
                                            else {"symbol": args.symbol})))
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1
    if args.once:
        outcome = run_once(symbols=(args.symbol,), profile=args.profile)
        print(json.dumps(outcome, indent=2, default=str))
        return 0 if outcome.get("ok") else 1
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
