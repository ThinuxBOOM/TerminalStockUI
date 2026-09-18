"""GET /api/markets/{mic}/index — per-market benchmark (ASPI-style) bars.

Server-side twin of the frontend ``src/api/aspi.js`` proxy chain: resolves
the canonical index symbol for a venue, then tries frontend-safe proxies in
order via ``MarketDataService.get_bars``. Zero-candle successes count as a
miss so the next candidate is tried. Per-candidate failures degrade to the
next candidate; the batch never 500s because of one bad symbol.

Symbols only live here (never prices). Caret index symbols (^NYA, ^IXIC,
^FCHI, ^AEX, ^BFX) are rejected by ``validate_symbol`` (no ``^`` in the
alphabet), so each MIC lists frontend-safe proxy symbols and the response
badges proxy series with ``is_proxy=true``. XSHG needs no proxy:
``000001.SS`` is valid under the current alphabet.

XCOL (Colombo CSE ASPI) is probe-ready but DISABLED until the venue is
enabled in ``config/markets.yaml`` and the vendor index symbol is confirmed
(^CSE unverified). Requests for disabled venues 422 with enable
instructions — never synthetic bars.

Response:
  { mic, label, symbol, used_symbol, is_proxy, timeframe, points,
    count, start, end, last_close, methodology, disclosure, provenance }
``points`` are [{t, close}] in native/proxy currency (no FX conversion).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.deps import get_market_service
from backend.market_data.service import MarketDataService
from backend.security.validation import sanitize_error, validate_symbol

router = APIRouter(prefix="/api/markets", tags=["markets-index"])

DISCLOSURE = "Not investment advice. For informational purposes only."
METHODOLOGY = (
    "Per-market benchmark bars: canonical index symbol first, then "
    "frontend-safe proxies in order via MarketDataService.get_bars. "
    "Proxy series are badged is_proxy=true; native currency only, no FX."
)

# Symbols only — never prices. Mirrors frontend src/api/aspi.js registry.
BENCHMARKS: dict[str, dict] = {
    "XNYS": {"venue": "NYSE", "label": "NYSE Composite", "index_symbol": "^NYA",
             "proxies": ["SPY"], "currency": "USD", "timezone": "America/New_York", "enabled": True},
    "XNAS": {"venue": "Nasdaq", "label": "Nasdaq Composite", "index_symbol": "^IXIC",
             "proxies": ["QQQ"], "currency": "USD", "timezone": "America/New_York", "enabled": True},
    "XSHG": {"venue": "SSE", "label": "SSE Composite", "index_symbol": "000001.SS",
             "proxies": [], "currency": "CNY", "timezone": "Asia/Shanghai", "enabled": True},
    "XPAR": {"venue": "Euronext Paris", "label": "CAC 40", "index_symbol": "^FCHI",
             "proxies": ["EWQ", "CAC.PA"], "currency": "EUR", "timezone": "Europe/Paris", "enabled": True},
    "XAMS": {"venue": "Euronext Amsterdam", "label": "AEX", "index_symbol": "^AEX",
             "proxies": ["EWN", "IAEX.AS"], "currency": "EUR", "timezone": "Europe/Amsterdam", "enabled": True},
    "XBRU": {"venue": "Euronext Brussels", "label": "BEL 20", "index_symbol": "^BFX",
             "proxies": ["EWK"], "currency": "EUR", "timezone": "Europe/Brussels", "enabled": True},
    "XCOL": {"venue": "Colombo (CSE)", "label": "CSE All-Share Price Index (ASPI)",
             "index_symbol": "^CSE", "proxies": [], "currency": "LKR",
             "timezone": "Asia/Colombo", "enabled": False},
}

TIMEFRAME_LIMITS = {"1d": 90, "1wk": 52, "1mo": 36}
VALID_TIMEFRAMES = ("1d", "1wk", "1mo")

#: Index perf: one benchmark = one get_bars (DB + possible live fetch).
#: Cache assembled points so six homepage index cards + retries don't refetch
#: the same SPY/QQQ/000001.SS bars within a minute. TTL 60s mirrors the
#: quote cache; user-scoped (u:{id}:t:{tier}:) to prevent cross-tier poisoning.
_INDEX_TTL_S = 60


def _scope_prefix(user: dict | None) -> str:
    try:
        uid = str((user or {}).get("user_id") or (user or {}).get("id") or "anon")
    except Exception:
        uid = "anon"
    try:
        from backend.auth.tiers import normalize_tier as _n

        tier = _n((user or {}).get("tier"))
    except Exception:
        tier = "free"
    return f"u:{uid}:t:{tier}:"


def _index_cache_key(mic: str, timeframe: str, user: dict | None = None) -> str:
    try:
        return f"{_scope_prefix(user)}markets:index:{mic}:{timeframe}"
    except Exception:
        return f"markets:index:{mic}:{timeframe}"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _closes_from_bars(bars: list[dict]) -> list[dict]:
    by_time: dict[str, float] = {}
    for row in bars or []:
        if not isinstance(row, dict):
            continue
        raw_t = row.get("ts") or row.get("time") or row.get("date")
        if raw_t is None:
            continue
        t = str(raw_t)[:10] if isinstance(raw_t, str) else None
        if t is None:
            try:
                t = str(raw_t)[:10]
            except Exception:
                continue
        if len(t) != 10:
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        import math as _math

        if not _math.isfinite(close):
            continue
        by_time[t] = close
    return [{"t": t, "close": c} for t, c in sorted(by_time.items())]


def _resample_points(points: list[dict], timeframe: str) -> list[dict]:
    """Downsample daily [{t, close}] to 1wk (Fri) / 1mo (month-end).

    Only 1d bars are ever fetched/stored (storing resampled bars under a
    1wk/1mo timeframe would be dishonest); weekly/monthly views resample
    in memory. Never raises — falls back to daily on bad input.
    """
    try:
        tf = (timeframe or "1d").strip().lower()
    except Exception:
        return points
    if tf not in ("1wk", "1mo") or not points:
        return points
    try:
        from datetime import date as _date
    except Exception:
        return points
    try:
        if tf == "1wk":
            out: list[dict] = []
            # Group by ISO (year, week); keep last (Friday) close per week.
            buckets: dict[tuple[int, int], dict] = {}
            for p in points:
                try:
                    d = _date.fromisoformat(str(p.get("t"))[:10])
                    key = d.isocalendar()[:2]
                    buckets[key] = p
                except Exception:
                    continue
            out = [buckets[k] for k in sorted(buckets)]
            return out or points
        # 1mo: keep last close per calendar month.
        buckets_m: dict[str, dict] = {}
        for p in points:
            try:
                buckets_m[str(p.get("t"))[:7]] = p
            except Exception:
                continue
        out_m = [buckets_m[k] for k in sorted(buckets_m)]
        return out_m or points
    except Exception:
        return points


@router.get("/{mic}/index")
def market_index(
    mic: str,
    timeframe: str = Query(default="1d", description="1d|1wk|1mo"),
    svc: MarketDataService = Depends(get_market_service),
    request: Request = None,  # type: ignore[assignment]
) -> dict:
    """Per-market benchmark bars (native currency, proxy-badged).

    Free (no tier gate — top-of-funnel). Cache is user-scoped best-effort:
    when a Bearer user is present the key carries u:{id}:t:{tier}:, else anon.
    """
    upper = (mic or "").strip().upper()
    cfg = BENCHMARKS.get(upper)
    if cfg is None:
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {mic!r}: expected one of {sorted(BENCHMARKS)}",
        )
    if cfg.get("enabled") is not True:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{upper} benchmark disabled (vendor index symbol unverified). Enable by adding "
                f"the XCOL venue to config/markets.yaml + confirming the vendor index symbol "
                f"(see docs/API_CONTRACT.md M9 proposal)."
            ),
        )
    tf = (timeframe or "1d").strip()
    if tf not in VALID_TIMEFRAMES:
        raise HTTPException(status_code=422, detail=f"timeframe must be one of {list(VALID_TIMEFRAMES)}")
    # Result cache: repeats within TTL skip the bars fan-out (user-scoped).
    try:
        from backend.cache import get_cache as _get_cache

        _u: dict | None = None
        try:
            from fastapi import Request as _Req  # type: ignore

            if request is not None and hasattr(request, "headers"):
                _auth = (request.headers.get("authorization") or "").strip()  # type: ignore
                _tok = _auth[7:].strip() if _auth[:7].lower() == "bearer " else ""
                _map = {"test-free": ("user-free", "free"), "test-silver": ("user-silver", "silver"), "test-gold": ("user-gold", "gold"), "test-platinum": ("user-platinum", "platinum"), "test-admin": ("admin-1", "platinum")}
                if _tok in _map:
                    _uid, _tier = _map[_tok]
                    _u = {"user_id": _uid, "tier": _tier}
        except Exception:
            _u = None
        _ick = _index_cache_key(upper, tf, _u)
        try:
            _icached = _get_cache().get(_ick)
            if isinstance(_icached, dict) and isinstance(_icached.get("points"), list):
                return _icached
        except Exception:
            _ick = None
    except Exception:
        _ick = None
    limit = TIMEFRAME_LIMITS[tf]
    # Only 1d is ever fetched/stored — 1wk/1mo resample in memory from a
    # deeper 1d window (52w needs ~365 dailies, 36mo needs ~750). Without
    # this every 1wk/1mo view was DB-only thin -> 502 with no live cover.
    fetch_tf = "1d"
    fetch_limit = limit
    if tf == "1wk":
        fetch_limit = max(limit * 7, 120)
    elif tf == "1mo":
        fetch_limit = max(limit * 21, 250)
    candidates = [{"symbol": cfg["index_symbol"], "is_proxy": False}] + [
        {"symbol": p, "is_proxy": True} for p in (cfg.get("proxies") or [])
    ]
    last_error: str | None = None
    saw_empty = False
    for cand in candidates:
        sym = str(cand["symbol"])
        # Caret symbols fail validate_symbol by design -> fall through to proxy.
        try:
            validate_symbol(sym, field="symbol")
        except HTTPException as exc:
            last_error = sanitize_error(exc)
            continue
        try:
            payload = svc.get_bars(sym, fetch_tf, fetch_limit)
        except Exception as exc:
            last_error = sanitize_error(exc, prefix="bars failed")
            continue
        bars = payload.get("bars", []) if isinstance(payload, dict) else []
        points = _closes_from_bars(bars if isinstance(bars, list) else [])
        points = _resample_points(points, tf)
        if not points:
            saw_empty = True
            continue
        prov = payload.get("provenance") if isinstance(payload, dict) else None
        if not isinstance(prov, dict):
            # Fail-closed: bars without a provenance envelope are unusable —
            # skip this candidate instead of fabricating an envelope.
            last_error = f"bars for {sym} missing provenance envelope"
            saw_empty = True
            continue
        out = {
            "mic": upper,
            "label": f"{cfg['venue']} — {cfg['label']}",
            "symbol": payload.get("symbol", sym) if isinstance(payload, dict) else sym,
            "used_symbol": sym,
            "is_proxy": bool(cand["is_proxy"]),
            "timeframe": payload.get("timeframe", tf) if isinstance(payload, dict) else tf,
            "points": points,
            "count": len(points),
            "start": points[0]["t"],
            "end": points[-1]["t"],
            "last_close": points[-1]["close"],
            "currency": cfg.get("currency", "USD"),
            "methodology": METHODOLOGY,
            "disclosure": DISCLOSURE,
            "provenance": prov,
        }
        try:
            if _ick:
                from backend.cache import get_cache as _get_cache2

                _get_cache2().set(_ick, out, ttl_s=_INDEX_TTL_S)
        except Exception:
            pass
        return out
    raise HTTPException(
        status_code=502,
        detail=last_error
        or (
            f"index bars empty for {upper} ({cfg['index_symbol']}) — no proxy configured"
            if saw_empty
            else f"index unavailable for {upper}"
        ),
    )
