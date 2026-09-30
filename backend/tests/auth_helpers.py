"""Test auth: override ``get_current_user`` with a fixed admin user.

Most suites exercise endpoint behavior (provenance, 422/423/502 contracts)
rather than authentication, so they swap the JWT dependency for this user.
``test_auth.py`` and ``test_route_auth.py`` cover the real token path.
"""

from __future__ import annotations

ADMIN_ID = "00000000-0000-4000-8000-000000000001"


class AdminUser(dict):
    """Dict that also supports attribute access (routers use both styles)."""

    def __getattr__(self, name: str):  # type: ignore[no-redef]
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


ADMIN_USER = AdminUser(
    {
        "id": ADMIN_ID,
        "user_id": ADMIN_ID,
        "email": "admin@test.local",
        "is_admin": True,
        "token_version": 0,
    }
)


def inject_admin_auth(app):  # type: ignore[no-untyped-def]
    from backend.auth.guards import get_current_user

    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    return app
