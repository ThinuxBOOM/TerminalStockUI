"""Background job stubs (M8 backend hardening, spec Sec 2 + Sec 6).

Arq/RQ-style registry with **no hard Redis dependency**. Each job logs
provenance + model/feature/data versions and is runnable in-memory via::

    python -m backend.workers.jobs --once
    python -m backend.workers.jobs --list
    python -m backend.workers.jobs --job ingest_bars --symbol AAPL

Jobs (registry of 6):
  - ingest_bars(symbol)            -> fetch/normalize OHLCV bars (stub)
  - refresh_forecast(symbol)       -> deterministic forecast refresh (stub)
  - evaluate_alerts()              -> alert-condition evaluation (stub)
  - generate_report(symbol, profile) -> scheduled report build (stub)
  - capture_snapshot(symbol, timeframe) -> compressed market snapshot
    (interval-configurable: 15m intraday / 1h daily; persists to
    market_snapshots when the table exists, in-memory fallback otherwise)
  - score_forecasts(symbol)        -> point-in-time forecast scoring
    (survivorship-aware; writes forecast_accuracy + refreshes the
    calibration_snapshots realized window)

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

#: Snapshot cadence defaults (minutes): intraday timeframes capture every
#: 15m, daily+ captures hourly. Overridable via env without code changes.
SNAPSHOT_INTERVAL_INTRADAY_MIN = 15
SNAPSHOT_INTERVAL_DAILY_MIN = 60
SNAPSHOT_DAILY_TIMEFRAMES = ("1d", "1w", "1mo")


def snapshot_interval_min(timeframe: str | None = None) -> int:
    """Capture cadence for *timeframe* (env-overridable, never raises)."""
    tf = (timeframe or "1d").strip() or "1d"
    try:
        if tf in SNAPSHOT_DAILY_TIMEFRAMES:
            return max(1, int(os.getenv("SNAPSHOT_INTERVAL_DAILY_MIN",
                                       str(SNAPSHOT_INTERVAL_DAILY_MIN))))
        return max(1, int(os.getenv("SNAPSHOT_INTERVAL_INTRADAY_MIN",
                                   str(SNAPSHOT_INTERVAL_INTRADAY_MIN))))
    except (TypeError, ValueError):
        return SNAPSHOT_INTERVAL_DAILY_MIN if tf in SNAPSHOT_DAILY_TIMEFRAMES \
            else SNAPSHOT_INTERVAL_INTRADAY_MIN

logger = logging.getLogger("onemarket.workers")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _data_version(now: Optional[datetime] = None) -> str:
    return f"{DATA_VERSION_FALLBACK_PREFIX}{(now or _utcnow()).date().isoformat()}"


def build_provenance(job: str, **extra: Any) -> dict:
    """Spec Sec 4 style provenance envelope + versions block for a job run.

    Job envelopes describe the RUN, not market data: they are never
    fallbacks (``fallback_used=False``). Success or failure is carried by
    the result's ``ok`` flag — a failed job is ``ok=False`` with a reason,
    never a fake-healthy envelope.
    """
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
        # Log the exception TYPE only: DB/audit messages can echo payload
        # text, and payloads must never leak key material into logs.
        logger.warning("audit append failed for job %s: %s", job, type(exc).__name__)


def _log_result(result: dict) -> dict:
    logger.info("job=%s ok=%s provenance=%s", result.get("job"), result.get("ok"),
                json.dumps(result.get("provenance", {}), default=str))
    return result


# ---------------------------------------------------------------- jobs ---

def ingest_bars(symbol: str, db: Any = None, **kwargs: Any) -> dict:
    """Ingest/normalize OHLCV bars for *symbol* via the real ingest chain.

    Fail-closed: per-symbol fetch/DB problems land in ``errors`` with
    ``ok=False`` when nothing was ingested — never a fake success.
    Optional kwargs (tests): ``registry``, ``fetch_fn``, ``db_url``.
    """
    job = "ingest_bars"
    sym = (symbol or "").strip().upper() or "AAPL"
    provenance = build_provenance(job, symbol=sym)
    try:
        from backend.market_data.ingest import ingest_symbols

        ingested, errors = ingest_symbols(
            [sym],
            db_url=kwargs.get("db_url"),
            registry=kwargs.get("registry"),
            fetch_fn=kwargs.get("fetch_fn"),
        )
    except Exception as exc:
        result = {
            "job": job, "ok": False, "symbol": sym,
            "bars_ingested": 0,
            "errors": {"_batch": f"{type(exc).__name__}: {str(exc)[:200]}"},
            "provenance": provenance,
        }
        _audit(db, job=job, entity_id=sym,
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    n_bars = sum(int(v) for v in (ingested or {}).values() if isinstance(v, (int, float)))
    ok = n_bars > 0 and not errors
    result = {
        "job": job, "ok": ok, "symbol": sym,
        "bars_ingested": n_bars,
        "errors": dict(errors or {}),
        "detail": "ingested via backend.market_data.ingest.ingest_symbols",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=sym,
           payload={"symbol": sym, "bars_ingested": n_bars, "ok": ok})
    return _log_result(result)


def refresh_forecast(symbol: str, db: Any = None, **kwargs: Any) -> dict:
    """Refresh the deterministic forecast for *symbol* (all horizons).

    Runs the real deterministic engine via :class:`ForecastService`.
    Fail-closed: engine failures (no bars, thin history) land in
    ``errors`` with ``ok=False`` — never a fake forecast. Optional
    kwargs (tests): ``forecast`` (ForecastService), ``market``.
    """
    job = "refresh_forecast"
    sym = (symbol or "").strip().upper() or "AAPL"
    provenance = build_provenance(job, symbol=sym)
    svc = kwargs.get("forecast")
    if svc is None:
        try:
            from backend.forecasting.service import ForecastService

            svc = ForecastService(market_service=kwargs.get("market"))
        except Exception as exc:
            result = {
                "job": job, "ok": False, "symbol": sym,
                "errors": {"_batch": f"{type(exc).__name__}: {str(exc)[:200]}"},
                "provenance": provenance,
            }
            _audit(db, job=job, entity_id=sym,
                   payload={"ok": False, "error": type(exc).__name__})
            return _log_result(result)
    horizons = [5, 21, 63]
    ran: dict[int, dict] = {}
    errors: dict[str, str] = {}
    for horizon in horizons:
        try:
            ran[horizon] = svc.forecast(sym, horizon)
        except Exception as exc:
            errors[str(horizon)] = f"{type(exc).__name__}: {str(exc)[:200]}"
    ok = bool(ran) and not errors
    result = {
        "job": job, "ok": ok, "symbol": sym,
        "horizons_days": horizons,
        "horizons_completed": sorted(ran),
        "errors": errors,
        "model_version": _MODEL_VERSION,
        "feature_version": _FEATURE_VERSION,
        "data_version": provenance["versions"]["data"],
        "detail": "deterministic engine refresh (no AI)",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=sym,
           payload={"symbol": sym, "horizons_completed": sorted(ran), "ok": ok})
    return _log_result(result)


def evaluate_alerts(db: Any = None, **kwargs: Any) -> dict:
    """Evaluate alert conditions via the shared alerts core.

    Without ``db`` this stays pure in-memory (no DB, no Redis, no network)
    so unit tests and ``--once`` never need infrastructure. With ``db`` it
    runs :func:`backend.api.alerts.evaluate_due_alerts` (same core as
    ``POST/GET /api/cron/evaluate``): fires due alerts, audits them, and
    attempts delivery best-effort. Optional ``market`` / ``forecast`` /
    ``notifier`` kwargs inject fakes (tests); otherwise service defaults
    are used. Delivery failure never blocks firing.
    """
    job = "evaluate_alerts"
    if db is None:
        # No alert definitions without a DB: report honestly instead of a
        # fake-healthy zero-check run.
        provenance = build_provenance(job)
        result = {
            "job": job, "ok": False,
            "alerts_checked": 0, "alerts_fired": 0,
            "errors": {"db": "no database: alert rules live in the alerts table"},
            "detail": "alert evaluation needs stored alert definitions",
            "provenance": provenance,
        }
        return _log_result(result)
    provenance = build_provenance(job)
    try:
        from backend.api.alerts import evaluate_due_alerts  # lazy: no hard api dep at import

        outcome = evaluate_due_alerts(
            db,
            market=kwargs.get("market"),
            forecast=kwargs.get("forecast"),
            notifier=kwargs.get("notifier"),
        )
    except Exception as exc:
        logger.warning("evaluate_alerts job failed: %s", type(exc).__name__)
        result = {
            "job": job, "ok": False,
            "alerts_checked": 0, "alerts_fired": 0,
            "errors": {"_batch": f"{type(exc).__name__}: {str(exc)[:200]}"},
            "provenance": provenance,
        }
        _audit(db, job=job, entity_id="all",
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    checked = 0
    try:
        checked = int(outcome.get("checked") or 0)  # type: ignore[union-attr]
    except (TypeError, ValueError, AttributeError):
        checked = 0
    try:
        raw_fired = outcome.get("fired") or []  # type: ignore[union-attr]
        fired_list = list(raw_fired) if isinstance(raw_fired, (list, tuple)) else []
    except (TypeError, ValueError, AttributeError):
        fired_list = []
    try:
        raw_errors = outcome.get("errors") or {}  # type: ignore[union-attr]
        errors = dict(raw_errors) if isinstance(raw_errors, dict) else {"_batch": "invalid-errors-shape"}
    except (TypeError, ValueError, AttributeError):
        errors = {}
    result = {
        "job": job, "ok": "_batch" not in errors,
        "alerts_checked": checked, "alerts_fired": len(fired_list),
        "fired": fired_list, "errors": errors,
        "detail": "alert rules evaluated via backend.api.alerts; delivery attempted best-effort",
        "disclosure": "Not investment advice. For informational purposes only.",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id="all",
           payload={"alerts_checked": checked,
                    "alerts_fired": len(fired_list)})
    return _log_result(result)


REPORT_PROFILES = ("quick_insight", "deep_research", "forecast_assist", "report")


def generate_report(symbol: str, profile: str = "quick_insight",
                    db: Any = None, **kwargs: Any) -> dict:
    """Scheduled AI report for *symbol* under task *profile* — NOT wired.

    Reports need a configured AI provider; until the report assembler
    lands, this job honestly reports ``ok=False`` instead of a fake
    success. Reachable via CLI only (no cron route, no UI).
    """
    job = "generate_report"
    sym = (symbol or "").strip().upper() or "AAPL"
    prof = (profile or "quick_insight").strip().lower()
    if prof not in REPORT_PROFILES:
        prof = "quick_insight"
    provenance = build_provenance(job, symbol=sym, profile=prof)
    result = {
        "job": job, "ok": False, "symbol": sym, "profile": prof,
        "errors": {"report": "not implemented: AI report assembler unwired"},
        "detail": "scheduled reports need a configured AI provider (V2)",
        "disclosure": "Not investment advice. For informational purposes only.",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=f"{sym}:{prof}",
           payload={"symbol": sym, "profile": prof, "ok": False})
    return _log_result(result)


def capture_snapshot(symbol: str, timeframe: str = "1d", db: Any = None,
                     **kwargs: Any) -> dict:
    """Capture a compressed market snapshot for *symbol*/*timeframe*.

    Fetches live bars via ``MarketDataService.get_bars`` (raises when no
    live data — recorded as ``ok=False``, never stubbed), compresses with
    :mod:`backend.market_data.snapshot_store` (smallest of gzip+json /
    delta-q100+gzip wins) and persists to ``market_snapshots`` when the
    table exists — otherwise the bounded in-memory store (graceful degrade
    before migration 0006). Optional kwargs: ``market`` (fake/service
    injection, tests), ``interval_min`` (cadence override; defaults to
    :func:`snapshot_interval_min`). Never raises on missing data: zero
    usable bars is ``ok=False`` with a reason, never an exception.
    """
    job = "capture_snapshot"
    sym = (symbol or "").strip().upper() or "AAPL"
    tf = (timeframe or kwargs.get("timeframe") or "1d").strip() or "1d"
    try:
        interval = int(kwargs.get("interval_min") or snapshot_interval_min(tf))
    except (TypeError, ValueError):
        interval = snapshot_interval_min(tf)
    provenance = build_provenance(job, symbol=sym, timeframe=tf)
    market = kwargs.get("market")
    if market is None:
        try:
            from backend.api.deps import get_market_service

            market = get_market_service()
        except Exception:
            try:
                from backend.market_data.service import MarketDataService

                market = MarketDataService()
            except Exception as exc:
                result = {"job": job, "ok": False, "symbol": sym, "timeframe": tf,
                          "errors": {"_batch": type(exc).__name__},
                          "provenance": provenance}
                _audit(db, job=job, entity_id=sym,
                       payload={"ok": False, "error": type(exc).__name__})
                return _log_result(result)
    try:
        payload = market.get_bars(sym, timeframe=tf, limit=250)
    except Exception as exc:
        result = {"job": job, "ok": False, "symbol": sym, "timeframe": tf,
                  "errors": {"bars": f"{type(exc).__name__}: {str(exc)[:200]}"},
                  "provenance": provenance}
        _audit(db, job=job, entity_id=sym,
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    rows = [r for r in (payload.get("bars") or [])
            if isinstance(r, dict) and r.get("close") is not None
            and r.get("open") is not None and r.get("high") is not None
            and r.get("low") is not None]
    provenance_block = payload.get("provenance") if isinstance(payload, dict) else None
    try:
        from backend.market_data import snapshot_store as _store

        if not rows:
            raise ValueError("no complete bars to snapshot")
        prov = dict(provenance_block or {})
        saved = _store.save_snapshot(
            db, symbol=sym, timeframe=tf, bars=rows,
            source=str(prov.get("source") or "yfinance"),
            quality_grade=str(prov.get("quality_grade") or "C"),
            provenance=prov)
    except ValueError as exc:
        result = {"job": job, "ok": False, "symbol": sym, "timeframe": tf,
                  "errors": {"bars": f"{type(exc).__name__}: {str(exc)[:200]}"},
                  "provenance": provenance}
        _audit(db, job=job, entity_id=sym,
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    except Exception as exc:  # pragma: no cover - defensive, never break ticks
        result = {"job": job, "ok": False, "symbol": sym, "timeframe": tf,
                  "errors": {"_batch": type(exc).__name__},
                  "provenance": provenance}
        _audit(db, job=job, entity_id=sym,
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    result = {
        "job": job, "ok": bool(saved.get("ok")), "symbol": sym, "timeframe": tf,
        "interval_min": interval, "snapshot_id": saved.get("snapshot_id"),
        "persisted": bool(saved.get("persisted")),
        "encoding": saved.get("encoding"), "n_bars": saved.get("n_bars"),
        "raw_bytes": saved.get("raw_bytes"),
        "compressed_bytes": saved.get("compressed_bytes"),
        "ratio": saved.get("ratio"),
        "size_reduction_pct": saved.get("size_reduction_pct"),
        "reason": saved.get("reason"),
        "detail": "compressed OHLCV snapshot (market_snapshots or in-memory fallback)",
        "provenance": provenance,
    }
    _audit(db, job=job, entity_id=f"{sym}:{tf}",
           payload={"symbol": sym, "timeframe": tf,
                    "encoding": saved.get("encoding"),
                    "n_bars": saved.get("n_bars"),
                    "persisted": bool(saved.get("persisted"))})
    return _log_result(result)


def score_forecasts(symbol: str | None = None, db: Any = None,
                    **kwargs: Any) -> dict:
    """Score matured forecasts point-in-time (survivorship-aware).

    Without ``db`` this stays pure in-memory (nothing to score: ``scored=0``,
    ``ok=True``) so unit tests and ``--once`` never need infrastructure.
    With ``db`` it runs
    :func:`backend.forecasting.accuracy.score_due_forecasts`: forecasts whose
    ``target_date`` is observable are scored from stored bars only, accuracy
    rows are appended, and the matching ``calibration_snapshots`` row gains a
    ``members["realized"]`` window feeding confidence evolution. Optional
    kwargs: ``symbols`` (list filter; ``symbol`` also accepted),
    ``market`` (reserved injection seam, currently unused — closes always
    resolve from stored bars to stay point-in-time).
    """
    job = "score_forecasts"
    provenance = build_provenance(job)
    syms: list[str] = []
    for raw in ([symbol] if symbol else []) + list(kwargs.get("symbols") or []):
        text = (raw or "").strip() if isinstance(raw, str) else ""
        if text:
            syms.append(text[:32])
    if db is None:
        result = {"job": job, "ok": True, "scored": 0, "unscored": 0,
                  "symbols": syms, "stub": True,
                  "detail": "in-memory stub: no DB, nothing scored",
                  "provenance": provenance}
        return _log_result(result)
    try:
        from backend.forecasting.accuracy import score_due_forecasts

        outcome = score_due_forecasts(db, symbols=syms or None)
    except Exception as exc:
        logger.warning("score_forecasts job failed: %s", type(exc).__name__)
        result = {"job": job, "ok": False, "scored": 0, "unscored": 0,
                  "symbols": syms,
                  "errors": {"_batch": f"{type(exc).__name__}: {str(exc)[:200]}"},
                  "provenance": provenance}
        _audit(db, job=job, entity_id="all",
               payload={"ok": False, "error": type(exc).__name__})
        return _log_result(result)
    result = {"job": job, "ok": not outcome.get("errors"), "symbols": syms,
              "scored": int(outcome.get("scored") or 0),
              "unscored": int(outcome.get("unscored") or 0),
              "hits": int(outcome.get("hits") or 0),
              "brier_mean": outcome.get("brier_mean"),
              "errors": dict(outcome.get("errors") or {}),
              "detail": "point-in-time scoring; per-row accuracy follows the "
                        "forecasts-tied window, aggregates persist via calibration",
              "disclosure": "Not investment advice. For informational purposes only.",
              "provenance": provenance}
    _audit(db, job=job, entity_id="all",
           payload={"scored": result["scored"], "unscored": result["unscored"],
                    "hits": result["hits"]})
    return _log_result(result)


JOB_REGISTRY: dict[str, Callable[..., dict]] = {
    "ingest_bars": ingest_bars,
    "refresh_forecast": refresh_forecast,
    "evaluate_alerts": evaluate_alerts,
    "generate_report": generate_report,
    "capture_snapshot": capture_snapshot,
    "score_forecasts": score_forecasts,
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
                # Type only: broker errors can echo the Redis URL (credentials).
                logger.warning("redis enqueue failed, falling back to in-process: %s",
                               type(exc).__name__)
    return fn(*args, **kwargs)


def run_once(symbols: tuple[str, ...] = ("AAPL",), profile: str = "quick_insight",
             db: Any = None) -> dict:
    """Run the full job set once in-memory (scheduler tick / smoke test)."""
    results: dict[str, Any] = {}
    try:
        syms = tuple(symbols) if symbols else ("AAPL",)
    except TypeError:
        syms = ("AAPL",)
    # Bound fan-out: scheduler ticks stay cheap even if callers pass huge lists.
    syms = tuple(syms[:25]) or ("AAPL",)
    for sym in syms:
        results[f"ingest_bars:{sym}"] = ingest_bars(sym, db=db)
        results[f"refresh_forecast:{sym}"] = refresh_forecast(sym, db=db)
        results[f"capture_snapshot:{sym}"] = capture_snapshot(sym, db=db)
        results[f"generate_report:{sym}:{profile}"] = generate_report(sym, profile, db=db)
    results["evaluate_alerts"] = evaluate_alerts(db=db)
    results["score_forecasts"] = score_forecasts(db=db)
    return {"ok": all(r.get("ok") for r in results.values()), "results": results}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OneMarket worker jobs (in-memory by default).")
    parser.add_argument("--once", action="store_true", help="Run all jobs once in-memory and exit.")
    parser.add_argument("--list", action="store_true", help="Print the job registry and exit.")
    parser.add_argument("--job", default=None, help="Run a single job by name.")
    parser.add_argument("--symbol", default="AAPL", help="Symbol for single-job runs.")
    parser.add_argument("--timeframe", default="1d", help="Bars timeframe for capture_snapshot.")
    parser.add_argument("--profile", default="quick_insight", help="Report profile for generate_report.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    if args.list:
        print(json.dumps(list_jobs(), indent=2))
        return 0
    if args.job:
        if args.job == "generate_report":
            job_kwargs: dict[str, Any] = {"symbol": args.symbol, "profile": args.profile}
        elif args.job == "capture_snapshot":
            job_kwargs = {"symbol": args.symbol, "timeframe": args.timeframe}
        elif args.job in ("evaluate_alerts", "score_forecasts"):
            job_kwargs = {}
        else:
            job_kwargs = {"symbol": args.symbol}
        result = enqueue(args.job, **job_kwargs)
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
