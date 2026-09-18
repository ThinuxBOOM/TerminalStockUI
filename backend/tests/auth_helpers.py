"""Test-only auth bypass for V1 suite (JWT tier gates landed in V2).

V2 added ``Depends(require_tier(...))`` (free/silver/gold/platinum) to
forecast/analytics/screener/backtest/alerts/providers/AI routers. The V1
tests below predate JWT and call those routes without a Bearer token, so
they 401 while asserting 200/422/423 shapes:

- test_ai_router, test_alerts, test_analytics_api, test_backtest_api,
  test_calibration_snapshots, test_e2e_journey, test_failure_modes,
  test_forecast_api, test_hardening, test_health, test_honesty_envelope,
  test_indicators, test_live_providers, test_load_smoke, test_provenance,
  test_provider_keys, test_screener, test_security_redaction, test_statements

``inject_admin_auth(app)`` overrides ``get_current_user`` (the leaf dep
inside every ``require_tier`` gate) with a platinum admin, so all gates
pass and the original fail-closed assertions (423 no-key, 422 bad input,
502 no live data, 200 shapes) are exercised again. Production behavior is
unchanged; new V2 tests (test_auth/test_tier_gates/test_billing) do NOT
use this helper and still assert real 401/402 gating.
"""

from __future__ import annotations


class AdminUser(dict):
    """Dict supporting both access styles used by routers.

    - dict-style (``user.get("tier")`` in screener/markets/news/ai helpers)
    - attribute-style (``getattr(user, "tier")`` in backend/auth/guards)
    """

    def __getattr__(self, name: str):  # type: ignore[no-redef]
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


ADMIN_USER = AdminUser(
    {
        "user_id": "test-admin",
        "id": "test-admin",
        "email": "admin@test.local",
        "tier": "platinum",
        "is_admin": True,
        "subscription_status": "comped",
    }
)


def inject_admin_auth(app):  # type: ignore[no-untyped-def]
    """Override JWT auth on a test FastAPI app with a platinum admin."""
    try:
        from backend.auth.guards import get_current_user
    except Exception:
        return app
    try:
        app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    except Exception:
        pass
    return app
