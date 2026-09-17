"""Validated forecasting engine baselines (Milestone 3, no AI).

Targets: direction probability (1/7/14/21 trading days), expected-return
range, volatility regime, large-drawdown probability.
Models: historical-drift, momentum, regularized logistic regression,
gradient-boosted direction (stub), empirical quantile bands.
Validation helpers: walk-forward splits with a no-leakage guard,
Brier score and calibration error. Deterministic throughout.
"""

__all__: list[str] = []
