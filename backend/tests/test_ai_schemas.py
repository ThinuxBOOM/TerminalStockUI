"""AI schema-validation tests (spec section 6 + M4/M5 acceptance).

Covers: malformed JSON fails safe, invalid probability/horizon rejected,
missing evidence_ids rejected, packet caps + secret/raw-data stripping.
"""

from __future__ import annotations

import pytest

from backend.ai.evidence import build_evidence_packet
from backend.ai.schemas import AIOpinion, EvidencePacket, parse_opinion_strict


def _valid_payload(**overrides):
    payload = {
        "direction": "bullish",
        "probability": 0.57,
        "time_horizon_days": 21,
        "catalysts": ["trend support"],
        "risks": ["valuation stretch"],
        "evidence_ids": ["bull-1", "risk-1"],
        "limitations": ["Not investment advice; deterministic core governs."],
    }
    payload.update(overrides)
    return payload


def test_valid_opinion_accepts_all_horizons():
    for horizon in (1, 7, 14, 21):
        opinion = AIOpinion.model_validate(_valid_payload(time_horizon_days=horizon))
        assert opinion.time_horizon_days == horizon
        assert 0.0 <= opinion.probability <= 1.0


def test_invalid_probability_rejected():
    for bad in (-0.1, 1.5, 2.0, float("nan"), float("inf"), "high", None, []):
        with pytest.raises(Exception):
            AIOpinion.model_validate(_valid_payload(probability=bad))


def test_invalid_horizon_rejected():
    for bad in (0, 5, 63, 30, 90, -5, "soon", None, 21.5):
        with pytest.raises(Exception):
            AIOpinion.model_validate(_valid_payload(time_horizon_days=bad))


def test_missing_evidence_ids_rejected():
    with pytest.raises(Exception):
        AIOpinion.model_validate(_valid_payload(evidence_ids=[]))
    with pytest.raises(Exception):
        AIOpinion.model_validate({k: v for k, v in _valid_payload().items() if k != "evidence_ids"})
    with pytest.raises(Exception):
        AIOpinion.model_validate(_valid_payload(evidence_ids=["  ", ""]))
    # Claims without grounding are rejected even if other fields look fine.
    with pytest.raises(Exception):
        AIOpinion.model_validate(_valid_payload(catalysts=["big catalyst"], evidence_ids=[]))


def test_invalid_direction_rejected():
    with pytest.raises(Exception):
        AIOpinion.model_validate(_valid_payload(direction="moon"))
    with pytest.raises(Exception):
        AIOpinion.model_validate(_valid_payload(direction=""))


def test_malformed_json_fails_safe():
    for bad in ["", "   ", "{not json", '{"direction": "bullish"', "[]", "null", "[1,2]"]:
        with pytest.raises(ValueError):
            parse_opinion_strict(bad)
    # Right shape, wrong values -> still safe failure (no partial opinion).
    with pytest.raises(ValueError):
        parse_opinion_strict('{"direction": "bullish", "probability": 9.9}')
    # Surrounding prose / fences are tolerated, schema errors are not.
    opinion = parse_opinion_strict(
        'Here is my take:\n```json\n{"direction": "neutral", "probability": 0.5, '
        '"time_horizon_days": 21, "catalysts": [], "risks": [], '
        '"evidence_ids": ["ev-1"], "limitations": ["thin evidence"]}```'
    )
    assert isinstance(opinion, AIOpinion)
    assert opinion.direction == "neutral"


def test_evidence_packet_caps_and_stripping():
    packet = build_evidence_packet(
        "aapl",
        {
            "top_bullish": [{"label": f"bull {i}"} for i in range(12)],
            "top_risks": [{"label": f"risk {i}"} for i in range(9)],
            "events": [{"label": f"event {i}"} for i in range(40)],
            "bars": [{"close": 1.0}] * 100,  # raw candles must never land in packet
            "candles": [1, 2, 3],
            "ohlcv": {"close": [1, 2]},
            "financial_statements": {"income": "full..."},
            "api_key": "sk-live-must-vanish",
            "nested": {"secret": "shh", "keep": "yes"},
        },
        {"source": "yfinance", "quality_grade": "B", "delay_minutes": 15},
    )
    assert isinstance(packet, EvidencePacket)
    assert packet.symbol == "AAPL"
    assert len(packet.top_bullish) == 5
    assert len(packet.top_risks) == 5
    assert len(packet.events) <= 20
    dumped = packet.model_dump_json()
    for forbidden in ("sk-live-must-vanish", "financial_statements", '"bars"', '"candles"', '"ohlcv"', "shh"):
        assert forbidden not in dumped
    assert len(packet.evidence_hash) >= 16
    assert packet.limitations  # always present


def test_evidence_hash_stable_for_same_inputs():
    kwargs = {
        "deterministic_outputs": {"top_bullish": [{"label": "trend"}], "summary": {"rsi": 55}},
        "provenance": {"source": "yfinance", "quality_grade": "A", "delay_minutes": 15},
    }
    first = build_evidence_packet("MSFT", **kwargs)
    second = build_evidence_packet("msft", **kwargs)
    assert first.evidence_hash == second.evidence_hash
    assert first.packet_id == second.packet_id


# ---------------------------------------------------------------------------
# Token efficiency + tier stubs (Agent 4): per-profile budgets, prompt
# truncation/summarization, TokenUsage schema, tier fields unenforced.
# ---------------------------------------------------------------------------


def _big_packet():
    return build_evidence_packet(
        "AAPL",
        {
            "top_bullish": [{"label": f"bull {i}", "detail": "x" * 400} for i in range(12)],
            "top_risks": [{"label": f"risk {i}", "detail": "y" * 400} for i in range(9)],
            "events": [{"label": f"event {i}", "detail": "z" * 300} for i in range(40)],
            "summary": {f"metric_{i}": "v" * 200 for i in range(30)},
            "candles": [1, 2, 3],  # must never survive
            "api_key": "sk-live-must-vanish",
        },
        {"source": "yfinance", "quality_grade": "B", "delay_minutes": 15},
    )


def test_profile_token_budgets_ordered():
    from backend.ai.evidence import PROFILE_TOKEN_BUDGETS

    assert PROFILE_TOKEN_BUDGETS["quick_insight"] <= 600
    assert PROFILE_TOKEN_BUDGETS["forecast_assist"] == 1000
    assert PROFILE_TOKEN_BUDGETS["deep_research"] == 4000
    assert PROFILE_TOKEN_BUDGETS["quick_insight"] < PROFILE_TOKEN_BUDGETS["forecast_assist"]
    assert PROFILE_TOKEN_BUDGETS["forecast_assist"] < PROFILE_TOKEN_BUDGETS["deep_research"]


def test_summarize_packet_enforces_caps_per_profile():
    from backend.ai.evidence import summarize_packet_for_profile

    packet = _big_packet()
    quick = summarize_packet_for_profile(packet, "quick_insight")
    deep = summarize_packet_for_profile(packet, "deep_research")
    assert len(quick["top_bullish"]) <= 5 and len(quick["top_risks"]) <= 5
    assert len(quick["events"]) <= 10  # quick summarizes harder
    assert len(deep["events"]) <= 20
    assert all(len(item["detail"]) <= 140 for item in quick["top_bullish"])
    assert len(quick["deterministic_summary"]) <= 8
    assert len(deep["deterministic_summary"]) <= 20
    dumped = str(quick) + str(deep)
    assert "sk-live-must-vanish" not in dumped
    assert "candles" not in quick["deterministic_summary"]


def test_packet_prompt_json_stays_in_budget():
    from backend.ai.evidence import packet_prompt_json

    packet = _big_packet()
    quick_json = packet_prompt_json(packet, "quick_insight")
    deep_json = packet_prompt_json(packet, "deep_research")
    assert len(quick_json) <= 600 * 4 + 64  # budget chars + truncation marker
    assert len(deep_json) <= 4000 * 4 + 64
    assert len(quick_json) < len(deep_json)  # quick summarizes harder


def test_render_prompt_truncates_packet_not_template():
    from backend.ai.prompts import get_prompt, render_prompt

    packet = _big_packet()
    text = render_prompt("quick_insight", packet)
    assert get_prompt("quick_insight").strip() in text  # template intact
    assert "TIME_HORIZON_DAYS: 21" in text
    packet_part = text.split("EVIDENCE_PACKET_JSON:")[1]
    assert len(packet_part) <= 600 * 4 + 64


def test_token_usage_schema_and_tier_stubs_unenforced():
    from backend.ai.schemas import (
        ForecastOpinionRequest,
        InsightRequest,
        TokenUsage,
    )

    usage = TokenUsage(provider="gemini", model="m", profile="quick_insight",
                       prompt_tokens=500, completion_tokens=100, total_tokens=600)
    assert usage.total_tokens == 600
    # Tier stubs accepted in any (or no) value — never enforced.
    for kwargs in ({}, {"user_tier": "free"}, {"user_tier": "platinum"},
                   {"user_tier": "nonsense-tier"}, {"call_type": "insight", "token_credits": 5}):
        req = InsightRequest(symbol="AAPL", **kwargs)
        assert req.symbol == "AAPL"
        forecast = ForecastOpinionRequest(symbol="aapl", horizon=21, **kwargs)
        assert forecast.symbol == "AAPL" and forecast.horizon == 21
