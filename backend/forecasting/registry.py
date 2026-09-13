"""Model registry: name -> version -> artifact path (Milestone 3).

Simple versioned registry for the deterministic baselines. No network, no
training here: it only records which (model_version, feature_version) pairs
are valid and where their artifacts would live. Artifacts for the closed-form
baselines (drift/momentum/quantiles) are stateless; fitted sklearn baselines
(logistic/gradient-boost) are refit deterministically per request in
backend.forecasting.service, so the registry tracks versions, not bytes.
"""

from __future__ import annotations

from backend.forecasting.features.features import FEATURE_VERSION
from backend.forecasting.features.euronext import EUX_FEATURE_VERSION
from backend.forecasting.features.sse import SSE_FEATURE_VERSION
from backend.forecasting.models import gradient_boost, historical_drift, logistic, momentum
from backend.forecasting.models import quantile_bands
from backend.forecasting.models import euronext_drift
from backend.forecasting.models import sse_drift

ENSEMBLE_NAME = "ensemble"
ENSEMBLE_VERSION = "ensemble-v1"
ENSEMBLE_MEMBERS = (
    historical_drift.MODEL_VERSION,
    momentum.MODEL_VERSION,
    logistic.MODEL_VERSION,
)

REGISTRY: dict[str, dict[str, dict[str, str]]] = {
    historical_drift.MODEL_NAME: {
        historical_drift.MODEL_VERSION: {
            "artifact_path": f"artifacts/{historical_drift.MODEL_NAME}/{historical_drift.MODEL_VERSION}.json",
            "feature_version": FEATURE_VERSION,
        }
    },
    momentum.MODEL_NAME: {
        momentum.MODEL_VERSION: {
            "artifact_path": f"artifacts/{momentum.MODEL_NAME}/{momentum.MODEL_VERSION}.json",
            "feature_version": FEATURE_VERSION,
        }
    },
    logistic.MODEL_NAME: {
        logistic.MODEL_VERSION: {
            "artifact_path": f"artifacts/{logistic.MODEL_NAME}/{logistic.MODEL_VERSION}.pkl",
            "feature_version": FEATURE_VERSION,
        }
    },
    gradient_boost.MODEL_NAME: {
        gradient_boost.MODEL_VERSION: {
            "artifact_path": f"artifacts/{gradient_boost.MODEL_NAME}/{gradient_boost.MODEL_VERSION}.pkl",
            "feature_version": FEATURE_VERSION,
        }
    },
    quantile_bands.MODEL_NAME: {
        quantile_bands.MODEL_VERSION: {
            "artifact_path": f"artifacts/{quantile_bands.MODEL_NAME}/{quantile_bands.MODEL_VERSION}.json",
            "feature_version": FEATURE_VERSION,
        }
    },
    sse_drift.MODEL_NAME: {
        sse_drift.MODEL_VERSION: {
            "artifact_path": f"artifacts/{sse_drift.MODEL_NAME}/{sse_drift.MODEL_VERSION}.json",
            "feature_version": SSE_FEATURE_VERSION,
        }
    },
    euronext_drift.MODEL_NAME: {
        euronext_drift.MODEL_VERSION: {
            "artifact_path": f"artifacts/{euronext_drift.MODEL_NAME}/{euronext_drift.MODEL_VERSION}.json",
            "feature_version": EUX_FEATURE_VERSION,
        }
    },
    ENSEMBLE_NAME: {
        ENSEMBLE_VERSION: {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION}.json",
            "feature_version": FEATURE_VERSION,
        }
    },
}


def list_models() -> list[dict[str, str]]:
    """Return every registered {name, version, artifact_path, feature_version}."""
    out: list[dict[str, str]] = []
    for name in sorted(REGISTRY):
        for version in sorted(REGISTRY[name]):
            entry = REGISTRY[name][version]
            out.append(
                {
                    "name": name,
                    "version": version,
                    "artifact_path": entry["artifact_path"],
                    "feature_version": entry["feature_version"],
                }
            )
    return out


__all__ = [
    "ENSEMBLE_NAME",
    "ENSEMBLE_VERSION",
    "ENSEMBLE_MEMBERS",
    "REGISTRY",
    "list_models",
]
