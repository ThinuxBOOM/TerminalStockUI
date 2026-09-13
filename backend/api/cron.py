"""Cron router (Phase 1a): Vercel Cron entry for daily bar ingestion.

Vercel Cron invokes via HTTP GET only -> ``GET /api/cron/ingest``.
``POST /api/cron/ingest`` with JSON ``{symbols: [...]}`` covers manual runs.
Both share :func:`backend.market_data.ingest.ingest_symbols` with
``scripts/backfill_bars.py`` (no HTTP in the shared path).

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

from backend.api.deps import get_registry
from backend.instruments.registry import InstrumentRegistry
from backend.market_data import ingest as ingest_module
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])

#: Test seam (singleton-override + reset hook, same pattern as
#: backend/api/fx.py get_fx_provider/reset_fx_provider): when set, the cron
#: endpoints ingest via this fetch callable instead of yfinance.
_fetch_override = None


def override_cron_fetch(fn) -> None:  # test hook
    """Override the per-symbol fetch used by the cron endpoints (tests)."""
    global _fetch_override
    _fetch_override = fn


def reset_cron() -> None:  # test hook
    """Clear the fetch override set by :func:`override_cron_fetch`."""
    global _fetch_override
    _fetch_override = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cron_secret() -> str:
    return (os.getenv("CRON_SECRET", "") or "").strip()


def _check_cron_auth(request: Request) -> None:
    """Enforce Bearer auth iff CRON_SECRET is set; 401 otherwise.

    The secret value itself is never logged.
    """
    secret = _cron_secret()
    if not secret:
        return  # local dev: open
    provided = request.headers.get("authorization", "") or ""
    if not hmac.compare_digest(f"Bearer {secret}", provided):
        logger.warning("cron auth rejected")
        raise HTTPException(status_code=401, detail="unauthorized")


class IngestRequest(BaseModel):
    symbols: list[str] | None = Field(default=None)


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
