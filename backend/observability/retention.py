"""Data-retention rules + purge helpers (M8 hardening, spec Milestone 0 + Sec 6).

Retention table (normative for this milestone):

| dataset                | table                   | retention | time column |
|------------------------|-------------------------|-----------|-------------|
| ticks                  | ticks                   | 90 days   | ts          |
| bars                   | price_bars              | 5 years   | ts          |
| forecasts              | forecasts               | 3 years   | created_at  |
| forecast_accuracy      | forecast_accuracy       | 3 years   | scored_at   |
| snapshots_raw          | market_snapshots        | 30 days   | created_at  |
| snapshots_compressed   | market_snapshots        | 1 year    | created_at  |
| market_snapshots       | market_snapshots        | 2 years   | ts          |
| audit                  | audit_logs              | 7 years   | created_at  |
| ai_token_logs          | ai_token_logs (legacy)  | 1 year    | created_at  |
| ai_token_ledger        | ai_token_ledger         | 1 year    | created_at  |
| provider_health_history| provider_health_history | 90 days   | ts          |
| indicator_cache        | indicator_cache         | 30 days   | updated_at  |

Rules:
  - ``purge_dry_run(session)`` returns per-dataset delete counts without
    touching data.
  - ``purge(session)`` deletes expired rows and returns the same count shape.
  - The audit hash-chain **head (max id) is never deleted**, even when it is
    older than the retention window, so ``verify_chain`` always has an anchor.
  - ``ticks`` / ``ai_token_logs`` / any 0006 table may not exist on older
    databases; both helpers treat a missing table as ``0`` instead of
    raising (graceful SQLite ``onemarket.db`` degrade).
  - ``ai_token_logs`` is the legacy dataset name (no model table); the 0006
    successor is ``ai_token_ledger`` with the same 1y window. Both are kept
    so old DBs and new DBs purge correctly.
  - ``forecast_accuracy`` shares the forecasts 3y window so scored
    denominators stay consistent with the forecast log (forecasts purge
    first, so rescore-after-purge is impossible).
  - ``market_snapshots`` 2y on ``ts`` is the outer backstop bound; finer
    per-encoding tiers fire on younger rows first (``snapshots_raw``: 30d on
    ``created_at`` for non-gzip captures; ``snapshots_compressed``: 1y on
    ``created_at`` for gzip captures). Rules run in list order, so rows older
    than 2y are still consumed by the backstop with identical counts.
  - Accuracy aggregates persist indefinitely: per-forecast rows follow the
    3y window above, while the trailing realized windows merged into
    ``calibration_snapshots.members["realized"]`` by scoring have no purge
    rule (calibration snapshots are never deleted).
  - ``indicator_cache`` is a cache, not history: rows older than the window
    are stale evictions, keyed on ``updated_at``.
  - Rules may carry an extra ``where`` SQL fragment (the snapshot tiers
    split one table by ``encoding``); the fragment is AND-ed onto the cutoff
    predicate for both counting and deleting.
  - Day counts default to the table above and can be overridden with
    ``RETENTION_<DATASET>_DAYS`` env vars (see infra/docker/.env.example)
    or an explicit ``retention_days={dataset: days}`` argument (tests).

Usage:
    python -m backend.observability.retention --dry-run [--database-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

RETENTION_TICKS_DAYS = 90
RETENTION_BARS_DAYS = 5 * 365  # 1825
RETENTION_FORECASTS_DAYS = 3 * 365  # 1095
RETENTION_AUDIT_DAYS = 7 * 365  # 2555
RETENTION_AI_TOKEN_LOGS_DAYS = 365
# --- 0006 revamp additions (additive; existing values untouched) ---
RETENTION_FORECAST_ACCURACY_DAYS = 3 * 365  # 1095 (tied to forecasts)
RETENTION_MARKET_SNAPSHOTS_DAYS = 2 * 365  # 730 (compressed raw; heavier than bars)
RETENTION_AI_TOKEN_LEDGER_DAYS = 365  # 1y (0006 successor of ai_token_logs)
RETENTION_PROVIDER_HEALTH_HISTORY_DAYS = 90  # ops samples; 90d like ticks
RETENTION_INDICATOR_CACHE_DAYS = 30  # stale cache eviction on updated_at
# --- Agent 3 snapshot tiers (additive; revamp values untouched) ---
RETENTION_SNAPSHOTS_RAW_DAYS = 30  # non-gzip captures; 30d on created_at
RETENTION_SNAPSHOTS_COMPRESSED_DAYS = 365  # gzip captures; 1y on created_at

RETENTION_RULES: list[dict[str, Any]] = [
    {"dataset": "ticks", "table": "ticks", "retention_days": RETENTION_TICKS_DAYS,
     "time_column": "ts", "notes": "raw intraday ticks; 90d"},
    {"dataset": "bars", "table": "price_bars", "retention_days": RETENTION_BARS_DAYS,
     "time_column": "ts", "notes": "OHLCV bars; 5y on bar time"},
    {"dataset": "forecasts", "table": "forecasts", "retention_days": RETENTION_FORECASTS_DAYS,
     "time_column": "created_at", "notes": "versioned forecast log; 3y"},
    {"dataset": "audit", "table": "audit_logs", "retention_days": RETENTION_AUDIT_DAYS,
     "time_column": "created_at", "notes": "append-only hash chain; 7y; head never deleted"},
    {"dataset": "ai_token_logs", "table": "ai_token_logs", "retention_days": RETENTION_AI_TOKEN_LOGS_DAYS,
     "time_column": "created_at", "notes": "AI token usage (legacy name); 1y"},
    # --- 0006 revamp (missing tables count as 0 via _table_exists) ---
    {"dataset": "forecast_accuracy", "table": "forecast_accuracy",
     "retention_days": RETENTION_FORECAST_ACCURACY_DAYS,
     "time_column": "scored_at", "notes": "per-forecast scores; 3y tied to forecasts"},
    {"dataset": "market_snapshots", "table": "market_snapshots",
     "retention_days": RETENTION_MARKET_SNAPSHOTS_DAYS,
     "time_column": "ts", "notes": "compressed snapshots; 2y on snapshot time"},
    # --- Agent 3 tiers (run AFTER the backstop: rows older than 2y are
    # consumed above with identical counts; tiers only see younger rows) ---
    {"dataset": "snapshots_raw", "table": "market_snapshots",
     "retention_days": RETENTION_SNAPSHOTS_RAW_DAYS,
     "time_column": "created_at", "notes": "raw (non-gzip) snapshots; 30d",
     "where": "\"encoding\" NOT LIKE '%gzip%'"},
    {"dataset": "snapshots_compressed", "table": "market_snapshots",
     "retention_days": RETENTION_SNAPSHOTS_COMPRESSED_DAYS,
     "time_column": "created_at", "notes": "compressed (gzip) snapshots; 1y",
     "where": "\"encoding\" LIKE '%gzip%'"},
    {"dataset": "ai_token_ledger", "table": "ai_token_ledger",
     "retention_days": RETENTION_AI_TOKEN_LEDGER_DAYS,
     "time_column": "created_at", "notes": "AI token ledger (0006); 1y"},
    {"dataset": "provider_health_history", "table": "provider_health_history",
     "retention_days": RETENTION_PROVIDER_HEALTH_HISTORY_DAYS,
     "time_column": "ts", "notes": "durable health samples; 90d"},
    {"dataset": "indicator_cache", "table": "indicator_cache",
     "retention_days": RETENTION_INDICATOR_CACHE_DAYS,
     "time_column": "updated_at", "notes": "stale cache eviction; 30d on updated_at"},
]

RETENTION_DAYS: dict[str, int] = {r["dataset"]: r["retention_days"] for r in RETENTION_RULES}

_ENV_KEYS = {
    "ticks": "RETENTION_TICKS_DAYS",
    "bars": "RETENTION_BARS_DAYS",
    "forecasts": "RETENTION_FORECASTS_DAYS",
    "audit": "RETENTION_AUDIT_DAYS",
    "ai_token_logs": "RETENTION_AI_TOKEN_LOGS_DAYS",
    "forecast_accuracy": "RETENTION_FORECAST_ACCURACY_DAYS",
    "market_snapshots": "RETENTION_MARKET_SNAPSHOTS_DAYS",
    "ai_token_ledger": "RETENTION_AI_TOKEN_LEDGER_DAYS",
    "provider_health_history": "RETENTION_PROVIDER_HEALTH_HISTORY_DAYS",
    "indicator_cache": "RETENTION_INDICATOR_CACHE_DAYS",
    "snapshots_raw": "RETENTION_SNAPSHOTS_RAW_DAYS",
    "snapshots_compressed": "RETENTION_SNAPSHOTS_COMPRESSED_DAYS",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_retention_days(overrides: Optional[Mapping[str, int]] = None) -> dict[str, int]:
    """Effective per-dataset retention days: defaults <- env <- overrides."""
    days = dict(RETENTION_DAYS)
    for dataset, env_key in _ENV_KEYS.items():
        raw = os.getenv(env_key, "").strip()
        if raw:
            try:
                days[dataset] = int(raw)
            except ValueError:
                pass
    if overrides:
        for k, v in overrides.items():
            if k in days:
                days[k] = int(v)
    return days


def cutoff_for(retention_days: int, now: Optional[datetime] = None) -> datetime:
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now - timedelta(days=int(retention_days))


def _cutoffs(now: Optional[datetime], days: Mapping[str, int]) -> dict[str, datetime]:
    now = now or _utcnow()
    return {dataset: cutoff_for(d, now) for dataset, d in days.items()}


def _table_exists(session: Any, table: str) -> bool:
    # NOTE: inspect via session.connection() (not the engine) so the
    # catalog lookup joins the session's transaction. Checking out a
    # separate pooled connection mid-transaction silently invalidates
    # pending DML on single-connection pools (SQLite StaticPool/:memory:).
    try:
        from sqlalchemy import inspect as _inspect

        return bool(_inspect(session.connection()).has_table(table))
    except Exception:
        return False


def _time_column(session: Any, table: str, preferred: str) -> Optional[str]:
    """Pick the first existing time column (preferred, then common fallbacks)."""
    candidates = [preferred, "ts", "created_at", "as_of", "time"]
    try:
        from sqlalchemy import inspect as _inspect

        cols = {c["name"] for c in _inspect(session.connection()).get_columns(table)}
    except Exception:
        return preferred
    for name in candidates:
        if name in cols:
            return name
    return None


def _audit_head_id(session: Any) -> Optional[int]:
    try:
        from sqlalchemy import text

        if not _table_exists(session, "audit_logs"):
            return None
        row = session.execute(text("SELECT MAX(id) AS max_id FROM audit_logs")).mappings().first()
        return int(row["max_id"]) if row and row["max_id"] is not None else None
    except Exception:
        return None


def _count_older(session: Any, table: str, column: str, cutoff: datetime,
                 *, exclude_id: Optional[int] = None,
                 exclude_col: str = "id",
                 extra_where: str | None = None) -> int:
    from sqlalchemy import text

    if not _table_exists(session, table):
        return 0
    stmt = f'SELECT COUNT(*) AS n FROM "{table}" WHERE "{column}" < :cutoff'
    params: dict[str, Any] = {"cutoff": cutoff}
    if exclude_id is not None:
        stmt += f' AND "{exclude_col}" != :exclude_id'
        params["exclude_id"] = exclude_id
    if extra_where:
        stmt += f' AND ({extra_where})'
    try:
        row = session.execute(text(stmt), params).mappings().first()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _delete_older(session: Any, table: str, column: str, cutoff: datetime,
                  *, exclude_id: Optional[int] = None,
                  exclude_col: str = "id",
                  extra_where: str | None = None) -> int:
    from sqlalchemy import text

    if not _table_exists(session, table):
        return 0
    stmt = f'DELETE FROM "{table}" WHERE "{column}" < :cutoff'
    params: dict[str, Any] = {"cutoff": cutoff}
    if exclude_id is not None:
        stmt += f' AND "{exclude_col}" != :exclude_id'
        params["exclude_id"] = exclude_id
    if extra_where:
        stmt += f' AND ({extra_where})'
    try:
        result = session.execute(text(stmt), params)
        return int(result.rowcount or 0)
    except Exception:
        return 0


def purge_dry_run(session: Any, now: Optional[datetime] = None,
                  retention_days: Optional[Mapping[str, int]] = None) -> dict:
    """Count expired rows per dataset without deleting anything."""
    days = get_retention_days(retention_days)
    cutoffs = _cutoffs(now, days)
    head_id = _audit_head_id(session)
    counts: dict[str, int] = {}
    for rule in RETENTION_RULES:
        dataset, table = rule["dataset"], rule["table"]
        col = _time_column(session, table, rule["time_column"]) if _table_exists(session, table) else None
        if col is None:
            counts[dataset] = 0
            continue
        if dataset == "audit":
            counts[dataset] = _count_older(session, table, col, cutoffs[dataset], exclude_id=head_id)
        else:
            counts[dataset] = _count_older(session, table, col, cutoffs[dataset],
                                           extra_where=rule.get("where"))
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "cutoffs": {k: v.isoformat() for k, v in cutoffs.items()},
        "audit_head_id": head_id,
        "deleted": False,
    }


def purge(session: Any, now: Optional[datetime] = None,
          retention_days: Optional[Mapping[str, int]] = None) -> dict:
    """Delete expired rows per dataset; the audit head (max id) is preserved."""
    days = get_retention_days(retention_days)
    cutoffs = _cutoffs(now, days)
    head_id = _audit_head_id(session)
    counts: dict[str, int] = {}
    for rule in RETENTION_RULES:
        dataset, table = rule["dataset"], rule["table"]
        col = _time_column(session, table, rule["time_column"]) if _table_exists(session, table) else None
        if col is None:
            counts[dataset] = 0
            continue
        if dataset == "audit":
            counts[dataset] = _delete_older(session, table, col, cutoffs[dataset], exclude_id=head_id)
        else:
            counts[dataset] = _delete_older(session, table, col, cutoffs[dataset],
                                            extra_where=rule.get("where"))
    try:
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        raise
    return {
        "counts": counts,
        "total": sum(counts.values()),
        "cutoffs": {k: v.isoformat() for k, v in cutoffs.items()},
        "audit_head_id": head_id,
        "deleted": True,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OneMarket retention purge (dry-run by default).")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", "sqlite:///./onemarket.db"))
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true", help="Actually delete expired rows.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        from backend.db.session import get_engine
        from sqlalchemy.orm import sessionmaker
    except ImportError:  # direct script run
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        from backend.db.session import get_engine
        from sqlalchemy.orm import sessionmaker

    engine = get_engine(args.database_url)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as session:
        result = purge(session) if args.apply else purge_dry_run(session)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
