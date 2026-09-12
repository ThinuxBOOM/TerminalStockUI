"""Exchange calendars / suffix map for XNYS/XNAS/XSHG/XPAR/XAMS/XBRU.

Yahoo-style provider suffixes: US none, SSE .SS, Euronext Paris .PA,
Amsterdam .AS, Brussels .BR.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EXCHANGE_META: dict[str, dict[str, str | int]] = {
    "XNYS": {
        "name": "New York Stock Exchange",
        "suffix": "",
        "timezone": "America/New_York",
        "currency": "USD",
        "country": "US",
        "delay_minutes": 15,
    },
    "XNAS": {
        "name": "Nasdaq",
        "suffix": "",
        "timezone": "America/New_York",
        "currency": "USD",
        "country": "US",
        "delay_minutes": 15,
    },
    "XSHG": {
        "name": "Shanghai Stock Exchange",
        "suffix": ".SS",
        "timezone": "Asia/Shanghai",
        "currency": "CNY",
        "country": "CN",
        "delay_minutes": 15,
    },
    "XPAR": {
        "name": "Euronext Paris",
        "suffix": ".PA",
        "timezone": "Europe/Paris",
        "currency": "EUR",
        "country": "FR",
        "delay_minutes": 15,
    },
    "XAMS": {
        "name": "Euronext Amsterdam",
        "suffix": ".AS",
        "timezone": "Europe/Amsterdam",
        "currency": "EUR",
        "country": "NL",
        "delay_minutes": 15,
    },
    "XBRU": {
        "name": "Euronext Brussels",
        "suffix": ".BR",
        "timezone": "Europe/Brussels",
        "currency": "EUR",
        "country": "BE",
        "delay_minutes": 15,
    },
}

#: provider suffix -> MIC (US markets have no suffix)
SUFFIX_TO_MIC: dict[str, str] = {
    str(meta["suffix"]): mic for mic, meta in EXCHANGE_META.items() if meta["suffix"]
}

SUPPORTED_MICS: tuple[str, ...] = tuple(EXCHANGE_META)

#: Continuous-session wall-clock windows per MIC (exchange-local time).
#: XSHG trades two sessions with a lunch break; US/Euronext are continuous.
TRADING_SESSIONS: dict[str, list[tuple[time, time]]] = {
    "XSHG": [(time(9, 30), time(11, 30)), (time(13, 0), time(15, 0))],
    "XNYS": [(time(9, 30), time(16, 0))],
    "XNAS": [(time(9, 30), time(16, 0))],
    "XPAR": [(time(9, 0), time(17, 30))],
    "XAMS": [(time(9, 0), time(17, 30))],
    "XBRU": [(time(9, 0), time(17, 30))],
}

#: Lunch-break window for XSHG (exchange-local). Returned as "lunch".
XSHG_LUNCH: tuple[time, time] = (time(11, 30), time(13, 0))

#: STUB holiday set for XSHG (Gregorian approximations of lunar festivals).
#: Covers New Year, Spring Festival, Qingming, Labour, Dragon Boat,
#: Mid-Autumn, National Day at date granularity only. Lunar-holiday dates
#: drift year to year — production MUST replace this with a licensed
#: China-market calendar. Fixed-rule holidays (Jan 1, May 1, Oct 1-7) are
#: handled in is_holiday() directly; this set holds the lunar approximations.
XSHG_HOLIDAY_STUB: frozenset[date] = frozenset({
    # Spring Festival (lunar new year) approximations
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31),
    date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3), date(2025, 2, 4),
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18), date(2026, 2, 19),
    date(2026, 2, 20),
    # Qingming (Tomb-Sweeping Day) approximations
    date(2025, 4, 4),
    date(2026, 4, 6),
    # Dragon Boat Festival approximations
    date(2025, 5, 31),
    date(2026, 6, 19),
    # Mid-Autumn Festival approximations
    date(2025, 10, 6),
    date(2026, 9, 25),
})


def suffix_for_mic(mic: str) -> str:
    try:
        return str(EXCHANGE_META[mic.upper()]["suffix"])
    except KeyError:
        raise ValueError(f"unsupported exchange MIC: {mic!r}") from None


def expected_delay_minutes(mic: str) -> int:
    try:
        return int(EXCHANGE_META[mic.upper()]["delay_minutes"])
    except KeyError:
        raise ValueError(f"unsupported exchange MIC: {mic!r}") from None


def split_provider_symbol(symbol: str) -> tuple[str, str | None]:
    """Split a provider symbol into (base, mic-or-None).

    Longest-suffix match so '.SS' wins correctly; bare US tickers -> None.
    """
    text = (symbol or "").strip().upper()
    for suffix in sorted(SUFFIX_TO_MIC, key=len, reverse=True):
        if suffix and text.endswith(suffix.upper()):
            return text[: -len(suffix)], SUFFIX_TO_MIC[suffix]
    return text, None


def provider_symbol_for(exchange_symbol: str, mic: str) -> str:
    """Canonical provider (Yahoo-style) symbol for an instrument."""
    base = (exchange_symbol or "").strip().upper()
    suffix = suffix_for_mic(mic)
    if suffix and not base.endswith(suffix.upper()):
        return base + suffix.upper()
    return base


def _easter_sunday(year: int) -> date:
    """Gregorian computus (Anonymous algorithm), deterministic, no network."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _is_euronext_holiday(day: date) -> bool:
    """STUB fixed-rule Euronext holiday check (XPAR/XAMS/XBRU share it)."""
    if day.month == 1 and day.day == 1:  # New Year
        return True
    if day.month == 5 and day.day == 1:  # Labour Day
        return True
    if day.month == 12 and day.day in (25, 26):  # Christmas / Boxing Day
        return True
    easter = _easter_sunday(day.year)
    if day == easter - timedelta(days=2):  # Good Friday
        return True
    if day == easter + timedelta(days=1):  # Easter Monday
        return True
    return False


def is_holiday(day: date | datetime, mic: str = "XSHG") -> bool:
    """STUB holiday check (XSHG + Euronext; other MICs always False).

    XSHG: New Year (Jan 1), Labour Day (May 1), National Day golden week
    (Oct 1-7) by fixed rule plus lunar-festival approximations in
    XSHG_HOLIDAY_STUB (Spring Festival / Qingming / Dragon Boat /
    Mid-Autumn). Documented stub: lunar dates drift and makeup-workday
    rules are NOT modelled — replace with a licensed calendar in prod.

    Euronext (XPAR/XAMS/XBRU, shared stub): New Year (Jan 1), Good Friday
    (Easter-2d), Easter Monday (Easter+1d), Labour Day (May 1), Christmas
    (Dec 25), Boxing Day (Dec 26). Documented stub: covers only these six
    fixed-rule/movable feasts; bridge days, exceptional closures, and any
    venue-specific differences are NOT modelled — replace with a licensed
    Euronext calendar in prod.
    """
    d = day.date() if isinstance(day, datetime) else day
    key = mic.upper()
    if key == "XSHG":
        if d.month == 1 and d.day == 1:  # New Year
            return True
        if d.month == 5 and d.day == 1:  # Labour Day (stub: full week varies)
            return True
        if d.month == 10 and 1 <= d.day <= 7:  # National Day golden week
            return True
        return d in XSHG_HOLIDAY_STUB
    if key in ("XPAR", "XAMS", "XBRU"):
        return _is_euronext_holiday(d)
    return False


def is_trading_day(day: date | datetime, mic: str = "XSHG") -> bool:
    """True when ``day`` is Mon-Fri and not a stub holiday for ``mic``."""
    d = day.date() if isinstance(day, datetime) else day
    if d.weekday() >= 5:  # Saturday / Sunday
        return False
    return not is_holiday(d, mic)


def _exchange_now(mic: str, dt: datetime) -> datetime:
    """Return ``dt`` in the exchange timezone.

    Naive inputs are assumed to already be exchange-local (documented);
    aware inputs are converted via ZoneInfo.
    """
    tz_name = str(EXCHANGE_META[mic.upper()]["timezone"])
    tz = ZoneInfo(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def market_state_at(
    mic: str,
    dt: datetime,
    *,
    as_of: datetime | None = None,
    delay_minutes: int | None = None,
    session_bars: int = 1,
) -> str:
    """Wall-clock (+optional freshness) state for ``mic`` at ``dt``.

    Without ``as_of`` returns ``open`` | ``closed`` | ``lunch`` (lunch only
    for XSHG 11:30-13:00 on trading days). With ``as_of`` (data timestamp)
    freshness is layered on top: ``stale`` wins over everything, and an
    ``open`` market with data older than the expected delay reports
    ``delayed``. Full return domain: open|closed|lunch|delayed|stale.

    XSHG sessions (Asia/Shanghai): 09:30-11:30 + 13:00-15:00; lunch
    11:30-13:00. US (09:30-16:00 ET) and Euronext (09:00-17:30 local,
    Paris/Amsterdam/Brussels, no lunch break) are continuous. Weekends +
    is_holiday() stub (XSHG lunar approximations; Euronext six-feast stub)
    -> closed.
    """
    key = mic.upper()
    if key not in EXCHANGE_META:
        raise ValueError(f"unsupported exchange MIC: {mic!r}")
    local = _exchange_now(key, dt)

    if as_of is not None:
        ref = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
        ts = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        age_min = (ref - ts).total_seconds() / 60
        try:
            expected = max(int(delay_minutes) if delay_minutes is not None
                           else int(EXCHANGE_META[key]["delay_minutes"]), 1)
        except (TypeError, ValueError):
            expected = 15
        stale_after = max(2 * expected, 24 * 60 if session_bars >= 1 else 2 * expected)
        if age_min > stale_after:
            return "stale"
        wall = market_state_at(key, dt)
        if wall in ("closed", "lunch"):
            return wall
        if age_min > expected:
            return "delayed"
        return "open"

    if not is_trading_day(local.date(), key):
        return "closed"
    t = local.time()
    if key == "XSHG":
        morning_open, morning_close = TRADING_SESSIONS["XSHG"][0]
        afternoon_open, afternoon_close = TRADING_SESSIONS["XSHG"][1]
        lunch_start, lunch_end = XSHG_LUNCH
        if morning_open <= t < morning_close:
            return "open"
        if lunch_start <= t < lunch_end:
            return "lunch"
        if afternoon_open <= t < afternoon_close:
            return "open"
        return "closed"
    sessions = TRADING_SESSIONS.get(key, [])
    for start, end in sessions:
        if start <= t < end:
            return "open"
    return "closed"
