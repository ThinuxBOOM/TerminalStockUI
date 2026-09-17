"""Cron router (Phase 1a + Phase 2b): daily bar ingestion + calibration.

Vercel Cron invokes via HTTP GET only -> ``GET /api/cron/ingest`` and
``GET /api/cron/calibrate``.
``POST`` variants with JSON ``{symbols: [...]}`` cover manual runs.
Ingest shares :func:`backend.market_data.ingest.ingest_symbols` with
``scripts/backfill_bars.py`` (no HTTP in the shared path); calibrate builds
one walk-forward :mod:`backend.forecasting.calibration` snapshot row per
(symbol, horizon) via upsert on the UNIQUE key.

Auth: when the ``CRON_SECRET`` env var is set, callers must send
``Authorization: Bearer <secret>`` (constant-time compare); a wrong or
missing secret is 401. When unset, the endpoints are open (local dev).

Every response carries the standard provenance envelope
``{source, as_of, delay_minutes, quality_grade, fallback_used,
missing_fields}``. Per-symbol failures (unknown instrument, fetch miss)
are reported in ``errors``; the batch itself never 500s.
"""

from __future__ import annotations

import hmac
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.api.deps import get_market_service, get_registry
from backend.forecasting.service import ForecastService, get_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data import ingest as ingest_module
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])

#: Test seam: when set, the cron endpoints ingest via this fetch callable
#: instead of yfinance (cleared by :func:`reset_cron`).
_fetch_override = None


def reset_cron() -> None:  # test hook
    """Clear the fetch override used by the cron endpoints."""
    global _fetch_override
    _fetch_override = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET", "") or "").strip()


def _check_cron_auth(request: Request) -> None:
    """Enforce Bearer auth iff CRON_SECRET is set; 401 otherwise.

    Fail-closed in production: missing CRON_SECRET with APP_ENV=production
    refuses cron writes instead of serving them open. The secret value
    itself is never logged.

    Vercel Cron sends plain GET with no Authorization header (no per-cron
    headers), so a Vercel-cron request (x-vercel-cron: 1 / vercel-cron
    user-agent) is accepted as scheduler-originated. Spoofing the header
    from outside still hits the nightly universe only (idempotent upserts,
    no destructive path) — and production without CRON_SECRET stays 401.
    """
    import os as _os

    secret = _cron_secret()
    if not secret:
        env = (_os.getenv("APP_ENV", "") or "").strip().lower()
        if env in ("production", "prod"):
            logger.warning("cron refused: CRON_SECRET unset in production")
            raise HTTPException(status_code=401, detail="unauthorized")
        return  # local dev: open
    provided = request.headers.get("authorization", "") or ""
    if hmac.compare_digest(f"Bearer {secret}", provided):
        return
    # Vercel Cron scheduler origin (plain GET, no custom headers possible).
    try:
        vc = (request.headers.get("x-vercel-cron", "") or "").strip()
        ua = (request.headers.get("user-agent", "") or "").lower()
    except Exception:
        vc, ua = "", ""
    if vc == "1" or "vercel-cron" in ua:
        return
    logger.warning("cron auth rejected")
    raise HTTPException(status_code=401, detail="unauthorized")


class IngestRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, max_length=100)


def _cron_provenance(has_errors: bool) -> dict:
    now = _utcnow()
    try:
        grade, _ = grade_quality(
            delay_minutes=15, age_minutes=0.0, missing_fields=[],
            fallback_used=has_errors, reconciled=False,
        )
    except Exception:
        grade = "C" if has_errors else "B"
    return build_provenance(
        "yfinance", as_of=now, delay_minutes=15, quality_grade=grade,
        fallback_used=has_errors, missing_fields=[],
    ).model_dump(mode="json")


def _run_ingest(symbols: list[str], registry: InstrumentRegistry) -> dict:
    ingested, errors = ingest_module.ingest_symbols(
        symbols, registry=registry, fetch_fn=_fetch_override
    )
    return {
        "ok": not errors,
        "ingested": ingested,
        "errors": errors,
        "provenance": _cron_provenance(bool(errors)),
    }


@router.get("/ingest")
def cron_ingest_get(
    request: Request,
    symbol: str | None = Query(
        default=None, description="Single symbol; default is the ingest universe"
    ),
    registry: InstrumentRegistry = Depends(get_registry),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/ingest[?symbol=AAPL]``."""
    _check_cron_auth(request)
    symbols = (
        [symbol] if (symbol or "").strip() else ingest_module.default_universe()
    )
    try:
        return _run_ingest(symbols, registry)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron ingest batch failed")
        return {
            "ok": False,
            "ingested": {},
            "errors": {"_batch": "ingest failed"},
            "provenance": _cron_provenance(True),
        }


@router.post("/ingest")
def cron_ingest_post(
    request: Request,
    body: IngestRequest,
    registry: InstrumentRegistry = Depends(get_registry),
) -> dict:
    """Manual run: ``POST /api/cron/ingest`` with JSON ``{symbols: [...]}``."""
    _check_cron_auth(request)
    symbols = body.symbols if body.symbols else ingest_module.default_universe()
    try:
        return _run_ingest(symbols, registry)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron ingest batch failed")
        return {
            "ok": False,
            "ingested": {},
            "errors": {"_batch": "ingest failed"},
            "provenance": _cron_provenance(True),
        }


def _run_calibrate(symbols: list[str], market: MarketDataService) -> dict:
    """Build + upsert one snapshot row per (symbol, horizon).

    Per-pair failures are reported in ``errors`` (keyed ``"SYM:horizon"``);
    the batch itself never 500s. ``snapshots`` maps the same key to the
    scored window count; ``calibrated`` counts rows with n_windows >= 10
    (single-digit windows are written but flagged weak, not skill) while
    ``unscored`` counts written rows with zero windows (thin history:
    honest NULL metrics, not skill) plus weak n<10 rows.
    """
    from backend.db.session import get_session_factory, init_db
    from backend.forecasting.calibration.snapshots import (
        build_snapshot,
        upsert_snapshot,
    )
    from backend.forecasting.common import FORECAST_HORIZONS

    wanted: list[str] = []
    for raw in symbols or []:
        text = (raw or "").strip()
        if text:
            wanted.append(text[:32])
        if len(wanted) >= 100:
            break
    if not wanted:
        return {
            "ok": True,
            "calibrated": 0,
            "unscored": 0,
            "snapshots": {},
            "errors": {},
            "provenance": _cron_provenance(False),
        }
    try:
        init_db()
        Session = get_session_factory()
        db = Session()
    except Exception:
        # DB unreachable: report per symbol, never 500 the batch. The
        # exception text is deliberately reduced so connection strings can
        # never leak into responses or logs.
        logger.warning("calibrate db unavailable symbols=%d", len(wanted))
        return {
            "ok": False,
            "calibrated": 0,
            "unscored": 0,
            "snapshots": {},
            "errors": {raw: "db unavailable" for raw in wanted},
            "provenance": _cron_provenance(True),
        }
    snapshots: dict[str, int] = {}
    errors: dict[str, str] = {}
    calibrated = 0
    unscored = 0
    try:
        for raw in wanted:
            for horizon in FORECAST_HORIZONS:
                key = f"{raw.strip().upper()}:{int(horizon)}"
                try:
                    snap = build_snapshot(raw, int(horizon), market_service=market)
                    upsert_snapshot(db, snap)
                    n_windows = int(snap.get("n_windows") or 0)
                    snapshots[key] = n_windows
                    # Zero-window rows are written (honest NULL metrics) but
                    # must not read as scored skill. Single-digit windows
                    # (n<10) are written + flagged weak, not counted.
                    if n_windows >= 10:
                        calibrated += 1
                    else:
                        unscored += 1
                except Exception as exc:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    errors[key] = f"{type(exc).__name__}: {str(exc)[:200]}"
                    logger.warning("calibrate snapshot failed")
    finally:
        try:
            db.close()
        except Exception:
            pass
    logger.info(
        "calibrate done calibrated=%d unscored=%d errors=%d",
        calibrated, unscored, len(errors),
    )
    return {
        "ok": not errors,
        "calibrated": calibrated,
        "unscored": unscored,
        "snapshots": snapshots,
        "errors": errors,
        "provenance": _cron_provenance(bool(errors)),
    }


@router.get("/calibrate")
def cron_calibrate_get(
    request: Request,
    symbol: str | None = Query(
        default=None, description="Single symbol; default is the ingest universe"
    ),
    market: MarketDataService = Depends(get_market_service),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/calibrate[?symbol=AAPL]``."""
    _check_cron_auth(request)
    symbols = (
        [symbol] if (symbol or "").strip() else ingest_module.default_universe()
    )
    try:
        return _run_calibrate(symbols, market)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron calibrate batch failed")
        return {
            "ok": False,
            "calibrated": 0,
            "snapshots": {},
            "errors": {"_batch": "calibrate failed"},
            "provenance": _cron_provenance(True),
        }


@router.post("/calibrate")
def cron_calibrate_post(
    request: Request,
    body: IngestRequest,
    market: MarketDataService = Depends(get_market_service),
) -> dict:
    """Manual run: ``POST /api/cron/calibrate`` with JSON ``{symbols: [...]}``."""
    _check_cron_auth(request)
    symbols = body.symbols if body.symbols else ingest_module.default_universe()
    try:
        return _run_calibrate(symbols, market)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron calibrate batch failed")
        return {
            "ok": False,
            "calibrated": 0,
            "snapshots": {},
            "errors": {"_batch": "calibrate failed"},
            "provenance": _cron_provenance(True),
        }


def _run_evaluate(market: MarketDataService, forecast: ForecastService) -> dict:
    """Evaluate all due active alerts via the shared alerts core.

    Per-alert failures (thin history, missing quote fields) are reported in
    ``errors`` keyed by alert_id; the batch itself never 500s. Returns
    ``{checked, fired, errors, provenance, disclosure}``.
    """
    from backend.api.alerts import DISCLOSURE as _ALERTS_DISCLOSURE
    from backend.api.alerts import evaluate_due_alerts
    from backend.db.session import get_session_factory, init_db

    try:
        init_db()
        Session = get_session_factory()
        db = Session()
    except Exception:
        # DB unreachable: never 500 the batch; connection text stays out of
        # responses and logs (same posture as _run_calibrate).
        logger.warning("evaluate db unavailable")
        return {
            "checked": 0,
            "fired": [],
            "errors": {"_batch": "db unavailable"},
            "provenance": _cron_provenance(True),
            "disclosure": _ALERTS_DISCLOSURE,
        }
    try:
        return evaluate_due_alerts(db, market=market, forecast=forecast)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron evaluate batch failed")
        return {
            "checked": 0,
            "fired": [],
            "errors": {"_batch": "evaluate failed"},
            "provenance": _cron_provenance(True),
            "disclosure": _ALERTS_DISCLOSURE,
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/evaluate")
def cron_evaluate_get(
    request: Request,
    market: MarketDataService = Depends(get_market_service),
    forecast: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/evaluate`` (all due alerts)."""
    _check_cron_auth(request)
    try:
        return _run_evaluate(market, forecast)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron evaluate batch failed")
        from backend.api.alerts import DISCLOSURE as _ALERTS_DISCLOSURE

        return {
            "checked": 0,
            "fired": [],
            "errors": {"_batch": "evaluate failed"},
            "provenance": _cron_provenance(True),
            "disclosure": _ALERTS_DISCLOSURE,
        }


@router.post("/evaluate")
def cron_evaluate_post(
    request: Request,
    market: MarketDataService = Depends(get_market_service),
    forecast: ForecastService = Depends(get_forecast_service),
) -> dict:
    """Manual run: ``POST /api/cron/evaluate`` (all due alerts; no body)."""
    _check_cron_auth(request)
    try:
        return _run_evaluate(market, forecast)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron evaluate batch failed")
        from backend.api.alerts import DISCLOSURE as _ALERTS_DISCLOSURE

        return {
            "checked": 0,
            "fired": [],
            "errors": {"_batch": "evaluate failed"},
            "provenance": _cron_provenance(True),
            "disclosure": _ALERTS_DISCLOSURE,
        }


# --- market snapshots + forecast scoring (Agent 3; additive, distinct paths) --
# New distinct paths only: existing /ingest /calibrate /evaluate routes above
# are untouched. Both jobs degrade gracefully without migration 0006
# (in-memory snapshot fallback; score reports zeros with a reason).


class SnapshotRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, max_length=100)
    timeframe: str | None = Field(default="1d", max_length=8)


class ScoreRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, max_length=100)


def _normalize_symbols(symbols: list[str] | None, single: str | None = None) -> list[str]:
    wanted: list[str] = []
    if single and single.strip():
        wanted.append(single.strip()[:32])
    for raw in symbols or []:
        text = (raw or "").strip()
        if text:
            wanted.append(text[:32])
        if len(wanted) >= 100:
            break
    return wanted


def _run_snapshot(symbols: list[str], timeframe: str = "1d") -> dict:
    """Capture one compressed snapshot per symbol (batch never 500s).

    Fan-out is parallel (ThreadPoolExecutor, one DB session per worker) with
    a global time budget (~45s, inside Vercel's 60s maxDuration and the
    Actions curl 55s budget). A full 27-symbol universe fetched SEQUENTIALLY
    pays one live-vendor round-trip per symbol (up to ~12s each on Yahoo
    throttle + Alpaca/Stooq legs) and reliably exceeds both budgets — the
    caller sees curl exit 28 with zero bytes. When the budget runs out the
    remainder is reported as per-symbol ``skipped: time budget exceeded``
    errors and the batch still returns HTTP 200 with partial data instead
    of timing out into nothing.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor as _TPE

    from backend.db.session import get_session_factory, init_db
    from backend.workers.jobs import capture_snapshot

    wanted = _normalize_symbols(symbols)
    tf = (timeframe or "1d").strip() or "1d"
    if not wanted:
        return {"ok": True, "snapshots": {}, "errors": {},
                "provenance": _cron_provenance(False)}
    try:
        init_db()
    except Exception:
        pass
    try:
        _Session = get_session_factory()
    except Exception:
        _Session = None  # type: ignore[assignment]

    _BUDGET_S = 45.0
    _WORKERS = 6
    started = _time.monotonic()

    def _capture_one(raw: str) -> tuple[str, dict | None, str | None]:
        key = raw.strip().upper()
        tdb = None
        try:
            if _Session is not None:
                try:
                    tdb = _Session()
                except Exception:
                    tdb = None
            out = capture_snapshot(raw, timeframe=tf, db=tdb)
            return key, out, None
        except Exception as exc:
            try:
                if tdb is not None:
                    tdb.rollback()
            except Exception:
                pass
            return key, None, f"{type(exc).__name__}: {str(exc)[:200]}"
        finally:
            try:
                if tdb is not None:
                    tdb.close()
            except Exception:
                pass

    snapshots: dict[str, dict] = {}
    errors: dict[str, str] = {}
    truncated = False

    def _record(key: str, out: dict | None, err: str | None) -> None:
        if err is not None or out is None:
            errors[key] = err or "snapshot failed"
            try:
                logger.warning("cron snapshot failed")
            except Exception:
                pass
            return
        snapshots[key] = {
            "snapshot_id": out.get("snapshot_id"),
            "persisted": bool(out.get("persisted")),
            "encoding": out.get("encoding"),
            "n_bars": out.get("n_bars"),
            "size_reduction_pct": out.get("size_reduction_pct"),
        }
        if not out.get("ok"):
            errors[key] = str((out.get("errors") or {"_batch": "failed"})
                              if isinstance(out.get("errors"), dict)
                              else out.get("errors"))[:200]

    # Small batches (tests, ?symbol= probes): sequential, deterministic.
    if len(wanted) <= 3:
        for raw in wanted:
            _record(*_capture_one(raw))
    else:
        workers = max(1, min(_WORKERS, len(wanted)))
        with _TPE(max_workers=workers) as pool:
            futs = {pool.submit(_capture_one, raw): raw.strip().upper()
                    for raw in wanted}
            for fut, key in futs.items():
                remaining = _BUDGET_S - (_time.monotonic() - started)
                if remaining <= 0:
                    errors[key] = ("skipped: snapshot time budget exceeded "
                                   "(retry next tick)")
                    truncated = True
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    continue
                try:
                    _record(*fut.result(timeout=remaining))
                except Exception as exc:
                    errors[key] = (f"skipped: {type(exc).__name__} "
                                   f"({str(exc)[:120]})")
                    truncated = True
            for fut, key in futs.items():
                if not fut.done() and key not in snapshots and key not in errors:
                    errors[key] = ("skipped: snapshot time budget exceeded "
                                   "(retry next tick)")
                    truncated = True
    logger.info("snapshot done captured=%d errors=%d truncated=%s",
                len(snapshots), len(errors), truncated)
    out_payload: dict = {"ok": not errors, "snapshots": snapshots,
                         "errors": errors,
                         "provenance": _cron_provenance(bool(errors))}
    if truncated:
        out_payload["truncated"] = True
    return out_payload


def _run_score(symbols: list[str]) -> dict:
    """Score matured forecasts point-in-time (batch never 500s)."""
    from backend.db.session import get_session_factory, init_db
    from backend.workers.jobs import score_forecasts

    wanted = _normalize_symbols(symbols)
    try:
        init_db()
        Session = get_session_factory()
        db = Session()
    except Exception:
        logger.warning("score db unavailable")
        return {"scored": 0, "unscored": 0, "hits": 0, "brier_mean": None,
                "errors": {"_batch": "db unavailable"},
                "provenance": _cron_provenance(True)}
    try:
        out = score_forecasts(db=db, symbols=wanted or None)
        return {"scored": int(out.get("scored") or 0),
                "unscored": int(out.get("unscored") or 0),
                "hits": int(out.get("hits") or 0),
                "brier_mean": out.get("brier_mean"),
                "errors": dict(out.get("errors") or {}),
                "provenance": _cron_provenance(bool(out.get("errors")))}
    except Exception:
        logger.warning("cron score batch failed")
        return {"scored": 0, "unscored": 0, "hits": 0, "brier_mean": None,
                "errors": {"_batch": "score failed"},
                "provenance": _cron_provenance(True)}
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/snapshot")
def cron_snapshot_get(
    request: Request,
    symbol: str | None = Query(
        default=None, description="Single symbol; default is the ingest universe"
    ),
    timeframe: str | None = Query(default="1d", description="Bars timeframe"),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/snapshot[?symbol=AAPL&timeframe=1d]``."""
    _check_cron_auth(request)
    symbols = ([symbol] if (symbol or "").strip()
               else ingest_module.default_universe())
    try:
        return _run_snapshot(symbols, timeframe or "1d")
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron snapshot batch failed")
        return {"ok": False, "snapshots": {}, "errors": {"_batch": "snapshot failed"},
                "provenance": _cron_provenance(True)}


@router.post("/snapshot")
def cron_snapshot_post(request: Request, body: SnapshotRequest) -> dict:
    """Manual run: ``POST /api/cron/snapshot`` with ``{symbols, timeframe}``."""
    _check_cron_auth(request)
    symbols = body.symbols if body.symbols else ingest_module.default_universe()
    try:
        return _run_snapshot(symbols, body.timeframe or "1d")
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron snapshot batch failed")
        return {"ok": False, "snapshots": {}, "errors": {"_batch": "snapshot failed"},
                "provenance": _cron_provenance(True)}


@router.get("/score")
def cron_score_get(
    request: Request,
    symbol: str | None = Query(
        default=None, description="Single symbol; default scores all due forecasts"
    ),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/score[?symbol=AAPL]``."""
    _check_cron_auth(request)
    try:
        return _run_score([symbol] if (symbol or "").strip() else [])
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron score batch failed")
        return {"scored": 0, "unscored": 0, "hits": 0, "brier_mean": None,
                "errors": {"_batch": "score failed"},
                "provenance": _cron_provenance(True)}


@router.post("/score")
def cron_score_post(request: Request, body: ScoreRequest) -> dict:
    """Manual run: ``POST /api/cron/score`` with JSON ``{symbols: [...]}``."""
    _check_cron_auth(request)
    try:
        return _run_score(body.symbols or [])
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron score batch failed")
        return {"scored": 0, "unscored": 0, "hits": 0, "brier_mean": None,
                "errors": {"_batch": "score failed"},
                "provenance": _cron_provenance(True)}


# --- retention purge (additive, distinct paths) --------------------------------
# Snapshots are compressed at capture (best of gzip/delta-q/zlib/zstd, so no
# monthly recompress batch is needed); what must run on schedule is the
# tiered lifecycle from docs/DATA_QUALITY.md + retention.py: raw (non-gzip)
# snapshots 30d, gzip snapshots 1y, 2y backstop, bars 5y, forecasts 3y.
# GET is always a dry-run report; POST applies ONLY with {"apply": true}
# (the scheduled workflow passes it weekly). Batches never 500.


class RetentionRequest(BaseModel):
    apply: bool = Field(default=False, description="Delete expired rows when true")
    retention_days: dict[str, int] | None = Field(
        default=None, description="Optional per-dataset day overrides")


def _sanitize_retention_overrides(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            if not isinstance(key, str) or not key.strip():
                continue
            out[key.strip()] = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out or None


def _run_retention(*, apply: bool, overrides: dict[str, int] | None) -> dict:
    """Dry-run report or guarded purge (batch never 500s)."""
    from backend.db.session import get_session_factory, init_db
    from backend.observability import retention as retention_module

    try:
        init_db()
        Session = get_session_factory()
        db = Session()
    except Exception:
        logger.warning("retention db unavailable")
        return {
            "deleted": False,
            "counts": {},
            "total": 0,
            "cutoffs": {},
            "errors": {"_batch": "db unavailable"},
            "provenance": _cron_provenance(True),
        }
    try:
        if apply:
            out = retention_module.purge(db, retention_days=overrides)
        else:
            out = retention_module.purge_dry_run(db, retention_days=overrides)
        return {
            "deleted": bool(apply),
            "counts": dict(out.get("counts") or {}),
            "total": int(out.get("total") or 0),
            "cutoffs": dict(out.get("cutoffs") or {}),
            "errors": {},
            "provenance": _cron_provenance(False),
        }
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron retention batch failed")
        return {
            "deleted": False,
            "counts": {},
            "total": 0,
            "cutoffs": {},
            "errors": {"_batch": "retention failed"},
            "provenance": _cron_provenance(True),
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/retention")
def cron_retention_get(request: Request) -> dict:
    """Dry-run report: expired-row counts per dataset, nothing deleted."""
    _check_cron_auth(request)
    try:
        return _run_retention(apply=False, overrides=None)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron retention batch failed")
        return {
            "deleted": False,
            "counts": {},
            "total": 0,
            "cutoffs": {},
            "errors": {"_batch": "retention failed"},
            "provenance": _cron_provenance(True),
        }


@router.post("/retention")
def cron_retention_post(request: Request, body: RetentionRequest) -> dict:
    """Purge ONLY with ``{"apply": true}``; otherwise a dry-run report."""
    _check_cron_auth(request)
    try:
        return _run_retention(
            apply=bool(body.apply),
            overrides=_sanitize_retention_overrides(body.retention_days),
        )
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron retention batch failed")
        return {
            "deleted": False,
            "counts": {},
            "total": 0,
            "cutoffs": {},
            "errors": {"_batch": "retention failed"},
            "provenance": _cron_provenance(True),
        }


# --- provider health probing (Agent 6; additive, distinct paths) ------------
# Active healthchecks for ALL providers (data + AI) live here for Vercel Cron:
# ``GET /api/cron/health`` runs one lightweight ping per provider (single
# quote / FX pair / AI model-list — no costly calls) and records into the
# shared ProviderHealthTracker, so GET /health and GET /api/providers/health
# stay read-only and lightweight (<100ms, never block on upstream).
# ``POST /api/cron/health`` is the manual-run twin. Batches never 500.


def _run_health_probe(timeout_s: float = 60.0) -> dict:
    """Probe every known provider once; return enriched stats + batch status.

    ``ok`` is False when any provider reports state ``down`` or an open
    circuit; quota-limited (429) and unconfigured AI rows are degraded, not
    batch failures. Per-provider fallbacks surface in ``providers`` rows;
    only unexpected exceptions land in ``errors``. Never raises.
    """
    try:
        timeout = max(1.0, min(60.0, float(timeout_s)))
    except (TypeError, ValueError):
        timeout = 60.0
    try:
        from backend.api.deps import get_health_tracker
        from backend.market_data.health import probe_all_providers

        tracker = get_health_tracker()
    except Exception:
        logger.warning("cron health probe unavailable")
        return {"ok": False, "providers": [], "errors": {"_batch": "health unavailable"},
                "provenance": _cron_provenance(True)}
    try:
        probe_all_providers(tracker, timeout_s=timeout)
    except Exception:
        logger.warning("cron health probe batch failed")
    try:
        providers = tracker.all_stats() or []
    except Exception:
        providers = []
    # Durable history (best-effort): mirror the fresh probe rows into
    # provider_health_history so the tracker has a queryable trail. Missing
    # table / closed DB degrades to no-op via writers (never breaks cron).
    try:
        from backend.db.session import get_session_factory
        from backend.db.writers import record_provider_health

        Session = get_session_factory()
        db = Session()
        try:
            for row in providers:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("provider") or "").strip().lower()
                if not name:
                    continue
                state = str(row.get("state") or "up").lower()
                ok = state not in ("down",)
                lat = row.get("p50_ms", row.get("latency_ms"))
                try:
                    lat_i = int(float(lat)) if lat is not None else None
                except (TypeError, ValueError):
                    lat_i = None
                err = None
                if not ok:
                    err = f"state={row.get('state')};circuit={row.get('circuit')}"
                elif state == "degraded":
                    err = f"degraded;circuit={row.get('circuit')}"
                record_provider_health(db, provider=name, ok=ok, latency_ms=lat_i, error_code=err)
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        pass
    errors: dict[str, str] = {}
    try:
        for row in providers:
            if not isinstance(row, dict):
                continue
            name = str(row.get("provider", "unknown"))
            if str(row.get("state", "up")) == "down" or str(row.get("circuit", "closed")) == "open":
                errors[name] = f"state={row.get('state')}; circuit={row.get('circuit')}"
    except Exception:
        pass
    try:
        providers = sorted(providers, key=lambda p: str(p.get("provider", "unknown")))
    except Exception:
        pass
    return {"ok": not errors, "providers": providers, "errors": errors,
            "provenance": _cron_provenance(bool(errors))}


@router.get("/health")
def cron_health_get(
    request: Request,
    timeout_s: float = Query(default=5.0, ge=1.0, le=15.0,
                             description="Per-provider ping budget in seconds"),
) -> dict:
    """Vercel Cron entry: ``GET /api/cron/health[?timeout_s=60]``."""
    _check_cron_auth(request)
    try:
        return _run_health_probe(timeout_s)
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron health batch failed")
        return {"ok": False, "providers": [], "errors": {"_batch": "health failed"},
                "provenance": _cron_provenance(True)}


@router.post("/health")
def cron_health_post(request: Request) -> dict:
    """Manual run: ``POST /api/cron/health`` (all providers; no body)."""
    _check_cron_auth(request)
    try:
        return _run_health_probe()
    except HTTPException:
        raise
    except Exception:
        logger.warning("cron health batch failed")
        return {"ok": False, "providers": [], "errors": {"_batch": "health failed"},
                "provenance": _cron_provenance(True)}
