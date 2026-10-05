"""CORS: same-origin in production (no CORS), Vite dev server in development,
explicit CORS_ORIGINS for split setups."""

from __future__ import annotations

from backend.api.main import _cors_origins


def test_dev_defaults_to_vite(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    assert "http://localhost:5173" in _cors_origins()


def test_production_defaults_to_no_cors(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert _cors_origins() == []


def test_explicit_origins_strip_trailing_slash(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example/, https://b.example")
    assert _cors_origins() == ["https://a.example", "https://b.example"]
