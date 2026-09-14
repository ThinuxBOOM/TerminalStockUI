"""Walk-forward calibration snapshots (Phase 2b).

Deterministic, no network. :func:`build_snapshot` replays the
:class:`~backend.forecasting.service.ForecastService` ensemble over trailing
price history through :class:`WalkForwardSplitter` (+ ``assert_no_leakage``)
and scores the replayed direction probabilities with
:mod:`backend.forecasting.calibration.metrics` (Brier, ECE, reliability
table) plus per-member hit rates.

Walk-forward shape (bounded + fast):
  * Bars: trailing ``SNAPSHOT_BAR_LIMIT`` (250) daily bars via the injected
    market service (``MarketDataService.get_bars`` caps at 250 anyway).
  * Splits: ``WalkForwardSplitter(train_size=100, test_size=1,
    gap=horizon, expanding=True, step=STRIDE)`` over the leakage-safe
    feature frame — one scored origin per fold (``test_size=1``), a purge
    gap of ``horizon`` bars so train labels can never straddle the test
    origin, and a stride of ``STRIDE`` origins to keep the
    ``universe x 3 horizons`` build well under 60s locally.
  * Members: the same ensemble members as
    :class:`~backend.forecasting.service.ForecastService` (``historical-
    drift`` + ``momentum`` + ``logistic-direction``, plus ``sse-drift`` /
    ``eux-drift`` on the routed venues), refit per fold on the train prefix
    only. Member dicts are keyed by model NAME (the same keys as
    ``ForecastService.forecast`` ``components``).
  * Labels: forward direction over ``horizon`` (``close[t+h] > close[t]``);
    tail origins with an unobservable horizon are skipped, never imputed.
  * Ensemble weighting mirrors the service: mean of the available US
    members, blended 50/50 with the venue drift when routed.

``build_snapshot`` is pure compute (the ``db`` kwarg is accepted for the
contracted signature and otherwise unused); persistence lives in
:func:`upsert_snapshot` and reads in :func:`get_latest_snapshot`, both
portable across Postgres and SQLite.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.forecasting.backtesting.walk_forward import (
    WalkForwardSplitter,
    assert_no_leakage,
)
from backend.forecasting.calibration.metrics import (
    brier_score,
    calibration_error,
    reliability_table,
)
from backend.forecasting.common import FORECAST_HORIZONS
from backend.forecasting.features.euronext import EUX_FEATURE_VERSION
from backend.forecasting.features.features import (
    FEATURE_VERSION,
    build_features,
    direction_label,
    log_returns,
)
from backend.forecasting.features.sse import SSE_FEATURE_VERSION
from backend.forecasting.models.euronext_drift import (
    MODEL_VERSION as EUX_DRIFT_VERSION,
    EuxDriftBaseline,
)
from backend.forecasting.models.historical_drift import HistoricalDriftBaseline
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.models.momentum import MomentumBaseline
from backend.forecasting.models.sse_drift import (
    MODEL_VERSION as SSE_DRIFT_VERSION,
    SseDriftBaseline,
)
from backend.forecasting.registry import ENSEMBLE_VERSION
from backend.forecasting.service import (
    EUX_BLEND_VERSION,
    SSE_BLEND_VERSION,
    _data_version,
    _is_euronext,
    _is_sse,
)

#: Trailing bars per snapshot (matches ForecastService.BAR_LIMIT; the market
#: service caps ``limit`` at 250 anyway, so this is the effective trailing
#: ~1y window).
SNAPSHOT_BAR_LIMIT = 250
#: Minimum bars to attempt a replay (mirrors ForecastService insufficient
#: history gate).
MIN_BARS = 100
#: Walk-forward shape: expanding origin, one scored bar per fold.
TRAIN_SIZE = 100
TEST_SIZE = 1
#: Origins stride: every STRIDE-th bar is scored (bounded + fast).
STRIDE = 10
#: Reliability bins (matches the calibration dashboard contract).
N_BINS = 10
#: Minimum scored windows before Brier/ECE read as skill (below this the
#: snapshot is flagged weak; cron counts only n>=MIN_SCORED_WINDOWS as
#: calibrated).
MIN_SCORED_WINDOWS = 10
#: Amber threshold: n below this carries wide uncertainty.
SMALL_SAMPLE_WINDOWS = 30

BASE_MEMBERS: tuple[str, ...] = ("historical-drift", "momentum", "logistic-direction")


def canonical_symbol(symbol: str, market_service: Any | None = None) -> str:
    """Upper exchange symbol for ``symbol`` (registry-aware, never raises)."""
    text = (symbol or "").strip().upper()
    if not text:
        return text
    try:
        registry = getattr(market_service, "registry", None)
        if registry is None:
            from backend.instruments.registry import InstrumentRegistry

            registry = InstrumentRegistry()
        instrument, _, _ = registry.resolve(text)
        if instrument is not None and getattr(instrument, "exchange_symbol", None):
            return str(instrument.exchange_symbol).upper()
    except Exception:
        pass
    return text


def _canonical_identity(
    symbol: str, market_service: Any | None = None
) -> tuple[str, str]:
    """(exchange_symbol, exchange_mic) for ``symbol`` (never raises)."""
    text = (symbol or "").strip().upper()
    try:
        registry = getattr(market_service, "registry", None)
        if registry is None:
            from backend.instruments.registry import InstrumentRegistry

            registry = InstrumentRegistry()
        instrument, _, _ = registry.resolve(text)
        if instrument is not None:
            sym = str(getattr(instrument, "exchange_symbol", "") or "").upper()
            mic = str(getattr(instrument, "exchange_mic", "") or "").upper()
            if sym:
                return sym, (mic or "XNAS")
    except Exception:
        pass
    return text, "XNAS"


def _versions(symbol: str, bars: dict) -> tuple[str, str, str, tuple[str, ...], str | None]:
    """(model_version, feature_version, data_version, members, extra) routing.

    Mirrors ``ForecastService.forecast`` venue routing so snapshot versions
    match the live forecast row for the same (symbol, horizon).
    """
    provenance = dict((bars or {}).get("provenance") or {})
    stamp = str(provenance.get("as_of", "unknown"))
    base_data_version = _data_version({**provenance, "as_of": stamp})
    sse = _is_sse(symbol, bars or {})
    eux = _is_euronext(symbol, bars or {}) and not sse
    if sse:
        return (SSE_BLEND_VERSION, SSE_FEATURE_VERSION,
                f"{base_data_version}-sse",
                (*BASE_MEMBERS, "sse-drift"), "sse-drift")
    if eux:
        return (EUX_BLEND_VERSION, EUX_FEATURE_VERSION,
                f"{base_data_version}-eux",
                (*BASE_MEMBERS, "eux-drift"), "eux-drift")
    return (ENSEMBLE_VERSION, FEATURE_VERSION, base_data_version,
            (*BASE_MEMBERS,), None)


def _zero_snapshot(
    symbol: str,
    exchange_mic: str,
    horizon: int,
    model_version: str,
    feature_version: str,
    data_version: str,
    members: tuple[str, ...],
) -> dict:
    """Zero-window snapshot: valid row shape, NULL metrics, never a crash."""
    return {
        "symbol": symbol,
        "exchange_mic": exchange_mic,
        "horizon_days": int(horizon),
        "model_version": model_version,
        "feature_version": feature_version,
        "data_version": data_version,
        "brier": None,
        "ece": None,
        "n_windows": 0,
        "reliability": [],
        "members": {name: {"hit_rate": None, "n": 0} for name in members},
    }


def _reliability_records(table: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for row in table.to_dict(orient="records"):
        out.append({
            "bin_low": float(row["bin_low"]),
            "bin_high": float(row["bin_high"]),
            "count": int(row["count"]),
            "mean_predicted": None
            if pd.isna(row["mean_predicted"]) else float(row["mean_predicted"]),
            "fraction_positive": None
            if pd.isna(row["fraction_positive"]) else float(row["fraction_positive"]),
        })
    return out


def _market_service(market_service: Any | None = None) -> Any:
    if market_service is not None:
        return market_service
    from backend.market_data.service import MarketDataService

    return MarketDataService()


def build_snapshot(
    symbol: str,
    horizon: int,
    *,
    market_service: Any | None = None,
    db: Any | None = None,  # accepted for the contracted signature; unused (pure compute)
) -> dict:
    """Build one calibration snapshot dict for (symbol, horizon).

    No network beyond what the injected ``market_service`` does (unit tests
    inject a fake). Empty/thin history yields a zero-window snapshot dict,
    never an exception (except for a bad ``horizon`` or empty symbol, which
    raise ``ValueError``). Deterministic: same bars -> same snapshot.
    """
    _ = db  # contracted kwarg; persistence lives in upsert_snapshot
    try:
        horizon = int(horizon)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon!r}") from exc
    if horizon not in FORECAST_HORIZONS:
        raise ValueError(
            f"horizon must be one of {list(FORECAST_HORIZONS)}, got {horizon}")
    sym_text = (symbol or "").strip().upper()
    if not sym_text:
        raise ValueError("symbol must be non-empty")
    market = _market_service(market_service)
    exchange_symbol, exchange_mic = _canonical_identity(sym_text, market)

    try:
        bars = market.get_bars(sym_text, timeframe="1d", limit=SNAPSHOT_BAR_LIMIT)
    except Exception:
        bars = {"bars": [], "provenance": {}}
    rows = (bars or {}).get("bars", []) or []
    model_version, feature_version, data_version, expected_members, extra = \
        _versions(sym_text, bars or {})
    zero = lambda: _zero_snapshot(
        exchange_symbol, exchange_mic, horizon, model_version,
        feature_version, data_version, expected_members)
    if len(rows) < MIN_BARS:
        return zero()
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
        features = build_features(frame)
    except (ValueError, TypeError, KeyError):
        return zero()
    if len(features) < MIN_BARS:
        return zero()
    closes_feat = frame["close"].loc[features.index]
    labels_full = direction_label(closes_feat, horizon)

    splitter = WalkForwardSplitter(
        train_size=TRAIN_SIZE, test_size=TEST_SIZE, gap=horizon,
        expanding=True, step=STRIDE)
    try:
        folds = list(splitter.splits(len(features)))
    except ValueError:
        return zero()

    y_true: list[float] = []
    y_prob: list[float] = []
    member_probs: dict[str, list[float]] = {m: [] for m in expected_members}
    member_labels: dict[str, list[float]] = {m: [] for m in expected_members}

    for train_idx, test_idx in folds:
        assert_no_leakage(train_idx, test_idx, horizon)
        train_close = closes_feat.iloc[train_idx]
        train_feat = features.iloc[train_idx]
        try:
            drift_p: float | None = float(
                HistoricalDriftBaseline()
                .fit(log_returns(train_close).dropna())
                .direction_probability(horizon).value)
        except (ValueError, TypeError):
            drift_p = None
        try:
            mom_p: float | None = float(
                MomentumBaseline().fit(train_close)
                .direction_probability(horizon).value)
        except (ValueError, TypeError):
            mom_p = None
        try:
            logreg = LogisticDirectionModel(horizons=[horizon]).fit(
                train_feat, train_close)
        except (ValueError, ImportError, TypeError):
            logreg = None
        extra_p: float | None = None
        if extra == "sse-drift":
            try:
                extra_p = float(
                    SseDriftBaseline()
                    .fit(log_returns(train_close).dropna())
                    .direction_probability(horizon).value)
            except (ValueError, TypeError):
                extra_p = None
        elif extra == "eux-drift":
            try:
                extra_p = float(
                    EuxDriftBaseline()
                    .fit(log_returns(train_close).dropna())
                    .direction_probability(horizon).value)
            except (ValueError, TypeError):
                extra_p = None
        if drift_p is None and mom_p is None and logreg is None and extra_p is None:
            continue
        for pos in (int(p) for p in test_idx):
            try:
                label = labels_full.iloc[pos]
            except (IndexError, KeyError):
                continue
            if pd.isna(label):
                continue  # horizon unobservable at the tail: skip, never impute
            label_f = float(label)
            window: dict[str, float] = {}
            if drift_p is not None:
                window["historical-drift"] = float(drift_p)
            if mom_p is not None:
                window["momentum"] = float(mom_p)
            if logreg is not None:
                try:
                    window["logistic-direction"] = float(
                        logreg.predict_direction_proba(
                            features.iloc[[pos]])[horizon].value)
                except (ValueError, IndexError, KeyError, TypeError):
                    pass
            us_parts = [window[m] for m in BASE_MEMBERS if m in window]
            if extra is not None and extra_p is not None:
                window[extra] = float(extra_p)
                if not us_parts:
                    # Venue drift alone still scores (US members all missing).
                    ensemble = float(extra_p)
                else:
                    ensemble = float(
                        (sum(us_parts) / len(us_parts) + float(extra_p)) / 2.0)
            else:
                if not us_parts:
                    continue
                ensemble = float(sum(us_parts) / len(us_parts))
            y_true.append(label_f)
            y_prob.append(ensemble)
            for name, proba in window.items():
                if name in member_probs:
                    member_probs[name].append(float(proba))
                    member_labels[name].append(label_f)

    if not y_true:
        return zero()
    n_windows = int(len(y_true))
    warning = (
        "insufficient windows (n<10): scores unreliable"
        if n_windows < MIN_SCORED_WINDOWS
        else ("small sample (n<30): wide uncertainty" if n_windows < SMALL_SAMPLE_WINDOWS else None)
    )
    table = reliability_table(y_true, y_prob, n_bins=N_BINS)
    members: dict[str, dict] = {}
    for name in expected_members:
        probs = member_probs.get(name, [])
        labs = member_labels.get(name, [])
        n = len(probs)
        if n == 0:
            members[name] = {"hit_rate": None, "n": 0}
            continue
        correct = sum(
            1 for p, y in zip(probs, labs)
            if (1.0 if float(p) >= 0.5 else 0.0) == float(y))
        members[name] = {"hit_rate": float(correct / n), "n": int(n)}
    return {
        "symbol": exchange_symbol,
        "exchange_mic": exchange_mic,
        "horizon_days": horizon,
        "model_version": model_version,
        "feature_version": feature_version,
        "data_version": data_version,
        "brier": float(brier_score(y_true, y_prob)),
        "ece": float(calibration_error(y_true, y_prob, n_bins=N_BINS)),
        "n_windows": int(len(y_true)),
        "warning": warning,
        "reliability": _reliability_records(table),
        "members": members,
    }


def upsert_snapshot(db: Any, snapshot: dict) -> Any:
    """Insert-or-update one snapshot row on the UNIQUE key. Commits.

    Portable across Postgres/SQLite (manual select-then-write, no
    dialect-specific ON CONFLICT). Returns the ORM row. Raises on DB
    errors (callers turn per-symbol failures into error entries).
    """
    from backend.db.models import CalibrationSnapshot

    required = ("symbol", "horizon_days", "model_version", "feature_version",
                "data_version")
    missing = [k for k in required if snapshot.get(k) in (None, "")]
    if missing:
        raise ValueError(f"snapshot missing required keys: {missing}")
    try:
        row = (
            db.query(CalibrationSnapshot)
            .filter(
                CalibrationSnapshot.symbol == str(snapshot["symbol"]).upper(),
                CalibrationSnapshot.horizon_days == int(snapshot["horizon_days"]),
                CalibrationSnapshot.model_version == str(snapshot["model_version"]),
                CalibrationSnapshot.feature_version == str(snapshot["feature_version"]),
                CalibrationSnapshot.data_version == str(snapshot["data_version"]),
            )
            .first()
        )
        if row is None:
            row = CalibrationSnapshot(
                symbol=str(snapshot["symbol"]).upper(),
                exchange_mic=str(snapshot.get("exchange_mic") or "XNAS"),
                horizon_days=int(snapshot["horizon_days"]),
                model_version=str(snapshot["model_version"]),
                feature_version=str(snapshot["feature_version"]),
                data_version=str(snapshot["data_version"]),
                brier=snapshot.get("brier"),
                ece=snapshot.get("ece"),
                n_windows=int(snapshot.get("n_windows") or 0),
                reliability=list(snapshot.get("reliability") or []),
                members=dict(snapshot.get("members") or {}),
            )
            db.add(row)
        else:
            row.exchange_mic = str(snapshot.get("exchange_mic") or row.exchange_mic)
            row.brier = snapshot.get("brier")
            row.ece = snapshot.get("ece")
            row.n_windows = int(snapshot.get("n_windows") or 0)
            row.reliability = list(snapshot.get("reliability") or [])
            row.members = dict(snapshot.get("members") or {})
        db.commit()
        try:
            db.refresh(row)
        except Exception:
            pass
        return row
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise


def get_latest_snapshot(
    db: Any, symbol: str, horizon: int, model_version: str
) -> Any | None:
    """Latest snapshot row for (symbol, horizon, model_version) or None.

    Symbol matching is on the stored exchange symbol (callers normalize via
    :func:`canonical_symbol`). Ordering is ``created_at`` descending. Never
    commits; callers catch all exceptions (best-effort reads).
    """
    from backend.db.models import CalibrationSnapshot

    return (
        db.query(CalibrationSnapshot)
        .filter(
            CalibrationSnapshot.symbol == (symbol or "").strip().upper(),
            CalibrationSnapshot.horizon_days == int(horizon),
            CalibrationSnapshot.model_version == str(model_version),
        )
        .order_by(CalibrationSnapshot.created_at.desc())
        .first()
    )


__all__ = [
    "N_BINS",
    "STRIDE",
    "TRAIN_SIZE",
    "TEST_SIZE",
    "SNAPSHOT_BAR_LIMIT",
    "BASE_MEMBERS",
    "build_snapshot",
    "canonical_symbol",
    "get_latest_snapshot",
    "upsert_snapshot",
]
