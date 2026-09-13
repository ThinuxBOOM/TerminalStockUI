"""Provenance envelope (spec section 4, docs/API_CONTRACT.md).

Every data-bearing response includes exactly these fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source: str = Field(description="Upstream provider that actually served the data")
    as_of: datetime = Field(description="Upstream timestamp the data is current as of (UTC)")
    delay_minutes: int = Field(ge=0, description="Known feed delay in minutes")
    quality_grade: str = Field(description="A/B/C/D/F data-quality grade")
    fallback_used: bool = Field(description="True when served from cache/secondary after primary failure")
    missing_fields: list[str] = Field(
        default_factory=list, description="Fields requested but unavailable"
    )


def build_provenance(
    source: str,
    *,
    as_of: datetime | None = None,
    delay_minutes: int = 0,
    quality_grade: str = "B",
    fallback_used: bool = False,
    missing_fields: list[str] | None = None,
) -> Provenance:
    ts = as_of or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return Provenance(
        source=source,
        as_of=ts,
        delay_minutes=int(delay_minutes),
        quality_grade=quality_grade,
        fallback_used=bool(fallback_used),
        missing_fields=list(missing_fields or []),
    )
