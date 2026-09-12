"""Background workers (M8 hardening, spec Sec 2 + Sec 6).

Arq/RQ-style job stubs. No hard Redis dependency: jobs run in-process via
``python -m backend.workers.jobs --once`` and only use Redis when an
optional broker URL + client library are both present.

Lazy re-exports (PEP 562) so ``python -m backend.workers.jobs`` does not
pre-import the submodule during package init (avoids runpy double-import
warning and keeps two module instances from diverging).
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "JOB_REGISTRY",
    "JOB_NAMES",
    "evaluate_alerts",
    "generate_report",
    "ingest_bars",
    "refresh_forecast",
    "run_once",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from backend.workers import jobs as _jobs

        return getattr(_jobs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
