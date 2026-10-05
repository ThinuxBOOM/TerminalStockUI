"""Migration 0013 against real Postgres (runs when TEST_POSTGRES_URL is set).

Everything happens inside one transaction that is rolled back, so the test
leaves the database exactly as it found it. CI applies all migrations first.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

PG_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="set TEST_POSTGRES_URL to a migrated Postgres")
SQL = (Path(__file__).resolve().parents[2] / "migrations" / "0013_repair_bar_timestamps.sql").read_text(encoding="utf-8")


def test_0013_moves_indices_and_dedupes_utc_midnight_bars():
    import psycopg

    with psycopg.connect(PG_URL) as conn:
        try:
            cur = conn.cursor()
            idx, sse = uuid.uuid4(), uuid.uuid4()
            sym = f"T{uuid.uuid4().hex[:6].upper()}"
            cur.execute("DELETE FROM instruments WHERE provider_symbol = '^AEX'")
            cur.execute(
                "INSERT INTO instruments (instrument_id, exchange_mic, exchange_symbol, provider_symbol, company_name, currency, timezone, trading_calendar)"
                " VALUES (%s, 'XNAS', '^AEX', '^AEX', 'AEX', 'USD', 'UTC', 'XNAS'),"
                "        (%s, 'XSHG', %s, %s, 'test', 'CNY', 'Asia/Shanghai', 'XSHG')",
                (idx, sse, sym, f"{sym}.SS"),
            )
            bars = [
                ("2026-01-25 16:00+00", 1400.0),  # local midnight of 26 Jan (kept)
                ("2026-01-26 00:00+00", 999.0),   # same session at 00:00 UTC (deleted)
                ("2026-01-28 00:00+00", 1410.0),  # orphan at 00:00 UTC (re-stamped)
            ]
            for ts, close in bars:
                cur.execute(
                    "INSERT INTO price_bars (instrument_id, ts, timeframe, open, high, low, close, volume, source, as_of, quality_grade)"
                    " VALUES (%s, %s, '1d', %s, %s, %s, %s, 1, 'yfinance', now(), 'B')",
                    (sse, ts, close, close, close, close),
                )
            cur.execute(SQL)
            cur.execute("SELECT exchange_mic, timezone, currency FROM instruments WHERE instrument_id = %s", (idx,))
            assert cur.fetchone() == ("XAMS", "Europe/Amsterdam", "EUR")
            cur.execute("SELECT to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), close FROM price_bars"
                        " WHERE instrument_id = %s ORDER BY ts", (sse,))
            rows = [(t, float(c)) for t, c in cur.fetchall()]
            assert rows == [("2026-01-25 16:00", 1400.0), ("2026-01-27 16:00", 1410.0)]
            cur.execute(SQL)  # idempotent
            cur.execute("SELECT count(*) FROM price_bars WHERE instrument_id = %s", (sse,))
            assert cur.fetchone()[0] == 2
        finally:
            conn.rollback()
