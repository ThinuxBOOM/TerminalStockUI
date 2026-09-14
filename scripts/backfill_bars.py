"""Backfill daily bars from yfinance into price_bars (idempotent upsert).

Reuses :func:`backend.market_data.ingest.ingest_symbols` directly (no HTTP),
so cron and CLI can never drift apart. Safe to re-run: rows are keyed
``(instrument_id, timeframe, ts)`` and merged on conflict.

Usage (repo root)::

    python3 scripts/backfill_bars.py [--symbols AAPL,MSFT] [--database-url URL]

Defaults: symbols from ``INGEST_SYMBOLS`` env or the built-in universe;
database from ``DATABASE_URL`` env. Stdlib + sqlalchemy + backend imports
only. Prints a redacted summary (counts only, never credentials).
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
        description="Backfill daily bars from yfinance into price_bars."
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
    try:
        from backend.market_data import ingest as ingest_module
    except ImportError as exc:
        print(
            json.dumps({
                "ok": False,
                "error": "ImportError: backend/market_data/ingest.py not importable "
                         "(run from repo root; got %s: %s)" % (type(exc).__name__, exc),
            }),
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "ImportError"}))
        return 2

    if (args.symbols or "").strip():
        symbols = [
            part.strip()
            for part in args.symbols.split(",")
            if part.strip()
        ]
    else:
        try:
            symbols = ingest_module.default_universe()
        except Exception as exc:
            print(
                "backfill: ERROR: cannot resolve ingest universe "
                "(INGEST_SYMBOLS env unreadable): %s: %s"
                % (type(exc).__name__, exc),
                file=sys.stderr,
            )
            return 2
    if not symbols:
        print(
            "backfill: ERROR: no symbols to ingest "
            "(--symbols empty and INGEST_SYMBOLS/default universe empty)",
            file=sys.stderr,
        )
        print(json.dumps({"ok": False, "error": "no-symbols", "symbols": 0, "bars": 0}))
        return 2
    db_url = (args.database_url or "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print(
            "backfill: WARN: DATABASE_URL unset — using backend default "
            "(sqlite ./onemarket.db for local dev). Set DATABASE_URL for "
            "Postgres/Supabase.",
            file=sys.stderr,
        )
    try:
        ingested, errors = ingest_module.ingest_symbols(
            symbols, db_url=db_url or None
        )
    except Exception as exc:  # unexpected: batch wrapper already swallows per-symbol
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    # Idempotent: ingest_symbols merges on (instrument_id, timeframe, ts), so
    # re-runs skip existing bars (update, never duplicate). Counts below are
    # upserted-row counts, safe to compare across runs.
    summary = {
        "ok": not errors,
        "ingested": ingested,
        "errors": errors,
        "symbols": len(symbols),
        "bars": sum(ingested.values()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
