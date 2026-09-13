"""M7 Euronext tests: resolution both-forms, market filter, name search, calendar, currency."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from backend.api.deps import reset_deps
from backend.api.main import create_app
from backend.instruments.calendars import is_holiday, is_trading_day, market_state_at
from backend.instruments.registry import InstrumentRegistry
from backend.instruments.search import search_instruments
from backend.market_data.health import market_state

PAR = ZoneInfo("Europe/Paris")
AMS = ZoneInfo("Europe/Amsterdam")
BRU = ZoneInfo("Europe/Brussels")


def _registry() -> InstrumentRegistry:
    return InstrumentRegistry()


# -- seed normalization ----------------------------------------------------

def test_euronext_exchange_symbol_normalized():
    reg = _registry()
    inst = reg.get_by_mic_symbol("XPAR", "MC")
    assert inst is not None
    assert inst.exchange_symbol == "MC"  # no suffix in exchange_symbol (M6 SSE convention)
    assert inst.provider_symbol == "MC.PA"
    assert inst.currency == "EUR"
    assert inst.country == "FR"
    assert inst.timezone == "Europe/Paris"
    assert inst.exchange_mic == "XPAR"
    assert inst.isin == "FR0000121014"


def test_euronext_blue_chip_seeds():
    reg = _registry()
    for mic, sym, prov, isin, country, tz in (
        ("XPAR", "OR", "OR.PA", "FR0000120321", "FR", "Europe/Paris"),  # L'Oreal
        ("XAMS", "ASML", "ASML.AS", "NL0010273215", "NL", "Europe/Amsterdam"),
        ("XAMS", "INGA", "INGA.AS", "NL0011821202", "NL", "Europe/Amsterdam"),  # ING
        ("XBRU", "ABI", "ABI.BR", "BE0974293251", "BE", "Europe/Brussels"),  # AB InBev
        ("XBRU", "UCB", "UCB.BR", "BE0003739530", "BE", "Europe/Brussels"),
        ("XPAR", "ACA", "ACA.PA", "FR0000045072", "FR", "Europe/Paris"),
    ):
        inst = reg.get_by_mic_symbol(mic, sym)
        assert inst is not None, f"missing Euronext seed {mic}:{sym}"
        assert inst.provider_symbol == prov
        assert inst.currency == "EUR"
        assert inst.country == country
        assert inst.timezone == tz
        assert inst.isin == isin
        assert inst.exchange_mic == mic


# -- resolution both forms -------------------------------------------------

def test_resolve_suffix_and_bare_forms():
    reg = _registry()
    inst_suf, _c, amb_suf = reg.resolve("MC.PA")
    assert inst_suf is not None and inst_suf.exchange_mic == "XPAR"
    assert inst_suf.exchange_symbol == "MC"
    assert not amb_suf
    inst_bare, _c2, amb_bare = reg.resolve("MC", market="XPAR")
    assert inst_bare is not None and inst_bare.exchange_mic == "XPAR"
    assert not amb_bare
    assert inst_bare.instrument_id == inst_suf.instrument_id
    # Bare globally resolves to the unique Euronext instrument (no ambiguity).
    inst_g, _c3, amb_g = reg.resolve("ASML", market="XAMS")
    assert inst_g is not None and inst_g.exchange_mic == "XAMS"
    assert not amb_g


def test_resolve_new_blue_chips_both_forms():
    reg = _registry()
    for bare, suffixed, mic in (
        ("OR", "OR.PA", "XPAR"),
        ("INGA", "INGA.AS", "XAMS"),
        ("ABI", "ABI.BR", "XBRU"),
        ("ASML", "ASML.AS", "XAMS"),
        ("ACA", "ACA.PA", "XPAR"),
        ("UCB", "UCB.BR", "XBRU"),
    ):
        a, _, _ = reg.resolve(suffixed)
        b, _, _ = reg.resolve(bare, market=mic)
        assert a is not None and b is not None, f"no resolution for {bare}/{suffixed}"
        assert a.exchange_mic == mic
        assert a.instrument_id == b.instrument_id


def test_resolve_wrong_market_returns_none():
    reg = _registry()
    inst, _c, _a = reg.resolve("MC", market="XNAS")
    assert inst is None
    inst2, _c2, _a2 = reg.resolve("MC.PA", market="XAMS")
    assert inst2 is None


def test_market_filter_search():
    reg = _registry()
    for mic in ("XPAR", "XAMS", "XBRU"):
        results = search_instruments(reg.all(), "A", market=mic)
        assert results, f"expected matches for market {mic}"
        assert all(r.exchange_mic == mic for r in results)
    # Venue-specific symbols land in the right market.
    assert any(r.exchange_symbol == "MC" for r in search_instruments(reg.all(), "MC", market="XPAR"))
    assert any(r.exchange_symbol == "ASML" for r in search_instruments(reg.all(), "ASML", market="XAMS"))
    assert any(r.exchange_symbol == "ABI" for r in search_instruments(reg.all(), "ABI", market="XBRU"))


# -- name search -----------------------------------------------------------

def test_lvmh_name_search():
    reg = _registry()
    results = search_instruments(reg.all(), "LVMH")
    assert results, "expected LVMH match"
    assert any(r.exchange_symbol == "MC" and r.exchange_mic == "XPAR" for r in results)
    results_lower = search_instruments(reg.all(), "lvmh")
    assert any(r.exchange_symbol == "MC" for r in results_lower)


def test_asml_name_search():
    reg = _registry()
    results = search_instruments(reg.all(), "ASML")
    assert results, "expected ASML match"
    assert any(r.exchange_symbol == "ASML" and r.exchange_mic == "XAMS" for r in results)


def test_search_result_exposes_required_fields():
    reg = _registry()
    results = search_instruments(reg.all(), "MC.PA")
    assert results
    top = results[0].model_dump_canonical()
    for field in ("company_name", "exchange_mic", "currency", "exchange_symbol", "provider_symbol"):
        assert field in top and top[field], f"missing {field}"
    assert top["exchange_mic"] == "XPAR"
    assert top["currency"] == "EUR"


# -- calendar --------------------------------------------------------------

def test_euronext_holiday_stub():
    # Fixed feasts.
    assert is_holiday(date(2026, 1, 1), "XPAR")  # New Year
    assert is_holiday(date(2026, 5, 1), "XPAR")  # Labour Day
    assert is_holiday(date(2026, 12, 25), "XAMS")  # Christmas
    assert is_holiday(date(2026, 12, 26), "XBRU")  # Boxing Day
    # Movable feasts 2026 (Easter Sunday 2026-04-05).
    assert is_holiday(date(2026, 4, 3), "XPAR")  # Good Friday
    assert is_holiday(date(2026, 4, 6), "XAMS")  # Easter Monday
    # Movable feasts 2025 (Easter Sunday 2025-04-20).
    assert is_holiday(date(2025, 4, 18), "XPAR")  # Good Friday
    assert is_holiday(date(2025, 4, 21), "XBRU")  # Easter Monday
    # Shared across venues.
    for mic in ("XPAR", "XAMS", "XBRU"):
        assert is_holiday(date(2026, 12, 25), mic)
        assert not is_trading_day(date(2026, 12, 25), mic)
    # Ordinary weekday is not a holiday.
    assert not is_holiday(date(2026, 9, 8), "XPAR")
    assert is_trading_day(date(2026, 9, 8), "XPAR")  # Tuesday
    assert not is_trading_day(date(2026, 9, 12), "XPAR")  # Saturday


def test_euronext_calendar_open_closed_per_venue():
    # Tuesday 2026-09-08, a trading day outside the holiday stub.
    assert market_state_at("XPAR", datetime(2026, 9, 8, 10, 0, tzinfo=PAR)) == "open"
    assert market_state_at("XAMS", datetime(2026, 9, 8, 10, 0, tzinfo=AMS)) == "open"
    assert market_state_at("XBRU", datetime(2026, 9, 8, 10, 0, tzinfo=BRU)) == "open"
    # No lunch break: midday is still open (unlike XSHG).
    assert market_state_at("XPAR", datetime(2026, 9, 8, 12, 30, tzinfo=PAR)) == "open"
    assert market_state_at("XAMS", datetime(2026, 9, 8, 12, 30, tzinfo=AMS)) == "open"
    assert market_state_at("XBRU", datetime(2026, 9, 8, 12, 30, tzinfo=BRU)) == "open"
    # Boundaries: 09:00 open, 17:30 close.
    assert market_state_at("XPAR", datetime(2026, 9, 8, 9, 0, tzinfo=PAR)) == "open"
    assert market_state_at("XPAR", datetime(2026, 9, 8, 17, 29, tzinfo=PAR)) == "open"
    assert market_state_at("XPAR", datetime(2026, 9, 8, 17, 30, tzinfo=PAR)) == "closed"
    # Off-hours closed.
    assert market_state_at("XPAR", datetime(2026, 9, 8, 8, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XPAR", datetime(2026, 9, 8, 18, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XAMS", datetime(2026, 9, 8, 8, 59, tzinfo=AMS)) == "closed"
    assert market_state_at("XBRU", datetime(2026, 9, 8, 18, 0, tzinfo=BRU)) == "closed"


def test_euronext_calendar_weekend_closed_per_venue():
    assert market_state_at("XPAR", datetime(2026, 9, 12, 10, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XAMS", datetime(2026, 9, 12, 10, 0, tzinfo=AMS)) == "closed"
    assert market_state_at("XBRU", datetime(2026, 9, 12, 10, 0, tzinfo=BRU)) == "closed"
    assert market_state_at("XPAR", datetime(2026, 9, 13, 10, 0, tzinfo=PAR)) == "closed"  # Sunday


def test_euronext_calendar_holiday_closed():
    # Christmas Friday 2026-12-25 and Good Friday 2026-04-03: closed at 10:00.
    assert market_state_at("XPAR", datetime(2026, 12, 25, 10, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XAMS", datetime(2026, 12, 25, 10, 0, tzinfo=AMS)) == "closed"
    assert market_state_at("XBRU", datetime(2026, 12, 25, 10, 0, tzinfo=BRU)) == "closed"
    assert market_state_at("XPAR", datetime(2026, 4, 3, 10, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XAMS", datetime(2026, 4, 6, 10, 0, tzinfo=AMS)) == "closed"  # Easter Mon


def test_calendar_freshness_delayed_stale():
    noon = datetime(2026, 9, 8, 10, 0, tzinfo=PAR)
    assert market_state_at("XPAR", noon, as_of=noon) == "open"
    assert market_state_at("XPAR", noon, as_of=noon - timedelta(minutes=30)) == "delayed"
    assert market_state_at("XPAR", noon, as_of=noon - timedelta(days=3)) == "stale"


# -- health helper ---------------------------------------------------------

def test_health_market_state_calendar_aware_euronext():
    open_at = datetime(2026, 9, 8, 10, 0, tzinfo=PAR)
    for mic, tz in (("XPAR", PAR), ("XAMS", AMS), ("XBRU", BRU)):
        at = datetime(2026, 9, 8, 10, 0, tzinfo=tz)
        assert market_state(at, delay_minutes=15, now=at, mic=mic, at=at) == "open"
    weekend = datetime(2026, 9, 12, 10, 0, tzinfo=PAR)
    assert market_state(weekend, delay_minutes=15, now=weekend, mic="XPAR", at=weekend) == "closed"
    holiday = datetime(2026, 12, 25, 10, 0, tzinfo=PAR)
    assert market_state(holiday, delay_minutes=15, now=holiday, mic="XPAR", at=holiday) == "closed"
    # Stale wins over calendar.
    assert market_state(open_at - timedelta(days=3), delay_minutes=15, now=open_at, mic="XPAR", at=open_at) == "stale"
    # Legacy freshness-only path preserved (no mic).
    now = datetime.now(timezone.utc)
    assert market_state(now, delay_minutes=15, now=now) == "open"
    assert market_state(now - timedelta(minutes=30), delay_minutes=15, now=now) == "delayed"


# -- currency + US/SSE unaffected ------------------------------------------

def test_euronext_currency_eur():
    reg = _registry()
    for i in reg.all():
        if i.exchange_mic in ("XPAR", "XAMS", "XBRU"):
            assert i.currency == "EUR", f"{i.exchange_mic}:{i.exchange_symbol} not EUR"


def test_us_sse_unaffected():
    reg = _registry()
    inst, _, _ = reg.resolve("AAPL")
    assert inst is not None and inst.exchange_mic == "XNAS" and inst.exchange_symbol == "AAPL"
    results = search_instruments(reg.all(), "AAP")
    assert results[0].exchange_symbol == "AAP"  # exact NYSE first, not AAPL
    sse, _, _ = reg.resolve("600519.SS")
    assert sse is not None and sse.exchange_mic == "XSHG" and sse.exchange_symbol == "600519"
    assert sse.currency == "CNY"
    sse_bare, _, _ = reg.resolve("600519", market="XSHG")
    assert sse_bare is not None and sse_bare.instrument_id == sse.instrument_id


# -- HTTP surfaces ---------------------------------------------------------

def test_http_resolve_surfaces_market_state_currency_exchange():
    reset_deps()
    client = TestClient(create_app())
    for symbol, mic in (("MC.PA", "XPAR"), ("ASML.AS", "XAMS"), ("UCB.BR", "XBRU")):
        body = client.get("/api/instruments/resolve", params={"symbol": symbol}).json()
        assert body["instrument"]["exchange_mic"] == mic
        assert body["instrument"]["currency"] == "EUR"
        assert body["market_state"] in ("open", "closed", "lunch", "delayed", "stale")
        assert body["currency"] == "EUR"
        assert body["exchange_mic"] == mic
    # Bare form with market scope also resolves.
    body = client.get("/api/instruments/resolve", params={"symbol": "MC", "market": "XPAR"}).json()
    assert body["instrument"]["exchange_mic"] == "XPAR"
    assert body["currency"] == "EUR"


def test_http_quote_euronext_currency_and_state():
    reset_deps()
    client = TestClient(create_app())
    resp = client.get("/api/market_data/quote", params={"symbol": "MC.PA"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currency"] == "EUR"
    assert (body.get("instrument") or {}).get("exchange_mic") == "XPAR"
    assert body["market_state"] in ("open", "closed", "delayed", "stale")


# -- Phase 1b pinned dates (library-verified, deterministic, offline) ---------

def test_phase1b_euronext_boxing_day_2025_closed():
    """2025-12-26 Friday Boxing Day: XPAR/XAMS/XBRU closed.

    Verified against exchange_calendars (not memory): library reports a
    non-session for all three Euronext venues; our is_holiday agrees.
    """
    import exchange_calendars as ec

    for mic in ("XPAR", "XAMS", "XBRU"):
        cal = ec.get_calendar(mic)
        assert not cal.is_session("2025-12-26"), f"library should say non-session {mic}"
        assert is_holiday(date(2025, 12, 26), mic), f"{mic} Boxing Day holiday"
        assert not is_trading_day(date(2025, 12, 26), mic)
    assert market_state_at("XPAR", datetime(2025, 12, 26, 10, 0, tzinfo=PAR)) == "closed"
    assert market_state_at("XAMS", datetime(2025, 12, 26, 10, 0, tzinfo=AMS)) == "closed"
    assert market_state_at("XBRU", datetime(2025, 12, 26, 10, 0, tzinfo=BRU)) == "closed"


def test_phase1b_euronext_normal_wednesday_open():
    """2025-09-03 Wednesday: normal session open for all Euronext venues."""
    for mic, tz in (("XPAR", PAR), ("XAMS", AMS), ("XBRU", BRU)):
        assert not is_holiday(date(2025, 9, 3), mic)
        assert is_trading_day(date(2025, 9, 3), mic)
        assert market_state_at(mic, datetime(2025, 9, 3, 10, 0, tzinfo=tz)) == "open"
        # No lunch break (unlike XSHG).
        assert market_state_at(mic, datetime(2025, 9, 3, 12, 30, tzinfo=tz)) == "open"


def test_phase1b_euronext_christmas_2025_and_new_year_2026():
    """2025-12-25 Christmas + 2026-01-01 New Year: Euronext closed."""
    for mic, tz in (("XPAR", PAR), ("XAMS", AMS), ("XBRU", BRU)):
        assert is_holiday(date(2025, 12, 25), mic)
        assert is_holiday(date(2026, 1, 1), mic)
        assert market_state_at(mic, datetime(2025, 12, 25, 10, 0, tzinfo=tz)) == "closed"
        assert market_state_at(mic, datetime(2026, 1, 1, 10, 0, tzinfo=tz)) == "closed"


def test_phase1b_euronext_library_determinism():
    """Repeat calls give identical answers (offline local data)."""
    d = date(2025, 12, 26)
    assert is_holiday(d, "XPAR") == is_holiday(d, "XPAR") is True
    assert is_trading_day(d, "XPAR") == is_trading_day(d, "XPAR") is False
