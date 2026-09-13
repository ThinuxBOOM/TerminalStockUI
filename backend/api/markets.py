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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_market_service, get_registry
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/markets", tags=["markets"])

KNOWN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})

DISCLOSURE = "Not investment advice. For informational purposes only."

ALLOWED_CCY = ("USD", "EUR", "CNY")


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
            stamps.append(datetime.fromisoformat(str(entry["as_of"]).replace("Z", "+00:00")))
        except (KeyError, ValueError):
            stamps.append(_utcnow())
    oldest = min(stamps)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    sources = sorted({str(e.get("source", "unknown")) for e in entries})
    fallback = any(bool(e.get("fallback_used")) for e in entries)
    missing = sorted({m for e in entries for m in (e.get("missing_fields") or [])})
    grade, _ = grade_quality(
        delay_minutes=max(int(e.get("delay_minutes", 15) or 0) for e in entries),
        age_minutes=max(0.0, (_utcnow() - oldest).total_seconds() / 60),
        missing_fields=missing,
        fallback_used=fallback,
        reconciled=False,
    )
    return build_provenance(
        "+".join(sources), as_of=oldest,
        delay_minutes=max(int(e.get("delay_minutes", 15) or 0) for e in entries),
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
    if norm not in KNOWN_MARKETS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {mic!r}: expected one of {sorted(KNOWN_MARKETS)}",
        )
    return norm


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _range_pct_from_quote(quote: dict, bars: dict | None) -> float | None:
    high = _as_float(quote.get("high"))
    low = _as_float(quote.get("low"))
    price = _as_float(quote.get("price"))
    if high is not None and low is not None and price not in (None, 0.0):
        try:
            return float((high - low) / abs(float(price)) * 100.0)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    # Fallback: latest bar high/low/close (best-effort, never raises).
    try:
        rows = (bars or {}).get("bars", []) or []
        if rows:
            last = rows[-1]
            b_high = _as_float(last.get("high"))
            b_low = _as_float(last.get("low"))
            b_close = _as_float(last.get("close")) or price
            if b_high is not None and b_low is not None and b_close not in (None, 0.0):
                return float((b_high - b_low) / abs(float(b_close)) * 100.0)
    except Exception:
        pass
    return None


def _collect_row(inst, svc: MarketDataService) -> dict:
    """Quote (+ best-effort bars) for one registry instrument. May raise."""
    symbol_key = inst.provider_symbol or inst.exchange_symbol
    quote = svc.get_quote(symbol_key, inst.exchange_mic)
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
            volume = int(float(raw_vol))
        except (TypeError, ValueError):
            volume = None
    turnover: float | None = None
    if price is not None and volume is not None:
        try:
            turnover = float(price) * float(volume)
        except (TypeError, ValueError):
            turnover = None
    provenance = quote.get("provenance")
    return {
        "symbol": symbol_key,
        "company_name": inst.company_name,
        "exchange_mic": inst.exchange_mic,
        "currency": quote.get("currency") or inst.currency,
        "price": price,
        "change_pct": change_pct,
        "volume": volume,
        "turnover": turnover,
        "range_pct": _range_pct_from_quote(quote, bars),
        "market_state": quote.get("market_state"),
        "provenance": provenance if isinstance(provenance, dict) else None,
    }


def _aggregate(mic: str, universe_size: int, rows: list[dict]) -> dict:
    changes = [r["change_pct"] for r in rows if r.get("change_pct") is not None]
    ranges = [r["range_pct"] for r in rows if r.get("range_pct") is not None]
    volumes = [r["volume"] for r in rows if r.get("volume") is not None]
    turnovers = [r["turnover"] for r in rows if r.get("turnover") is not None]
    states: dict[str, int] = {}
    for r in rows:
        key = str(r.get("market_state") or "unknown")
        states[key] = states.get(key, 0) + 1
    return {
        "mic": mic,
        "symbols_total": int(universe_size),
        "quoted": len(rows),
        "advancers": sum(1 for c in changes if c > 0),
        "decliners": sum(1 for c in changes if c < 0),
        "unchanged": sum(1 for c in changes if c == 0),
        "avg_change_pct": (float(sum(changes) / len(changes)) if changes else None),
        "total_volume": int(sum(volumes)) if volumes else 0,
        "total_turnover": float(sum(turnovers)) if turnovers else 0.0,
        "avg_range_pct": (float(sum(ranges) / len(ranges)) if ranges else None),
        "market_state_counts": states,
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
) -> tuple[list[dict], int]:
    universe = [i for i in registry.all() if i.exchange_mic == mic]
    rows: list[dict] = []
    for inst in universe:
        symbol_key = inst.provider_symbol or inst.exchange_symbol
        try:
            rows.append(_collect_row(inst, svc))
        except Exception as exc:  # per-symbol degrade, never 500
            skipped.append({
                "symbol": symbol_key,
                "mic": mic,
                "reason": f"{type(exc).__name__}: {exc}",
            })
    return rows, len(universe)


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
    skipped: list[dict] = []
    markets: list[dict] = []
    all_provenance: list[dict] = []
    for mic in sorted(KNOWN_MARKETS):
        rows, universe_size = _scan_mic(mic, registry, svc, skipped)
        agg = _aggregate(mic, universe_size, rows)
        markets.append(agg)
        if isinstance(agg.get("provenance"), dict):
            all_provenance.append(agg["provenance"])
    return {
        "markets": markets,
        "count": len(markets),
        "target_ccy": ccy,
        "turnover_note": _turnover_note(ccy),
        "skipped": skipped,
        "provenance": _combine_provenance(all_provenance),
        "disclosure": DISCLOSURE,
    }


@router.get("/{mic}/liquidity")
def market_liquidity(
    mic: str,
    target_ccy: str | None = Query(
        default=None,
        description="Display grouping only (USD|EUR|CNY); turnover stays native, no FX",
    ),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Single-market liquidity + breadth + per-symbol rows."""
    norm = _validate_mic(mic)
    ccy = _normalize_target_ccy(target_ccy)
    skipped: list[dict] = []
    rows, universe_size = _scan_mic(norm, registry, svc, skipped)
    agg = _aggregate(norm, universe_size, rows)
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
    return {
        **agg,
        "rows": wire_rows,
        "skipped": skipped,
        "target_ccy": ccy,
        "turnover_note": _turnover_note(ccy),
    }
