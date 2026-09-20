"""Backfill ensemble-v3 calibration snapshots (idempotent upsert).

Builds one walk-forward snapshot row per (symbol, horizon) via
:func:`backend.forecasting.calibration.snapshots.build_snapshot` and
persists with :func:`upsert_snapshot` on the UNIQUE key. Safe to re-run:
rows are merged on (symbol, horizon, model/feature/data version).

Why a script (not only cron): ``GET /api/cron/calibrate`` runs under the
serverless tick budget, so a full-universe backfill (29 symbols x 4
horizons of 500-bar walk-forward replays) must be sharded. This CLI has
no tick budget — run it from CI/local in shards and let cron keep rows
fresh afterwards.

Usage (repo root)::

    python scripts/backfill_calibration.py [--symbols AAPL,MSFT] [--database-url URL]

Defaults: symbols from ``INGEST_SYMBOLS`` env or the built-in universe;
database from ``DATABASE_URL`` env (backend default sqlite
./onemarket.db for local dev). Prints a redacted summary (counts only,
never credentials).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill ensemble-v3 calibration snapshots."
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols (default: INGEST_SYMBOLS or built-in universe).",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Database URL (default: DATABASE_URL env).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_url = (args.database_url or "").strip() or os.getenv("DATABASE_URL", "").strip()
    if db_url:
        # Set before backend imports so the cached engine binds to it.
        os.environ["DATABASE_URL"] = db_url
    else:
        print(
            "backfill: WARN: DATABASE_URL unset — using backend default "
            "(sqlite ./onemarket.db for local dev). Set DATABASE_URL for "
            "Postgres/Supabase.",
            file=sys.stderr,
        )
    try:
        from backend.market_data import ingest as ingest_module
        from backend.forecasting.common import FORECAST_HORIZONS
        from backend.forecasting.calibration.snapshots import (
            build_snapshot,
            upsert_snapshot,
        )
        from backend.db.session import get_session_factory, init_db
        from backend.market_data.service import MarketDataService
    except ImportError as exc:
        print(
            json.dumps({
                "ok": False,
                "error": "ImportError: backend not importable "
                         "(run from repo root; got %s: %s)" % (type(exc).__name__, exc),
            }),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "ImportError"}))
        return 2

    if (args.symbols or "").strip():
        symbols = [p.strip() for p in args.symbols.split(",") if p.strip()]
    else:
        try:
            symbols = ingest_module.default_universe()
        except Exception as exc:
            print(
                "backfill: ERROR: cannot resolve ingest universe: %s: %s"
                % (type(exc).__name__, exc),
                file=sys.stderr,
            )
            return 2
    if not symbols:
        print(
            "backfill: ERROR: no symbols (--symbols empty and "
            "INGEST_SYMBOLS/default universe empty)",
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "no-symbols"}))
        return 2

    try:
        init_db()
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": "db-unavailable: %s" % type(exc).__name__}),
        )
        return 1

    market = MarketDataService()
    Session = get_session_factory()
    snapshots: dict[str, int] = {}
    errors: dict[str, str] = {}
    calibrated = 0
    unscored = 0
    for raw in symbols:
        symbol = (raw or "").strip().upper()[:32]
        if not symbol:
            continue
        for horizon in FORECAST_HORIZONS:
            key = "%s:%d" % (symbol, int(horizon))
            db = Session()
            try:
                try:
                    snap = build_snapshot(raw, int(horizon), market_service=market)
                    upsert_snapshot(db, snap)
                except Exception as exc:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    errors[key] = "%s: %s" % (type(exc).__name__, str(exc)[:200])
                    continue
                n_windows = int(snap.get("n_windows") or 0)
                snapshots[key] = n_windows
                if n_windows >= 10:
                    calibrated += 1
                else:
                    unscored += 1
            finally:
                try:
                    db.close()
                except Exception:
                    pass
    summary = {
        "ok": not errors,
        "calibrated": calibrated,
        "unscored": unscored,
        "snapshots": snapshots,
        "errors": errors,
        "symbols": len(symbols),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
