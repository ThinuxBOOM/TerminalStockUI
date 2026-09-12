"""scikit-learn is optional: import never fails; fit degrades with a clear error.

Regression test for the CI outage where a missing `sklearn` broke collection
of every module importing `backend.api.main` (via
forecasting/models/__init__.py -> gradient_boost/logistic). The sklearn
estimators are now imported lazily: modules import fine without sklearn,
`fit()` raises an informative ImportError, and ForecastService degrades to
the drift+momentum ensemble instead of 500ing.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.forecasting.models import gradient_boost, logistic
from backend.forecasting.models.gradient_boost import GradientBoostDirectionModel
from backend.forecasting.models.logistic import LogisticDirectionModel
from backend.forecasting.service import ForecastService
from backend.tests.fixtures import FIXED_AS_OF, make_ohlcv


class _FakeMarket:
    """Minimal MarketDataService double returning a fixed frame."""

    def __init__(self, frame: pd.DataFrame, instrument_id: str = "XNAS-AAPL") -> None:
        self._frame = frame
        self._instrument_id = instrument_id

    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 250) -> dict:
        frame = self._frame.tail(int(limit))
        rows = [
            {
                "ts": ts.isoformat(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
                "missing_fields": [],
            }
            for ts, r in frame.iterrows()
        ]
        return {
            "symbol": symbol.strip().upper(),
            "instrument_id": self._instrument_id,
            "timeframe": timeframe,
            "bars": rows,
            "provenance": {
                "source": "yfinance",
                "as_of": FIXED_AS_OF,
                "delay_minutes": 15,
                "quality_grade": "B",
                "fallback_used": False,
                "missing_fields": [],
            },
        }


def _features_and_close():
    from backend.forecasting.features.features import build_features

    frame = make_ohlcv(n=120, seed=7)
    feats = build_features(frame)
    return feats, frame["close"]


def test_models_construct_without_sklearn():
    # Construction must never touch sklearn (only fit() does).
    assert LogisticDirectionModel(horizons=[21]).models_ == {}
    assert GradientBoostDirectionModel(horizons=[21]).models_ == {}


def test_logistic_fit_raises_helpful_importerror_without_sklearn(monkeypatch):
    monkeypatch.setattr(logistic, "LogisticRegression", None)
    feats, closes = _features_and_close()
    with pytest.raises(ImportError, match="scikit-learn"):
        LogisticDirectionModel(horizons=[21]).fit(feats, closes)


def test_gradient_boost_fit_raises_helpful_importerror_without_sklearn(monkeypatch):
    monkeypatch.setattr(gradient_boost, "GradientBoostingClassifier", None)
    feats, closes = _features_and_close()
    with pytest.raises(ImportError, match="scikit-learn"):
        GradientBoostDirectionModel(horizons=[21]).fit(feats, closes)


def test_service_degrades_to_drift_momentum_without_logistic(monkeypatch):
    def _boom(self, features, close):
        raise ImportError("scikit-learn is not installed (simulated)")

    monkeypatch.setattr(LogisticDirectionModel, "fit", _boom)
    svc = ForecastService(market_service=_FakeMarket(make_ohlcv(n=252, seed=3)))
    res = svc.forecast("AAPL", 21, as_of=FIXED_AS_OF)
    assert 0.0 <= res["direction_probability"] <= 1.0
    assert "logistic-direction" not in res["components"]
    assert set(res["components"]) >= {"historical-drift", "momentum"}
