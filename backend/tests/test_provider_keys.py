"""Phase 3c provider-keys tests (offline, sqlite + monkeypatched SECRET_KEY).

Covers: validation matrix, save->status roundtrip with zero key material,
env-only configured, DB-only configured, _load_api_key precedence
(store > env > db), wrong SECRET_KEY -> unconfigured (never 500),
budget roundtrip + validation, audit redaction.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.security.store_db as store_db
from backend.ai.providers.base import get_default_secret_store, set_default_secret_store
from backend.ai.providers.gemini import GeminiProvider
from backend.db.session import init_db, reset_engine
from backend.security.secrets import EncryptedSecretStore, reset_fernet

PROVIDERS = ("gemini", "openai", "anthropic", "xai")


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/provider_keys.db"
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key-for-unit-tests-123")
    monkeypatch.setenv("DATABASE_URL", url)
    for p in PROVIDERS:
        monkeypatch.delenv(f"{p.upper()}_API_KEY", raising=False)
    reset_engine()
    reset_fernet()
    set_default_secret_store(EncryptedSecretStore())
    init_db(url)
    yield url
    reset_fernet()
    reset_engine()
    set_default_secret_store(None)


def _client() -> TestClient:
    from backend.api.main import create_app

    return TestClient(create_app())


# --- validation matrix -----------------------------------------------------

def test_keys_validation_matrix(isolated):
    c = _client()
    # unknown provider
    r = c.post("/api/providers/keys", json={"provider": "nope", "model": "m", "api_key": "sk-x"})
    assert r.status_code == 422, r.text
    # empty / whitespace / missing key
    for bad in ("", "   "):
        r = c.post("/api/providers/keys", json={"provider": "gemini", "model": "m", "api_key": bad})
        assert r.status_code == 422, r.text
    r = c.post("/api/providers/keys", json={"provider": "gemini", "model": "m"})
    assert r.status_code == 422, r.text
    # key too long
    r = c.post("/api/providers/keys", json={"provider": "gemini", "model": "m", "api_key": "k" * 2001})
    assert r.status_code == 422, r.text
    # model too long / wrong type
    r = c.post("/api/providers/keys", json={"provider": "gemini", "model": "m" * 65, "api_key": "sk-x"})
    assert r.status_code == 422, r.text
    r = c.post("/api/providers/keys", json={"provider": "gemini", "model": 123, "api_key": "sk-x"})
    assert r.status_code == 422, r.text
    # error bodies never echo the key
    secret = "sk-super-secret-never-echo-001"
    r = c.post("/api/providers/keys", json={"provider": "bogus", "model": "m", "api_key": secret})
    assert r.status_code == 422
    assert secret not in r.text


def test_budget_validation_matrix(isolated):
    c = _client()
    r = c.post("/api/providers/budget", json={"provider": "nope", "monthly_usd": 10})
    assert r.status_code == 422, r.text
    for bad in (-1, "lots", None, True, {}):
        r = c.post("/api/providers/budget", json={"provider": "gemini", "monthly_usd": bad})
        assert r.status_code == 422, f"{bad!r}: {r.text}"
    r = c.post("/api/providers/budget", json={"provider": "gemini"})
    assert r.status_code == 422, r.text
    # NaN/Inf are not JSON-compliant over the wire; the UI guards with
    # Number.isFinite client-side. Server-side they must still reject via
    # the shared helper (ValueError -> 422 path in the endpoint).
    with pytest.raises(ValueError):
        store_db.set_db_budget("gemini", float("nan"))
    with pytest.raises(ValueError):
        store_db.set_db_budget("gemini", float("inf"))
    with pytest.raises(ValueError):
        store_db.set_db_budget("gemini", float("-inf"))
    # Raw-wire NaN (Python json NaN literal) must never 500 nor persist.
    for raw in ('{"provider":"gemini","monthly_usd":NaN}',
                '{"provider":"gemini","monthly_usd":Infinity}'):
        try:
            r = c.post("/api/providers/budget", content=raw,
                       headers={"Content-Type": "application/json"})
        except ValueError:
            continue  # client-side JSON guard refused to send; helper asserts above cover it
        assert r.status_code in (400, 422), r.text
    assert store_db.get_db_budget("gemini") is None


# --- save -> status roundtrip, zero key material ----------------------------

def test_save_status_roundtrip_no_key_material(isolated):
    c = _client()
    secret = "sk-live-roundtrip-abc-123"
    r = c.post("/api/providers/keys", json={"provider": "gemini", "model": "gemini-3.7-flash", "api_key": secret})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "provider": "gemini", "model": "gemini-3.7-flash", "configured": True}
    assert secret not in r.text

    r = c.get("/api/providers/keys/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "providers" in data
    by_name = {p["provider"]: p for p in data["providers"]}
    assert set(by_name) == set(PROVIDERS)
    row = by_name["gemini"]
    assert row["configured"] is True
    assert row["model"] == "gemini-3.7-flash"
    assert row["updated_at"]
    for p in data["providers"]:
        assert set(p) == {"provider", "model", "configured", "updated_at"}
    # zero key material: scan full body
    assert secret not in r.text
    assert "ciphertext" not in r.text.lower()
    assert "api_key" not in r.text.lower()
    # ciphertext at rest is real and never leaks
    stored = store_db.get_db_secret("gemini")
    assert stored == secret
    from backend.db.session import get_session_factory

    db = get_session_factory()()
    try:
        db_row = db.get(store_db.ProviderSecret, "gemini")
        assert db_row is not None
        assert db_row.ciphertext != secret
        assert db_row.ciphertext not in r.text
    finally:
        db.close()


def test_audit_redacted_after_key_and_budget_save(isolated):
    c = _client()
    secret = "sk-audit-must-not-persist-999"
    assert c.post("/api/providers/keys", json={"provider": "xai", "model": "grok-x", "api_key": secret}).status_code == 200
    assert c.post("/api/providers/budget", json={"provider": "xai", "monthly_usd": 12.5}).status_code == 200
    from sqlalchemy import select

    from backend.db.models import AuditLog
    from backend.db.session import get_session_factory

    db = get_session_factory()()
    try:
        rows = db.execute(select(AuditLog).order_by(AuditLog.id)).scalars().all()
    finally:
        db.close()
    actions = [r.action for r in rows]
    assert "provider.key_saved" in actions
    assert "provider.budget_saved" in actions
    blob = str([(r.action, r.entity_type, r.entity_id, r.payload, r.hash) for r in rows])
    assert secret not in blob
    assert "ciphertext" not in blob.lower()
    # key-saved audit targets provider:<name>
    key_rows = [r for r in rows if r.action == "provider.key_saved"]
    assert key_rows and key_rows[-1].entity_id == "xai"


# --- env-only / db-only configured ------------------------------------------

def test_env_set_shows_configured_without_db_row(isolated, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "env-only-key-456")
    c = _client()
    r = c.get("/api/providers/keys/status")
    assert r.status_code == 200, r.text
    by_name = {p["provider"]: p for p in r.json()["providers"]}
    assert by_name["openai"]["configured"] is True
    assert by_name["openai"]["model"] == ""
    assert by_name["openai"]["updated_at"] is None
    # no DB row was created by the env fallback
    assert store_db.get_db_secret("openai") is None
    p = GeminiProvider(model="m")
    # sanity: openai provider via base chain also sees env
    from backend.ai.providers.openai import OpenAIProvider

    assert OpenAIProvider(model="m").is_configured() is True


def test_db_row_shows_configured_with_env_unset(isolated):
    secret = "sk-db-only-789"
    store_db.put_db_secret("anthropic", secret, "claude-x")
    c = _client()
    r = c.get("/api/providers/keys/status")
    assert r.status_code == 200, r.text
    by_name = {p["provider"]: p for p in r.json()["providers"]}
    assert by_name["anthropic"]["configured"] is True
    assert by_name["anthropic"]["model"] == "claude-x"
    from backend.ai.providers.anthropic import AnthropicProvider

    prov = AnthropicProvider(model="claude-x")
    assert prov.is_configured() is True
    assert prov._load_api_key() == secret


# --- precedence: store > env > db --------------------------------------------

def test_load_api_key_order_store_beats_env_beats_db(isolated, monkeypatch):
    store_db.put_db_secret("gemini", "db-winner-key", "m-db")
    monkeypatch.setenv("GEMINI_API_KEY", "env-winner-key")

    injected = EncryptedSecretStore()
    injected.put("gemini", "api_key", "store-winner-key")
    p = GeminiProvider(model="gemini-3.7-flash", secret_store=injected)
    assert p._load_api_key() == "store-winner-key"
    assert p.is_configured() is True

    # default-store tier also beats env/db
    default_store = EncryptedSecretStore()
    default_store.put("gemini", "api_key", "default-store-winner")
    set_default_secret_store(default_store)
    p2 = GeminiProvider(model="gemini-3.7-flash")
    assert p2._load_api_key() == "default-store-winner"

    # env beats db once stores are empty
    set_default_secret_store(EncryptedSecretStore())
    p3 = GeminiProvider(model="gemini-3.7-flash", secret_store=EncryptedSecretStore())
    assert p3._load_api_key() == "env-winner-key"
    assert p3.is_configured() is True

    # db is last resort
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    p4 = GeminiProvider(model="gemini-3.7-flash", secret_store=EncryptedSecretStore())
    assert p4._load_api_key() == "db-winner-key"
    assert p4.is_configured() is True

    # nothing configured -> stub path (None / False, never raise)
    from backend.security.store_db import ProviderSecret
    from backend.db.session import get_session_factory

    db = get_session_factory()()
    try:
        row = db.get(ProviderSecret, "gemini")
        if row is not None:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    p5 = GeminiProvider(model="gemini-3.7-flash", secret_store=EncryptedSecretStore())
    assert p5._load_api_key() is None
    assert p5.is_configured() is False


# --- wrong SECRET_KEY -> unconfigured, never 500 ------------------------------

def test_wrong_secret_key_treated_as_unconfigured(isolated, monkeypatch):
    c = _client()
    secret = "sk-rotation-sensitive-321"
    assert c.post("/api/providers/keys", json={"provider": "gemini", "model": "m1", "api_key": secret}).status_code == 200

    monkeypatch.setenv("SECRET_KEY", "a-different-secret-key-that-rotates-xyz")
    reset_fernet()

    # direct helper never raises
    assert store_db.get_db_secret("gemini") is None
    assert store_db.db_configured("gemini") is False

    # provider chain degrades to unconfigured
    p = GeminiProvider(model="m1", secret_store=EncryptedSecretStore())
    assert p._load_api_key() is None
    assert p.is_configured() is False

    # HTTP surface stays 200 with configured=false (never 500, no leak)
    r = c.get("/api/providers/keys/status")
    assert r.status_code == 200, r.text
    # find gemini row
    gem = [x for x in r.json()["providers"] if x["provider"] == "gemini"][0]
    assert gem["configured"] is False
    assert secret not in r.text
    assert "ciphertext" not in r.text.lower()


# --- budget roundtrip ----------------------------------------------------------

def test_budget_roundtrip(isolated):
    c = _client()
    r = c.post("/api/providers/budget", json={"provider": "gemini", "monthly_usd": 25})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "provider": "gemini", "monthly_usd": 25.0}

    r = c.get("/api/providers/budget")
    assert r.status_code == 200, r.text
    assert r.json() == {"budgets": {"gemini": 25.0}}

    r = c.post("/api/providers/budget", json={"provider": "gemini", "monthly_usd": 30.5})
    assert r.status_code == 200, r.text
    assert c.get("/api/providers/budget").json()["budgets"]["gemini"] == 30.5

    # helper-level roundtrip mirrors the HTTP path
    assert store_db.get_db_budget("gemini") == 30.5
    with pytest.raises(ValueError):
        store_db.set_db_budget("gemini", -1)
    with pytest.raises(ValueError):
        store_db.put_db_secret("gemini", "   ", "m")
