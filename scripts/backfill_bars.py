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
    from backend.market_data import ingest as ingest_module

    if (args.symbols or "").strip():
        symbols = [
            part.strip()
            for part in args.symbols.split(",")
            if part.strip()
        ]
    else:
        symbols = ingest_module.default_universe()
    db_url = (args.database_url or "").strip() or os.getenv("DATABASE_URL", "")
    try:
        ingested, errors = ingest_module.ingest_symbols(
            symbols, db_url=db_url or None
        )
    except Exception as exc:  # unexpected: batch wrapper already swallows per-symbol
        print(json.dumps({"ok": False, "error": type(exc).__name__}))
        return 1
    summary = {
        "ok": not errors,
        "ingested": ingested,
        "errors": errors,
        "symbols": len(symbols),
        "bars": sum(ingested.values()),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
