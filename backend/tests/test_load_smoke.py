"""M8 load smoke: sequential quote+forecast budget + concurrent watchlist.

Budget (documents capacity, NOT a strict perf gate):
  - 50 sequential TestClient quote + forecast calls complete in < 30s.
  - A 6-symbol watchlist refreshed concurrently via threads in < 30s.
All calls run against live doubles (deterministic stub quotes marked
live: fallback_used=False, price present; bars from the seeded DB with
live provenance): no network, deterministic.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from backend.ai.providers import base as base_module
from backend.api import ai as ai_api
from backend.api.deps import get_market_service, reset_deps
from backend.api.main import create_app
from backend.cache import InMemoryCache
from backend.forecasting.service import reset_forecast_service
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService
from backend.security import secrets as secrets_module
from backend.security.secrets import EncryptedSecretStore

SEQUENTIAL_BUDGET_S = 30.0
WATCHLIST_BUDGET_S = 30.0
WATCHLIST = ["AAPL", "MSFT", "NVDA", "600519.SS", "MC.PA", "ASML.AS"]


def _build_app():
    from backend.api import deps as deps_module

    reset_deps()
    reset_forecast_service()
    ai_api.reset_ai_router()
    secrets_module.reset_fernet()
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    # Fail-closed live double: stub quotes marked live.
    _orig = provider.get_quote

    def _live_get_quote(symbol: str, *args, **kwargs) -> dict:  # type: ignore[no-untyped-def]
        upper = (symbol or "").strip().upper() if isinstance(symbol, str) else ""
        if not upper:
            return _orig(symbol, *args, **kwargs)
        q = dict(_orig(symbol, *args, **kwargs))
        q["fallback_used"] = False
        q.pop("fallback", None)
        q.pop("circuit_open", None)
        if q.get("price") is None:
            q["price"] = 150.0
        return q

    provider.get_quote = _live_get_quote  # type: ignore[method-assign]
    stub = MarketDataService(
        registry=InstrumentRegistry(), provider=provider,
        health=tracker, cache=InMemoryCache(),
    )
    deps_module._service = stub
    deps_module._registry = stub.registry
    deps_module._health = stub.health
    base_module.set_default_secret_store(EncryptedSecretStore())
    app = create_app()
    app.dependency_overrides[get_market_service] = lambda: stub
    return app


def _teardown() -> None:
    base_module.set_default_secret_store(None)
    secrets_module.reset_fernet()
    ai_api.reset_ai_router()
    reset_deps()
    reset_forecast_service()


def test_load_smoke_50_sequential_quote_plus_forecast():
    app = _build_app()
    client = TestClient(app)
    try:
        symbols = ["AAPL", "MSFT", "NVDA", "600519.SS", "MC.PA"]
        started = time.perf_counter()
        n_ok = 0
        for i in range(50):
            sym = symbols[i % len(symbols)]
            q = client.get("/api/market_data/quote", params={"symbol": sym})
            assert q.status_code == 200, q.text
            assert q.json()["price"] is not None
            assert q.json()["provenance"]["fallback_used"] is False
            f = client.get(f"/api/forecast/{sym}", params={"horizon": 21})
            assert f.status_code == 200, f.text
            n_ok += 1
        elapsed = time.perf_counter() - started
        # Budget note: documents throughput (~2 calls/req); not a hard SLO.
        print(f"\n[load-smoke] 50x(quote+forecast) = {n_ok * 2} calls in "
              f"{elapsed:.1f}s (budget {SEQUENTIAL_BUDGET_S:.0f}s)")
        assert elapsed < SEQUENTIAL_BUDGET_S, f"load smoke over budget: {elapsed:.1f}s"
    finally:
        _teardown()


def test_load_smoke_watchlist_concurrent_refresh():
    app = _build_app()
    try:
        def _refresh(symbol: str) -> tuple[str, int, bool]:
            # One client per thread: TestClient instances are cheap; the
            # underlying app + live-double service are shared and thread-safe
            # for these read-only live paths.
            with TestClient(app) as client:
                resp = client.get("/api/market_data/quote", params={"symbol": symbol})
                ok = resp.status_code == 200 and resp.json().get("price") is not None
                try:
                    prov_ok = resp.json().get("provenance", {}).get("fallback_used") is False
                except Exception:
                    prov_ok = False
                return symbol, resp.status_code, ok and prov_ok

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_refresh, s) for s in WATCHLIST for _ in range(4)]
            results = [f.result() for f in futures]
        elapsed = time.perf_counter() - started
        assert len(results) == len(WATCHLIST) * 4
        for symbol, status, ok in results:
            assert status == 200, symbol
            assert ok, symbol
        print(f"\n[load-smoke] watchlist {len(results)} concurrent quotes in "
              f"{elapsed:.1f}s (budget {WATCHLIST_BUDGET_S:.0f}s)")
        assert elapsed < WATCHLIST_BUDGET_S, f"watchlist smoke over budget: {elapsed:.1f}s"
    finally:
        _teardown()
