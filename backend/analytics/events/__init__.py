"""Corporate-event timeline normalizer (Milestone 2). Pure functions, no network."""

from .timeline import (
    CANONICAL_COLUMNS,
    EVENT_TYPES,
    build_timeline,
    normalize_dividends,
    normalize_earnings,
    normalize_events,
    normalize_splits,
)

__all__ = [
    "normalize_events",
    "normalize_earnings",
    "normalize_dividends",
    "normalize_splits",
    "build_timeline",
    "EVENT_TYPES",
    "CANONICAL_COLUMNS",
]
