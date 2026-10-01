"""Forecast service: daily bars in, v4 forecasts out.

Per symbol and horizon (1, 7, 14, 21 trading days):

* **Return range** — quantiles of the h-day return from the volatility model
  (log-HAR, pooled). Walk-forward, the 80% band contains ~80% of outcomes.
* **Drop risk** — probability of a 10%+ close-to-close drop within h days.
* **Outperformance** — probability of beating the median S&P 500 stock over
  h days, and the stock's percentile among the universe (US listings only).
* **Chance of rising** — the historical base rate, tilted by the model only
  as far as walk-forward testing supports.

Every number ships with its measured record from the active model bundle
(see :mod:`backend.forecasting.v4`). Nothing is fitted per request.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pandas as pd

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.v4 import features as F
from backend.forecasting.v4.serve import forecast_frame
from backend.forecasting.v4.store import get_bundle, latest_cross_section
from backend.market_data.frames import bars_frame, market_symbol_for, venue_of
from backend.market_data.service import MarketDataService

DISCLOSURE = (
    "Model output, not investment advice. The return range and drop risk are "
    "well calibrated in walk-forward tests; the outperformance ranking has a "
    "small measured edge; the chance of rising is close to the historical base "
    "rate because no model reliably beats it. Past accuracy does not guarantee "
    "future results."
)
VALIDATION_STATUS = "measured"
FEATURE_VERSION = "v4-features-1"
#: Bars loaded per symbol: 253 sessions are needed for 1-year features; the
#: margin absorbs holidays and duplicate-session rows in older databases.
BAR_LIMIT = 520
_MARKET_TTL_S = 300
_FORECAST_TTL_S = 300
_FORECAST_CACHE_MAX = 1000


class ForecastUnavailable(ValueError):
    """No forecast can be made honestly (no model, too little history)."""


def _target_date(as_of_iso: str, horizon_days: int, mic: str = "XNAS") -> str:
    """Advance ``horizon_days`` trading days on the exchange calendar."""
    try:
        base = datetime.fromisoformat(str(as_of_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        base = datetime.now(timezone.utc)
    day = base.date()
    try:
        from backend.instruments.calendars import _lib_is_session

        counted, guard = 0, 0
        while counted < horizon_days and guard < horizon_days * 4 + 30:
            guard += 1
            day += timedelta(days=1)
            session = _lib_is_session(day, mic)
            if session is None:
                if day.weekday() < 5:
                    counted += 1
            elif session:
                counted += 1
        if counted >= horizon_days:
            return day.isoformat()
    except Exception:  # calendar library unavailable: calendar days
        pass
    return (base.date() + timedelta(days=horizon_days)).isoformat()


def _data_version(provenance: dict, as_of: str) -> str:
    return f"{provenance.get('source', 'unknown')}-bars-{as_of}"


def _deterministic_id(symbol: str, horizon: int, as_of: str, version: str) -> str:
    digest = hashlib.sha256("|".join([symbol, str(horizon), as_of, version]).encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def signal_strength(rank: float | None) -> str | None:
    """How far into the tails of today's universe a stock's score sits."""
    if rank is None:
        return None
    tail = max(rank, 1 - rank)
    if tail >= 0.9:
        return "strong"
    if tail >= 0.75:
        return "moderate"
    return "weak"


def confidence_label(res: dict, horizon_report: dict) -> str:
    """'moderate' only for a strong relative signal at a horizon whose ranking
    edge is statistically significant (IC t >= 2); otherwise 'low'. Never
    'high': no measured edge here justifies it."""
    out = horizon_report.get("out") or {}
    t = out.get("ic_t")
    if res.get("relative_available") and signal_strength(res.get("out_rank")) == "strong" and (t or 0) >= 2:
        return "moderate"
    return "low"


def measured_for(horizon_report: dict) -> dict:
    """The record shown next to each number (compact)."""
    rng = horizon_report.get("range") or {}
    dd = horizon_report.get("drop_risk") or {}
    up = horizon_report.get("up") or {}
    out = horizon_report.get("out") or {}
    deciles = out.get("deciles") or []
    spread = None
    if len(deciles) >= 2:
        spread = deciles[-1]["mean_fwd_log_return"] - deciles[0]["mean_fwd_log_return"]
    return {
        "range_coverage_80": rng.get("coverage_80"),
        "range_points": rng.get("n"),
        "drop_risk_skill": dd.get("skill"),
        "up_skill": up.get("skill"),
        "up_skill_ci95": up.get("skill_ci95"),
        "up_points": up.get("points"),
        "out_ic": out.get("ic_mean"),
        "out_ic_t": out.get("ic_t"),
        "out_decile_spread": round(spread, 5) if spread is not None else None,
        "out_hit_rate": out.get("hit_rate"),
    }


class ForecastService:
    """Loads bars, computes v4 forecasts for every horizon, caches briefly."""

    def __init__(self, market_service: MarketDataService | None = None) -> None:
        self.market = market_service or MarketDataService()
        self._lock = threading.Lock()
        self._markets: dict[str, tuple[float, pd.DataFrame | None]] = {}
        self._cache: dict[tuple[str, str], tuple[float, dict]] = {}

    # -- data ---------------------------------------------------------------
    def _load(self, symbol: str) -> tuple[pd.DataFrame, dict, str]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        sym = symbol.strip().upper()
        payload = self.market.get_bars(sym, timeframe="1d", limit=BAR_LIMIT)
        frame = bars_frame(payload)
        if len(frame) < F.WARMUP:
            raise ForecastUnavailable(
                f"{sym} needs {F.WARMUP} daily bars for a forecast; {len(frame)} are stored")
        return frame, payload, venue_of(payload, sym)

    def _market_frame(self, mic: str) -> pd.DataFrame | None:
        sym = market_symbol_for(mic)
        now = time.monotonic()
        with self._lock:
            hit = self._markets.get(sym)
            if hit and now - hit[0] < _MARKET_TTL_S:
                return hit[1]
        try:
            frame = bars_frame(self.market.get_bars(sym, timeframe="1d", limit=BAR_LIMIT))
            frame = frame if len(frame) >= F.WARMUP else None
        except Exception:
            frame = None  # market features fall back to their training medians
        with self._lock:
            self._markets[sym] = (now, frame)
        return frame

    # -- public -------------------------------------------------------------
    def forecast_all(self, symbol: str) -> dict[int, dict]:
        """Every horizon for one symbol (one bars load, cached ~5 minutes)."""
        bundle = get_bundle()
        if bundle is None:
            raise ForecastUnavailable("no forecast model is installed (run scripts/train_models.py)")
        sym = symbol.strip().upper()
        key = (sym, bundle.version)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < _FORECAST_TTL_S:
                return hit[1]
        frame, payload, mic = self._load(sym)
        market = self._market_frame(mic)
        cs = latest_cross_section(bundle)
        sector = bundle.sectors.get(sym.replace(".", "-")) or bundle.sectors.get(sym)
        raw = forecast_frame(frame, market, bundle, cs, sector=sector, mic=mic, horizons=list(FORECAST_HORIZONS))
        provenance = dict(payload.get("provenance") or {})
        provenance["granularity"] = "1d"
        if market is None:
            provenance.setdefault("missing_fields", [])
            provenance["missing_fields"] = list(provenance["missing_fields"]) + [f"market series {market_symbol_for(mic)}"]
        out = {h: self._payload(sym, mic, res, bundle, provenance) for h, res in raw.items()}
        with self._lock:
            if len(self._cache) >= _FORECAST_CACHE_MAX:
                self._cache.clear()
            self._cache[key] = (now, out)
        return out

    def forecast(self, symbol: str, horizon: int, **_legacy) -> dict:
        h = int(horizon)
        if h not in FORECAST_HORIZONS:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
        return self.forecast_all(symbol)[h]

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._markets.clear()

    # -- payload -------------------------------------------------------------
    def _payload(self, sym: str, mic: str, res: dict, bundle, provenance: dict) -> dict:
        h = int(res["horizon_days"])
        hr = (bundle.report.get("horizons") or {}).get(str(h), {})
        q = res["quantiles"]
        pq = res["price_quantiles"]
        as_of = res["as_of"]
        data_version = _data_version(provenance, as_of)
        target_date = _target_date(as_of, h, mic)
        confidence = confidence_label(res, hr)
        payload = {
            "symbol": sym,
            "exchange_mic": mic,
            "horizon_days": h,
            "as_of": as_of,
            "target_date": target_date,
            # Chance of rising (base rate tilted by the model).
            "direction_probability": round(res["p_up"], 5),
            "direction_probability_raw": round(res["p_up_raw"], 5),
            "base_rate": round(res["base_up"], 5),
            # Outperformance vs. the median S&P 500 stock.
            "outperform_probability": round(res["p_out"], 5) if res["p_out"] is not None else None,
            "outperform_rank": round(res["out_rank"], 4) if res["out_rank"] is not None else None,
            "relative_available": bool(res["relative_available"]),
            "signal_strength": signal_strength(res["out_rank"]),
            "drivers": res["drivers"],
            # Range and risk.
            "expected_return_range": {
                "low": q["0.10"], "mid": q["0.50"], "high": q["0.90"],
                "coverage": "80% (volatility model)",
            },
            "quantiles": {k: round(v, 6) for k, v in q.items()},
            "price_quantiles": {k: round(v, 4) for k, v in pq.items()},
            "target_price": {"last_close": res["last_close"], "low": pq["0.10"], "mid": pq["0.50"], "high": pq["0.90"]},
            "volatility_forecast_annual": round(res["vol_annual_forecast"], 5),
            "volatility_regime": res["regime"],
            "drawdown_probability": round(res["drawdown_prob"], 5),
            "drawdown_detail": {"threshold": res["drawdown_threshold"], "horizon_days": h},
            "confidence": confidence,
            "measured": measured_for(hr),
            "features": res["features"],
            "model_version": bundle.version,
            "feature_version": FEATURE_VERSION,
            "data_version": data_version,
            "cross_section_as_of": res["cross_section_as_of"],
            "provenance": provenance,
            "disclosure": DISCLOSURE,
            "validation_status": VALIDATION_STATUS,
        }
        payload["record"] = {
            "forecast_id": _deterministic_id(sym, h, as_of, bundle.version),
            "symbol": sym, "horizon_days": h, "target_date": target_date,
            "direction_prob": payload["direction_probability"],
            "expected_ret_low": q["0.10"], "expected_ret_high": q["0.90"],
            "volatility_regime": res["regime"], "drawdown_prob": payload["drawdown_probability"],
            "confidence": confidence, "model_version": bundle.version,
            "feature_version": FEATURE_VERSION, "data_version": data_version,
            "provenance": provenance,
        }
        return payload


_service: ForecastService | None = None
_service_lock = threading.Lock()


def get_forecast_service() -> ForecastService:
    global _service
    with _service_lock:
        if _service is None:
            try:
                from backend.api.deps import get_market_service

                _service = ForecastService(market_service=get_market_service())
            except Exception:
                _service = ForecastService()
        return _service


def reset_forecast_service() -> None:  # test hook
    global _service
    with _service_lock:
        _service = None


def clear_forecast_cache() -> None:  # test hook
    if _service is not None:
        _service.clear_cache()


def finite(x) -> float | None:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None
