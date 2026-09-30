"""Auth: register/login/me/refresh/logout, revocation, admin gates, per-user
alerts, and a sweep proving every data route requires a login."""

from __future__ import annotations

import re
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend import settings
from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.db.models import User
from backend.db.session import get_db, get_engine, init_db
from backend.security.passwords import hash_password
from backend.security.rate_limit import reset_rate_limiter

PASSWORD = "supersecretpw"


@pytest.fixture()
def env(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/auth.db"
    init_db(url)
    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    reset_deps()
    reset_rate_limiter()
    app = create_app()

    def _override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app), Session, app


def _register(client: TestClient, email="user@example.com", password=PASSWORD):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_admin(Session, email: str) -> None:
    db = Session()
    try:
        user = db.query(User).filter(User.email == email).one()
        user.is_admin = True
        db.commit()
    finally:
        db.close()


# --- register / login / me ---------------------------------------------------


def test_register_normalizes_email_and_me_roundtrip(env):
    client, _, _ = env
    resp = _register(client, email="  User@Example.com  ")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "user@example.com"
    assert body["is_admin"] is False
    assert "password_hash" not in body and "tier" not in body
    me = client.get("/api/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


def test_duplicate_register_409(env):
    client, _, _ = env
    assert _register(client).status_code == 201
    assert _register(client).status_code == 409


def test_register_validation_422(env):
    client, _, _ = env
    assert _register(client, email="not-an-email").status_code == 422
    assert _register(client, password="short").status_code == 422
    assert _register(client, password="x" * 80).status_code == 422  # > 72 bytes


def test_registration_can_be_closed(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    assert _register(client).status_code == 403


def test_registration_closed_by_default_in_production(monkeypatch):
    from backend.api.auth import registration_open

    monkeypatch.delenv("ALLOW_REGISTRATION", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert registration_open() is False


def test_login_failures_share_one_message(env):
    client, _, _ = env
    _register(client)
    wrong = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrongpassword"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    malformed = client.post("/api/auth/login", json={"email": "@@", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == malformed.status_code == 401
    assert wrong.json() == unknown.json() == malformed.json()


def test_login_ok(env):
    client, _, _ = env
    _register(client)
    resp = client.post("/api/auth/login", json={"email": "USER@example.com", "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


# --- tokens ---------------------------------------------------------------------


def test_missing_tampered_and_expired_tokens_401(env):
    client, _, _ = env
    token = _register(client).json()["access_token"]
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(token[:-2] + "xx")).status_code == 401
    payload = jwt.decode(token, settings.secret_key(), algorithms=["HS256"])
    payload["exp"] = int(time.time()) - 10
    expired = jwt.encode(payload, settings.secret_key(), algorithm="HS256")
    assert client.get("/api/auth/me", headers=_bearer(expired)).status_code == 401


def test_token_signed_with_other_key_401(env):
    client, _, _ = env
    token = _register(client).json()["access_token"]
    payload = jwt.decode(token, settings.secret_key(), algorithms=["HS256"])
    forged = jwt.encode(payload, "test-only-secret-key-for-unit-tests-123", algorithm="HS256")
    assert client.get("/api/auth/me", headers=_bearer(forged)).status_code == 401


def test_refresh_token_is_not_an_access_token(env):
    client, _, _ = env
    _register(client)
    refresh = client.cookies.get("refresh_token")
    assert refresh
    assert client.get("/api/auth/me", headers=_bearer(refresh)).status_code == 401


def test_refresh_rotates_and_logout_revokes_everything(env):
    client, _, _ = env
    access = _register(client).json()["access_token"]
    refreshed = client.post("/api/auth/refresh")
    assert refreshed.status_code == 200
    old_refresh = client.cookies.get("refresh_token")
    assert client.post("/api/auth/logout", headers=_bearer(access)).status_code == 204
    # The access token and the refresh token issued before logout are dead.
    assert client.get("/api/auth/me", headers=_bearer(access)).status_code == 401
    client.cookies.set("refresh_token", old_refresh, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401


def test_refresh_without_cookie_401(env):
    client, _, _ = env
    assert client.post("/api/auth/refresh").status_code == 401


# --- authorization ---------------------------------------------------------------


_PUBLIC = {"/", "/health", "/api/auth/register", "/api/auth/login", "/api/auth/refresh"}


def test_every_data_route_requires_login(env):
    client, _, app = env
    checked = 0
    for route_path, operations in app.openapi()["paths"].items():
        if route_path in _PUBLIC or route_path.startswith("/api/cron/"):
            continue  # cron routes use CRON_SECRET (see test_cron_retention.py)
        path = re.sub(r"\{[^}]+\}", "X", route_path)
        for method in operations:
            resp = client.request(method.upper(), path)
            assert resp.status_code == 401, f"{method.upper()} {route_path} -> {resp.status_code}"
            checked += 1
    assert checked > 40


def test_provider_settings_are_admin_only(env):
    client, Session, _ = env
    user_token = _register(client, email="user@example.com").json()["access_token"]
    _register(client, email="admin@example.com")
    _make_admin(Session, "admin@example.com")
    admin_token = client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD},
    ).json()["access_token"]
    body = {"provider": "gemini", "api_key": "k" * 20}
    assert client.post("/api/providers/keys", json=body, headers=_bearer(user_token)).status_code == 403
    assert client.get("/api/providers/keys/status", headers=_bearer(user_token)).status_code == 403
    assert client.post("/api/providers/budget", json={"provider": "gemini", "monthly_usd": 5},
                       headers=_bearer(user_token)).status_code == 403
    assert client.get("/api/providers/keys/status", headers=_bearer(admin_token)).status_code == 200


def test_alerts_are_private_per_user(env):
    client, _, _ = env
    alice = _register(client, email="alice@example.com").json()["access_token"]
    bob = _register(client, email="bob@example.com").json()["access_token"]
    created = client.post(
        "/api/alerts/", json={"symbol": "AAPL", "condition": "price_above", "threshold": 100},
        headers=_bearer(alice),
    )
    assert created.status_code == 200, created.text
    alert_id = created.json()["alert"]["alert_id"]
    assert client.get("/api/alerts/", headers=_bearer(alice)).json()["total"] == 1
    assert client.get("/api/alerts/", headers=_bearer(bob)).json()["total"] == 0
    assert client.patch(f"/api/alerts/{alert_id}", json={"is_active": False},
                        headers=_bearer(bob)).status_code == 404
    assert client.delete(f"/api/alerts/{alert_id}", headers=_bearer(bob)).status_code == 404
    assert client.delete(f"/api/alerts/{alert_id}", headers=_bearer(alice)).status_code == 204


def test_password_hash_roundtrip_rejects_garbage():
    from backend.security.passwords import verify_password

    stored = hash_password(PASSWORD)
    assert stored.startswith("$2b$")
    assert verify_password(PASSWORD, stored) is True
    assert verify_password("wrong-password", stored) is False
    assert verify_password(PASSWORD, "not-a-hash") is False
    assert verify_password(PASSWORD, None) is False
