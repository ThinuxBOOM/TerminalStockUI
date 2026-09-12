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

from backend.api.main import app  # noqa: E402

try:
    from mangum import Mangum

    handler = Mangum(app, lifespan="off")
except Exception:  # mangum missing locally / import failure -> raw ASGI app
    handler = app

__all__ = ["app", "handler"]
