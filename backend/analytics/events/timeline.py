"""Corporate-event timeline normalizer (Milestone 2).

Merges earnings, dividends, splits (plus optional filings/news) into one
time-ordered canonical timeline. Pure functions: no network, no randomness.

Canonical row: date (datetime64), event_type, title, amount, currency,
adjustment_factor, source. Split rows carry adjustment_factor, the factor
by which prices BEFORE the ex-date must be multiplied (e.g. a "2:1" split
-> 0.5). Rows without a parseable date are dropped and counted; any drops
downgrade quality to "degraded" with a reason.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import pandas as pd

from ..common import DEGRADED, MetricResult, unavailable

EVENT_TYPES = ("earnings", "dividend", "split", "filing", "news")
CANONICAL_COLUMNS = ("date", "event_type", "title", "amount",
                     "currency", "adjustment_factor", "source")
FORMULA = ("timeline = concat(normalized earnings/dividends/splits/...) "
           "sorted by (date, event_type); exact-duplicate rows removed")

_FIELD_ALIASES = {
    "date": ("date", "ex_date", "exdate", "announcement_date", "pay_date",
             "report_date", "fiscal_date", "timestamp"),
    "title": ("title", "name", "description", "label"),
    "amount": ("amount", "value", "dividend", "dividend_amount", "eps",
               "eps_actual", "eps_estimate", "revenue", "price"),
    "currency": ("currency", "ccy"),
    "source": ("source", "provider"),
}


def _pick(record: Mapping, names: Sequence[str]) -> Any:
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    return None


def _parse_split_factor(record: Mapping) -> float | None:
    if "adjustment_factor" in record and record["adjustment_factor"] is not None:
        try:
            return float(record["adjustment_factor"])
        except (ValueError, TypeError):
            return None
    if "split_ratio" in record and record["split_ratio"] is not None:
        text = str(record["split_ratio"]).strip()
        if ":" in text:
            left, _, right = text.partition(":")
            try:
                return float(right) / float(left)
            except (ValueError, ZeroDivisionError):
                return None
    num = record.get("split_numerator", None)
    den = record.get("split_denominator", None)
    if num is not None and den is not None:
        try:
            return float(den) / float(num)
        except (ValueError, ZeroDivisionError, TypeError):
            return None
    return None


def normalize_events(
    records: Sequence[Mapping] | pd.DataFrame,
    event_type: str,
    default_source: str = "unknown",
) -> MetricResult:
    """Normalize one event-type feed into canonical rows.

    Accepts a list of dicts (with aliases, see _FIELD_ALIASES) or a
    DataFrame with canonical/alias columns.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"event_type must be one of {EVENT_TYPES}, got {event_type!r}")
    if isinstance(records, pd.DataFrame):
        rows: list[dict] = records.to_dict(orient="records")
    elif isinstance(records, Sequence):
        rows = [dict(r) for r in records]
    else:
        return unavailable(FORMULA, [event_type],
                           f"records must be a DataFrame or a sequence of mappings")
    canada: list[dict] = []
    dropped = 0
    for row in rows:
        if not isinstance(row, Mapping):
            dropped += 1
            continue
        try:
            date = pd.to_datetime(_pick(row, _FIELD_ALIASES["date"]), utc=True)
        except (ValueError, TypeError):
            date = None
        if date is None or pd.isna(date):
            dropped += 1
            continue
        if event_type == "split":
            # A split row without a positive finite adjustment factor cannot
            # adjust prices (and a null factor would silently skip the split).
            # Drop + count it instead of emitting an unusable row.
            factor = _parse_split_factor(row)
            try:
                factor_ok = (
                    factor is not None
                    and math.isfinite(float(factor))
                    and float(factor) > 0
                )
            except (TypeError, ValueError):
                factor_ok = False
            if not factor_ok:
                dropped += 1
                continue
        else:
            factor = None
        canada.append({
            "date": date,
            "event_type": event_type,
            "title": _pick(row, _FIELD_ALIASES["title"]),
            "amount": _pick(row, _FIELD_ALIASES["amount"]),
            "currency": _pick(row, _FIELD_ALIASES["currency"]),
            "adjustment_factor": factor,
            "source": _pick(row, _FIELD_ALIASES["source"]) or default_source,
        })
    frame = pd.DataFrame(canada, columns=list(CANONICAL_COLUMNS))
    if dropped:
        return MetricResult(frame, FORMULA, [event_type], DEGRADED,
                            reason=f"dropped {dropped} rows without a parseable date")
    return MetricResult(frame, FORMULA, [event_type], "ok")


def normalize_earnings(records: Sequence[Mapping] | pd.DataFrame,
                       default_source: str = "unknown") -> MetricResult:
    """Normalize earnings announcements / results (alias-aware)."""
    return normalize_events(records, "earnings", default_source)


def normalize_dividends(records: Sequence[Mapping] | pd.DataFrame,
                        default_source: str = "unknown") -> MetricResult:
    """Normalize dividend declarations / payments (alias-aware)."""
    return normalize_events(records, "dividend", default_source)


def normalize_splits(records: Sequence[Mapping] | pd.DataFrame,
                     default_source: str = "unknown") -> MetricResult:
    """Normalize stock splits with adjustment factors (alias-aware)."""
    return normalize_events(records, "split", default_source)


def build_timeline(*frames: pd.DataFrame | MetricResult) -> MetricResult:
    """Merge normalized frames into one deduped, time-ordered timeline."""
    parts: list[pd.DataFrame] = []
    for item in frames:
        frame = item.value if isinstance(item, MetricResult) else item
        if frame is None:
            return unavailable(FORMULA, list(EVENT_TYPES),
                               "cannot build a timeline from an unavailable feed")
        if not isinstance(frame, pd.DataFrame):
            return unavailable(FORMULA, list(EVENT_TYPES),
                               "timeline inputs must be DataFrames or MetricResults")
        parts.append(frame)
    if not parts:
        empty = pd.DataFrame(columns=list(CANONICAL_COLUMNS))
        return MetricResult(empty, FORMULA, list(EVENT_TYPES), "ok")
    merged = pd.concat(parts, ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], utc=True)
    before = len(merged)
    merged = merged.drop_duplicates().sort_values(
        ["date", "event_type"], kind="mergesort").reset_index(drop=True)
    removed = before - len(merged)
    if removed:
        return MetricResult(merged, FORMULA, list(EVENT_TYPES), DEGRADED,
                            reason=f"removed {removed} exact-duplicate rows")
    return MetricResult(merged, FORMULA, list(EVENT_TYPES), "ok")


__all__ = [
    "normalize_events", "normalize_earnings", "normalize_dividends",
    "normalize_splits", "build_timeline", "EVENT_TYPES", "CANONICAL_COLUMNS",
]
