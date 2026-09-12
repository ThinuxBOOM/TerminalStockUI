"""Security tests: Fernet round-trip + redaction (keys never leak to logs/API)."""

from __future__ import annotations

import pytest

from backend.security.authorization import Role, check_permission
from backend.security.secrets import (
    EncryptedSecretStore,
    decrypt_secret,
    encrypt_secret,
    redact_mapping,
    redact_string,
    reset_fernet,
)


@pytest.fixture(autouse=True)
def _fresh_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-secret-key-for-unit-tests-123")
    reset_fernet()
    yield
    reset_fernet()


def test_fernet_round_trip():
    token = encrypt_secret("sk-gemini-123")
    assert token != "sk-gemini-123"
    assert decrypt_secret(token) == "sk-gemini-123"


def test_store_encrypts_at_rest_and_describes_safely():
    store = EncryptedSecretStore()
    store.put("gemini", "api_key", "sk-secret")
    assert store._vault[("gemini", "api_key")] != "sk-secret"  # ciphertext at rest
    assert store.get("gemini", "api_key") == "sk-secret"  # decrypt at call time
    described = store.describe()
    assert described == [{"provider": "gemini", "name": "api_key", "configured": True}]
    assert "sk-secret" not in str(described)


def test_redact_mapping_nested():
    payload = {"actor": "user:1", "api_key": "sk-x", "nested": {"token": "t", "ok": 1},
               "items": [{"password": "pw"}]}
    clean = redact_mapping(payload)
    assert clean["api_key"] == "[REDACTED]"
    assert clean["nested"]["token"] == "[REDACTED]"
    assert clean["nested"]["ok"] == 1
    assert clean["items"][0]["password"] == "[REDACTED]"
    assert "sk-x" not in str(clean)


def test_redact_string():
    assert "sk-x" not in redact_string("call failed api_key=sk-x retrying")


def test_authorization_matrix():
    assert check_permission(Role.ADMIN, "configure_providers")
    assert check_permission("analyst", "research")
    assert not check_permission("viewer", "configure_providers")
    assert check_permission("viewer", "read")
    assert not check_permission("nobody", "read")
