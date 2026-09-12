"""Bounded, evidence-grounded prompt templates (Milestone 4).

Every prompt instructs the model to:
- return STRICT JSON matching the AIOpinion schema and nothing else,
- ground every catalyst/risk in the supplied evidence_ids,
- stay within direction/probability/horizon bounds (5/21/63 days only),
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


def render_prompt(profile: str, packet: EvidencePacket, *, horizon: int | None = None) -> str:
    """Render template + evidence packet JSON into the final model prompt."""
    template = get_prompt(profile)
    effective_horizon = horizon
    if effective_horizon not in (5, 21, 63):
        effective_horizon = _PROFILE_HORIZON_HINT.get((profile or "").strip().lower().replace(" ", "_").replace("-", "_")) or 21
    packet_json = packet.model_dump_json(indent=1)
    # Hard bound: prompts never exceed ~12k chars (packet builder already caps).
    if len(packet_json) > 9000:
        packet_json = packet_json[:9000] + '\n  "...truncated": true\n}'
    return (
        f"{template}\n"
        f"TIME_HORIZON_DAYS: {effective_horizon}\n"
        f"EVIDENCE_PACKET_JSON:\n{packet_json}\n"
    )
