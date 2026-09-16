"""GET /api/markets/overview + GET /api/markets/{mic}/liquidity.

Per-market liquidity + breadth aggregates for the homepage (XNYS/XNAS/XSHG/
XPAR/XAMS/XBRU). Fans out registry symbols -> MarketDataService quote+bars,
reusing the screener.py pattern: per-symbol try/except degrades to
``skipped: [{symbol, mic, reason}]`` — the endpoints never 500 because of
one bad symbol.

Turnover is native ``price * volume`` per symbol with NO FX conversion;
``?target_ccy=USD|EUR|CNY`` is a display-grouping passthrough only (the
response carries a ``turnover_note`` saying so).

UTC/provenance conventions: per-row quote provenance envelopes are passed
through untouched; per-market and top-level envelopes merge via the
screener ``_combine_provenance`` pattern (oldest as_of wins, sources
joined, fallback sticky, missing unioned, grade recomputed).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_market_service, get_registry
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/markets", tags=["markets"])

_BUILTIN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})


def _known_markets() -> frozenset:
    """Canonical venue set: YAML registry union built-ins (never empty)."""
    try:
        from backend.instruments.config import market_mics

        configured = set(market_mics(enabled_only=True))
    except Exception:
        configured = set()
    return frozenset(configured | set(_BUILTIN_MARKETS))


KNOWN_MARKETS = _BUILTIN_MARKETS  # compat alias (tests import this name)

DISCLOSURE = "Not investment advice. For informational purposes only."

ALLOWED_CCY = ("USD", "EUR", "CNY")

#: Overview/liquidity perf: the per-symbol quote+bars fan-out dominates wall
#: time (same pattern as screener: quotes ~93% of a cold scan). Cache the
#: assembled envelopes so homepage repeat views + ASPI Top-20 fan-outs don't
#: rescan on every request. TTL 45s mirrors the screener cache (quote cache
#: itself is 60s).
_MARKETS_OVERVIEW_TTL_S = 45
_MARKETS_LIQUIDITY_TTL_S = 45


def _overview_cache_key(target_ccy: str | None) -> str:
    return f"markets:overview:{(target_ccy or 'ALL').upper()}"


def _liquidity_cache_key(mic: str, target_ccy: str | None, limit: int, sort: str) -> str:
    return f"markets:liquidity:{mic}:{(target_ccy or 'ALL').upper()}:{int(limit)}:{(sort or 'turnover').lower()}"


#: Liquidity history: daily aggregates straight from stored 1d price_bars
#: (DB-only, never a live fetch — so it can't time out like the quote
#: fan-out). Window -> number of daily points returned (capped 250).
_HISTORY_WINDOW_DAYS = {"1D": 20, "5D": 30, "1M": 30, "3M": 90, "6M": 125, "1Y": 250}
_HISTORY_TTL_S = 60

_MIC_CURRENCIES = {
    "XNYS": "USD",
    "XNAS": "USD",
    "XSHG": "CNY",
    "XPAR": "EUR",
    "XAMS": "EUR",
    "XBRU": "EUR",
}


def _normalize_history_window(window: str | None) -> str:
    w = (window or "1D").strip().upper()
    return w if w in _HISTORY_WINDOW_DAYS else "1D"


def _history_cache_key(mic: str, window: str) -> str:
    return f"markets:liquidity-history:{mic}:{window}"


def _empty_history(mic: str, window: str, currency: str, missing: list[str]) -> dict:
    try:
        prov = build_provenance(
            f"price_bars:{mic}", as_of=_utcnow(), delay_minutes=15,
            quality_grade="B", fallback_used=False, missing_fields=missing,
        ).model_dump(mode="json")
    except Exception:
        prov = {
            "source": f"price_bars:{mic}", "as_of": _utcnow().isoformat(),
            "delay_minutes": 15, "quality_grade": "B",
            "fallback_used": False, "missing_fields": missing,
        }
    return {
        "mic": mic, "window": window, "currency": currency,
        "points": [], "provenance": prov, "disclosure": DISCLOSURE,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _combine_provenance(entries: list[dict]) -> dict:
    """Merge per-row quote provenance dicts into one envelope.

    Oldest as_of wins, sources joined, fallback sticky, missing unioned,
    grade recomputed via grade_quality. Empty: honest non-fallback envelope.
    Mirrors backend/api/screener.py::_combine_provenance.
    """
    if not entries:
        return build_provenance(
            "markets", as_of=_utcnow(), delay_minutes=15,
            quality_grade="B", fallback_used=False, missing_fields=[],
        ).model_dump(mode="json")
    stamps: list[datetime] = []
    for entry in entries:
        try:
            if not isinstance(entry, dict):
                raise ValueError("bad provenance entry")
            raw = entry.get("as_of")
            stamp = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            stamps.append(stamp)
        except Exception:
            stamps.append(_utcnow())
    oldest = min(stamps)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    try:
        sources = sorted({str(e.get("source", "unknown")) for e in entries if isinstance(e, dict)})
        fallback = any(bool(e.get("fallback_used")) for e in entries if isinstance(e, dict))
        missing = sorted({m for e in entries if isinstance(e, dict) for m in (e.get("missing_fields") or [])})
        delays = [int(e.get("delay_minutes", 15) or 0) for e in entries if isinstance(e, dict)]
        max_delay = max(delays) if delays else 15
        grade, _ = grade_quality(
            delay_minutes=max_delay,
            age_minutes=max(0.0, (_utcnow() - oldest).total_seconds() / 60),
            missing_fields=missing,
            fallback_used=fallback,
            reconciled=False,
        )
    except Exception:
        sources, fallback, missing, max_delay, grade = ["markets"], False, [], 15, "B"
    return build_provenance(
        "+".join(sources), as_of=oldest,
        delay_minutes=max_delay,
        quality_grade=grade, fallback_used=fallback, missing_fields=missing,
    ).model_dump(mode="json")


def _normalize_target_ccy(target_ccy: str | None) -> str | None:
    if target_ccy is None:
        return None
    ccy = (target_ccy or "").strip().upper()
    if ccy in ALLOWED_CCY:
        return ccy
    raise HTTPException(
        status_code=422,
        detail=f"target_ccy must be one of {sorted(ALLOWED_CCY)}, got {target_ccy!r}",
    )


def _turnover_note(target_ccy: str | None) -> str:
    if target_ccy:
        return (
            f"Turnover sums native price*volume per symbol with no FX conversion "
            f"(mixed currencies; target_ccy={target_ccy} is display grouping only)."
        )
    return (
        "Turnover sums native price*volume per symbol with no FX conversion "
        "(mixed currencies)."
    )


def _validate_mic(mic: str) -> str:
    norm = (mic or "").strip().upper()
    if norm not in _known_markets():
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {mic!r}: expected one of {sorted(_known_markets())}",
        )
    return norm


def _as_float(value: object) -> float | None:
    import math as _math

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not _math.isfinite(number):
        return None
    return number


def _range_pct_from_quote(quote: dict, bars: dict | None) -> float | None:
    import math as _math

    high = _as_float(quote.get("high"))
    low = _as_float(quote.get("low"))
    price = _as_float(quote.get("price"))
    if high is not None and low is not None and price not in (None, 0.0):
        try:
            candidate = float((high - low) / abs(float(price)) * 100.0)
            if _math.isfinite(candidate):
                return candidate
        except (TypeError, ValueError, ZeroDivisionError, OverflowError):
            pass
    # Fallback: latest bar high/low/close (best-effort, never raises).
    try:
        rows = (bars or {}).get("bars", []) or []
        if rows:
            last = rows[-1] if isinstance(rows[-1], dict) else {}
            b_high = _as_float(last.get("high"))
            b_low = _as_float(last.get("low"))
            b_close = _as_float(last.get("close")) or price
            if b_high is not None and b_low is not None and b_close not in (None, 0.0):
                candidate = float((b_high - b_low) / abs(float(b_close)) * 100.0)
                if _math.isfinite(candidate):
                    return candidate
    except Exception:
        pass
    return None


def _collect_row(inst, svc: MarketDataService) -> dict:
    """Quote (+ best-effort bars) for one registry instrument. May raise."""
    symbol_key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None)
    if not symbol_key:
        raise ValueError("missing symbol")
    quote = svc.get_quote(symbol_key, getattr(inst, "exchange_mic", None))
    if not isinstance(quote, dict):
        raise ValueError("quote unavailable")
    # Perf: bars are only needed for the range_pct fallback. When the quote
    # already carries a usable high/low, skip the extra get_bars round-trip
    # (DB + possible live fetch) — this halves per-symbol I/O on overview
    # scans and is why cold homepage loads timed out.
    bars = None
    try:
        q_high = quote.get("high")
        q_low = quote.get("low")
        q_high_f = float(q_high) if q_high is not None and not isinstance(q_high, bool) else None
        q_low_f = float(q_low) if q_low is not None and not isinstance(q_low, bool) else None
        import math as _math

        quote_has_range = (
            q_high_f is not None and q_low_f is not None
            and _math.isfinite(q_high_f) and _math.isfinite(q_low_f)
        )
    except (TypeError, ValueError, OverflowError):
        quote_has_range = False
    if not quote_has_range:
        try:
            bars = svc.get_bars(symbol_key, timeframe="1d", limit=5)
        except Exception:
            bars = None
    price = _as_float(quote.get("price"))
    change_pct = _as_float(quote.get("change_pct"))
    raw_vol = quote.get("volume")
    volume: int | None = None
    if raw_vol is not None and not isinstance(raw_vol, bool):
        try:
            import math as _math

            vol_f = float(raw_vol)
            if _math.isfinite(vol_f):
                volume = int(vol_f)
        except (TypeError, ValueError, OverflowError):
            volume = None
    turnover: float | None = None
    if price is not None and volume is not None:
        try:
            import math as _math

            candidate = float(price) * float(volume)
            turnover = candidate if _math.isfinite(candidate) else None
        except (TypeError, ValueError, OverflowError):
            turnover = None
    provenance = quote.get("provenance")
    try:
        company = getattr(inst, "company_name", "")
        mic_val = getattr(inst, "exchange_mic", None)
        ccy_fallback = getattr(inst, "currency", "USD")
    except Exception:
        company, mic_val, ccy_fallback = "", None, "USD"
    return {
        "symbol": symbol_key,
        "company_name": company,
        "exchange_mic": mic_val,
        "currency": quote.get("currency") or ccy_fallback,
        "price": price,
        "change_pct": change_pct,
        "volume": volume,
        "turnover": turnover,
        "range_pct": _range_pct_from_quote(quote, bars),
        "market_state": quote.get("market_state"),
        "provenance": provenance if isinstance(provenance, dict) else None,
    }


def _aggregate(
    mic: str, universe_size: int, rows: list[dict],
    turnover_note: str | None = None,
) -> dict:
    import math as _math

    changes = [r["change_pct"] for r in rows if isinstance(r.get("change_pct"), (int, float)) and _math.isfinite(r["change_pct"])]
    ranges = [r["range_pct"] for r in rows if isinstance(r.get("range_pct"), (int, float)) and _math.isfinite(r["range_pct"])]
    volumes = [r["volume"] for r in rows if isinstance(r.get("volume"), int)]
    turnovers = [r["turnover"] for r in rows if isinstance(r.get("turnover"), (int, float)) and _math.isfinite(r["turnover"])]
    states: dict[str, int] = {}
    for r in rows:
        try:
            key = str(r.get("market_state") or "unknown")
        except Exception:
            key = "unknown"
        states[key] = states.get(key, 0) + 1
    try:
        avg_change = float(sum(changes) / len(changes)) if changes else None
        if avg_change is not None and not _math.isfinite(avg_change):
            avg_change = None
    except Exception:
        avg_change = None
    try:
        total_volume = int(sum(volumes)) if volumes else 0
    except Exception:
        total_volume = 0
    try:
        total_turnover = float(sum(turnovers)) if turnovers else 0.0
        if not _math.isfinite(total_turnover):
            total_turnover = 0.0
    except Exception:
        total_turnover = 0.0
    try:
        avg_range = float(sum(ranges) / len(ranges)) if ranges else None
        if avg_range is not None and not _math.isfinite(avg_range):
            avg_range = None
    except Exception:
        avg_range = None
    return {
        "mic": mic,
        "symbols_total": int(universe_size),
        "quoted": len(rows),
        "advancers": sum(1 for c in changes if c > 0),
        "decliners": sum(1 for c in changes if c < 0),
        "unchanged": sum(1 for c in changes if c == 0),
        "avg_change_pct": avg_change,
        "total_volume": total_volume,
        "total_turnover": total_turnover,
        "avg_range_pct": avg_range,
        "market_state_counts": states,
        "turnover_note": turnover_note,
        "provenance": _combine_provenance(
            [r["provenance"] for r in rows if isinstance(r.get("provenance"), dict)]
        ),
        "disclosure": DISCLOSURE,
    }


def _scan_mic(
    mic: str,
    registry: InstrumentRegistry,
    svc: MarketDataService,
    skipped: list[dict],
    limit: int | None = None,
    sort: str = "turnover",
) -> tuple[list[dict], int]:
    try:
        universe = [i for i in registry.all() if getattr(i, "exchange_mic", None) == mic]
    except Exception as exc:
        skipped.append({"symbol": "_universe", "mic": mic, "reason": f"{type(exc).__name__}: {exc}"})
        return [], 0
    rows: list[dict] = []

    def _one(inst) -> tuple[str, dict | None]:
        try:
            symbol_key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
        except Exception:
            return "skip", {"symbol": "UNKNOWN", "mic": mic, "reason": "bad registry entry"}
        try:
            return "ok", _collect_row(inst, svc)
        except HTTPException:
            # Never let one bad symbol abort the batch; degrade to skipped.
            return "skip", {"symbol": symbol_key, "mic": mic, "reason": "HTTPException"}
        except Exception as exc:  # per-symbol degrade, never 500
            return "skip", {
                "symbol": symbol_key,
                "mic": mic,
                "reason": f"{type(exc).__name__}: {exc}",
            }

    # Bounded parallel fan-out (quote + 5-bar fetch per symbol are
    # independent). Sequential per-market scans held the request thread for
    # the full universe; 8 workers cut wall time ~8x with identical rows.
    # Per-future 12s timeout: one hung Yahoo quote must degrade to skipped,
    # never hold the Vercel function to its 60s kill (then every symbol 502s).
    workers = max(1, min(8, len(universe) or 1))
    local_skipped: list[dict] = []
    try:
        from concurrent.futures import as_completed as _as_completed

        from concurrent.futures import ThreadPoolExecutor as _TPE

        with _TPE(max_workers=workers) as pool:
            futs = {pool.submit(_one, inst): inst for inst in universe}
            for fut in _as_completed(futs, timeout=45):
                inst = futs[fut]
                try:
                    kind, payload = fut.result(timeout=12)
                except Exception as exc:
                    try:
                        sk = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
                    except Exception:
                        sk = "UNKNOWN"
                    local_skipped.append({"symbol": sk, "mic": mic, "reason": f"TimeoutError: {type(exc).__name__}"})
                    continue
                if kind == "ok" and isinstance(payload, dict):
                    rows.append(payload)
                elif isinstance(payload, dict):
                    local_skipped.append(payload)
            # Any futures still pending after the 45s batch budget: cancel
            # and mark skipped so the request returns instead of hanging.
            for fut, inst in futs.items():
                if not fut.done():
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    try:
                        sk = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
                    except Exception:
                        sk = "UNKNOWN"
                    if not any(s.get("symbol") == sk for s in local_skipped):
                        local_skipped.append({"symbol": sk, "mic": mic, "reason": "TimeoutError: batch budget"})
    except Exception:
        for inst in universe:
            kind, payload = _one(inst)
            if kind == "ok" and isinstance(payload, dict):
                rows.append(payload)
            elif isinstance(payload, dict):
                local_skipped.append(payload)
    skipped.extend(local_skipped)
    # Server-side sort + limit BEFORE wire assembly (liquidity charts only
    # render the head; transferring the full tail wastes time + bytes).
    try:
        key_fn = {
            "turnover": lambda r: (r.get("turnover") is not None, r.get("turnover") or 0.0),
            "change": lambda r: (r.get("change_pct") is not None, abs(r.get("change_pct") or 0.0)),
            "volume": lambda r: (r.get("volume") is not None, r.get("volume") or 0),
        }.get((sort or "turnover").strip().lower(), None)
        if key_fn is not None:
            rows.sort(key=key_fn, reverse=True)
    except Exception:
        pass
    if limit is not None:
        try:
            rows = rows[: max(1, int(limit))]
        except (TypeError, ValueError):
            pass
    return rows, len(universe)


@router.get("")
def list_markets(
    registry: InstrumentRegistry = Depends(get_registry),
) -> dict:
    """Venue discovery (single source for frontend selectors).

    Returns every known MIC with label/currency/timezone + symbol counts so
    selectors never hardcode the venue set. `config/markets.yaml` venues
    union built-ins; counts come from the live registry (incl. EXTRA_SYMBOLS).
    """
    try:
        from backend.instruments.calendars import EXCHANGE_META as _BUILTIN
        from backend.instruments.config import load_market_configs
    except Exception:
        _BUILTIN, load_market_configs = {}, lambda: ()
    try:
        items = registry.all()
    except Exception:
        items = []
    counts: dict[str, int] = {}
    for inst in items or []:
        try:
            mic = str(getattr(inst, "exchange_mic", "") or "").upper()
        except Exception:
            continue
        if mic:
            counts[mic] = counts.get(mic, 0) + 1
    venues: list[dict] = []
    seen: set[str] = set()
    try:
        configured = list(load_market_configs())
    except Exception:
        configured = []
    for cfg in configured:
        try:
            mic = str(cfg.mic).upper()
        except Exception:
            continue
        if mic in seen:
            continue
        seen.add(mic)
        venues.append({
            "mic": mic,
            "label": str(getattr(cfg, "name", mic)),
            "currency": str(getattr(cfg, "currency", "USD")),
            "timezone": str(getattr(cfg, "timezone", "UTC")),
            "suffix": str(getattr(cfg, "provider_suffix", "")),
            "enabled": bool(getattr(cfg, "enabled", True)),
            "symbols": int(counts.get(mic, 0)),
        })
    for mic in sorted(_known_markets()):
        if mic in seen:
            continue
        meta = _BUILTIN.get(mic, {}) if isinstance(_BUILTIN, dict) else {}
        venues.append({
            "mic": mic,
            "label": str(meta.get("name", mic)) if isinstance(meta, dict) else mic,
            "currency": str(meta.get("currency", "USD")) if isinstance(meta, dict) else "USD",
            "timezone": str(meta.get("timezone", "UTC")) if isinstance(meta, dict) else "UTC",
            "suffix": str(meta.get("suffix", "")) if isinstance(meta, dict) else "",
            "enabled": True,
            "symbols": int(counts.get(mic, 0)),
        })
    venues.sort(key=lambda v: str(v.get("mic") or ""))
    return {
        "markets": venues,
        "count": len(venues),
        "provenance": _combine_provenance([]),
        "disclosure": DISCLOSURE,
    }


@router.get("/overview")
def markets_overview(
    target_ccy: str | None = Query(
        default=None,
        description="Display grouping only (USD|EUR|CNY); turnover stays native, no FX",
    ),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Per-MIC liquidity + breadth aggregates across all known markets."""
    ccy = _normalize_target_ccy(target_ccy)
    # Result cache: identical overviews within TTL skip the quote fan-out
    # (cold scans exceed the 60s frontend timeout; cached repeats are fast).
    try:
        from backend.cache import get_cache as _get_cache

        _ck = _overview_cache_key(ccy)
        try:
            _cached = _get_cache().get(_ck)
            if isinstance(_cached, dict) and isinstance(_cached.get("markets"), list):
                return _cached
        except Exception:
            _ck = None
    except Exception:
        _ck = None
    skipped: list[dict] = []
    markets: list[dict] = []
    all_provenance: list[dict] = []
    # Fan out per-MIC scans concurrently (6 markets x symbols each); each
    # _scan_mic already parallelizes its symbols, so cap outer workers.
    mics = sorted(_known_markets())

    def _scan_one(mic: str) -> tuple[dict, list[dict]]:
        local_skipped: list[dict] = []
        rows, universe_size = _scan_mic(mic, registry, svc, local_skipped)
        agg = _aggregate(mic, universe_size, rows, _turnover_note(ccy))
        return agg, local_skipped

    try:
        with ThreadPoolExecutor(max_workers=min(6, len(mics) or 1)) as pool:
            for agg, local in pool.map(_scan_one, mics):
                markets.append(agg)
                skipped.extend(local)
                if isinstance(agg.get("provenance"), dict):
                    all_provenance.append(agg["provenance"])
        markets.sort(key=lambda m: str(m.get("mic") or ""))
    except Exception:
        for mic in mics:
            rows, universe_size = _scan_mic(mic, registry, svc, skipped)
            agg = _aggregate(mic, universe_size, rows, _turnover_note(ccy))
            markets.append(agg)
            if isinstance(agg.get("provenance"), dict):
                all_provenance.append(agg["provenance"])
    out = {
        "markets": markets,
        "count": len(markets),
        "target_ccy": ccy,
        "turnover_note": _turnover_note(ccy),
        "skipped": skipped,
        "provenance": _combine_provenance(all_provenance),
        "disclosure": DISCLOSURE,
    }
    try:
        if _ck:
            from backend.cache import get_cache as _get_cache2

            _get_cache2().set(_ck, out, ttl_s=_MARKETS_OVERVIEW_TTL_S)
    except Exception:
        pass
    return out


@router.get("/{mic}/liquidity")
def market_liquidity(
    mic: str,
    target_ccy: str | None = Query(
        default=None,
        description="Display grouping only (USD|EUR|CNY); turnover stays native, no FX",
    ),
    limit: int = Query(
        default=50, ge=1, le=100,
        description="Max per-symbol rows (charts render the head; default 50)",
    ),
    sort: str = Query(
        default="turnover",
        description="Row order: turnover|change|volume",
    ),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Single-market liquidity + breadth + per-symbol rows."""
    norm = _validate_mic(mic)
    ccy = _normalize_target_ccy(target_ccy)
    try:
        from backend.cache import get_cache as _get_cache3

        _lck = _liquidity_cache_key(norm, ccy, int(limit), str(sort))
        try:
            _lcached = _get_cache3().get(_lck)
            if isinstance(_lcached, dict) and isinstance(_lcached.get("rows"), list):
                return _lcached
        except Exception:
            _lck = None
    except Exception:
        _lck = None
    skipped: list[dict] = []
    rows, universe_size = _scan_mic(norm, registry, svc, skipped, limit=limit, sort=sort)
    agg = _aggregate(norm, universe_size, rows, _turnover_note(ccy))
    wire_rows = [
        {
            "symbol": r["symbol"],
            "price": r["price"],
            "change_pct": r["change_pct"],
            "volume": r["volume"],
            "turnover": r["turnover"],
            "range_pct": r["range_pct"],
            "market_state": r["market_state"],
            "provenance": r["provenance"],
        }
        for r in rows
    ]
    out = {
        **agg,
        "rows": wire_rows,
        "skipped": skipped,
        "target_ccy": ccy,
        "turnover_note": _turnover_note(ccy),
    }
    try:
        if _lck:
            from backend.cache import get_cache as _get_cache4

            _get_cache4().set(_lck, out, ttl_s=_MARKETS_LIQUIDITY_TTL_S)
    except Exception:
        pass
    return out


@router.get("/{mic}/liquidity/history")
def market_liquidity_history(
    mic: str,
    window: str = Query(default="1D", description="History window: 1D|5D|1M|3M|6M|1Y"),
    registry: InstrumentRegistry = Depends(get_registry),
) -> dict:
    """Daily liquidity history from stored 1d bars (DB-only, never live).

    Per trading date: total turnover (sum of native close*volume, no FX),
    total volume, advancers/decliners/unchanged + avg_change_pct from
    per-symbol close-vs-prev-close returns. Symbols missing a date simply
    don't contribute that day — no forward-fill, no synthetic points.

    Fail-closed: thin/empty history returns ``points: []`` with a valid
    provenance envelope (frontend renders its honest placeholder), never
    500 and never fabricated points. Unknown MIC -> 422, bad window falls
    back to 1D (frontend contract).
    """
    norm = _validate_mic(mic)
    w = _normalize_history_window(window)
    want = _HISTORY_WINDOW_DAYS[w]
    currency = _MIC_CURRENCIES.get(norm, "USD")
    try:
        from backend.cache import get_cache as _get_cache_h

        _hck = _history_cache_key(norm, w)
        try:
            _hcached = _get_cache_h().get(_hck)
            if isinstance(_hcached, dict) and isinstance(_hcached.get("points"), list):
                return _hcached
        except Exception:
            _hck = None
    except Exception:
        _hck = None
    try:
        universe = [i for i in registry.all() if getattr(i, "exchange_mic", None) == norm]
    except Exception:
        return _empty_history(norm, w, currency, ["history", "universe"])
    if not universe:
        return _empty_history(norm, w, currency, ["history"])
    try:
        from backend.db.models import Instrument as DBInstrument
        from backend.db.models import PriceBar
        from backend.db.session import get_session_factory
    except Exception:
        return _empty_history(norm, w, currency, ["history"])
    try:
        Session = get_session_factory()
        db = Session()
        try:
            per_symbol_cap = max(1, int(want) * 2)
            row_limit = max(100, min(20000, len(universe) * per_symbol_cap))
            pairs = (
                db.query(PriceBar, DBInstrument)
                .join(DBInstrument, PriceBar.instrument_id == DBInstrument.instrument_id)
                .filter(DBInstrument.exchange_mic == norm, PriceBar.timeframe == "1d")
                .order_by(PriceBar.ts.desc())
                .limit(row_limit)
                .all()
            )
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        return _empty_history(norm, w, currency, ["history"])
    if not pairs:
        return _empty_history(norm, w, currency, ["history"])
    try:
        import math as _math

        # Per-symbol sorted bars + per-date turnover/volume buckets.
        by_symbol: dict[str, list[tuple[str, float | None, float | None]]] = {}
        turnover_by_day: dict[str, float] = {}
        volume_by_day: dict[str, float] = {}
        latest_ts_by_day: dict[str, object] = {}
        sources: set[str] = set()
        latest_ts = None
        for bar, _inst in pairs:
            try:
                raw_ts = getattr(bar, "ts", None)
                if raw_ts is None:
                    continue
                day = str(raw_ts)[:10]
                if len(day) != 10:
                    continue
                try:
                    close = float(getattr(bar, "close", None))  # type: ignore[arg-type]
                    if not _math.isfinite(close):
                        close = None  # type: ignore[assignment]
                except (TypeError, ValueError):
                    close = None
                try:
                    vol_raw = getattr(bar, "volume", None)
                    vol = float(vol_raw) if vol_raw is not None and not isinstance(vol_raw, bool) else None
                    if vol is not None and (not _math.isfinite(vol) or vol < 0):
                        vol = None
                except (TypeError, ValueError):
                    vol = None
                try:
                    src = str(getattr(bar, "source", "") or "").strip()
                    if src:
                        sources.add(src)
                except Exception:
                    pass
                if latest_ts is None:
                    latest_ts = raw_ts
                try:
                    sym = str(getattr(_inst, "provider_symbol", "") or getattr(_inst, "exchange_symbol", "") or "").strip().upper()
                except Exception:
                    sym = ""
                if not sym:
                    continue
                by_symbol.setdefault(sym, []).append((day, close, vol))
                if close is not None and vol is not None:
                    try:
                        t = float(close) * float(vol)
                        if _math.isfinite(t):
                            turnover_by_day[day] = turnover_by_day.get(day, 0.0) + t
                    except (TypeError, ValueError, OverflowError):
                        pass
                if vol is not None:
                    volume_by_day[day] = volume_by_day.get(day, 0.0) + float(vol)
                if day not in latest_ts_by_day:
                    latest_ts_by_day[day] = raw_ts
            except Exception:
                continue
        # Per-symbol daily returns (close vs prev close) for breadth.
        returns_by_day: dict[str, list[float]] = {}
        for _sym, rows in by_symbol.items():
            rows.sort(key=lambda r: r[0])
            for i in range(1, len(rows)):
                prev_day, prev_close, _pv = rows[i - 1]
                day, close, _v = rows[i]
                if close is None or prev_close is None or prev_close == 0:
                    continue
                try:
                    ret = (float(close) - float(prev_close)) / abs(float(prev_close)) * 100.0
                    if _math.isfinite(ret):
                        returns_by_day.setdefault(day, []).append(ret)
                except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                    continue
        days = sorted(set(list(turnover_by_day.keys()) + list(volume_by_day.keys()) + list(returns_by_day.keys())))
        days = days[-want:] if len(days) > want else days
        points: list[dict] = []
        for day in days:
            rets = returns_by_day.get(day, [])
            adv = sum(1 for r in rets if r > 0)
            dec = sum(1 for r in rets if r < 0)
            unch = sum(1 for r in rets if r == 0)
            try:
                avg = float(sum(rets) / len(rets)) if rets else None
                if avg is not None and not _math.isfinite(avg):
                    avg = None
            except Exception:
                avg = None
            points.append({
                "t": f"{day}T00:00:00Z",
                "turnover": turnover_by_day.get(day),
                "volume": volume_by_day.get(day),
                "advancers": adv if rets else None,
                "decliners": dec if rets else None,
                "unchanged": unch if rets else None,
                "avg_change_pct": avg,
            })
        # Provenance: latest stored bar timestamp, delay 15, graded honestly.
        try:
            as_of = latest_ts if latest_ts is not None else _utcnow()
            if not hasattr(as_of, "tzinfo"):
                as_of = _utcnow()
            elif getattr(as_of, "tzinfo", None) is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        except Exception:
            as_of = _utcnow()
        try:
            age_min = max(0.0, (_utcnow() - as_of).total_seconds() / 60)
        except Exception:
            age_min = 0.0
        try:
            grade, _ = grade_quality(
                delay_minutes=15, age_minutes=age_min,
                missing_fields=[] if points else ["history"],
                fallback_used=False, reconciled=False,
            )
        except Exception:
            grade = "B"
        try:
            prov = build_provenance(
                "+".join(sorted(sources)) or f"price_bars:{norm}", as_of=as_of,
                delay_minutes=15, quality_grade=grade, fallback_used=False,
                missing_fields=[] if points else ["history"],
            ).model_dump(mode="json")
        except Exception:
            prov = {
                "source": "+".join(sorted(sources)) or f"price_bars:{norm}",
                "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
                "delay_minutes": 15, "quality_grade": grade,
                "fallback_used": False, "missing_fields": [] if points else ["history"],
            }
        out = {
            "mic": norm, "window": w, "currency": currency,
            "points": points, "provenance": prov, "disclosure": DISCLOSURE,
        }
        try:
            if _hck:
                from backend.cache import get_cache as _get_cache_h2

                _get_cache_h2().set(_hck, out, ttl_s=_HISTORY_TTL_S)
        except Exception:
            pass
        return out
    except Exception:
        return _empty_history(norm, w, currency, ["history"])
