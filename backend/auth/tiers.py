"""Future auth/tier prep stubs (NO enforcement, NO login, NO billing).

The subscription plan (Free/Silver/Gold/Platinum + paid Anthropic/xAI/OpenAI
keys) lands later with a third users DB. This module only defines the
forward-compatible surface so revamp code can already carry user_id/tier
without branching later:

- TIER_QUOTAS: monthly call allocations per plan (reference copy of the
  product spec; routers MUST NOT gate on it yet).
- can_use_feature(): pure tier->feature check for future UI gating.
- get_current_user_stub(): FastAPI dependency returning guest today.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

VALID_TIERS: tuple[str, ...] = ("free", "silver", "gold", "platinum")

TIER_QUOTAS: dict[str, dict[str, Any]] = {
    "free": {
        "providers": ["gemini"],
        "quick_insight_per_day": 20,
        "forecast_assist_per_month": 0,
        "report_per_month": 0,
        "deep_research_per_month": 0,
    },
    "silver": {
        "providers": ["gemini", "openai"],
        "quick_insight_per_month": 1000,
        "forecast_assist_per_month": 400,
        "report_per_month": 100,
        "deep_research_per_month": 0,
    },
    "gold": {
        "providers": ["gemini", "openai", "xai"],
        "quick_insight_per_month": 3000,
        "forecast_assist_per_month": 1200,
        "report_per_month": 300,
        "deep_research_per_month": 50,
    },
    "platinum": {
        "providers": ["gemini", "openai", "xai", "anthropic"],
        "quick_insight_per_month": 8000,
        "forecast_assist_per_month": 3000,
        "report_per_month": 800,
        "deep_research_per_month": 200,
    },
}

# Call-type -> minimum tier that will be allowed once gating lands.
FEATURE_MIN_TIER: dict[str, str] = {
    "quick_insight": "free",
    "forecast_assist": "free",
    "report": "silver",
    "deep_research": "silver",
}

_TIER_RANK = {name: i for i, name in enumerate(VALID_TIERS)}


def normalize_tier(tier: str | None) -> str:
    cand = str(tier or "free").strip().lower()
    return cand if cand in _TIER_RANK else "free"


def can_use_feature(tier: str | None, feature: str) -> bool:
    """Pure tier check for FUTURE gating. Always True-safe on unknown input."""
    try:
        need = FEATURE_MIN_TIER.get(str(feature or "").strip().lower(), "free")
        return _TIER_RANK[normalize_tier(tier)] >= _TIER_RANK[normalize_tier(need)]
    except Exception:
        return True


def quota_for(tier: str | None, call_type: str) -> int | None:
    """Monthly quota for (tier, call_type) or None when unlimited/unknown."""
    try:
        quotas = TIER_QUOTAS[normalize_tier(tier)]
        key = f"{str(call_type or '').strip().lower()}_per_month"
        value = quotas.get(key, quotas.get(f"{str(call_type or '').strip().lower()}_per_day"))
        return int(value) if isinstance(value, (int, float)) else None
    except Exception:
        return None


def get_current_user_stub(request: Request | None = None) -> dict[str, Any]:
    """Guest stub dependency: {user_id None, tier free, is_guest True}.

    Reads optional X-Tier / X-User-Id headers for forward-compat testing
    (still guest: user_id stays None unless a future auth middleware sets
    request.state.user). Never raises, never gates.
    """
    tier = "free"
    try:
        if request is not None:
            hdr = request.headers.get("x-tier", "") if hasattr(request, "headers") else ""
            if str(hdr or "").strip().lower() in _TIER_RANK:
                tier = str(hdr).strip().lower()
            state_user = getattr(getattr(request, "state", None), "user", None)
            if isinstance(state_user, dict) and state_user.get("tier") in _TIER_RANK:
                tier = str(state_user["tier"]).strip().lower()
    except Exception:
        tier = "free"
    return {"user_id": None, "tier": tier, "is_guest": True}


__all__ = [
    "FEATURE_MIN_TIER",
    "TIER_QUOTAS",
    "VALID_TIERS",
    "can_use_feature",
    "get_current_user_stub",
    "normalize_tier",
    "quota_for",
]
