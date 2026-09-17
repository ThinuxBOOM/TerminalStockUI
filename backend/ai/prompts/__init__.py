"""Bounded, evidence-grounded prompt templates (Milestone 4).

Every prompt instructs the model to:
- return STRICT JSON matching the AIOpinion schema and nothing else,
- ground every catalyst/risk in the supplied evidence_ids,
- stay within direction/probability/horizon bounds (1/7/14/21 days only),
- state limitations, and
- include the "not investment advice" disclosure.

Randomness is controlled in code (temperature 0.1); prompts reinforce
deterministic, conservative behaviour.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from backend.ai.schemas import EvidencePacket

PROMPT_DIR = Path(__file__).resolve().parent

PROFILES = ("quick_insight", "forecast_assist", "deep_research", "report")

_PROFILE_FILES = {
    "quick_insight": "quick_insight.txt",
    "forecast_assist": "forecast_assist.txt",
    "deep_research": "deep_research.txt",
    "report": "report.txt",
}

_PROFILE_HORIZON_HINT = {
    "quick_insight": 21,
    "forecast_assist": None,  # caller-supplied horizon wins
    "deep_research": 63,
    "report": 21,
}


def list_profiles() -> list[str]:
    return list(PROFILES)


@lru_cache(maxsize=8)
def get_prompt(profile: str) -> str:
    """Load the raw template for a profile (KeyError/ValueError if unknown)."""
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in _PROFILE_FILES:
        raise ValueError(f"unknown AI profile: {profile!r}; expected one of {PROFILES}")
    path = PROMPT_DIR / _PROFILE_FILES[key]
    return path.read_text(encoding="utf-8").strip() + "\n"


# Token-efficient input budgets per profile (~4 chars/token).
# Quick Insight ~300-600 tokens, Forecast Assist ~1000, Deep Research 4000
# max, Report ~2000. render_prompt() truncates ONLY the packet JSON (never
# the template) so prompts stay within budget without losing instructions.
MAX_PROMPT_TOKENS_BY_PROFILE: dict[str, int] = {
    "quick_insight": 600,
    "forecast_assist": 1000,
    "deep_research": 4000,
    "report": 2000,
}


def estimate_prompt_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for budget logging."""
    return max(1, len(text or "") // 4)


def render_prompt(
    profile: str,
    packet: EvidencePacket,
    *,
    horizon: int | None = None,
    max_tokens: int | None = None,
) -> str:
    """Render template + budget-truncated evidence packet JSON."""
    template = get_prompt(profile)
    key = (profile or "").strip().lower().replace(" ", "_").replace("-", "_")
    if key not in _PROFILE_FILES:
        # get_prompt already raised; keep mypy happy.
        key = "quick_insight"
    effective_horizon = horizon
    if effective_horizon not in (1, 7, 14, 21):
        effective_horizon = _PROFILE_HORIZON_HINT.get(key) or 21
    budget = int(max_tokens or MAX_PROMPT_TOKENS_BY_PROFILE.get(key, 600))
    try:
        from backend.ai.evidence import packet_prompt_json

        packet_json = packet_prompt_json(packet, key, max_tokens=budget)
    except Exception:
        packet_json = packet.model_dump_json(indent=1)
        budget_chars = max(512, budget * 4)
        if len(packet_json) > budget_chars:
            packet_json = packet_json[:budget_chars] + '\n  "...truncated": true\n}'
    return (
        f"{template}\n"
        f"TIME_HORIZON_DAYS: {effective_horizon}\n"
        f"EVIDENCE_PACKET_JSON:\n{packet_json}\n"
    )
