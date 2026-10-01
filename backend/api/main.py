"""FastAPI application factory.

Run from the repository root:

    uvicorn backend.app:app --port 8000

Every ``/api/*`` router requires a logged-in user (the dependency is declared
on each router) except ``/api/auth/*`` (login itself) and ``/api/cron/*``
(scheduler, authenticated with ``CRON_SECRET``). ``/health`` is public.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__ as _version
from backend import settings
from backend.api.ai import router as ai_router
from backend.api.alerts import router as alerts_router
from backend.api.analytics_api import router as analytics_router
from backend.api.audit import router as audit_router
from backend.api.auth import router as auth_router
from backend.api.cron import router as cron_router
from backend.api.deps import reset_deps  # noqa: F401  (public test hook)
from backend.api.forecast import router as forecast_router
from backend.api.fx import router as fx_router
from backend.api.health import router as health_router
from backend.api.instruments import router as instruments_router
from backend.api.market_data import router as market_data_router
from backend.api.market_data import securities_router
from backend.api.market_index import router as market_index_router
from backend.api.risk import router as risk_router
from backend.api.markets import router as markets_router
from backend.api.news import router as news_router
from backend.api.premarket import router as premarket_router
from backend.api.providers import router as providers_router
from backend.api.screener import router as screener_router
from backend.api.signals import router as signals_router
from backend.security.middleware import (
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    rate_limit_middleware,
)

_DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _cors_origins() -> list[str]:
    """Extra browser origins allowed to call the API.

    The production deployment serves the frontend and the API from the same
    origin behind Caddy, so no CORS is needed there. ``CORS_ORIGINS``
    (comma-separated) is for split setups; dev defaults to the Vite server.
    """
    configured = [
        origin.strip().rstrip("/")
        for origin in (os.getenv("CORS_ORIGINS", "") or "").split(",")
        if origin.strip()
    ]
    if configured:
        return configured
    return [] if settings.is_production() else list(_DEV_ORIGINS)


def create_app() -> FastAPI:
    settings.validate_startup()
    app = FastAPI(
        title="OneMarket Analyzer",
        version=_version,
        # Interactive docs expose the whole API surface; keep them off in production.
        docs_url=None if settings.is_production() else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production() else "/openapi.json",
    )
    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
            max_age=600,
        )
    app.middleware("http")(rate_limit_middleware)
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    for router in (
        health_router,
        auth_router,
        cron_router,
        instruments_router,
        market_data_router,
        securities_router,
        providers_router,
        forecast_router,
        analytics_router,
        ai_router,
        audit_router,
        alerts_router,
        fx_router,
        screener_router,
        news_router,
        signals_router,
        premarket_router,
        markets_router,
        market_index_router,
        risk_router,
    ):
        app.include_router(router)

    @app.get("/", tags=["health"])
    def root() -> dict:
        return {"name": "OneMarket Analyzer", "version": _version, "health": "/health"}

    return app


app = create_app()
