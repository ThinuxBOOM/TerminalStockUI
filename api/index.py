"""Vercel Python serverless entry for OneMarket Analyzer.

Vercel invokes ``handler``; local dev keeps using ``uvicorn backend.app:app``.

Layout expectation: repo root (``onemarket-analyzer/``) on ``sys.path`` so
``backend.*`` is importable. When Vercel runs this file with ``api/`` as CWD,
we insert the parent (repo root) explicitly.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # onemarket-analyzer/
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    from backend.api.main import app  # noqa: E402
except ImportError as exc:  # clear message instead of a bare traceback on Vercel
    raise RuntimeError(
        "api/index.py: cannot import backend.api.main.app — "
        "expected repo root (%r) on sys.path with backend/api/main.py present. "
        "Check Vercel Root Directory and that backend/ is committed." % (_ROOT,)
    ) from exc

try:
    from mangum import Mangum

    handler = Mangum(app, lifespan="off")
except Exception:  # mangum missing locally / import failure -> raw ASGI app
    handler = app

__all__ = ["app", "handler"]
