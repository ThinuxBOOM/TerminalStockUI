"""FastAPI application factory.

Canonical run (repo root):
    uvicorn backend.app:app --port 8000
Also supported (backend/ as CWD, per README, or a flat Docker image
where backend/ contents sit at top level):
    uvicorn api.main:app --port 8000
"""

from __future__ import annotations

import os
import sys


def _ensure_canonical_package() -> None:
    """Make `backend.*` importable in flat layouts.

    In the canonical layout (repo root on sys.path) this is a no-op. When
    backend/ itself is top-level (no parent `backend` package importable),
    register a lightweight `backend` package pointing at this directory so
    the rest of the tree can keep using canonical absolute imports.
    """
    try:
        import backend  # noqa: F401

        if getattr(backend, "__version__", None) is not None:
            return
    except ImportError:
        pass
    import types

    here = os.path.dirname(os.path.abspath(__file__))  # .../backend/api
    backend_dir = os.path.dirname(here)  # .../backend
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    pkg = sys.modules.get("backend")
    if pkg is None or not hasattr(pkg, "__path__"):
        pkg = types.ModuleType("backend")
        pkg.__path__ = [backend_dir]  # type: ignore[attr-defined]
        version = "0.1.0"
        try:
            import re

            with open(os.path.join(backend_dir, "__init__.py"), encoding="utf-8") as fh:
                match = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", fh.read())
            if match:
                version = match.group(1)
        except OSError:
            pass
        pkg.__version__ = version  # type: ignore[attr-defined]
        sys.modules["backend"] = pkg


_ensure_canonical_package()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__ as _version
from backend.api.deps import reset_deps  # noqa: F401  (public test hook)
from backend.api.health import router as health_router
from backend.api.instruments import router as instruments_router
from backend.api.market_data import router as market_data_router
from backend.api.market_data import securities_router
from backend.api.providers import router as providers_router
from backend.api.forecast import router as forecast_router
from backend.api.analytics_api import router as analytics_router
from backend.api.backtest import router as backtest_router
from backend.api.ai import router as ai_router
from backend.api.audit import router as audit_router
from backend.api.alerts import router as alerts_router
from backend.api.cron import router as cron_router
from backend.api.fx import router as fx_router
from backend.api.markets import router as markets_router
from backend.api.liquidation_proxy import router as liquidation_router
from backend.api.market_index import router as market_index_router
from backend.api.screener import router as screener_router
from backend.api.news import router as news_router
from backend.api.signals import router as signals_router


def _cors_origins() -> list[str]:
    """Explicit allow-list from CORS_ORIGINS (comma-separated) + FRONTEND_URL.

    Defaults cover local Vite dev + Vercel previews. Production should set
    CORS_ORIGINS to the exact frontend origin(s) — never "*".

    Split-deploy note (frontend on Vercel, backend on Oracle Cloud): the
    browser origin is the Vercel app, so the Oracle backend must allow it —
    set ``CORS_ORIGINS=https://<your-app>.vercel.app`` (or the simpler
    single-origin ``FRONTEND_URL`` alias below) on the backend host. Same-
    origin Vercel monorepo deploys need no CORS setting at all.
    """
    raw = os.getenv("CORS_ORIGINS", "") or ""
    single = (os.getenv("FRONTEND_URL", "") or "").strip()
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if single:
        parts.append(single)
    configured = []
    for origin in parts:
        # Origins never carry a path: trailing slashes break matching.
        normalized = origin.rstrip("/")
        if normalized and normalized not in configured:
            configured.append(normalized)
    if configured:
        return configured
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


def create_app() -> FastAPI:
    app = FastAPI(title="OneMarket Analyzer", version=_version)
    # Hardening middlewares (all no-ops in local dev by default; enforced
    # via env in staging/prod — see backend/security/*.py):
    #  - CORS allow-list (CORS_ORIGINS)
    #  - Security headers (CSP, HSTS in production, X-Frame-Options, ...)
    #  - Request body size cap (MAX_REQUEST_BYTES, default 1 MiB)
    #  - Sliding-window rate limiting (RATE_LIMIT_PER_MIN, default 300)
    #  - Opt-in API-key auth (API_KEY; open when unset)
    from backend.security.middleware import (
        RequestSizeLimitMiddleware,
        SecurityHeadersMiddleware,
        api_key_middleware,
        rate_limit_middleware,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Role"],
        max_age=600,
    )
    app.middleware("http")(api_key_middleware)
    app.middleware("http")(rate_limit_middleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    try:
        from backend.security.secrets import warn_if_default_secret_key

        warn_if_default_secret_key()
    except Exception:
        pass
    # Fail-closed production guard (loud RuntimeError on missing/weak
    # SECRET_KEY when APP_ENV=production; dev/test keep offline behavior).
    try:
        from backend.security.secrets import assert_secret_strength

        assert_secret_strength()
    except RuntimeError:
        raise
    except Exception:
        pass
    app.include_router(health_router)  # GET /health
    app.include_router(instruments_router)
    app.include_router(market_data_router)
    app.include_router(securities_router)
    app.include_router(providers_router)
    app.include_router(forecast_router)
    app.include_router(analytics_router)
    app.include_router(backtest_router)
    app.include_router(ai_router)
    app.include_router(audit_router)
    app.include_router(alerts_router)
    app.include_router(cron_router)
    app.include_router(fx_router)
    app.include_router(screener_router)
    app.include_router(news_router)
    app.include_router(signals_router)
    app.include_router(markets_router)
    app.include_router(liquidation_router)
    app.include_router(market_index_router)

    @app.get("/", tags=["health"])
    def root() -> dict:
        return {"name": "OneMarket Analyzer", "version": _version, "health": "/health"}

    return app


app = create_app()
