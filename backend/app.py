"""Backend entrypoint (repo-root style): uvicorn backend.app:app."""

try:
    from backend.api.main import app, create_app
except ImportError:  # backend/ as CWD
    from api.main import app, create_app  # type: ignore[no-redef]

__all__ = ["app", "create_app"]
