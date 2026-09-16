"""GET /api/analytics/{symbol} — deterministic analytics bundle (Milestone 2 modules).

Technical indicators run on offline deterministic bars; statement-based
families (fundamentals/quality/valuation) go through the existing
backend/analytics/ modules with an empty statement mapping, so they return
the modules' own "unavailable" results (formula + source_fields +
quality_flag + reason) instead of fabricated numbers. No network, no AI.
Every response carries the provenance envelope + timestamp.
"""

from __future__ import annotations

import math

import pandas as pd
from fastapi import APIRouter, Depends, Query

from backend.analytics.common import MetricResult
from backend.analytics.technical.overlays import compute_indicators, parse_indicators
from backend.analytics.fundamentals import (
    debt_to_assets,
    debt_to_equity,
    earnings_growth,
    gross_margin,
    interest_coverage,
    net_margin,
    operating_margin,
    revenue_growth,
    roe,
    roic,
)
from backend.analytics.quality import altman_z, beneish_m, dupont, piotroski_score
from backend.analytics.technical import (
    atr,
    bollinger,
    ema,
    is_volume_anomaly,
    macd,
    rsi,
    sma,
    volatility,
)
from backend.analytics.valuation import dcf_sensitivity, peer_compare, wacc
from backend.api.deps import get_market_service
from backend.market_data.service import MarketDataService

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

BAR_LIMIT = 120
INDICATOR_MAX_POINTS = 1000
EMPTY_STATEMENTS: dict = {}
NOTE_UNAVAILABLE = (
    "Statement feed not wired in M3: fundamentals/quality/valuation report "
    "the analytics modules' own 'unavailable' results (no fabricated inputs)."
)
NOTE = NOTE_UNAVAILABLE  # compat alias (live banner now comes from _statements_note)
#: DCF stub defaults once a real free-cash-flow is available (the stub keeps
#: its single-stage Gordon shape; these grids only replace the previous
#: empty-grid call that forced "unavailable").
DCF_DEFAULT_DISCOUNT_RATES = (0.07, 0.09, 0.11)
DCF_DEFAULT_TERMINAL_RATES = (0.01, 0.02, 0.03)
DCF_DEFAULT_GROWTH = 0.05
DISCLOSURE = "Not investment advice. For informational purposes only."


def _safe_number(value) -> float | bool | None:
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(number, float) and (math.isnan(number) or math.isinf(number)):
        return None
    return number


def _json_safe(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return _safe_number(value)
    if isinstance(value, pd.Series):
        valid = value.dropna()
        last_idx = str(valid.index[-1]) if len(valid) else None
        return {
            "kind": "series",
            "latest": _safe_number(valid.iloc[-1]) if len(valid) else None,
            "last_index": last_idx,
            "n_points": int(len(valid)),
        }
    if isinstance(value, pd.DataFrame):
        latest: dict = {}
        if len(value):
            row = value.iloc[-1]
            for col in value.columns:
                latest[str(col)] = _json_safe(row[col])
        return {
            "kind": "frame",
            "columns": [str(c) for c in value.columns],
            "latest": latest,
            "n_rows": int(len(value)),
        }
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


def _serialize(result: MetricResult) -> dict:
    return {
        "value": _json_safe(result.value),
        "formula": result.formula,
        "source_fields": list(result.source_fields),
        "quality_flag": result.quality_flag,
        "reason": result.reason,
    }


def _technical_bundle(frame: pd.DataFrame) -> dict:
    """Per-indicator degrade: one failing metric never 500s the bundle."""
    specs = [
        ("sma_20", lambda: sma(frame["close"], window=20)),
        ("ema_20", lambda: ema(frame["close"], window=20)),
        ("rsi_14", lambda: rsi(frame["close"], window=14)),
        ("macd", lambda: macd(frame["close"])),
        ("bollinger_20", lambda: bollinger(frame["close"], window=20)),
        ("atr_14", lambda: atr(frame["high"], frame["low"], frame["close"], window=14)),
        ("volatility_21", lambda: volatility(frame["close"], window=21)),
        ("volume_anomaly", lambda: is_volume_anomaly(frame["volume"], window=20)),
    ]
    out: dict = {}
    for name, thunk in specs:
        try:
            out[name] = _serialize(thunk())
        except Exception:
            out[name] = {
                "value": None,
                "formula": name,
                "source_fields": [],
                "quality_flag": "unavailable",
                "reason": "insufficient history",
            }
    return out


def _statements_note(info: dict) -> str:
    """Honest banner: name the live feed + periods, or keep the M3 caveat."""
    try:
        source = (info or {}).get("source")
    except Exception:
        source = None
    if not source:
        return NOTE_UNAVAILABLE
    try:
        ends = list((info or {}).get("fiscal_ends") or [])
        filed = (info or {}).get("filed_as_of")
        fallback = bool((info or {}).get("fallback_used"))
    except Exception:
        ends, filed, fallback = [], None, False
    detail = f"FY{', FY'.join(str(e)[:4] for e in ends)}" if ends else "latest annual"
    if filed:
        detail += f", filed {filed}"
    if fallback:
        detail += " (SEC EDGAR unreachable, Yahoo fallback)"
    return (
        f"Statements: {source} ({detail}); fundamentals/quality/valuation "
        "computed from filed annuals — missing line items report "
        "'unavailable', never zero-filled."
    )


def _fundamentals_bundle(fin: dict | None = None) -> dict:
    fin = fin if isinstance(fin, dict) and fin else EMPTY_STATEMENTS
    return {
        "revenue_growth": _serialize(revenue_growth(fin)),
        "earnings_growth": _serialize(earnings_growth(fin)),
        "gross_margin": _serialize(gross_margin(fin)),
        "operating_margin": _serialize(operating_margin(fin)),
        "net_margin": _serialize(net_margin(fin)),
        "debt_to_equity": _serialize(debt_to_equity(fin)),
        "debt_to_assets": _serialize(debt_to_assets(fin)),
        "interest_coverage": _serialize(interest_coverage(fin)),
        "roe": _serialize(roe(fin)),
        "roic": _serialize(roic(fin)),
    }


def _quality_bundle(fin: dict | None = None) -> dict:
    fin = fin if isinstance(fin, dict) and fin else EMPTY_STATEMENTS
    return {
        "piotroski": _serialize(piotroski_score(fin)),
        "altman_z": _serialize(altman_z(fin)),
        "beneish_m": _serialize(beneish_m(fin)),
        "dupont": _serialize(dupont(fin)),
    }


def _valuation_bundle(fin: dict | None = None) -> dict:
    live = fin if isinstance(fin, dict) and fin else EMPTY_STATEMENTS
    try:
        from backend.analytics.common import get_number as _get_number

        base_fcf = _get_number(live, "base_fcf")
    except Exception:
        base_fcf = None
    if base_fcf is not None and base_fcf > 0:
        dcf = dcf_sensitivity(
            base_fcf, DCF_DEFAULT_GROWTH,
            list(DCF_DEFAULT_DISCOUNT_RATES),
            list(DCF_DEFAULT_TERMINAL_RATES),
        )
    else:
        dcf = dcf_sensitivity(0, DCF_DEFAULT_GROWTH, [], [])
    return {
        "wacc": _serialize(wacc(live)),
        "dcf_sensitivity": _serialize(dcf),
        "peer_compare": _serialize(peer_compare({}, [])),
    }


@router.get("/{symbol}")
def get_analytics(
    symbol: str,
    indicators: str | None = Query(
        default=None,
        description="Comma-separated overlays, e.g. SMA20,EMA12,RSI14,MACD,BB20,VWAP,ATR14",
    ),
    svc: MarketDataService = Depends(get_market_service),
) -> dict:
    """Deterministic analytics for one symbol (technical live, statements unavailable)."""
    from fastapi import HTTPException as _HTTPException

    sym = (symbol or "").strip().upper()
    if not sym:
        raise _HTTPException(status_code=422, detail="symbol must be a non-empty string")
    # Overlay request parsing (Backend Agent 5 contract): comma-separated,
    # validated; unknown -> 422 with the exact detail string. Blank/omitted
    # means no overlay work (backward compat: no `indicators` key).
    if indicators is None or not str(indicators).strip():
        wanted: list[str] = []
    else:
        try:
            wanted = parse_indicators(indicators)
        except ValueError as exc:
            raise _HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        bars = svc.get_bars(sym, timeframe="1d", limit=BAR_LIMIT)
    except _HTTPException:
        raise
    except Exception as exc:
        try:
            from backend.market_data.providers.base import ProviderError as _PE

            if isinstance(exc, _PE):
                raise _HTTPException(status_code=502, detail=str(exc)) from exc
        except _HTTPException:
            raise
        except Exception:
            pass
        raise _HTTPException(status_code=502, detail=f"analytics bars failed: {exc}") from exc
    rows = bars.get("bars", []) if isinstance(bars, dict) else []
    if not rows:
        raise _HTTPException(status_code=422, detail=f"insufficient history for {sym!r}: 0 bars")
    try:
        frame = pd.DataFrame(
            {
                "open": [r["open"] for r in rows],
                "high": [r["high"] for r in rows],
                "low": [r["low"] for r in rows],
                "close": [r["close"] for r in rows],
                "volume": [float(r["volume"] or 0) for r in rows],
            },
            index=pd.to_datetime([r["ts"] for r in rows]),
        )
    except _HTTPException:
        raise
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=f"analytics frame failed: {exc}") from exc
    if frame.empty or len(frame) < 2:
        raise _HTTPException(status_code=422, detail=f"insufficient history for {sym!r}: {len(frame)} bars")
    provenance = dict(bars.get("provenance", {})) if isinstance(bars, dict) else {}
    # Free statement feed (SEC EDGAR for US, Yahoo annuals for SSE/Euronext
    # + US fallback). Best-effort: any failure keeps today's honest
    # "unavailable" sections (never fabricated, never a 500).
    try:
        mic: str | None = None
        try:
            registry = getattr(svc, "registry", None)
            if registry is not None:
                inst, _, _ = registry.resolve(sym)
                if inst is not None:
                    mic = getattr(inst, "exchange_mic", None)
        except Exception:
            mic = None
        try:
            last_close: float | None = float(frame["close"].iloc[-1])
        except (TypeError, ValueError, IndexError, KeyError):
            last_close = None
        from backend.market_data.statements.resolver import get_statements

        fin_live, stmt_info = get_statements(
            sym, mic, {"price": last_close} if last_close else None
        )
    except Exception:
        fin_live, stmt_info = {}, {"source": None, "reason": "resolver failed"}
    if not isinstance(fin_live, dict) or not fin_live:
        fin_live = {}
    if not isinstance(stmt_info, dict):
        stmt_info = {"source": None}
    out: dict = {
        "symbol": sym,
        "as_of": str(provenance.get("as_of")),
        "provenance": provenance,
        "technical": _technical_bundle(frame),
        "fundamentals": _fundamentals_bundle(fin_live),
        "quality": _quality_bundle(fin_live),
        "valuation": _valuation_bundle(fin_live),
        "statements": _json_safe(stmt_info),
        "note": _statements_note(stmt_info),
        "disclosure": DISCLOSURE,
    }
    if wanted:
        try:
            computed = compute_indicators(frame, wanted, max_points=INDICATOR_MAX_POINTS)
        except ValueError as exc:
            raise _HTTPException(status_code=422, detail=str(exc)) from exc
        payload: dict = dict(computed)
        payload["provenance"] = provenance
        out["indicators"] = payload
    return out
