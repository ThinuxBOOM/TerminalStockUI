"""Phase 4c: calendar-aware market_state via MIC in get_quote.

Offline, fixed dates, explicit now/at -- never wall-clock. Stub provider
as_of is pinned by monkeypatching (stub stamps _utcnow() by default).
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
    """Stub-mode service whose quotes all stamp ``fixed_as_of``.

    Wraps the real stub so price/currency/missing-field shape stays
    realistic; only ``as_of`` is pinned for determinism.
    """
    tracker = ProviderHealthTracker()
    provider = YFinanceProvider(
        stub_mode=True, on_call=lambda p, ms, ok: tracker.record(p, ms, ok)
    )
    svc = MarketDataService(
        registry=InstrumentRegistry(), provider=provider, health=tracker, cache=None
    )
    orig_get = provider.get_quote

    def _fixed(symbol: str, *a, **k):  # type: ignore[no-untyped-def]
        q = dict(orig_get(symbol, *a, **k))
        q["as_of"] = fixed_as_of
        return q

    provider.get_quote = _fixed  # type: ignore[method-assign]
    # SSE chain uses a secondary provider; pin it too for determinism.
    try:
        orig_ak = svc._call_akshare

        def _fixed_ak(ak_code: str):  # type: ignore[no-untyped-def]
            q = orig_ak(ak_code)
            if q is None:
                return None
            q = dict(q)
            q["as_of"] = fixed_as_of
            return q

        svc._call_akshare = _fixed_ak  # type: ignore[method-assign]
    except Exception:
        pass
    return svc


def test_sunday_closed_via_get_quote():
    """Sunday 2026-09-13: XNAS/XPAR/XSHG all closed via get_quote."""
    for symbol, sunday in (
        ("AAPL", SUN_XNAS),
        ("MC.PA", SUN_XPAR),
        ("600519.SS", SUN_XSHG),
    ):
        svc = _svc_with_fixed_as_of(sunday)
        out = svc.get_quote(symbol)
        assert out["market_state"] == "closed", (symbol, out["market_state"])


def test_weekday_session_open_preserved_via_get_quote():
    """Fresh weekday session data still reads open."""
    for symbol, at in (
        ("AAPL", WED_XNAS),
        ("MC.PA", TUE_XPAR),
        ("600519.SS", TUE_XSHG_OPEN),
    ):
        svc = _svc_with_fixed_as_of(at)
        out = svc.get_quote(symbol)
        assert out["market_state"] == "open", (symbol, out["market_state"])


def test_xshg_lunch_maps_to_closed():
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


def test_unknown_mic_fallback_equals_legacy():
    """Unknown MICs: calendar-aware == legacy freshness-only output."""
    at = TUE_XSHG_OPEN
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

    def _boom(mic: str, dt: datetime, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar down")

    monkeypatch.setattr(health_module, "market_state_at", _boom)
    at = WED_XNAS
    expected = market_state(at, delay_minutes=15, now=at)
    assert expected == "open"
    svc = _svc_with_fixed_as_of(at)
    out = svc.get_quote("AAPL")
    assert out["market_state"] == expected


def test_service_passes_mic_now_at(monkeypatch):
    """Wiring: get_quote forwards mic + now/at=as_of to market_state."""
    import backend.market_data.service as service_module

    seen: dict = {}
    orig = service_module.market_state

    def _spy(as_of: datetime, **k):  # type: ignore[no-untyped-def]
        seen.update(k)
        seen["as_of"] = as_of
        return orig(as_of, **k)

    monkeypatch.setattr(service_module, "market_state", _spy)
    svc = _svc_with_fixed_as_of(TUE_XPAR)
    out = svc.get_quote("MC.PA")
    assert out["market_state"] == "open"
    assert seen.get("mic") == "XPAR"
    assert seen.get("now") == TUE_XPAR
    assert seen.get("at") == TUE_XPAR


def test_enrich_sunday_closed_and_exception_fallback(monkeypatch):
    """_enrich_market_state: Sunday as_of -> closed; exception -> unchanged."""
    sunday_iso = SUN_XNAS.isoformat()
    out = {
        "market_state": "open",  # stale service value to be upgraded
        "instrument": {"exchange_mic": "XNAS"},
        "provenance": {"as_of": sunday_iso, "delay_minutes": 15},
    }
    enriched = _enrich_market_state(dict(out))
    assert enriched["market_state"] == "closed"

    import backend.api.market_data as api_md

    def _boom(as_of: datetime, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("calendar down")

    monkeypatch.setattr(api_md, "_calendar_market_state", _boom)
    out2 = {
        "market_state": "open",
        "instrument": {"exchange_mic": "XNAS"},
        "provenance": {"as_of": sunday_iso, "delay_minutes": 15},
    }
    assert _enrich_market_state(dict(out2))["market_state"] == "open"
