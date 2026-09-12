"""AI blend-policy tests (Milestone 5 acceptance).

Covers: weight cap enforced at 0.20, safe presets only, AI-disabled mode
leaves the forecast intact, disagreement lowers confidence, AI can only
nudge (never override) the quantitative core.
"""

from __future__ import annotations

import pytest

from backend.ai.blend import (
    AI_WEIGHT_MAX,
    AI_WEIGHT_PRESETS,
    ai_disabled_forecast,
    blend_forecast,
    resolve_ai_weight,
)
from backend.ai.schemas import AIOpinion


def _opinion(prob: float, horizon: int = 21) -> AIOpinion:
    return AIOpinion(
        direction="bullish" if prob > 0.55 else ("bearish" if prob < 0.45 else "neutral"),
        probability=prob,
        time_horizon_days=horizon,
        catalysts=["momentum"],
        risks=["valuation"],
        evidence_ids=["bull-1"],
        limitations=["Not investment advice."],
        provider="gemini",
        model="gemini-3.7-flash",
    )


def test_weight_cap_enforced():
    assert AI_WEIGHT_MAX == 0.20
    assert resolve_ai_weight(0.5) == 0.20
    assert resolve_ai_weight(1.0) == 0.20
    assert resolve_ai_weight(99.0) == 0.20
    assert resolve_ai_weight(0.21) == 0.20


def test_only_safe_presets():
    assert set(AI_WEIGHT_PRESETS) == {0.0, 0.1, 0.2}
    assert resolve_ai_weight(None) == 0.1  # default preset
    assert resolve_ai_weight(0.0) == 0.0
    assert resolve_ai_weight(0.1) == 0.1
    assert resolve_ai_weight(0.2) == 0.20
    assert resolve_ai_weight(0.15) == 0.1  # snaps down, never up past cap
    assert resolve_ai_weight(0.19) == 0.1
    assert resolve_ai_weight(-0.5) == 0.0
    with pytest.raises(ValueError):
        resolve_ai_weight(float("nan"))


def test_disabling_ai_leaves_forecast_intact():
    quant = 0.64
    out = blend_forecast(quant, _opinion(0.9), ai_weight=0.2, ai_enabled=False)
    assert out["blended_prob"] == pytest.approx(quant)
    assert out["ai_applied"] is False
    assert out["ai_weight"] == 0.0
    assert out["confidence_lowered"] is False

    out = blend_forecast(quant, None, ai_weight=0.2, ai_enabled=True)
    assert out["blended_prob"] == pytest.approx(quant)
    assert out["ai_applied"] is False

    out = blend_forecast(quant, _opinion(0.9), ai_weight=0.0, ai_enabled=True)
    assert out["blended_prob"] == pytest.approx(quant)

    disabled = ai_disabled_forecast(quant, quant_confidence=0.7)
    assert disabled["blended_prob"] == pytest.approx(quant)
    assert disabled["confidence"] == pytest.approx(0.7)


def test_blend_math_bounded():
    out = blend_forecast(0.6, _opinion(0.8), ai_weight=0.2, ai_enabled=True)
    assert out["blended_prob"] == pytest.approx(0.8 * 0.2 + 0.6 * 0.8)
    assert abs(out["blended_prob"] - 0.6) <= AI_WEIGHT_MAX + 1e-9  # bounded nudge
    # Sweep: AI can never move the forecast more than the cap.
    for quant in (0.1, 0.5, 0.9):
        for ai_prob in (0.0, 0.5, 1.0):
            preview = blend_forecast(quant, _opinion(ai_prob), ai_weight=0.2)
            assert abs(preview["blended_prob"] - quant) <= AI_WEIGHT_MAX + 1e-9


def test_disagreement_lowers_confidence():
    agree = blend_forecast(0.6, _opinion(0.62), ai_weight=0.2, quant_confidence=0.8)
    assert agree["confidence_lowered"] is False
    assert agree["confidence"] == pytest.approx(0.8)

    clash = blend_forecast(0.8, _opinion(0.2), ai_weight=0.2, quant_confidence=0.8)
    assert clash["disagreement"] == pytest.approx(0.6)
    assert clash["confidence_lowered"] is True
    assert clash["confidence"] < 0.8  # lowered, never raised


def test_blend_rejects_bad_quant():
    with pytest.raises(ValueError):
        blend_forecast(1.5, _opinion(0.5))
    with pytest.raises(ValueError):
        blend_forecast(float("nan"), _opinion(0.5))
