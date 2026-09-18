"""V2 auth tests: register/login/me roundtrip, wrong-pw 401, expired 401,
default tier free, duplicate 409 (isolated SQLite file DB per test)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from backend.api.main import create_app
from backend.auth.guards import create_access_token
from backend.db.models import Base
from backend.db.session import get_db, get_engine, init_db

TEST_SECRET = "test-auth-secret-key-0123456789abcdef"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    # Ensure the fallback `users` mapping is registered before create_all.
    from backend.api import auth as _auth_module  # noqa: F401
    from backend.auth import guards as _guards_module  # noqa: F401

    url = f"sqlite:///{tmp_path}/auth.db"
    init_db(url)
    assert "users" in Base.metadata.tables, "users table must exist after init_db"
    Session = sessionmaker(bind=get_engine(url), autoflush=False, expire_on_commit=False)
    app = create_app()

    def _override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


def _register(client: TestClient, email="user@example.com", password="supersecretpw"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


def test_register_defaults_free_and_me_roundtrip(client: TestClient):
    resp = _register(client, email="  User@Example.com  ")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "user@example.com"  # lower(trim())
    assert body["tier"] == "free"  # default tier
    assert body["access_token"] and body["token_type"] == "bearer"
    assert "password_hash" not in resp.text
    assert "refresh_token" in (resp.headers.get("set-cookie", "") or "")

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["email"] == "user@example.com"
    assert me_body["tier"] == "free"
    assert me_body["is_admin"] is False
    assert "access_token" not in me_body
    assert "password_hash" not in me.text


def test_login_ok_and_refresh_rotation(client: TestClient):
    assert _register(client).status_code == 201
    login = client.post("/api/auth/login", json={"email": "user@example.com", "password": "supersecretpw"})
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200

    # Refresh cookie (stored by the TestClient jar on login) rotates.
    refreshed = client.post("/api/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"]
    assert "refresh_token" in (refreshed.headers.get("set-cookie", "") or "")


def test_login_wrong_password_401_no_enumeration(client: TestClient):
    assert _register(client).status_code == 201
    bad_pw = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrongpassword1"})
    assert bad_pw.status_code == 401
    assert bad_pw.json()["detail"] == "invalid email or password"
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrongpassword1"})
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == bad_pw.json()["detail"]  # same message


def test_duplicate_register_409(client: TestClient):
    assert _register(client).status_code == 201
    dup = _register(client, email="USER@example.com")  # case-insensitive duplicate
    assert dup.status_code == 409


def test_me_unauthenticated_and_tampered_401(client: TestClient):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer bogus.token.here"}).status_code == 401


def test_expired_token_401(client: TestClient):
    reg = _register(client)
    assert reg.status_code == 201
    user_id = reg.json()["id"]
    expired = create_access_token(user_id, tier="free", is_admin=False, expires_in_s=-10)
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401


def test_register_validation_422(client: TestClient):
    assert _register(client, email="not-an-email").status_code == 422
    assert _register(client, email="ok@example.com", password="short").status_code == 422


def test_refresh_without_cookie_401():
    # Fresh client with no login cookie jar.
    app = create_app()
    bare = TestClient(app)
    resp = bare.post("/api/auth/refresh")
    assert resp.status_code == 401
