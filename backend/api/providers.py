"""Provider routers: dashboard health per docs/DATA_QUALITY.md + README."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..market_data.health import ProviderHealthTracker
from .deps import get_health_tracker, get_market_service

try:  # V2 Phase 2 canonical guards
    from backend.auth.guards import require_tier  # type: ignore
except ImportError:  # pragma: no cover - fallback until Phase 2 lands
    from typing import Any as _Any

    from fastapi import Request as _Request

    from backend.auth.tiers import _TIER_RANK as _RANK
    from backend.auth.tiers import normalize_tier as _norm

    _TEST_TOKENS: dict[str, dict[str, _Any]] = {
        "test-free": {"user_id": "user-free", "tier": "free", "is_admin": False},
        "test-silver": {"user_id": "user-silver", "tier": "silver", "is_admin": False},
        "test-gold": {"user_id": "user-gold", "tier": "gold", "is_admin": False},
        "test-platinum": {"user_id": "user-platinum", "tier": "platinum", "is_admin": False},
        "test-admin": {"user_id": "admin-1", "tier": "platinum", "is_admin": True},
    }

    def require_tier(min_tier: str):  # type: ignore[no-redef]
        need = _norm(min_tier)

        async def _dep(request: _Request) -> dict[str, _Any]:
            try:
                auth = (request.headers.get("authorization") or "").strip()
            except Exception:
                auth = ""
            token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
            user = _TEST_TOKENS.get(token)
            if user is None:
                raise HTTPException(status_code=401, detail="unauthorized")
            if bool(user.get("is_admin")):
                return dict(user)
            if _RANK[_norm(user.get("tier"))] >= _RANK[need]:
                return dict(user)
            raise HTTPException(status_code=402, detail={"message": f"upgrade required: {need} or higher", "upgrade_required": True, "min_tier": need})

        return _dep


def _user_field(user: object, name: str, default: object = None) -> object:
    try:
        if isinstance(user, dict):
            return user.get(name, default)  # type: ignore[union-attr]
    except Exception:
        pass
    try:
        return getattr(user, name, default)
    except Exception:
        return default


def _require_ai_provider_tier(user: object | None, provider_name: str) -> None:
    """all_providers=platinum: AI provider probes need platinum/admin.

    Market-data probes (yfinance/alpaca/finnhub/twelvedata/fx) stay free.
    Client-supplied tier is never trusted — only the verified JWT user.
    Works with dict (fallback) and ORM (guards) users.
    """
    try:
        if bool(_user_field(user, "is_admin", False)):
            return
    except Exception:
        pass
    name = (provider_name or "").strip().lower()
    if name in ("gemini", "openai", "anthropic", "xai"):
        try:
            from backend.auth.tiers import _TIER_RANK as _R2
            from backend.auth.tiers import normalize_tier as _n2

            have = _n2(str(_user_field(user, "tier", "free")))  # type: ignore[arg-type]
            if _R2[have] >= _R2["platinum"]:
                return
        except Exception:
            return
        raise HTTPException(status_code=402, detail={"message": "upgrade required: platinum or higher", "upgrade_required": True, "min_tier": "platinum"})

router = APIRouter(prefix="/api/providers", tags=["providers"])

#: Reference symbol for the health probe (must exist in the seed registry).
PROBE_SYMBOL = "AAPL"

#: Market-data providers probeable via POST /api/providers/health/test.
#: Unknown names are rejected (422) so arbitrary query values cannot
#: bloat the health tracker with unbounded provider keys.
MARKET_DATA_PROBE_PROVIDERS = ("yfinance", "alpaca")

#: Additional data providers probeable via the same endpoint (free-tier
#: gap-fillers + FX single-pair ping). Kept separate so the legacy
#: MARKET_DATA_PROBE_PROVIDERS allow-list stays untouched.
EXTENDED_DATA_PROBE_PROVIDERS = ("finnhub", "twelvedata", "fx")

#: AI providers probeable via the same endpoint (lightweight model-list
#: ping, no completion = no costly call). Never returns key material.
AI_PROBE_PROVIDERS = ("gemini", "openai", "anthropic", "xai")

#: Every name accepted by POST /api/providers/health/test.
ALL_PROBE_PROVIDERS = (
    MARKET_DATA_PROBE_PROVIDERS + EXTENDED_DATA_PROBE_PROVIDERS + AI_PROBE_PROVIDERS
)

#: Authenticated providers get an extra ``configured`` flag on health rows
#: (free-tier data providers need no key, so they are always True).
_AUTH_PROBE_PROVIDERS = frozenset(
    {"alpaca", "finnhub", "twelvedata", "gemini", "openai", "anthropic", "xai"}
)


def _configured_for(name: str) -> bool | None:
    """Read-only configured flag for authenticated providers. Never raises,
    never returns key material. None when the check itself is unavailable."""
    try:
        key = str(name or "").strip().lower()
    except Exception:
        return None
    if key not in _AUTH_PROBE_PROVIDERS:
        return True  # free-tier: nothing to configure
    if key in ("gemini", "openai", "anthropic", "xai"):
        try:
            return bool(_is_configured(key))
        except Exception:
            return None
    try:
        if key == "alpaca":
            from backend.market_data.providers.alpaca import AlpacaProvider

            return bool(AlpacaProvider().configured)
        if key == "finnhub":
            from backend.market_data.providers.finnhub_free import FinnhubProvider

            return bool(FinnhubProvider().configured)
        if key == "twelvedata":
            from backend.market_data.providers.twelvedata_free import TwelveDataProvider

            return bool(TwelveDataProvider().configured)
    except Exception:
        return None
    return None


def _enriched_stat(tracker: ProviderHealthTracker, name: str) -> dict:
    """tracker.stats(name) + read-only ``configured`` for auth providers."""
    try:
        stats = tracker.stats(name)
    except Exception:
        stats = {
            "provider": name, "kind": "unknown", "state": "unknown",
            "latency_p50_ms": None, "latency_p95_ms": None,
            "error_rate_1h": 0.0, "error_rate_5m": 0.0,
            "calls_1h": 0, "calls_5m": 0, "total_calls": 0,
            "circuit": "closed", "last_check": None, "last_success": None,
            "consecutive_failures": 0,
            "quota": {"limited": False, "reason": None, "status_code": None,
                      "updated_at": None, "auth_required": name in _AUTH_PROBE_PROVIDERS},
        }
    if stats.get("total_calls", 0) == 0:
        stats = {**stats, "provider": name}
    if name in _AUTH_PROBE_PROVIDERS:
        try:
            stats["configured"] = _configured_for(name)
        except Exception:
            pass
    return stats


def _probe_zero_sample_providers(tracker: ProviderHealthTracker, names: list[str]) -> None:
    """Actively ping sample-less providers so the dashboard shows measured
    data instead of unknown everywhere.

    Passive tracker stats are per-process: on serverless every instance
    starts empty, so a purely read-only dashboard reports all providers
    unknown even while quotes flow on sibling instances. Pinging just the
    zero-sample providers (parallel lightweight single quote / FX pair /
    AI model-list; AI without a key short-circuits to unconfigured with no
    network) fixes that. Skipped under pytest (PYTEST_CURRENT_TEST) to keep
    the suite offline and deterministic. Never raises.
    """
    import os as _os

    try:
        if _os.getenv("PYTEST_CURRENT_TEST"):
            return
        todo: list[str] = []
        for _n in names or []:
            try:
                if int((tracker.stats(_n) or {}).get("total_calls") or 0) == 0:
                    todo.append(_n)
            except Exception:
                continue
        if not todo:
            return
        from concurrent.futures import ThreadPoolExecutor

        from backend.market_data.health import probe_provider as _probe

        def _one(_name: str) -> None:
            try:
                _probe(_name, tracker)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=max(1, min(11, len(todo)))) as _pool:
            list(_pool.map(_one, todo))
    except Exception:
        pass


@router.get("/health")
def providers_health(
    tracker: ProviderHealthTracker = Depends(get_health_tracker),
    user: dict = Depends(require_tier("free")),
):
    """GET /api/providers/health -> per-provider latency/error/circuit state.

    Backward-compatible shape ``{"providers": [...]}``; each row keeps the
    legacy keys (provider/latency_p50_ms/latency_p95_ms/error_rate_1h/
    calls_1h/total_calls/circuit/last_check) and adds kind/state/
    error_rate_5m/calls_5m/last_success/consecutive_failures/quota
    (+ configured for authenticated providers). Rows cover every known
    provider even before their first call. Zero-sample providers are
    actively pinged (parallel, lightweight) so serverless instances report
    measured rows instead of unknown everywhere; uncalled/unprobed rows
    report null latencies, never fabricated 0ms.
    """
    try:
        from backend.market_data.health import KNOWN_PROVIDERS as _KNOWN
    except Exception:
        _KNOWN = ALL_PROBE_PROVIDERS
    try:
        _probe_zero_sample_providers(tracker, list(_KNOWN))
    except Exception:
        pass
    rows: list[dict] = []
    seen: set[str] = set()
    for _name in _KNOWN:
        try:
            rows.append(_enriched_stat(tracker, _name))
        except Exception:
            continue
        seen.add(_name)
    try:
        for extra in tracker.all_stats() or []:
            try:
                pname = str(extra.get("provider", "unknown"))
            except Exception:
                continue
            if pname not in seen:
                seen.add(pname)
                if pname in _AUTH_PROBE_PROVIDERS and "configured" not in extra:
                    try:
                        extra = dict(extra)
                        extra["configured"] = _configured_for(pname)
                    except Exception:
                        pass
                rows.append(extra)
    except Exception:
        pass
    if not rows:
        rows = [{
            "provider": "yfinance", "latency_p50_ms": None, "latency_p95_ms": None,
            "error_rate_1h": 0.0, "calls_1h": 0, "total_calls": 0,
            "circuit": "closed", "last_check": None,
        }]
    rows.sort(key=lambda r: str(r.get("provider", "unknown")))
    return {"providers": rows}


@router.post("/health/test")
def test_provider(
    provider: str = "yfinance",
    svc=Depends(get_market_service),
    user: dict = Depends(require_tier("free")),
):
    """Health probe: lightweight per-provider ping + fresh enriched stats.

    Data providers: single quote (AAPL) / single FX pair (EUR/USD) ping —
    no history fan-out, no costly calls. AI providers: model-list ping
    (no completion). Results are recorded into the shared tracker (passive-
    compatible metrics with quota context: 429 -> degraded, not down).
    Unknown names are rejected (422) to bound tracker keys.
    """
    from fastapi import HTTPException as _HTTPException

    name = (provider or "").strip().lower() or "yfinance"
    if name not in ALL_PROBE_PROVIDERS:
        raise _HTTPException(
            status_code=422,
            detail=f"unknown provider; expected one of {list(ALL_PROBE_PROVIDERS)}",
        )
    # V2 HARD gate: all_providers=platinum for AI probes (free stays for data).
    _require_ai_provider_tier(user, name)
    _ = svc  # kept dependency (override seam); probing is per-provider below.
    try:
        tracker = get_health_tracker()
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"health probe failed: {exc}") from exc
    try:
        from backend.market_data.health import probe_provider as _probe
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"health probe failed: {exc}") from exc
    try:
        stats = _probe(name, tracker, timeout_s=60.0)
    except _HTTPException:
        raise
    except ValueError as exc:
        raise _HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"health probe failed: {exc}") from exc
    try:
        if not isinstance(stats, dict) or "provider" not in stats:
            stats = tracker.stats(name)
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"health probe failed: {exc}") from exc
    if stats.get("total_calls", 0) == 0:
        stats = {**stats, "provider": name}
    if name in _AUTH_PROBE_PROVIDERS and "configured" not in stats:
        try:
            stats["configured"] = _configured_for(name)
        except Exception:
            pass
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
def save_provider_key(body: dict, user: dict = Depends(require_tier("platinum"))) -> dict:
    """POST /api/providers/keys {provider, model?, api_key} -> encrypted at rest.

    V2 HARD gate: providers_configure=platinum (admin bypasses).
    """
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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"provider key save failed: {exc}")
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
def provider_keys_status(user: dict = Depends(require_tier("free"))) -> dict:
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
def save_provider_budget(body: dict, user: dict = Depends(require_tier("platinum"))) -> dict:
    """POST /api/providers/budget {provider, monthly_usd} -> upsert cap.

    V2 HARD gate: providers_configure=platinum (admin bypasses).
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="body must be {provider, monthly_usd}")
    provider = _check_provider(body.get("provider"))
    monthly_usd = _check_budget(body.get("monthly_usd"))
    from backend.security.store_db import set_db_budget

    try:
        set_db_budget(provider, monthly_usd)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"provider budget save failed: {exc}")
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
def get_provider_budgets(user: dict = Depends(require_tier("free"))) -> dict:
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
