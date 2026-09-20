"""Model registry: name -> version -> artifact path (ensemble-v3).

Simple versioned registry for the deterministic baselines. No network, no
training here: it only records which (model_version, feature_version) pairs
are valid and where their artifacts would live. Artifacts for the closed-form
baselines (drift/momentum/quantiles) are stateless; fitted sklearn baselines
(logistic/gradient-boost) are refit deterministically per request in
backend.forecasting.service, so the registry tracks versions, not bytes.

ensemble-v3 changes (accuracy + honesty upgrade over v2):
  * ensemble-v1/v2 retained as legacy (old audit rows still resolve).
  * ensemble-v3 = adaptive-weighted + isotonic-calibrated mean of 6 members:
    historical-drift-v1, momentum-v1, logistic-direction-v3 (v2 features),
    gradient-boost-direction-v1 (v2 features), trend-persistence-v1,
    mean-reversion-v1 (new contrarian member, v2 features).
  * Isotonic (n>=50) / Platt (30<=n<50) per-horizon calibrators fit on
    walk-forward OOF pairs; shrinkage fallback below 30 pairs.
  * Inverse-Brier adaptive weights from trailing snapshots; fixed-weight
    fallback when skill unknown.
  * ML members stamp features-v2; closed-form members stay features-v1.
"""

from __future__ import annotations

from backend.forecasting.features.features import EXTENDED_FEATURE_VERSION, FEATURE_VERSION
from backend.forecasting.features.euronext import EUX_FEATURE_VERSION
from backend.forecasting.features.sse import SSE_FEATURE_VERSION
from backend.forecasting.models import gradient_boost, historical_drift, logistic, momentum
from backend.forecasting.models import quantile_bands
from backend.forecasting.models import euronext_drift
from backend.forecasting.models import sse_drift

ENSEMBLE_NAME = "ensemble"
ENSEMBLE_VERSION = "ensemble-v3"
ENSEMBLE_VERSION_V2 = "ensemble-v2"
ENSEMBLE_VERSION_V1 = "ensemble-v1"
TREND_PERSISTENCE_VERSION = "trend-persistence-v1"
MEAN_REVERSION_VERSION = "mean-reversion-v1"
CALIBRATOR_VERSION = "isotonic-platt-v1"
ENSEMBLE_MEMBERS = (
    historical_drift.MODEL_VERSION,
    momentum.MODEL_VERSION,
    logistic.MODEL_VERSION,
    gradient_boost.MODEL_VERSION,
    TREND_PERSISTENCE_VERSION,
    MEAN_REVERSION_VERSION,
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
        # Legacy v2 (v1 features) retained for old rows; current is v3.
        "logistic-direction-v2": {
            "artifact_path": f"artifacts/{logistic.MODEL_NAME}/logistic-direction-v2.pkl",
            "feature_version": FEATURE_VERSION,
        },
        logistic.MODEL_VERSION: {
            "artifact_path": f"artifacts/{logistic.MODEL_NAME}/{logistic.MODEL_VERSION}.pkl",
            "feature_version": EXTENDED_FEATURE_VERSION,
        },
    },
    gradient_boost.MODEL_NAME: {
        # Legacy stub retained for old rows.
        "gradient-boost-direction-v1-stub": {
            "artifact_path": f"artifacts/{gradient_boost.MODEL_NAME}/gradient-boost-direction-v1-stub.pkl",
            "feature_version": FEATURE_VERSION,
        },
        gradient_boost.MODEL_VERSION: {
            "artifact_path": f"artifacts/{gradient_boost.MODEL_NAME}/{gradient_boost.MODEL_VERSION}.pkl",
            "feature_version": EXTENDED_FEATURE_VERSION,
        },
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
    "trend-persistence": {
        TREND_PERSISTENCE_VERSION: {
            "artifact_path": f"artifacts/trend-persistence/{TREND_PERSISTENCE_VERSION}.json",
            "feature_version": EXTENDED_FEATURE_VERSION,
        }
    },
    "mean-reversion": {
        MEAN_REVERSION_VERSION: {
            "artifact_path": f"artifacts/mean-reversion/{MEAN_REVERSION_VERSION}.json",
            "feature_version": EXTENDED_FEATURE_VERSION,
        }
    },
    "ensemble-calibrator": {
        CALIBRATOR_VERSION: {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{CALIBRATOR_VERSION}.json",
            "feature_version": EXTENDED_FEATURE_VERSION,
        }
    },
    ENSEMBLE_NAME: {
        ENSEMBLE_VERSION_V1: {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V1}.json",
            "feature_version": FEATURE_VERSION,
        },
        ENSEMBLE_VERSION_V2: {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V2}.json",
            "feature_version": EXTENDED_FEATURE_VERSION,
        },
        ENSEMBLE_VERSION: {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION}.json",
            "feature_version": EXTENDED_FEATURE_VERSION,
        },
    },
    # Blended venue ensembles (defined in forecasting.service as
    # ENSEMBLE_VERSION+SSE/EUX_DRIFT_VERSION). v1/v2 (legacy) and v3
    # registered so blend versions resolve in provenance instead of dangling.
    f"{ENSEMBLE_NAME}+{sse_drift.MODEL_VERSION}": {
        f"{ENSEMBLE_VERSION_V1}+{sse_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V1}+{sse_drift.MODEL_VERSION}.json",
            "feature_version": SSE_FEATURE_VERSION,
        },
        f"{ENSEMBLE_VERSION_V2}+{sse_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V2}+{sse_drift.MODEL_VERSION}.json",
            "feature_version": SSE_FEATURE_VERSION,
        },
        f"{ENSEMBLE_VERSION}+{sse_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION}+{sse_drift.MODEL_VERSION}.json",
            "feature_version": SSE_FEATURE_VERSION,
        },
    },
    f"{ENSEMBLE_NAME}+{euronext_drift.MODEL_VERSION}": {
        f"{ENSEMBLE_VERSION_V1}+{euronext_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V1}+{euronext_drift.MODEL_VERSION}.json",
            "feature_version": EUX_FEATURE_VERSION,
        },
        f"{ENSEMBLE_VERSION_V2}+{euronext_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION_V2}+{euronext_drift.MODEL_VERSION}.json",
            "feature_version": EUX_FEATURE_VERSION,
        },
        f"{ENSEMBLE_VERSION}+{euronext_drift.MODEL_VERSION}": {
            "artifact_path": f"artifacts/{ENSEMBLE_NAME}/{ENSEMBLE_VERSION}+{euronext_drift.MODEL_VERSION}.json",
            "feature_version": EUX_FEATURE_VERSION,
        },
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
    "ENSEMBLE_VERSION_V2",
    "ENSEMBLE_VERSION_V1",
    "TREND_PERSISTENCE_VERSION",
    "MEAN_REVERSION_VERSION",
    "CALIBRATOR_VERSION",
    "ENSEMBLE_MEMBERS",
    "REGISTRY",
    "list_models",
]
