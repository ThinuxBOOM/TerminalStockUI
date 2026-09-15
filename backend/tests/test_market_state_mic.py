"""Phase 4c: calendar-aware market_state via MIC in get_quote.

Offline, fixed dates. Staleness and the exchange calendar are evaluated at
WALL-CLOCK now (never at the data timestamp — now=as_of would pin age to 0
and badge stale data MARKET OPEN), so tests pin BOTH as_of (stub provider)
and now (monkeypatched module _utcnow).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.api.market_data import _enrich_market_state
from backend.instruments.calendars import market_state_at
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.health import ProviderHealthTracker, market_state
from backend.market_data.providers.yfinance import YFinanceProvider
from backend.market_data.service import MarketDataService

NY = ZoneInfo("America/New_York")
PAR = ZoneInfo("Europe/Paris")
SH = ZoneInfo("Asia/Shanghai")

# Fixed pins (never wall-clock).
SUN_XNAS = datetime(2026, 9, 13, 12, 0, tzinfo=NY)  # Sunday
SUN_XPAR = datetime(2026, 9, 13, 12, 0, tzinfo=PAR)  # Sunday
SUN_XSHG = datetime(2026, 9, 13, 12, 0, tzinfo=SH)  # Sunday
WED_XNAS = datetime(2025, 9, 3, 10, 0, tzinfo=NY)  # Wednesday session
TUE_XPAR = datetime(2026, 9, 8, 10, 0, tzinfo=PAR)  # Tuesday session
TUE_XSHG_OPEN = datetime(2026, 9, 8, 10, 0, tzinfo=SH)  # morning session
TUE_XSHG_LUNCH = datetime(2026, 9, 8, 12, 0, tzinfo=SH)  # lunch break


def _svc_with_fixed_as_of(fixed_as_of: datetime) -> MarketDataService:
    """Live-double service whose quotes all stamp ``fixed_as_of``.

    Fail-closed: uses live quotes (``fallback_used=False``) so
    ``MarketDataService.get_quote`` serves them instead of raising.
    Wraps the real stub shape then flips to live; only ``as_of`` is
    pinned for determinism.
    """
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    orig_get = provider.get_quote

    def _fixed(symbol: str, *a, **k):  # type: ignore[no-untyped-def]
        q = dict(orig_get(symbol, *a, **k))
        q["as_of"] = fixed_as_of
        q["fallback_used"] = False
        q.pop("fallback", None)
        return q

    provider.get_quote = _fixed  # type: ignore[method-assign]
    svc = MarketDataService(
        registry=InstrumentRegistry(), provider=provider, health=tracker,
        cache=None, akshare_provider=None,
    )
    return svc


def test_sunday_closed_via_get_quote(monkeypatch):
    """Sunday 2026-09-13: XNAS/XPAR/XSHG all closed via get_quote."""
    import backend.market_data.service as service_module

    for symbol, sunday in (
        ("AAPL", SUN_XNAS),
        ("MC.PA", SUN_XPAR),
        ("600519.SS", SUN_XSHG),
    ):
        monkeypatch.setattr(service_module, "_utcnow", lambda sunday=sunday: sunday)
        svc = _svc_with_fixed_as_of(sunday)
        out = svc.get_quote(symbol)
        assert out["market_state"] == "closed", (symbol, out["market_state"])


def test_weekday_session_open_preserved_via_get_quote(monkeypatch):
    """Fresh weekday session data still reads open (now pinned in-session)."""
    import backend.market_data.service as service_module

    for symbol, at in (
        ("AAPL", WED_XNAS),
        ("MC.PA", TUE_XPAR),
        ("600519.SS", TUE_XSHG_OPEN),
    ):
        monkeypatch.setattr(service_module, "_utcnow", lambda at=at: at)
        svc = _svc_with_fixed_as_of(at)
        out = svc.get_quote(symbol)
        assert out["market_state"] == "open", (symbol, out["market_state"])


def test_stale_data_badges_stale_not_open(monkeypatch):
    """Old data evaluated at wall-clock now reads stale (never open)."""
    import backend.market_data.service as service_module

    # WED_XNAS is long past: even mid-session, the badge must say stale.
    monkeypatch.setattr(service_module, "_utcnow", lambda: TUE_XPAR)
    svc = _svc_with_fixed_as_of(WED_XNAS)
    out = svc.get_quote("AAPL")
    assert out["market_state"] == "stale", out["market_state"]


def test_xshg_lunch_maps_to_closed(monkeypatch):
    """XSHG lunch window maps to closed in the health contract."""
    # Calendar layer returns lunch; health/service map it to closed.
    assert market_state_at("XSHG", TUE_XSHG_LUNCH) == "lunch"
    assert (
        market_state(
            TUE_XSHG_LUNCH,
            delay_minutes=15,
            now=TUE_XSHG_LUNCH,
            mic="XSHG",
            at=TUE_XSHG_LUNCH,
        )
        == "closed"
    )
    import backend.market_data.service as service_module

    monkeypatch.setattr(
        service_module, "_utcnow", lambda: TUE_XSHG_LUNCH
    )
    svc = _svc_with_fixed_as_of(TUE_XSHG_LUNCH)
    out = svc.get_quote("600519.SS")
    assert out["market_state"] == "closed"


def test_stale_wins_over_calendar():
    """Stale data still stale even when the calendar is open (per contract)."""
    # Explicit now/at, never wall-clock.
    assert (
        market_state(
            TUE_XSHG_OPEN - timedelta(days=3),
            delay_minutes=15,
            now=TUE_XSHG_OPEN,
            mic="XSHG",
            at=TUE_XSHG_OPEN,
        )
        == "stale"
    )
    assert (
        market_state(
            WED_XNAS - timedelta(days=3),
            delay_minutes=15,
            now=WED_XNAS,
            mic="XNAS",
            at=WED_XNAS,
        )
        == "stale"
    )
    assert (
        market_state(
            TUE_XPAR - timedelta(days=3),
            delay_minutes=15,
            now=TUE_XPAR,
            mic="XPAR",
            at=TUE_XPAR,
        )
        == "stale"
    )


def test_unknown_mic_fallback_equals_legacy(monkeypatch):
    """Unknown MICs: calendar-aware == legacy freshness-only output."""
    import backend.market_data.service as service_module

    at = TUE_XSHG_OPEN
    monkeypatch.setattr(service_module, "_utcnow", lambda: at)
    legacy = market_state(at, delay_minutes=15, now=at)
    assert (
        market_state(at, delay_minutes=15, now=at, mic="XXXX", at=at) == legacy
    )
    # Via get_quote: unknown symbol + unknown market scope.
    svc = _svc_with_fixed_as_of(at)
    out = svc.get_quote("ZZZ_UNKNOWN_123", market="XXXX")
    assert out["market_state"] == legacy


def test_calendar_exception_falls_back_to_freshness(monkeypatch):
    """market_state_at raising -> freshness result, never a break."""
    import backend.market_data.health as health_module
    import backend.market_data.service as service_module

    def _boom(mic: str, dt: datetime, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar down")

    monkeypatch.setattr(health_module, "market_state_at", _boom)
    at = WED_XNAS
    monkeypatch.setattr(service_module, "_utcnow", lambda: at)
    expected = market_state(at, delay_minutes=15, now=at)
    assert expected == "open"
    svc = _svc_with_fixed_as_of(at)
    out = svc.get_quote("AAPL")
    assert out["market_state"] == expected


def test_service_passes_mic_and_wall_clock_now(monkeypatch):
    """Wiring: get_quote forwards mic + wall-clock now to market_state."""
    import backend.market_data.service as service_module

    seen: dict = {}
    orig = service_module.market_state

    def _spy(as_of: datetime, **k):  # type: ignore[no-untyped-def]
        seen.update(k)
        seen["as_of"] = as_of
        return orig(as_of, **k)

    monkeypatch.setattr(service_module, "market_state", _spy)
    pinned_now = TUE_XPAR + timedelta(minutes=5)
    monkeypatch.setattr(service_module, "_utcnow", lambda: pinned_now)
    svc = _svc_with_fixed_as_of(TUE_XPAR)
    out = svc.get_quote("MC.PA")
    assert out["market_state"] == "open"
    assert seen.get("mic") == "XPAR"
    # now is wall-clock (pinned), never the data timestamp.
    assert seen.get("now") == pinned_now
    assert "at" not in seen


def test_enrich_sunday_closed_and_exception_fallback(monkeypatch):
    """_enrich_market_state: Sunday as_of + Sunday now -> closed; exception -> unchanged."""
    import backend.api.market_data as api_md

    monkeypatch.setattr(api_md, "_utcnow", lambda: SUN_XNAS)
    sunday_iso = SUN_XNAS.isoformat()
    out = {
        "market_state": "open",  # stale service value to be upgraded
        "instrument": {"exchange_mic": "XNAS"},
        "provenance": {"as_of": sunday_iso, "delay_minutes": 15},
    }
    enriched = _enrich_market_state(dict(out))
    assert enriched["market_state"] == "closed"

    def _boom(as_of: datetime, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar down")

    monkeypatch.setattr(api_md, "_calendar_market_state", _boom)
    out2 = {
        "market_state": "open",
        "instrument": {"exchange_mic": "XNAS"},
        "provenance": {"as_of": sunday_iso, "delay_minutes": 15},
    }
    assert _enrich_market_state(dict(out2))["market_state"] == "open"


def test_enrich_stale_data_badges_stale_not_open(monkeypatch):
    """Old as_of evaluated at wall-clock now reads stale (never open)."""
    import backend.api.market_data as api_md

    monkeypatch.setattr(api_md, "_utcnow", lambda: TUE_XPAR)
    out = {
        "market_state": "open",
        "instrument": {"exchange_mic": "XNAS"},
        "provenance": {"as_of": WED_XNAS.isoformat(), "delay_minutes": 15},
    }
    assert _enrich_market_state(dict(out))["market_state"] == "stale"
