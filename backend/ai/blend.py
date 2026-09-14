"""Fixed blend policy (Milestone 5).

- AI NEVER overrides the quantitative core; it contributes a bounded opinion.
- ai_weight is capped at 0.20 and is NOT user-adjustable beyond the safe
  presets (0.0, 0.1, 0.2). Anything else snaps down to the nearest preset.
- Disabling AI (ai_enabled=False, weight 0, or opinion None) leaves the
  forecast intact: blended_prob == quant_prob, confidence unchanged.
- Disagreement between quant and AI lowers confidence (never raises it).
"""

from __future__ import annotations

from typing import Any

from backend.ai.schemas import AIOpinion, DISCLAIMER

AI_WEIGHT_MAX = 0.20
AI_WEIGHT_PRESETS: tuple[float, ...] = (0.0, 0.1, 0.2)
AI_WEIGHT_DEFAULT = 0.1
DISAGREEMENT_THRESHOLD = 0.10


def _reject_bool(value: Any, name: str) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got bool")


def adjust_confidence_label(label: str | None, disagreement: float) -> tuple[str | None, bool]:
    """Downgrade a low|moderate|high label one notch on AI disagreement.

    Returns (adjusted_label, lowered). Unknown/None labels pass through as
    (label, False) except unknown strings map to "low" conservatively when
    disagreement fires. Never raises on bad input.
    """
    if label is None:
        return None, False
    try:
        norm = str(label).strip().lower()
    except Exception:
        return label, False
    if norm not in ("low", "moderate", "high"):
        # Conservative: unknown label + clash -> low, else passthrough.
        try:
            _d = float(disagreement)
        except (TypeError, ValueError):
            return label, False
        if _d > DISAGREEMENT_THRESHOLD:
            return "low", True
        return label, False
    try:
        _d = float(disagreement)
    except (TypeError, ValueError):
        return norm, False
    if _d > DISAGREEMENT_THRESHOLD:
        mapping = {"high": "moderate", "moderate": "low", "low": "low"}
        return mapping[norm], True
    return norm, False


def resolve_ai_weight(requested: float | None = None, *, ai_enabled: bool = True) -> float:
    """Resolve a safe AI weight.

    - ai_enabled=False -> 0.0 (AI-disabled mode).
    - None -> default preset (0.1).
    - Otherwise clamp to [0, 0.20] and snap DOWN to the nearest preset so
      users can never exceed the cap or invent intermediate weights.
    """
    if isinstance(ai_enabled, bool) is False:
        # Coerce truthiness but reject non-bool confusion downstream; keep
        # strict: ai_enabled must be bool-like.
        pass
    if not ai_enabled:
        return 0.0
    if requested is None:
        return AI_WEIGHT_DEFAULT
    _reject_bool(requested, "ai_weight")
    try:
        value = float(requested)
    except (TypeError, ValueError) as exc:
        raise ValueError("ai_weight must be a number") from exc
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf
        raise ValueError("ai_weight must be a finite number")
    clamped = min(max(value, 0.0), AI_WEIGHT_MAX)
    preset = 0.0
    for candidate in AI_WEIGHT_PRESETS:
        if candidate <= clamped + 1e-9:
            preset = candidate
    return preset


def _check_prob(value: Any, name: str) -> float:
    _reject_bool(value, name)
    try:
        prob = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number in [0, 1]") from exc
    if not 0.0 <= prob <= 1.0 or prob != prob:
        raise ValueError(f"{name} must be a finite number in [0, 1]")
    return prob


def blend_forecast(
    quant_prob: float,
    ai_opinion: AIOpinion | dict[str, Any] | None,
    *,
    ai_weight: float | None = None,
    ai_enabled: bool = True,
    quant_confidence: float | None = None,
    quant_confidence_label: str | None = None,
) -> dict[str, Any]:
    """Blend a quantitative direction probability with a bounded AI opinion.

    Returns a preview dict (never mutates stored forecasts):
        {quant_prob, ai_prob, ai_weight, ai_enabled, ai_applied,
         blended_prob, disagreement, confidence, confidence_lowered,
         confidence_label, confidence_label_lowered, disclaimer}

    ``quant_confidence_label`` wires AI disagreement into the DISPLAYED
    low|moderate|high label (the numeric ``quant_confidence`` path is
    unchanged). The live forecast endpoint is quant-only; callers that
    show a label alongside the blend preview should render
    ``confidence_label`` (already downgraded on clash), not the raw quant
    label.
    """
    quant = _check_prob(quant_prob, "quant_prob")
    weight = resolve_ai_weight(ai_weight, ai_enabled=ai_enabled)

    base_confidence: float | None = None
    if quant_confidence is not None:
        base_confidence = _check_prob(quant_confidence, "quant_confidence")

    opinion: AIOpinion | None = None
    if isinstance(ai_opinion, AIOpinion):
        opinion = ai_opinion
    elif isinstance(ai_opinion, dict):
        opinion = AIOpinion.model_validate(ai_opinion)

    if not ai_enabled or opinion is None or weight <= 0.0:
        _dis = 0.0 if opinion is None else round(abs(quant - float(opinion.probability)), 6)
        _lbl, _lbl_lowered = (quant_confidence_label, False) if quant_confidence_label is None else (quant_confidence_label, False)
        # Disabled path never lowers: label passes through intact.
        return {
            "quant_prob": quant,
            "ai_prob": None,
            "ai_weight": 0.0 if not ai_enabled or opinion is None else weight,
            "ai_enabled": bool(ai_enabled) and opinion is not None and weight > 0.0,
            "ai_applied": False,
            "blended_prob": quant,
            "disagreement": _dis,
            "confidence": base_confidence,
            "confidence_lowered": False,
            "confidence_label": _lbl,
            "confidence_label_lowered": False,
            "disclaimer": DISCLAIMER,
        }

    ai_prob = float(opinion.probability)
    blended = (1.0 - weight) * quant + weight * ai_prob
    blended = min(max(blended, 0.0), 1.0)
    disagreement = abs(quant - ai_prob)

    lowered = disagreement > DISAGREEMENT_THRESHOLD
    confidence: float | None = None
    if base_confidence is not None:
        if lowered:
            confidence = base_confidence * max(0.5, 1.0 - disagreement)
            confidence = round(min(max(confidence, 0.0), 1.0), 6)
        else:
            confidence = base_confidence

    if quant_confidence_label is None:
        confidence_label = None
        label_lowered = False
    else:
        confidence_label, label_lowered = adjust_confidence_label(
            quant_confidence_label, disagreement
        )

    return {
        "quant_prob": quant,
        "ai_prob": ai_prob,
        "ai_weight": weight,
        "ai_enabled": True,
        "ai_applied": True,
        "blended_prob": round(blended, 6),
        "disagreement": round(disagreement, 6),
        "confidence": confidence,
        "confidence_lowered": lowered,
        "confidence_label": confidence_label,
        "confidence_label_lowered": label_lowered,
        "disclaimer": DISCLAIMER,
    }


def ai_disabled_forecast(
    quant_prob: float,
    *,
    quant_confidence: float | None = None,
    quant_confidence_label: str | None = None,
) -> dict[str, Any]:
    """Explicit AI-disabled path: quantitative forecast passes through intact."""
    return blend_forecast(
        quant_prob, None, ai_weight=0.0, ai_enabled=False,
        quant_confidence=quant_confidence,
        quant_confidence_label=quant_confidence_label,
    )
