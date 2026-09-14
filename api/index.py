"""Vercel Python serverless entry for OneMarket Analyzer.

File-based function (``api/index.py`` -> route ``/api``): Vercel loads the
top-level ``app`` ASGI app natively — no Mangum adapter needed on the current
Python runtime. Local dev keeps using ``uvicorn backend.app:app``.

IMPORTANT: ``app`` must stay a top-level ``from backend.api.main import app``
so Vercel's build-time function discovery sees it. Do NOT move it inside a
``try/except`` (that hides it from static analysis and the build fails with
``The pattern "api/index.py" ... doesn't match any Serverless Functions``).

Layout: repo root (``TerminalStockUI/``) on ``sys.path`` so ``backend.*`` is
importable. The insert below is a no-op on Vercel (CWD is already the project
root) and helps when this file is loaded with ``api/`` as CWD.
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # repo root: TerminalStockUI/
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.api.main import app  # noqa: E402  (must stay top-level for Vercel)

__all__ = ["app"]
