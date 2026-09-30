"""ASGI entrypoint: ``uvicorn backend.app:app`` (run from the repository root)."""

from backend.api.main import app, create_app

__all__ = ["app", "create_app"]
