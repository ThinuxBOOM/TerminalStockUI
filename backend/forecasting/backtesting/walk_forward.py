"""Walk-forward validation with a no-leakage guard (Milestone 3).

Corporate-action-adjusted price assumption (load-bearing): every splitter
and guard here assumes the input series is ALREADY adjusted for splits and
dividends upstream (market_data normalization layer). A split left
unadjusted looks like a -50% crash / +100% jump, silently corrupting
features, labels and scores. Callers must only pass adjusted closes;
unadjusted prices invalidate every result downstream.

Rules enforced:
  * Time-ordered rolling/expanding splits only (no shuffling, no k-fold).
  * Train strictly precedes test, with a configurable gap (purge) between
    them so label horizons cannot straddle the boundary.
  * assert_no_leakage raises LeakageError on any index overlap or on test
    rows that are not strictly after the train block (+ gap).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np


class LeakageError(AssertionError):
    """Raised when a train/test split violates time ordering or overlaps."""


@dataclass(frozen=True)
class WalkForwardSplitter:
    """Rolling- or expanding-origin splitter over positional indexes.

    Attributes:
        train_size: Trailing window length (rolling) or minimum length
            (expanding=True, where the train block grows from 0).
        test_size: Length of each forward test block.
        gap: Bars skipped between train end and test start (purge zone so
            that forward-label horizons ending inside the gap cannot leak;
            choose gap >= max label horizon when labels are precomputed).
            Default 0 is unsafe alone — direct callers must pass an explicit
            gap; the backtest API enforces gap >= max(horizons) with 422.
        expanding: If True, train blocks start at 0 and grow.
        step: How far the origin advances per fold (default = test_size).
    """

    train_size: int
    test_size: int
    gap: int = 0
    expanding: bool = False
    step: int | None = None

    def __post_init__(self) -> None:
        for name in ("train_size", "test_size"):
            value = getattr(self, name)
            if int(value) < 1:
                raise ValueError(f"{name} must be >= 1, got {value!r}")
        if int(self.gap) < 0:
            raise ValueError(f"gap must be >= 0, got {self.gap!r}")
        step = self.test_size if self.step is None else self.step
        if int(step) < 1:
            raise ValueError(f"step must be >= 1, got {self.step!r}")
        object.__setattr__(self, "train_size", int(self.train_size))
        object.__setattr__(self, "test_size", int(self.test_size))
        object.__setattr__(self, "gap", int(self.gap))
        object.__setattr__(self, "step", int(step))

    def splits(self, n: int) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield (train_idx, test_idx) positional index pairs, time-ordered."""
        total = int(n)
        if total < 0:
            raise ValueError("n must be >= 0")
        origin = self.train_size
        yielded = 0
        while True:
            test_start = origin + self.gap
            test_end = test_start + self.test_size
            if test_end > total:
                break
            train_idx = (np.arange(0, origin, dtype=int) if self.expanding
                         else np.arange(origin - self.train_size, origin, dtype=int))
            test_idx = np.arange(test_start, test_end, dtype=int)
            assert_no_leakage(train_idx, test_idx, self.gap)
            yield train_idx, test_idx
            yielded += 1
            origin += self.step
        if yielded == 0:
            raise ValueError(
                f"no folds possible with n={total}, train_size={self.train_size}, "
                f"test_size={self.test_size}, gap={self.gap}")

    def n_splits(self, n: int) -> int:
        """Count folds without materializing them."""
        return sum(1 for _ in self.splits(n))


def assert_no_leakage(train_idx, test_idx, gap: int = 0) -> None:
    """Raise LeakageError unless train strictly precedes test (+ gap).

    Conditions: no shared positions; every test position is at least
    gap+1 bars after the latest train position (so a gap of G purges G
    bars between the blocks).
    """
    train = np.asarray(train_idx).ravel()
    test = np.asarray(test_idx).ravel()
    if train.size == 0 or test.size == 0:
        raise LeakageError("train and test index sets must both be non-empty")
    if set(map(int, train)) & set(map(int, test)):
        raise LeakageError("train and test index sets overlap")
    latest_train, earliest_test = int(train.max()), int(test.min())
    if earliest_test - latest_train - 1 < int(gap):
        raise LeakageError(
            f"test starts at {earliest_test} but train ends at {latest_train} "
            f"with gap={gap}: blocks too close (need >= {gap} purged bars)")


__all__ = ["WalkForwardSplitter", "assert_no_leakage", "LeakageError"]
