"""Isotonic / Platt calibrators for ensemble-v3 (deterministic, no network).

Fits a per-horizon probability calibrator on walk-forward OUT-OF-FOLD
(raw_prob, label) pairs -- never in-sample. Small samples fall back to
the v2 shrinkage so the forecast path never crashes and never overfits:

  * n < MIN_ISOTONIC_SAMPLES (30) -> shrinkage fallback (no fit).
  * 30 <= n < ~100 -> Platt (logistic) scaling is more stable; isotonic
    available explicitly via kind="isotonic".
  * sklearn missing -> shrinkage fallback (ImportError-safe, mirrors the
    logistic/GB degrade path).

All fits are deterministic (IsotonicRegression out_of_bounds="clip",
LogisticRegression fixed seed not needed -- lbfgs deterministic for
1-D input). Calibrators serialize to plain dicts {xs, ys} so snapshots
can persist them without pickles.
"""

from __future__ import annotations

import math

import numpy as np

#: Minimum OOF pairs before isotonic/Platt is attempted. Below this the
#: reliability curve is noise -- return shrinkage (v2 behavior).
MIN_ISOTONIC_SAMPLES = 30
#: Floor/ceiling applied after calibration (mirrors service PROB_FLOOR/CAP).
CAL_FLOOR = 0.05
CAL_CAP = 0.95
#: v2 shrinkage factor used as fallback (service CALIBRATION_SHRINKAGE).
SHRINKAGE = 0.8


def _as_arrays(raw, labels) -> tuple[np.ndarray, np.ndarray]:
    try:
        p = np.asarray(list(raw), dtype=float).ravel()
        y = np.asarray(list(labels), dtype=float).ravel()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"calibrator inputs must be numeric: {exc}") from exc
    if p.shape != y.shape:
        raise ValueError(f"shape mismatch: raw {p.shape} vs labels {y.shape}")
    if p.size == 0:
        raise ValueError("calibrator inputs must be non-empty")
    mask = np.isfinite(p) & np.isfinite(y) & (p >= 0.0) & (p <= 1.0)
    mask &= (y == 0.0) | (y == 1.0)
    p, y = p[mask], y[mask]
    if p.size == 0:
        raise ValueError("no finite in-range (prob, 0/1-label) pairs")
    return p, y


def _shrink(p_raw: float) -> float:
    try:
        raw = float(p_raw)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(raw):
        return 0.5
    raw = min(max(raw, 0.0), 1.0)
    cal = 0.5 + (raw - 0.5) * SHRINKAGE
    return min(max(cal, CAL_FLOOR), CAL_CAP)


def fit_isotonic(
    raw_probs, labels, *, kind: str = "auto"
) -> dict | None:
    """Fit a calibrator dict on OOF pairs; None means "use shrinkage".

    Returns {"kind": "isotonic", "xs": [...], "ys": [...]} or
    {"kind": "platt", "a": float, "b": float} or None (fallback).
    Never raises on small/de generate data -- returns None instead.
    kind="auto": isotonic when n>=50 else platt when 30<=n<50.
    """
    try:
        p, y = _as_arrays(raw_probs, labels)
    except ValueError:
        return None
    n = int(p.size)
    if n < MIN_ISOTONIC_SAMPLES:
        return None
    if len(np.unique(y)) < 2:
        return None  # single-class OOF: no slope to learn
    want = str(kind or "auto").lower()
    if want == "auto":
        want = "isotonic" if n >= 50 else "platt"
    if want == "isotonic":
        try:
            from sklearn.isotonic import IsotonicRegression
        except ImportError:
            return None
        try:
            # out_of_bounds="clip" keeps tails bounded deterministically.
            ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            ys = ir.fit_transform(p, y)
            xs = ir.X_thresholds_
            # Deduplicate thresholds (isotonic collapses flat runs).
            order = np.argsort(xs, kind="stable")
            xs_s = np.asarray(xs)[order]
            ys_s = np.asarray(ys)[order]
            # Keep last value per duplicated x (monotone plateau end).
            uniq_x, idx = np.unique(xs_s, return_index=False), None
            # Rebuild ys at unique xs via max (plateau value).
            best: dict[float, float] = {}
            for x, v in zip(xs_s.tolist(), ys_s.tolist()):
                best[float(x)] = float(v)
            uniq_x = sorted(best)
            uniq_y = [best[x] for x in uniq_x]
            _ = idx
            return {
                "kind": "isotonic",
                "xs": [float(v) for v in uniq_x],
                "ys": [float(v) for v in uniq_y],
                "n": n,
            }
        except Exception:
            return None
    if want == "platt":
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            return None
        try:
            X = p.reshape(-1, 1)
            clf = LogisticRegression(C=1.0, max_iter=2000)
            clf.fit(X, y.astype(int))
            a = float(clf.coef_.ravel()[0])
            b = float(clf.intercept_.ravel()[0])
            if not (math.isfinite(a) and math.isfinite(b)):
                return None
            return {"kind": "platt", "a": a, "b": b, "n": n}
        except Exception:
            return None
    return None


def apply_calibrator(p_raw: float, calibrator: dict | None) -> float:
    """Apply a fitted calibrator (or shrinkage fallback); never raises."""
    if not isinstance(calibrator, dict):
        return _shrink(p_raw)
    try:
        raw = float(p_raw)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(raw):
        return 0.5
    raw = min(max(raw, 0.0), 1.0)
    try:
        kind = str(calibrator.get("kind") or "").lower()
        if kind == "isotonic":
            xs = [float(v) for v in (calibrator.get("xs") or [])]
            ys = [float(v) for v in (calibrator.get("ys") or [])]
            if len(xs) != len(ys) or not xs:
                return _shrink(raw)
            out = float(np.interp(raw, xs, ys))
        elif kind == "platt":
            a = float(calibrator.get("a", 0.0))
            b = float(calibrator.get("b", 0.0))
            if not (math.isfinite(a) and math.isfinite(b)):
                return _shrink(raw)
            z = max(min(a * raw + b, 50.0), -50.0)
            out = 1.0 / (1.0 + math.exp(-z))
        else:
            return _shrink(raw)
    except Exception:
        return _shrink(raw)
    if not math.isfinite(out):
        return _shrink(raw)
    return min(max(out, CAL_FLOOR), CAL_CAP)


def brier_to_weights(member_brier: dict[str, float | None]) -> dict[str, float] | None:
    """Inverse-Brier adaptive weights; None when unusable (caller: fixed).

    w_i = (1/Brier_i) / sum(1/Brier_j). Members with missing/non-finite
    Brier or Brier<=0 are dropped. Requires >=1 usable member; single
    member -> {name: 1.0}. Deterministic, never raises.
    """
    try:
        items = list((member_brier or {}).items())
    except Exception:
        return None
    inv: dict[str, float] = {}
    for name, b in items:
        try:
            if b is None or isinstance(b, bool):
                continue
            v = float(b)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v) or v <= 1e-9 or v > 1.0:
            continue
        inv[str(name)] = 1.0 / v
    if not inv:
        return None
    total = sum(inv.values())
    if not total > 0 or not math.isfinite(total):
        return None
    return {k: v / total for k, v in inv.items()}


def cross_fitted_scores(
    raw_probs, labels, *, n_bins: int = 10
) -> tuple[dict | None, float | None, float | None]:
    """Honest calibrated skill via 2-fold time-ordered cross-fit.

    Fits the calibrator on the first half of OOF pairs and scores the
    second half (then vice versa); returns (full_calibrator, brier, ece)
    where the calibrator is fit on ALL pairs (for live application) but
    brier/ece are the cross-fitted (honest, not in-sample) estimates.
    Returns (None, None, None) when n < 2*MIN_ISOTONIC_SAMPLES or either
    half is single-class. Deterministic, never raises.
    """
    try:
        p = [float(v) for v in list(raw_probs)]
        y = [float(v) for v in list(labels)]
    except (TypeError, ValueError):
        return None, None, None
    n = len(p)
    if n < 2 * MIN_ISOTONIC_SAMPLES:
        # Still return the full fit (for application) with honest Nones.
        try:
            full = fit_isotonic(p, y, kind="auto")
        except Exception:
            full = None
        return full, None, None
    mid = n // 2
    try:
        cal_a = fit_isotonic(p[:mid], y[:mid], kind="auto")
        cal_b = fit_isotonic(p[mid:], y[mid:], kind="auto")
    except Exception:
        return None, None, None
    if cal_a is None or cal_b is None:
        try:
            full = fit_isotonic(p, y, kind="auto")
        except Exception:
            full = None
        return full, None, None
    try:
        pred = [apply_calibrator(v, cal_a) for v in p[mid:]]
        pred += [apply_calibrator(v, cal_b) for v in p[:mid]]
        # Reorder to original time order: second-half preds first then first.
        # For Brier/ECE order is irrelevant (means over pairs).
        yt = y[mid:] + y[:mid]
        brier = float(sum((a - b) ** 2 for a, b in zip(pred, yt)) / len(yt))
        # ECE via the shared metrics helper (import lazily to avoid cycle).
        from backend.forecasting.calibration.metrics import calibration_error

        ece = float(calibration_error(yt, pred, n_bins=int(n_bins)))
        full = fit_isotonic(p, y, kind="auto")
        return full, brier, ece
    except Exception:
        return None, None, None


__all__ = [
    "MIN_ISOTONIC_SAMPLES",
    "CAL_FLOOR",
    "CAL_CAP",
    "SHRINKAGE",
    "fit_isotonic",
    "apply_calibrator",
    "brier_to_weights",
    "cross_fitted_scores",
]
