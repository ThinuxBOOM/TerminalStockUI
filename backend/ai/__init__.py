"""OneMarket Analyzer AI orchestration (Milestones 4-5).

Deterministic analytics and forecasting are the source of truth.
This package adds structured, evidence-grounded, bounded AI opinions only.

Layout:
    backend/ai/schemas.py      EvidencePacket + AIOpinion (strict JSON schemas)
    backend/ai/evidence.py     build_evidence_packet() — capped, no raw data/keys
    backend/ai/providers/      BaseProvider + gemini/openai/anthropic/xai
    backend/ai/prompts/        bounded, evidence-grounded prompt templates
    backend/ai/router.py       profile map, evidence-hash cache, token log, tracker
    backend/ai/blend.py        fixed blend policy (AI weight capped at 0.20)
"""

from __future__ import annotations

__all__: list[str] = []
