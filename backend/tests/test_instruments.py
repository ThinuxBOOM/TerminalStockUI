"""Instrument resolution tests: ticker ambiguity + .SS/.PA/.AS/.BR suffixes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.instruments.calendars import (
    SUPPORTED_MICS,
    expected_delay_minutes,
    is_holiday,
    is_trading_day,
    market_state_at,
    provider_symbol_for,
    split_provider_symbol,
    suffix_for_mic,
)
from backend.instruments.registry import InstrumentRegistry
from backend.instruments.search import search_instruments


def _registry() -> InstrumentRegistry:
    return InstrumentRegistry()


def test_all_spec_mics_supported():
    for mic in ("XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"):
        assert mic in SUPPORTED_MICS
        assert isinstance(expected_delay_minutes(mic), int)


def test_suffix_map():
    assert suffix_for_mic("XSHG") == ".SS"
    assert suffix_for_mic("XPAR") == ".PA"
    assert suffix_for_mic("XAMS") == ".AS"
    assert suffix_for_mic("XBRU") == ".BR"
    assert suffix_for_mic("XNAS") == ""
    assert split_provider_symbol("600519.SS") == ("600519", "XSHG")
    assert split_provider_symbol("MC.PA") == ("MC", "XPAR")
    assert split_provider_symbol("ASML.AS") == ("ASML", "XAMS")
    assert split_provider_symbol("UCB.BR") == ("UCB", "XBRU")
    assert split_provider_symbol("AAPL") == ("AAPL", None)
    assert provider_symbol_for("MC.PA", "XPAR") == "MC.PA"
    assert provider_symbol_for("AAPL", "XNAS") == "AAPL"


def test_instrument_record_has_spec_section4_fields():
    inst = _registry().get_by_mic_symbol("XNAS", "AAPL")
    assert inst is not None
    for field in ("instrument_id", "exchange_mic", "exchange_symbol", "provider_symbol",
                  "isin", "company_name", "currency", "country", "sector",
                  "timezone", "trading_calendar", "is_active"):
        assert field in inst.model_dump_canonical(), f"missing {field}"


def test_exact_beats_prefix_aap_vs_aapl():
    reg = _registry()
    results = search_instruments(reg.all(), "AAP")
    assert results, "expected matches for AAP"
    assert results[0].exchange_symbol == "AAP"  # exact NYSE match first, not AAPL
    assert any(r.exchange_symbol == "AAPL" for r in results)


def test_resolve_bare_aapl_prefers_nasdaq():
    inst, _cands, _amb = _registry().resolve("AAPL")
    assert inst is not None and inst.exchange_symbol == "AAPL" and inst.exchange_mic == "XNAS"


def test_resolve_suffix_instruments():
    reg = _registry()
    for symbol, mic in (("600519.SS", "XSHG"), ("MC.PA", "XPAR"),
                        ("ASML.AS", "XAMS"), ("UCB.BR", "XBRU")):
        inst, _cands, ambiguous = reg.resolve(symbol)
        assert inst is not None, f"no resolution for {symbol}"
        assert inst.exchange_mic == mic, f"{symbol} -> {inst.exchange_mic}, want {mic}"
        assert not ambiguous


def test_resolve_case_insensitive_and_market_scope():
    reg = _registry()
    inst, _, _ = reg.resolve("aapl", market="XNAS")
    assert inst is not None and inst.exchange_mic == "XNAS"
    inst, _, _ = reg.resolve("mc.pa")
    assert inst is not None and inst.exchange_mic == "XPAR"


def test_resolve_unknown_returns_none():
    inst, cands, _amb = _registry().resolve("ZZZ_NOPE_123")
    assert inst is None and cands == []


def test_search_http_exact_first():
    reset_deps()
    client = TestClient(create_app())
    body = client.get("/api/instruments/search", params={"q": "AAP"}).json()
    assert body["results"][0]["exchange_symbol"] == "AAP"
    body = client.get("/api/instruments/search", params={"q": "600519.SS"}).json()
    assert body["results"][0]["exchange_mic"] == "XSHG"
    body = client.get("/api/instruments/search", params={"q": "MC.PA"}).json()
    assert body["results"][0]["exchange_mic"] == "XPAR"


def test_search_market_filter_and_empty():
    reset_deps()
    client = TestClient(create_app())
    body = client.get("/api/instruments/search", params={"q": "A", "market": "XSHG"}).json()
    assert body["results"] and all(r["exchange_mic"] == "XSHG" for r in body["results"])
    assert client.get("/api/instruments/search", params={"q": "A", "market": "NOPE"}).status_code == 422


# -- Phase 1b pinned dates (exchange-calendars backed, deterministic) --------

def test_phase1b_us_christmas_2025_closed():
    """2025-12-25 Thursday Christmas: XNYS/XNAS closed (library)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
    assert is_holiday(date(2025, 12, 25), "XNYS")
    assert is_holiday(date(2025, 12, 25), "XNAS")
    assert not is_trading_day(date(2025, 12, 25), "XNYS")
    assert not is_trading_day(date(2025, 12, 25), "XNAS")
    assert market_state_at("XNYS", datetime(2025, 12, 25, 10, 0, tzinfo=NY)) == "closed"
    assert market_state_at("XNAS", datetime(2025, 12, 25, 10, 0, tzinfo=NY)) == "closed"


def test_phase1b_us_normal_wednesday_open():
    """2025-09-03 Wednesday: normal session open for XNYS/XNAS."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
    assert not is_holiday(date(2025, 9, 3), "XNYS")
    assert is_trading_day(date(2025, 9, 3), "XNYS")
    assert is_trading_day(date(2025, 9, 3), "XNAS")
    assert market_state_at("XNYS", datetime(2025, 9, 3, 10, 0, tzinfo=NY)) == "open"
    assert market_state_at("XNAS", datetime(2025, 9, 3, 10, 0, tzinfo=NY)) == "open"


def test_phase1b_new_year_2026_holiday_all_venues():
    """2026-01-01 Thursday New Year: holiday everywhere (library + stub)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    for mic in ("XNYS", "XNAS", "XPAR", "XAMS", "XBRU", "XSHG"):
        assert is_holiday(date(2026, 1, 1), mic), f"{mic} Jan1 should be holiday"
        assert not is_trading_day(date(2026, 1, 1), mic), f"{mic} Jan1 not trading"
    NY = ZoneInfo("America/New_York")
    PAR = ZoneInfo("Europe/Paris")
    assert market_state_at("XNYS", datetime(2026, 1, 1, 10, 0, tzinfo=NY)) == "closed"
    assert market_state_at("XPAR", datetime(2026, 1, 1, 10, 0, tzinfo=PAR)) == "closed"


def test_phase1b_delay_metadata_unchanged():
    """Per-MIC delay_minutes stays 15 (contractual, byte-identical META)."""
    for mic in ("XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"):
        assert expected_delay_minutes(mic) == 15


def test_phase1b_unknown_mic_behavior_unchanged():
    """Unknown MICs: market_state_at ValueError, is_holiday False default."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    import pytest

    with pytest.raises(ValueError):
        market_state_at("XXXX", datetime(2025, 9, 3, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert is_holiday(date(2025, 9, 3), "XXXX") is False


def test_phase1b_deterministic_repeat_calls():
    """Same date -> same answer on repeat (deterministic, offline local data)."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    NY = ZoneInfo("America/New_York")
    d = date(2025, 12, 25)
    assert is_holiday(d, "XNYS") == is_holiday(d, "XNYS")
    assert is_trading_day(d, "XNAS") == is_trading_day(d, "XNAS")
    dt = datetime(2025, 9, 3, 10, 0, tzinfo=NY)
    assert market_state_at("XNYS", dt) == market_state_at("XNYS", dt) == "open"
