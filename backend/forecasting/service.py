"""Forecast orchestrator: market_data -> features -> models -> bands (Milestone 3).

Deterministic, no AI, no network. The market-data bars used here are the
offline-capable deterministic stub (seeded by symbol), so runs are
reproducible per symbol/day without any provider call.

Pipeline per (symbol, horizon):
  1. MarketDataService.get_bars(symbol, limit=250) -> OHLCV + provenance.
  2. build_features(OHLCV) (past-only, leakage-safe).
  3. Ensemble direction = mean of available {historical-drift, momentum,
     logistic-direction} probabilities (logistic failure falls back cleanly).
  4. Expected-return range from empirical quantile bands (median -> mid).
  5. Volatility regime + drawdown probability from quantile_bands estimators.
  6. Confidence from member agreement (+ quality-grade downgrade).
  7. Record dict whose keys mirror infra/migrations/0001_initial.sql
     forecasts table columns.

Library code never reads the wall clock for values: `as_of` is taken from
the bars provenance envelope (caller-supplied override wins in tests).
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd

from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.features import (
    FEATURE_VERSION,
    build_extended_features,
    build_features,
    log_returns,
)
from backend.forecasting.features.sse import (
    SSE_FEATURE_VERSION,
    build_sse_features,
    limit_proximity_triggered,
)
from backend.forecasting.features.euronext import (
    EUX_FEATURE_VERSION,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.historical_drift import (
    MODEL_VERSION as HISTORICAL_DRIFT_VERSION,
)
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.logistic import MODEL_VERSION as LOGISTIC_VERSION
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.models.momentum import MODEL_VERSION as MOMENTUM_VERSION
from backend.forecasting.models.quantile_bands import (
    drawdown_probability,
    return_quantiles,
    volatility_regime,
)
from backend.forecasting.models.sse_drift import (
    MODEL_VERSION as SSE_DRIFT_VERSION,
    SseDriftBaseline,
)
from backend.forecasting.models.euronext_drift import (
    MODEL_VERSION as EUX_DRIFT_VERSION,
    EuxDriftBaseline,
)
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.market_data.service import MarketDataService

DISCLOSURE = "Not investment advice"
BAR_LIMIT = 250
#: Member-agreement spread (max(p) - min(p)) for full 3-member "high"
#: confidence. Values unchanged; named so the band lives in one place.
CONFIDENCE_HIGH_MAX_SPREAD = 0.08
#: Spread ceiling for "moderate" confidence (above this -> "low").
CONFIDENCE_MODERATE_MAX_SPREAD = 0.15
#: Drawdown probability at/above which confidence drops one notch.
DRAWDOWN_PENALTY_THRESHOLD = 0.25
#: Volatility regimes that cost one confidence notch (strongest bucket first).
VOLATILITY_PENALTY_REGIMES = frozenset({"high", "elevated", "extreme"})
#: Members needed for "high" confidence (thin ensembles cap at moderate).
FULL_ENSEMBLE_MIN_MODELS = 3
TREND_PERSISTENCE_VERSION = "trend-persistence-v1"
SSE_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{SSE_DRIFT_VERSION}"
EUX_BLEND_VERSION = f"{ENSEMBLE_VERSION}+{EUX_DRIFT_VERSION}"
#: Ensemble member key (probas dict) -> stamped model version. Used to derive
#: model_members/evidence_ids from the members that actually ran (a failed
#: logistic fit must not be listed as evidence).
MEMBER_VERSIONS = {
    "historical-drift": HISTORICAL_DRIFT_VERSION,
    "momentum": MOMENTUM_VERSION,
    "logistic-direction": LOGISTIC_VERSION,
    "trend-persistence": TREND_PERSISTENCE_VERSION,
    "sse-drift": SSE_DRIFT_VERSION,
    "eux-drift": EUX_DRIFT_VERSION,
}
#: Bound on the in-memory record mirror (prevents unbounded growth on
#: long-lived processes; oldest rows are dropped, newest preserved).
MAX_RECORDS = 500
EURONEXT_SUFFIXES = (".PA", ".AS", ".BR")
EURONEXT_MICS = ("XPAR-", "XAMS-", "XBRU-")


def _finite_or_none(value: object) -> float | None:
    """Return ``float(value)`` when finite, else None (JSON-safe sanitize)."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_sse(symbol: str, bars: dict) -> bool:
    """Route helper: True for Shanghai (XSHG) instruments.

    SSE when the user symbol carries the Yahoo ``.SS`` suffix OR the
    resolved ``instrument_id`` carries the ``XSHG-`` prefix (MIC-based).
    """
    sym = (symbol or "").strip().upper()
    if sym.endswith(".SS"):
        return True
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    return inst.startswith("XSHG-")


def _is_euronext(symbol: str, bars: dict) -> bool:
    """Route helper: True for Euronext (XPAR/XAMS/XBRU) instruments.

    Euronext when the user symbol carries a Yahoo ``.PA`` (Paris) /
    ``.AS`` (Amsterdam) / ``.BR`` (Brussels) suffix OR the resolved
    ``instrument_id`` carries an ``XPAR-`` / ``XAMS-`` / ``XBRU-`` prefix
    (MIC-based). Euronext venues share a continuous 09:00-17:30 session
    with no daily price limits, so one ``eux-drift`` path covers all three.
    """
    sym = (symbol or "").strip().upper()
    if sym.endswith(EURONEXT_SUFFIXES):
        return True
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    return inst.startswith(EURONEXT_MICS)


def _penalize_confidence(level: str) -> str:
    """Downgrade confidence one notch: high->moderate->low (low stays low)."""
    mapping = {"high": "moderate", "moderate": "low", "low": "low"}
    return mapping.get(str(level), "low")

_service: ForecastService | None = None  # module singleton (see getter below)

#: Short-TTL in-process forecast cache: (SYMBOL, horizon, data_version) ->
#: (expires_monotonic, payload). Avoids recomputing the 3-model ensemble on
#: horizon tab switches / screener re-renders. Bounded to prevent growth.
_FORECAST_CACHE: dict[tuple[str, int, str], tuple[float, dict]] = {}
_FORECAST_CACHE_TTL_S = 300
_FORECAST_CACHE_MAX = 500


def _forecast_cache_get(symbol: str, horizon: int, data_version: str) -> dict | None:
    try:
        key = (str(symbol).strip().upper(), int(horizon), str(data_version))
    except Exception:
        return None
    try:
        hit = _FORECAST_CACHE.get(key)
    except Exception:
        return None
    if not hit:
        return None
    expires, payload = hit
    if expires < time.monotonic():
        try:
            _FORECAST_CACHE.pop(key, None)
        except Exception:
            pass
        return None
    return payload


def _forecast_cache_set(symbol: str, horizon: int, data_version: str, payload: dict) -> None:
    try:
        key = (str(symbol).strip().upper(), int(horizon), str(data_version))
    except Exception:
        return
    try:
        if len(_FORECAST_CACHE) >= _FORECAST_CACHE_MAX:
            oldest = next(iter(_FORECAST_CACHE))
            _FORECAST_CACHE.pop(oldest, None)
        _FORECAST_CACHE[key] = (time.monotonic() + _FORECAST_CACHE_TTL_S, payload)
    except Exception:
        pass


def clear_forecast_cache() -> None:  # test hook
    try:
        _FORECAST_CACHE.clear()
    except Exception:
        pass
    try:
        _cached_target_date.cache_clear()
    except Exception:
        pass


def _cap_stub_confidence(level: str, provenance: dict) -> str:
    """Cap overconfident labels on synthetic/stale data (accuracy honesty).

    Stub bars (fallback_used=True, grade C) can otherwise serve "high"
    confidence from a seeded random walk. Cap stub/high-stale at moderate;
    D/F still cap at low inside :func:`_confidence`.
    """
    try:
        prov = provenance or {}
        fallback = bool(prov.get("fallback_used"))
        grade = str(prov.get("quality_grade") or "").upper()
    except Exception:
        return level
    if (fallback or grade == "C") and level == "high":
        return "moderate"
    return level


def _trend_persistence_signal(ohlcv: pd.DataFrame) -> float | None:
    """4th ensemble member from past market data (trend persistence).

    Past-only: 63d trailing return scaled by 63d volatility + drawdown
    penalty + RSI persistence. Deterministic, bounded [0, 1], None when
    history is too short or non-finite. Never raises.
    """
    try:
        ext = build_extended_features(ohlcv)
    except Exception:
        return None
    try:
        if len(ext) == 0:
            return None
        last = ext.iloc[-1]
        mom = float(last.get("mom_63", float("nan")))
        vol = float(last.get("vol_63", float("nan")))
        dd = float(last.get("trail_dd_63", 0.0) or 0.0)
        rsi = float(last.get("rsi_14", 50.0) or 50.0)
    except Exception:
        return None
    try:
        import math as _math

        if not (_math.isfinite(mom) and _math.isfinite(vol)):
            return None
        scale = vol if vol > 1e-6 else 0.2
        # Risk-adjusted quarterly drift + drawdown drag + RSI tilt.
        z = (mom / scale) * 2.0 + (dd * 2.0) + ((rsi - 50.0) / 50.0) * 0.5
        z = max(min(z, 6.0), -6.0)
        return 1.0 / (1.0 + _math.exp(-z))
    except Exception:
        return None


def _confidence(
    spread: float,
    n_models: int,
    quality_grade: str,
    regime: str | None = None,
    drawdown_prob: float | None = None,
) -> str:
    """Deterministic confidence from member agreement.

    spread = max(p) - min(p) over ensemble members. Full agreement + 3
    members -> high; moderate agreement -> moderate; else low. Thin
    ensembles (fallback) cap at moderate; poor data quality caps at low.

    Regime-aware penalties (applied AFTER the spread/size/quality logic
    via :func:`_penalize_confidence`, never raising, flooring at "low"):
      * volatility regime "high" (top-tercile trailing vol, the strongest
        bucket quantile_bands emits), "elevated" or "extreme" -> one notch.
      * drawdown probability >= 0.25 -> one notch down.
    ``regime=None`` / ``drawdown_prob=None`` are no-ops (backward compat).
    """
    if n_models <= 1:
        level = "low"
    elif spread <= CONFIDENCE_HIGH_MAX_SPREAD and n_models >= FULL_ENSEMBLE_MIN_MODELS:
        level = "high"
    elif spread <= CONFIDENCE_MODERATE_MAX_SPREAD:
        level = "moderate"
    else:
        level = "low"
    if n_models < FULL_ENSEMBLE_MIN_MODELS and level == "high":
        level = "moderate"
    if str(quality_grade).upper() in ("D", "F") and level != "low":
        level = "low"
    if regime is not None:
        try:
            _r = str(regime).strip().lower()
        except Exception:
            _r = ""
        if _r in VOLATILITY_PENALTY_REGIMES:
            level = _penalize_confidence(level)
    _dd = drawdown_prob
    if isinstance(_dd, bool):
        _dd = None
    if isinstance(_dd, (int, float)):
        try:
            if float(_dd) >= DRAWDOWN_PENALTY_THRESHOLD:
                level = _penalize_confidence(level)
        except (TypeError, ValueError):
            pass
    return level


def _target_mic(symbol: str, bars: dict, sse: bool, eux: bool) -> str:
    """Exchange MIC for trading-day arithmetic (suffix/instrument-derived)."""
    if sse:
        return "XSHG"
    sym = (symbol or "").strip().upper()
    for suffix, mic in ((".PA", "XPAR"), (".AS", "XAMS"), (".BR", "XBRU")):
        if sym.endswith(suffix):
            return mic
    try:
        inst = str((bars or {}).get("instrument_id") or "").upper()
    except Exception:
        inst = ""
    for mic in ("XPAR", "XAMS", "XBRU", "XNYS", "XSHG"):
        if inst.startswith(mic + "-"):
            return mic
    return "XNAS"


@lru_cache(maxsize=512)
def _cached_target_date(as_of_iso: str, horizon_days: int, mic: str = "XNAS") -> str:
    """Memoized trading-day target (session-calendar lookups dominate)."""
    return _target_date(str(as_of_iso), int(horizon_days), str(mic))


def _target_date(as_of_iso: str, horizon_days: int, mic: str = "XNAS") -> str:
    """Advance ``horizon_days`` TRADING days (horizons are trading days).

    Counts sessions via the exchange calendar (weekends/holidays skipped);
    falls back to calendar days only when the calendar is unavailable.
    """
    try:
        base = datetime.fromisoformat(str(as_of_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        horizon = int(horizon_days)
    except (TypeError, ValueError):
        horizon = 0
    if horizon <= 0:
        return base.date().isoformat()
    try:
        from backend.instruments.calendars import _lib_is_session

        day = base.date()
        counted, guard = 0, 0
        while counted < horizon and guard < horizon * 4 + 30:
            guard += 1
            day += timedelta(days=1)
            session = _lib_is_session(day, mic)
            if session is None:
                # Calendar unavailable (e.g. XSHG stub decision): weekdays
                # approximate sessions; still closer than calendar days.
                if day.weekday() < 5:
                    counted += 1
            elif session:
                counted += 1
        if counted >= horizon:
            return day.isoformat()
    except Exception:
        pass
    return (base + timedelta(days=horizon)).date().isoformat()


def _data_version(provenance: dict) -> str:
    source = str(provenance.get("source", "unknown"))
    as_of = str(provenance.get("as_of", "unknown"))
    day = as_of[:10] if len(as_of) >= 10 else as_of
    return f"{source}-bars-{day}"


def _deterministic_id(symbol: str, horizon: int, as_of: str, data_version: str) -> str:
    material = "|".join([symbol.upper(), str(horizon), as_of, data_version])
    digest = hashlib.sha256(material.encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


class ForecastService:
    """Stateless orchestrator (an in-memory record list mirrors the DB table)."""

    def __init__(self, market_service: MarketDataService | None = None) -> None:
        self.market = market_service or MarketDataService()
        self.records: list[dict] = []

    # -- internals ------------------------------------------------------
    def _load(self, symbol: str) -> tuple[pd.DataFrame, dict]:
        # Harden: validate symbol early (ValueError, never AttributeError)
        # and coerce malformed bar rows to ValueError (never KeyError).
        # Single-pass frame build (one loop over rows, not six).
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        bars = self.market.get_bars(symbol.strip().upper(), timeframe="1d", limit=BAR_LIMIT)
        rows = bars.get("bars", [])
        if len(rows) < 100:
            raise ValueError(f"insufficient history for {symbol!r}: {len(rows)} bars")
        try:
            recs: list[tuple] = []
            for r in rows:
                if not isinstance(r, dict):
                    raise ValueError("malformed bar row")
                recs.append((
                    r["open"], r["high"], r["low"], r["close"],
                    float((r.get("volume")) or 0),
                    r.get("ts"),
                ))
            index = pd.to_datetime([t for _, _, _, _, _, t in recs])
            frame = pd.DataFrame(
                {
                    "open": [o for o, _, _, _, _, _ in recs],
                    "high": [h for _, h, _, _, _, _ in recs],
                    "low": [lo for _, _, lo, _, _, _ in recs],
                    "close": [c for _, _, _, c, _, _ in recs],
                    "volume": [v for _, _, _, _, v, _ in recs],
                },
                index=index,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"malformed bars for {symbol!r}: {type(exc).__name__}") from exc
        return frame, bars

    def _resolve_versions(
        self, symbol: str, bars: dict, provenance: dict, stamp: str
    ) -> tuple[str, bool, bool, str]:
        base_data_version = _data_version({**provenance, "as_of": stamp})
        sse = _is_sse(symbol, bars)
        eux = _is_euronext(symbol, bars) and not sse
        if sse:
            data_version = f"{base_data_version}-sse"
        elif eux:
            data_version = f"{base_data_version}-eux"
        else:
            data_version = base_data_version
        return base_data_version, sse, eux, data_version

    # -- public ---------------------------------------------------------
    def forecast(
        self, symbol: str, horizon: int, as_of: str | None = None
    ) -> dict:
        try:
            horizon = int(horizon)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon!r}") from exc
        if horizon not in FORECAST_HORIZONS:
            raise ValueError(f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        sym_upper = symbol.strip().upper()
        ohlcv, bars = self._load(symbol)
        provenance = dict(bars.get("provenance", {}))
        stamp = as_of or str(provenance.get("as_of"))
        base_data_version = _data_version({**provenance, "as_of": stamp})
        sse = _is_sse(symbol, bars)
        eux = _is_euronext(symbol, bars) and not sse
        if sse:
            data_version = f"{base_data_version}-sse"
        elif eux:
            data_version = f"{base_data_version}-eux"
        else:
            data_version = base_data_version
        # Short-TTL cache: horizon tab switches + screener re-renders skip
        # the ensemble recompute (key includes data_version day stamp).
        if as_of is None:
            try:
                cached = _forecast_cache_get(sym_upper, horizon, data_version)
            except Exception:
                cached = None
            if isinstance(cached, dict) and cached.get("horizon_days") == horizon:
                return cached
        closes = ohlcv["close"]
        features = build_features(ohlcv)

        # -- direction ensemble (drift + momentum + logistic) ------------
        lret = log_returns(closes).dropna()
        drift_p = float(
            HistoricalDriftBaseline()
            .fit(lret)
            .direction_probability(horizon, as_of=stamp, data_version=data_version)
            .value
        )
        momentum_p = float(
            MomentumBaseline()
            .fit(closes)
            .direction_probability(horizon, as_of=stamp, data_version=data_version)
            .value
        )
        probas: dict[str, float] = {
            "historical-drift": drift_p,
            "momentum": momentum_p,
        }
        formulas: dict[str, str] = {}
        try:
            logreg = LogisticDirectionModel(horizons=[horizon]).fit(features, closes)
            logistic_p = float(
                logreg.predict_direction_proba(
                    features.iloc[[-1]], as_of=stamp, data_version=data_version
                )[horizon].value
            )
            probas["logistic-direction"] = logistic_p
        except (ValueError, ImportError):
            pass  # single-class / too-few-rows / sklearn missing: drift+momentum
        # 4th member: trend-persistence from past market data (63d trend +
        # drawdown + RSI persistence). Additive; failure skips cleanly so
        # thin ensembles keep their existing behavior.
        try:
            trend_p = _trend_persistence_signal(ohlcv)
            if trend_p is not None and 0.0 <= float(trend_p) <= 1.0:
                probas["trend-persistence"] = float(trend_p)
        except Exception:
            pass
        members = sorted(probas)
        if probas:
            us_direction = float(sum(probas.values()) / len(probas))
            us_spread = float(max(probas.values()) - min(probas.values()))
        else:  # defensive: fits below always seed >= 2 members; never empty
            us_direction, us_spread = 0.5, 0.0

        # -- return range / regime / drawdown ----------------------------
        band = return_quantiles(
            closes, horizons=[horizon], as_of=stamp, data_version=data_version
        )[horizon].value
        regime_res = volatility_regime(closes, as_of=stamp, data_version=data_version)
        dd_res = drawdown_probability(
            closes, horizon, as_of=stamp, data_version=data_version
        )
        us_range = {
            "low": float(band["low"]),
            "mid": float(band["median"]),
            "high": float(band["high"]),
            "lower_q": float(band["lower_q"]),
            "upper_q": float(band["upper_q"]),
            "n_windows": int(band["n_windows"]),
        }
        regime = str(regime_res.value["regime"])
        dd_prob = float(dd_res.value["probability"])

        if sse:
            # -- SSE path: blend US ensemble 50/50 with SSE drift ---------
            # sse-drift sees the same trailing log-returns (winsorized
            # internally to the +/-10% limit band) with wider tails.
            sse_model = SseDriftBaseline().fit(lret)
            sse_p = float(
                sse_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["sse-drift"] = sse_p
            direction = float((us_direction + sse_p) / 2.0)
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the wider SSE drift band; mid is the mean of mids.
            sse_band = sse_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            expected_range = {
                "low": float(min(us_range["low"], sse_band["low"])),
                "mid": float((us_range["mid"] + float(sse_band["mid"])) / 2.0),
                "high": float(max(us_range["high"], sse_band["high"])),
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
            }
            try:
                sse_features = build_sse_features(ohlcv)
                proximity_fired = limit_proximity_triggered(sse_features)
            except ValueError:
                proximity_fired = False
            base_confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            confidence = (
                _penalize_confidence(base_confidence)
                if proximity_fired
                else base_confidence
            )
            model_version = SSE_BLEND_VERSION
            feature_version = SSE_FEATURE_VERSION
        elif eux:
            # -- Euronext path: blend US ensemble 50/50 with eux drift --
            # eux-drift sees the same trailing log-returns (winsorized
            # internally to the +/-15% robustness cap; Euronext has no
            # hard daily limits) with a z=1.15 band. No limit-proximity
            # penalty applies (no limits); Euronext features are built to
            # stamp the eux feature version.
            eux_model = EuxDriftBaseline().fit(lret)
            eux_p = float(
                eux_model.direction_probability(
                    horizon, as_of=stamp, data_version=data_version
                ).value
            )
            probas["eux-drift"] = eux_p
            direction = float((us_direction + eux_p) / 2.0)
            spread = float(max(probas.values()) - min(probas.values())) if probas else 0.0
            # Union of ranges: conservative envelope of the US empirical
            # band and the eux drift band; mid is the mean of mids.
            eux_band = eux_model.expected_return_range(
                horizon, as_of=stamp, data_version=data_version
            ).value
            expected_range = {
                "low": float(min(us_range["low"], eux_band["low"])),
                "mid": float((us_range["mid"] + float(eux_band["mid"])) / 2.0),
                "high": float(max(us_range["high"], eux_band["high"])),
                "lower_q": float(us_range["lower_q"]),
                "upper_q": float(us_range["upper_q"]),
                "n_windows": int(us_range["n_windows"]),
            }
            # NOTE: the EUX feature frame is version-stamped via
            # EUX_FEATURE_VERSION above; its values are not consumed by the
            # drift blend, so no second feature build is needed here.
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            model_version = EUX_BLEND_VERSION
            feature_version = EUX_FEATURE_VERSION
        else:
            direction = us_direction
            spread = us_spread
            expected_range = us_range
            confidence = _confidence(
                spread,
                len(probas),
                str(provenance.get("quality_grade", "B")),
                regime,
                dd_prob,
            )
            model_version = ENSEMBLE_VERSION
            feature_version = FEATURE_VERSION
        # Honesty cap: stub/stale (fallback or grade C) never serves "high".
        try:
            confidence = _cap_stub_confidence(confidence, provenance)
        except Exception:
            pass

        # -- JSON safety: non-finite floats are invalid JSON (NaN/inf) ----
        # Finite inputs pass through bit-identical; only pathological model
        # outputs (e.g. exp() overflow on extreme synthetic drift) map to
        # None instead of leaking NaN/inf to the wire/DB.
        _clean_direction = _finite_or_none(direction)
        if _clean_direction is None:
            raise ValueError("non-finite direction probability")
        direction = _clean_direction
        dd_prob = _finite_or_none(dd_prob)
        for _bound in ("low", "mid", "high"):
            expected_range[_bound] = _finite_or_none(expected_range.get(_bound))
        for _name, _value in list(probas.items()):
            _clean_member = _finite_or_none(_value)
            # JSON safety: non-finite members sanitize to None (never NaN/inf).
            # Reachable only on pathological model output; direction already
            # validated finite above, so this never changes valid ensembles.
            probas[_name] = _clean_member
        # Evidence = members that actually ran (a failed/skipped fit is never
        # listed: its version would otherwise render as contributing evidence).
        model_members = [
            MEMBER_VERSIONS[name]
            for name in sorted(probas)
            if probas[name] is not None and name in MEMBER_VERSIONS
        ]

        instrument_id = bars.get("instrument_id") or f"stub-{symbol.strip().upper()}"
        target_date = _cached_target_date(
            stamp, horizon, _target_mic(symbol, bars, sse, eux)
        )
        record = {
            "forecast_id": _deterministic_id(symbol, horizon, stamp, data_version),
            "instrument_id": instrument_id,
            "horizon_days": horizon,
            "target_date": target_date,
            "direction_prob": direction,
            "expected_ret_low": expected_range["low"],
            "expected_ret_high": expected_range["high"],
            "volatility_regime": regime,
            "drawdown_prob": dd_prob,
            "confidence": confidence,
            "model_version": model_version,
            "feature_version": feature_version,
            "data_version": data_version,
            "ai_provider": None,
            "ai_model": None,
            "ai_weight": 0,
            "provenance": provenance,
            "created_at": stamp,
        }
        self.records.append(record)
        if len(self.records) > MAX_RECORDS:
            del self.records[: len(self.records) - MAX_RECORDS]

        payload = {
            "symbol": symbol.strip().upper(),
            "horizon_days": horizon,
            "direction_probability": direction,
            "expected_return_range": expected_range,
            "volatility_regime": regime,
            "drawdown_probability": dd_prob,
            "confidence": confidence,
            "model_version": model_version,
            "model_members": model_members,
            "components": dict(probas),
            "formulas": dict(formulas),
            "feature_version": feature_version,
            "data_version": data_version,
            "as_of": stamp,
            "target_date": target_date,
            "provenance": provenance,
            "disclosure": DISCLOSURE,
            "record": record,
        }
        if as_of is None:
            try:
                _forecast_cache_set(sym_upper, horizon, data_version, payload)
            except Exception:
                pass
        return payload

    def forecast_all(self, symbol: str, as_of: str | None = None) -> dict[int, dict]:
        """All horizons with ONE bars load + ONE feature build.

        Shared: bars, OHLCV frame, v1 features, log-returns, quantile bands
        (single multi-horizon call), volatility regime (horizon-independent),
        trend-persistence signal and the multi-horizon logistic fit. Per
        horizon only the cheap drift/momentum scalars, venue-drift blend,
        drawdown probability and record assembly remain.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError(f"symbol must be a non-empty string, got {symbol!r}")
        sym_upper = symbol.strip().upper()
        ohlcv, bars = self._load(symbol)
        provenance = dict(bars.get("provenance", {}))
        stamp = as_of or str(provenance.get("as_of"))
        _, sse, eux, data_version = self._resolve_versions(symbol, bars, provenance, stamp)
        # Cache fast path: all three horizons warm -> return without recompute.
        if as_of is None:
            try:
                warm = {
                    h: _forecast_cache_get(sym_upper, int(h), data_version)
                    for h in FORECAST_HORIZONS
                }
                if all(isinstance(v, dict) for v in warm.values()):
                    return {int(h): v for h, v in warm.items()}  # type: ignore[misc]
            except Exception:
                pass
        closes = ohlcv["close"]
        features = build_features(ohlcv)
        lret = log_returns(closes).dropna()
        try:
            bands_all = return_quantiles(
                closes, horizons=list(FORECAST_HORIZONS),
                as_of=stamp, data_version=data_version,
            )
        except Exception:
            bands_all = {}
        try:
            regime_res = volatility_regime(closes, as_of=stamp, data_version=data_version)
            regime = str(regime_res.value["regime"])
        except Exception:
            regime_res, regime = None, "normal"
        try:
            trend_p = _trend_persistence_signal(ohlcv)
            if trend_p is not None and not 0.0 <= float(trend_p) <= 1.0:
                trend_p = None
        except Exception:
            trend_p = None
        try:
            logreg_all = LogisticDirectionModel(
                horizons=list(FORECAST_HORIZONS)
            ).fit(features, closes)
        except (ValueError, ImportError):
            logreg_all = None
        try:
            drift_model = HistoricalDriftBaseline().fit(lret)
        except ValueError:
            drift_model = None
        try:
            momentum_model = MomentumBaseline().fit(closes)
        except ValueError:
            momentum_model = None
        out: dict[int, dict] = {}
        for horizon in FORECAST_HORIZONS:
            horizon = int(horizon)
            probas: dict[str, float] = {}
            if drift_model is not None:
                try:
                    probas["historical-drift"] = float(
                        drift_model.direction_probability(
                            horizon, as_of=stamp, data_version=data_version
                        ).value
                    )
                except ValueError:
                    pass
            if momentum_model is not None:
                try:
                    probas["momentum"] = float(
                        momentum_model.direction_probability(
                            horizon, as_of=stamp, data_version=data_version
                        ).value
                    )
                except ValueError:
                    pass
            if logreg_all is not None:
                try:
                    probas["logistic-direction"] = float(
                        logreg_all.predict_direction_proba(
                            features.iloc[[-1]], as_of=stamp,
                            data_version=data_version,
                        )[horizon].value
                    )
                except (ValueError, IndexError, KeyError):
                    pass
            if trend_p is not None:
                probas["trend-persistence"] = float(trend_p)
            if probas:
                us_direction = float(sum(probas.values()) / len(probas))
            else:
                us_direction = 0.5
            band_obj = bands_all.get(horizon) if isinstance(bands_all, dict) else None
            try:
                band = band_obj.value if band_obj is not None else None
                us_range = {
                    "low": float(band["low"]),
                    "mid": float(band["median"]),
                    "high": float(band["high"]),
                    "lower_q": float(band["lower_q"]),
                    "upper_q": float(band["upper_q"]),
                    "n_windows": int(band["n_windows"]),
                }
            except Exception:
                # Fallback: single-horizon band (mirrors forecast()).
                single = return_quantiles(
                    closes, horizons=[horizon], as_of=stamp,
                    data_version=data_version,
                )[horizon].value
                us_range = {
                    "low": float(single["low"]),
                    "mid": float(single["median"]),
                    "high": float(single["high"]),
                    "lower_q": float(single["lower_q"]),
                    "upper_q": float(single["upper_q"]),
                    "n_windows": int(single["n_windows"]),
                }
            try:
                dd_prob = float(
                    drawdown_probability(
                        closes, horizon, as_of=stamp, data_version=data_version
                    ).value["probability"]
                )
            except Exception:
                dd_prob = float("nan")
            if sse:
                sse_model = SseDriftBaseline().fit(lret)
                sse_p = float(
                    sse_model.direction_probability(
                        horizon, as_of=stamp, data_version=data_version
                    ).value
                )
                probas["sse-drift"] = sse_p
                direction = float((us_direction + sse_p) / 2.0)
                sse_band = sse_model.expected_return_range(
                    horizon, as_of=stamp, data_version=data_version
                ).value
                expected_range = {
                    "low": float(min(us_range["low"], sse_band["low"])),
                    "mid": float((us_range["mid"] + float(sse_band["mid"])) / 2.0),
                    "high": float(max(us_range["high"], sse_band["high"])),
                    "lower_q": float(us_range["lower_q"]),
                    "upper_q": float(us_range["upper_q"]),
                    "n_windows": int(us_range["n_windows"]),
                }
                try:
                    sse_features = build_sse_features(ohlcv)
                    proximity_fired = limit_proximity_triggered(sse_features)
                except ValueError:
                    proximity_fired = False
                base_conf = _confidence(
                    float(max(probas.values()) - min(probas.values())) if probas else 0.0,
                    len(probas), str(provenance.get("quality_grade", "B")),
                    regime, dd_prob,
                )
                confidence = _penalize_confidence(base_conf) if proximity_fired else base_conf
                model_version, feature_version = SSE_BLEND_VERSION, SSE_FEATURE_VERSION
            elif eux:
                eux_model = EuxDriftBaseline().fit(lret)
                eux_p = float(
                    eux_model.direction_probability(
                        horizon, as_of=stamp, data_version=data_version
                    ).value
                )
                probas["eux-drift"] = eux_p
                direction = float((us_direction + eux_p) / 2.0)
                eux_band = eux_model.expected_return_range(
                    horizon, as_of=stamp, data_version=data_version
                ).value
                expected_range = {
                    "low": float(min(us_range["low"], eux_band["low"])),
                    "mid": float((us_range["mid"] + float(eux_band["mid"])) / 2.0),
                    "high": float(max(us_range["high"], eux_band["high"])),
                    "lower_q": float(us_range["lower_q"]),
                    "upper_q": float(us_range["upper_q"]),
                    "n_windows": int(us_range["n_windows"]),
                }
                confidence = _confidence(
                    float(max(probas.values()) - min(probas.values())) if probas else 0.0,
                    len(probas), str(provenance.get("quality_grade", "B")),
                    regime, dd_prob,
                )
                model_version, feature_version = EUX_BLEND_VERSION, EUX_FEATURE_VERSION
            else:
                direction = us_direction
                expected_range = us_range
                confidence = _confidence(
                    float(max(probas.values()) - min(probas.values())) if probas else 0.0,
                    len(probas), str(provenance.get("quality_grade", "B")),
                    regime, dd_prob,
                )
                model_version, feature_version = ENSEMBLE_VERSION, FEATURE_VERSION
            try:
                confidence = _cap_stub_confidence(confidence, provenance)
            except Exception:
                pass
            clean_direction = _finite_or_none(direction)
            if clean_direction is None:
                raise ValueError("non-finite direction probability")
            direction = clean_direction
            dd_prob = _finite_or_none(dd_prob)
            for bound in ("low", "mid", "high"):
                expected_range[bound] = _finite_or_none(expected_range.get(bound))
            for name, value in list(probas.items()):
                probas[name] = _finite_or_none(value)
            model_members = [
                MEMBER_VERSIONS[name]
                for name in sorted(probas)
                if probas[name] is not None and name in MEMBER_VERSIONS
            ]
            instrument_id = bars.get("instrument_id") or f"stub-{sym_upper}"
            target_date = _cached_target_date(
                stamp, horizon, _target_mic(symbol, bars, sse, eux)
            )
            record = {
                "forecast_id": _deterministic_id(symbol, horizon, stamp, data_version),
                "instrument_id": instrument_id,
                "horizon_days": horizon,
                "target_date": target_date,
                "direction_prob": direction,
                "expected_ret_low": expected_range["low"],
                "expected_ret_high": expected_range["high"],
                "volatility_regime": regime,
                "drawdown_prob": dd_prob,
                "confidence": confidence,
                "model_version": model_version,
                "feature_version": feature_version,
                "data_version": data_version,
                "ai_provider": None,
                "ai_model": None,
                "ai_weight": 0,
                "provenance": provenance,
                "created_at": stamp,
            }
            self.records.append(record)
            if len(self.records) > MAX_RECORDS:
                del self.records[: len(self.records) - MAX_RECORDS]
            payload = {
                "symbol": sym_upper,
                "horizon_days": horizon,
                "direction_probability": direction,
                "expected_return_range": expected_range,
                "volatility_regime": regime,
                "drawdown_probability": dd_prob,
                "confidence": confidence,
                "model_version": model_version,
                "model_members": model_members,
                "components": dict(probas),
                "formulas": {},
                "feature_version": feature_version,
                "data_version": data_version,
                "as_of": stamp,
                "target_date": target_date,
                "provenance": provenance,
                "disclosure": DISCLOSURE,
                "record": record,
            }
            if as_of is None:
                try:
                    _forecast_cache_set(sym_upper, horizon, data_version, payload)
                except Exception:
                    pass
            out[int(horizon)] = payload
        return out


def get_forecast_service() -> ForecastService:
    global _service
    if _service is None:
        try:
            from backend.api.deps import get_market_service

            _service = ForecastService(market_service=get_market_service())
        except Exception:
            _service = ForecastService()
    return _service


def reset_forecast_service() -> None:  # test hook
    global _service
    _service = None
    try:
        clear_forecast_cache()
    except Exception:
        pass


__all__ = [
    "DISCLOSURE",
    "TREND_PERSISTENCE_VERSION",
    "ForecastService",
    "clear_forecast_cache",
    "get_forecast_service",
    "reset_forecast_service",
]
