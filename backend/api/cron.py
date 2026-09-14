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
    if not hmac.compare_digest(f"Bearer {secret}", provided):
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
