"""Backend runner: python -m backend.main / uvicorn backend.main:app."""

try:
    from backend.api.main import app, create_app
except ImportError:  # backend/ as CWD
    from api.main import app, create_app  # type: ignore[no-redef]

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
