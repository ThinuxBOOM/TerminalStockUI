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
from backend.api.screener import router as screener_router


def create_app() -> FastAPI:
    app = FastAPI(title="OneMarket Analyzer", version=_version)
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

    @app.get("/", tags=["health"])
    def root() -> dict:
        return {"name": "OneMarket Analyzer", "version": _version, "health": "/health"}

    return app


app = create_app()
