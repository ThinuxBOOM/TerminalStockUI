"""Per-user daily cap on live AI calls.

Every AI call a user triggers is recorded in ``ai_token_ledger`` (see
``backend/api/ai.py::_persist_ledger``). Before starting a new call the API
counts that user's rows in the trailing 24 hours and refuses with 429 once
``AI_DAILY_CALLS_PER_USER`` (default 50; 0 = unlimited) is reached. This
bounds what any single account can spend on the operator's provider keys.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.db.models import AiTokenLedger
from backend.db.session import ensure_schema, get_session_factory


def daily_limit() -> int:
    try:
        return max(0, int((os.getenv("AI_DAILY_CALLS_PER_USER", "50") or "50").strip()))
    except ValueError:
        return 50


def calls_last_24h(user_id: str) -> int:
    try:
        key = uuid.UUID(str(user_id))
    except ValueError:
        return 0
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    ensure_schema()
    db = get_session_factory()()
    try:
        return int(db.execute(
            select(func.count()).select_from(AiTokenLedger)
            .where(AiTokenLedger.user_id == key, AiTokenLedger.created_at >= since)
        ).scalar_one())
    finally:
        db.close()


def enforce_ai_quota(user_id: str) -> None:
    limit = daily_limit()
    if limit <= 0:
        return
    used = calls_last_24h(user_id)
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"daily AI limit reached ({limit} calls per 24h); try again later",
        )


__all__ = ["calls_last_24h", "daily_limit", "enforce_ai_quota"]
