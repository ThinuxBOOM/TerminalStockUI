"""Provider routers: dashboard health per docs/DATA_QUALITY.md + README."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..market_data.health import ProviderHealthTracker
from .deps import get_health_tracker, get_market_service

router = APIRouter(prefix="/api/providers", tags=["providers"])

#: Reference symbol for the health probe (must exist in the seed registry).
PROBE_SYMBOL = "AAPL"


@router.get("/health")
def providers_health(tracker: ProviderHealthTracker = Depends(get_health_tracker)):
    """GET /api/providers/health -> per-provider latency/error/circuit state."""
    stats = tracker.all_stats()
    if not stats:
        stats = [{
            "provider": "yfinance", "latency_p50_ms": 0.0, "latency_p95_ms": 0.0,
            "error_rate_1h": 0.0, "calls_1h": 0, "total_calls": 0,
            "circuit": "closed", "last_check": None,
        }]
    return {"providers": stats}


@router.post("/health/test")
def test_provider(provider: str = "yfinance", svc=Depends(get_market_service)):
    """Health probe: fetch a reference quote and return the fresh stats."""
    svc.get_quote(PROBE_SYMBOL)
    tracker = get_health_tracker()
    stats = tracker.stats(provider)
    if stats["total_calls"] == 0:
        stats = {**stats, "provider": provider}
    return stats


# --- Phase 3c: encrypted provider keys + budgets (additive; health above untouched) ---

_ALLOWED_PROVIDERS = ("gemini", "openai", "anthropic", "xai")
_ALLOWED_SET = frozenset(_ALLOWED_PROVIDERS)


def _check_provider(value: object) -> str:
    name = value.strip().lower() if isinstance(value, str) else ""
    if name not in _ALLOWED_SET:
        raise HTTPException(status_code=422, detail=f"unknown provider; expected one of {list(_ALLOWED_PROVIDERS)}")
    return name


def _check_model(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="model must be a string <= 64 characters")
    if len(value) > 64:
        raise HTTPException(status_code=422, detail="model must be a string <= 64 characters")
    return value


def _check_api_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=422, detail="api_key must be a non-empty string")
    if len(value) > 2000:
        raise HTTPException(status_code=422, detail="api_key must be <= 2000 characters")
    return value


def _check_budget(value: object) -> float:
    import math

    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail="monthly_usd must be a finite number >= 0")
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="monthly_usd must be a finite number >= 0")
    if not math.isfinite(num) or num < 0:
        raise HTTPException(status_code=422, detail="monthly_usd must be a finite number >= 0")
    return num


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    from datetime import datetime, timezone

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(value)


def _is_configured(provider: str) -> bool:
    """configured = secret-store OR env OR DB. Each step exception-safe."""
    try:
        from backend.ai.providers.base import get_default_secret_store

        if bool(get_default_secret_store().has(provider, "api_key")):
            return True
    except Exception:
        pass
    try:
        import os

        if bool(os.getenv(f"{provider.upper()}_API_KEY", "").strip()):
            return True
    except Exception:
        pass
    try:
        from backend.security.store_db import db_configured

        if bool(db_configured(provider)):
            return True
    except Exception:
        pass
    return False


@router.post("/keys")
def save_provider_key(body: dict) -> dict:
    """POST /api/providers/keys {provider, model?, api_key} -> encrypted at rest."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be {provider, model?, api_key}")
    provider = _check_provider(body.get("provider"))
    model = _check_model(body.get("model", ""))
    api_key = _check_api_key(body.get("api_key"))
    from backend.security.store_db import put_db_secret

    try:
        put_db_secret(provider, api_key, model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    api_key = ""
    try:
        from backend.api.audit import append_audit_log

        # Open a short-lived session here (router has no Depends(get_db) to
        # keep the legacy health/test signatures untouched).
        from backend.db.session import get_session_factory

        db = get_session_factory()()
        try:
            append_audit_log(
                db,
                actor="system",
                action="provider.key_saved",
                entity_type="provider",
                entity_id=provider,
                payload={"provider": provider, "model": model, "configured": True},
            )
        finally:
            db.close()
    except Exception:
        pass
    return {"ok": True, "provider": provider, "model": model, "configured": True}


@router.get("/keys/status")
def provider_keys_status() -> dict:
    """GET /api/providers/keys/status -> config flags only, never key material."""
    rows: dict[str, object] = {}
    try:
        from backend.db.session import get_session_factory
        from backend.security.store_db import ProviderSecret

        db = get_session_factory()()
        try:
            for row in db.query(ProviderSecret).all():
                rows[str(row.provider)] = row
        finally:
            db.close()
    except Exception:
        rows = {}
    out: list[dict] = []
    for name in _ALLOWED_PROVIDERS:
        row = rows.get(name)
        model = str(getattr(row, "model", "") or "") if row is not None else ""
        updated_at = _iso_or_none(getattr(row, "updated_at", None)) if row is not None else None
        out.append({"provider": name, "model": model, "configured": _is_configured(name), "updated_at": updated_at})
    return {"providers": out}


@router.post("/budget")
def save_provider_budget(body: dict) -> dict:
    """POST /api/providers/budget {provider, monthly_usd} -> upsert cap."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be {provider, monthly_usd}")
    provider = _check_provider(body.get("provider"))
    monthly_usd = _check_budget(body.get("monthly_usd"))
    from backend.security.store_db import set_db_budget

    try:
        set_db_budget(provider, monthly_usd)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        from backend.api.audit import append_audit_log
        from backend.db.session import get_session_factory

        db = get_session_factory()()
        try:
            append_audit_log(
                db,
                actor="system",
                action="provider.budget_saved",
                entity_type="provider",
                entity_id=provider,
                payload={"provider": provider, "monthly_usd": monthly_usd},
            )
        finally:
            db.close()
    except Exception:
        pass
    return {"ok": True, "provider": provider, "monthly_usd": monthly_usd}


@router.get("/budget")
def get_provider_budgets() -> dict:
    """GET /api/providers/budget -> {budgets: {provider: monthly_usd}}."""
    budgets: dict[str, float] = {}
    try:
        from backend.db.session import get_session_factory
        from backend.security.store_db import ProviderBudget

        db = get_session_factory()()
        try:
            for row in db.query(ProviderBudget).all():
                try:
                    budgets[str(row.provider)] = float(row.monthly_usd)  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
        finally:
            db.close()
    except Exception:
        budgets = {}
    return {"budgets": budgets}
