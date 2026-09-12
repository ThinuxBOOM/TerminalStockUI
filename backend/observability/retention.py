"""Data-retention rules + purge helpers (M8 hardening, spec Milestone 0 + Sec 6).

Retention table (normative for this milestone):

| dataset       | table          | retention | time column          |
|---------------|----------------|-----------|----------------------|
| ticks         | ticks          | 90 days   | ts / created_at      |
| bars          | price_bars     | 5 years   | ts                   |
| forecasts     | forecasts      | 3 years   | created_at           |
| audit         | audit_logs     | 7 years   | created_at           |
| ai_token_logs | ai_token_logs  | 1 year    | created_at           |

Rules:
  - ``purge_dry_run(session)`` returns per-dataset delete counts without
    touching data.
  - ``purge(session)`` deletes expired rows and returns the same count shape.
  - The audit hash-chain **head (max id) is never deleted**, even when it is
    older than the retention window, so ``verify_chain`` always has an anchor.
  - ``ticks`` / ``ai_token_logs`` tables may not exist on older databases;
    both helpers treat a missing table as ``0`` instead of raising.
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
     "time_column": "created_at", "notes": "AI token usage; 1y"},
]

RETENTION_DAYS: dict[str, int] = {r["dataset"]: r["retention_days"] for r in RETENTION_RULES}

_ENV_KEYS = {
    "ticks": "RETENTION_TICKS_DAYS",
    "bars": "RETENTION_BARS_DAYS",
    "forecasts": "RETENTION_FORECASTS_DAYS",
    "audit": "RETENTION_AUDIT_DAYS",
    "ai_token_logs": "RETENTION_AI_TOKEN_LOGS_DAYS",
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
                 exclude_col: str = "id") -> int:
    from sqlalchemy import text

    if not _table_exists(session, table):
        return 0
    stmt = f'SELECT COUNT(*) AS n FROM "{table}" WHERE "{column}" < :cutoff'
    params: dict[str, Any] = {"cutoff": cutoff}
    if exclude_id is not None:
        stmt += f' AND "{exclude_col}" != :exclude_id'
        params["exclude_id"] = exclude_id
    try:
        row = session.execute(text(stmt), params).mappings().first()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _delete_older(session: Any, table: str, column: str, cutoff: datetime,
                  *, exclude_id: Optional[int] = None,
                  exclude_col: str = "id") -> int:
    from sqlalchemy import text

    if not _table_exists(session, table):
        return 0
    stmt = f'DELETE FROM "{table}" WHERE "{column}" < :cutoff'
    params: dict[str, Any] = {"cutoff": cutoff}
    if exclude_id is not None:
        stmt += f' AND "{exclude_col}" != :exclude_id'
        params["exclude_id"] = exclude_id
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
            counts[dataset] = _count_older(session, table, col, cutoffs[dataset])
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
            counts[dataset] = _delete_older(session, table, col, cutoffs[dataset])
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
