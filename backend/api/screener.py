"""GET /api/screener — rank the seed universe by forecast direction probability.

Deterministic, no AI, no network beyond what the market-data providers
already do (offline stub fallback keeps the scan usable on outage).
For each registry instrument in scope: quote (price/currency/market_state/
provenance via MarketDataService) + deterministic forecast at the
requested horizon (raw direction_probability/confidence/model_version —
label bands are the caller's job) + one quality signal.

Quality note: the statement feed is not wired, so the analytics quality
modules report "unavailable" by design. The scan reuses
``piotroski_score({})`` (cheap, no I/O) and surfaces its
quality_flag/reason per row instead of fabricating a score.

Per-symbol failures degrade to ``skipped: [{symbol, reason}]`` — the
endpoint never 500s because of one bad symbol. Ranked by
direction_probability desc, filtered to direction >= min_direction.

UTC/provenance conventions: timestamps and provenance envelopes are
passed through untouched from the underlying services (UTC ISO).
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.analytics.quality import piotroski_score
from backend.api.deps import get_market_service, get_registry
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.service import ForecastService
from backend.instruments.registry import InstrumentRegistry
from backend.market_data.provenance import build_provenance
from backend.market_data.quality import grade_quality
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/screener", tags=["screener"])

KNOWN_MARKETS = frozenset({"XNYS", "XNAS", "XSHG", "XPAR", "XAMS", "XBRU"})

DISCLOSURE = "Not investment advice. For informational purposes only."

#: Quality signal inputs: no statement feed in this phase, so the shared
#: EMPTY mapping mirrors backend/api/analytics_api.py (modules return
#: their own "unavailable" results instead of fabricated numbers).
EMPTY_STATEMENTS: dict = {}


def _quality_signal() -> dict:
    """One cheap quality signal reused from the analytics quality module."""
    try:
        result = piotroski_score(EMPTY_STATEMENTS)
        return {
            "metric": "piotroski",
            "quality_flag": result.quality_flag,
            "reason": result.reason,
        }
    except Exception:
        return {
            "metric": "piotroski",
            "quality_flag": "unavailable",
            "reason": "quality signal unavailable",
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _combine_provenance(entries: list[dict]) -> dict:
    """Merge per-row quote provenance dicts into one scan-level envelope.

    Oldest as_of wins, sources are joined, fallback is sticky (any row on
    fallback flags the scan), missing fields are unioned, and the grade is
    recomputed via grade_quality. Empty scan: honest non-fallback envelope
    (nothing served, nothing fallback). Additive only; per-row envelopes
    are untouched.
    """
    if not entries:
        return build_provenance(
            "screener", as_of=_utcnow(), delay_minutes=15,
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
        sources, fallback, missing, max_delay, grade = ["screener"], False, [], 15, "B"
    return build_provenance(
        "+".join(sources), as_of=oldest,
        delay_minutes=max_delay,
        quality_grade=grade, fallback_used=fallback, missing_fields=missing,
    ).model_dump(mode="json")


def _normalize_market(market: str | None) -> str | None:
    mic = (market or "").strip().upper()
    if not mic or mic == "ALL":
        return None
    if mic not in KNOWN_MARKETS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown market {market!r}: expected one of "
            f"{sorted(KNOWN_MARKETS)} or ALL",
        )
    return mic


@router.get("")
def screen(
    market: str | None = Query(default=None, description="MIC scope or ALL"),
    min_direction: float = Query(
        default=0.5, ge=0.0, le=1.0,
        description="Minimum direction_probability to include",
    ),
    horizon: int = Query(default=21, description="Trading-day horizon: 5, 21 or 63"),
    limit: int = Query(default=20, ge=1, le=50, description="Max rows (cap 50)"),
    registry: InstrumentRegistry = Depends(get_registry),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Scan the registry universe, rank by forecast direction probability."""
    try:
        horizon_int = int(horizon)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        ) from None
    if horizon_int not in FORECAST_HORIZONS:
        raise HTTPException(
            status_code=422,
            detail=f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}",
        )
    horizon = horizon_int
    mic = _normalize_market(market)

    try:
        universe = registry.all()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"screener universe failed: {exc}") from exc
    if mic is not None:
        try:
            universe = [i for i in universe if getattr(i, "exchange_mic", None) == mic]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"screener filter failed: {exc}") from exc
    universe_size = len(universe)

    try:
        forecaster = ForecastService(market_service=svc)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"screener forecaster failed: {exc}") from exc
    quality = _quality_signal()

    results: list[dict] = []
    skipped: list[dict] = []
    for inst in universe:
        try:
            symbol_key = getattr(inst, "provider_symbol", None) or getattr(inst, "exchange_symbol", None) or "UNKNOWN"
        except Exception:
            skipped.append({"symbol": "UNKNOWN", "reason": "bad registry entry"})
            continue
        try:
            quote = svc.get_quote(symbol_key, getattr(inst, "exchange_mic", None))
            if not isinstance(quote, dict):
                raise ValueError("quote unavailable")
            fc = forecaster.forecast(symbol_key, horizon)
            if not isinstance(fc, dict):
                raise ValueError("forecast unavailable")
            direction = float(fc["direction_probability"])
            if not (0.0 <= direction <= 1.0):
                raise ValueError(f"direction_probability out of range: {direction!r}")
            results.append({
                "symbol": symbol_key,
                "company_name": inst.company_name,
                "exchange_mic": inst.exchange_mic,
                "currency": quote.get("currency") or inst.currency,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
                "market_state": quote.get("market_state"),
                "direction_probability": direction,
                "confidence": fc.get("confidence"),
                "model_version": fc.get("model_version"),
                "horizon": horizon,
                "horizons": [horizon],
                "quality": quality,
                "provenance": quote.get("provenance"),
            })
        except HTTPException as exc:
            # Per-symbol degrade (never abort the batch on one bad symbol):
            # HTTP errors from the underlying services become skipped entries.
            skipped.append({
                "symbol": symbol_key,
                "reason": f"{type(exc).__name__}: {exc.detail if hasattr(exc, 'detail') else exc}",
            })
        except Exception as exc:  # per-symbol degrade, never 500
            skipped.append({
                "symbol": symbol_key,
                "reason": f"{type(exc).__name__}: {exc}",
            })

    ranked = sorted(results, key=lambda r: r["direction_probability"], reverse=True)
    filtered = [r for r in ranked if r["direction_probability"] >= float(min_direction)]
    page = filtered[: int(limit)]
    return {
        "results": page,
        "count": len(page),
        "universe_size": universe_size,
        "skipped": skipped,
        "horizon": horizon,
        "provenance": _combine_provenance(
            [r["provenance"] for r in page if isinstance(r.get("provenance"), dict)]
        ),
        "disclosure": DISCLOSURE,
    }
