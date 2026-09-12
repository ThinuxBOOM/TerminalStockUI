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
    for horizon in (5, 21, 63):
        opinion = AIOpinion.model_validate(_valid_payload(time_horizon_days=horizon))
        assert opinion.time_horizon_days == horizon
        assert 0.0 <= opinion.probability <= 1.0


def test_invalid_probability_rejected():
    for bad in (-0.1, 1.5, 2.0, float("nan"), float("inf"), "high", None, []):
        with pytest.raises(Exception):
            AIOpinion.model_validate(_valid_payload(probability=bad))


def test_invalid_horizon_rejected():
    for bad in (0, 1, 7, 30, 90, -5, "soon", None, 21.5):
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
