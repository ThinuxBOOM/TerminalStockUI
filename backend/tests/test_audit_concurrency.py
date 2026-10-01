"""Concurrent audit appends must not fork the hash chain (Postgres only).

Runs when TEST_POSTGRES_URL points at a migrated Postgres database (CI's
migrations job sets it); skipped otherwise. Without the advisory lock in
append_audit_log, 8 writers x 25 appends produced dozens of rows sharing a
prev_hash and the verifier reported the log as tampered.
"""

from __future__ import annotations

import os
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.api.audit import append_audit_log
from backend.db.session import normalize_postgres_url
from backend.observability.audit_verify import verify_database

URL = (os.getenv("TEST_POSTGRES_URL") or "").strip()

pytestmark = pytest.mark.skipif(not URL, reason="set TEST_POSTGRES_URL to a migrated Postgres")


def test_concurrent_appends_keep_one_chain():
    url = normalize_postgres_url(URL)
    engine = create_engine(url, pool_size=10)
    Session = sessionmaker(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_logs RESTART IDENTITY"))

    def writer(n: int) -> None:
        for i in range(25):
            db = Session()
            try:
                append_audit_log(db, actor=f"w{n}", action="test.race", entity_type="t",
                                 entity_id=str(i), payload={"i": i})
            finally:
                db.close()

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with engine.begin() as conn:
        forks = conn.execute(text(
            "SELECT count(*) FROM (SELECT prev_hash FROM audit_logs "
            "GROUP BY prev_hash HAVING count(*) > 1) x")).scalar()
        rows = conn.execute(text("SELECT count(*) FROM audit_logs")).scalar()
        conn.execute(text("TRUNCATE audit_logs RESTART IDENTITY"))
    engine.dispose()
    assert rows == 200
    assert forks == 0
    assert verify_database(url)["ok"] is True
