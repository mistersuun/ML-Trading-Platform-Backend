"""Purged, embargoed expanding-window time-series split."""
from __future__ import annotations

import numpy as np

import config


class PurgedTimeSeriesSplit:
    """Expanding-window CV with fixed-size test blocks at the end of the sample.

    A row's label spans bars t..t+horizon, so training rows stop `gap = horizon + embargo`
    rows before each test block: max train label time < test start - embargo.
    The number of folds is reduced when the data cannot support `n_splits` blocks with at
    least `min_train_size` training rows each.
    """

    def __init__(self, n_splits: int = 5, test_size: int = config.ML_CV_TEST_BARS,
                 horizon: int = config.ML_HORIZON_BARS, embargo: int = config.ML_EMBARGO_BARS,
                 min_train_size: int = 100):
        self.n_splits = n_splits
        self.test_size = test_size
        self.horizon = horizon
        self.embargo = embargo
        self.min_train_size = min_train_size

    @property
    def gap(self) -> int:
        return self.horizon + self.embargo

    def split(self, X, y=None, groups=None):
        n = len(X)
        for k in range(self.n_splits):
            test_start = n - (self.n_splits - k) * self.test_size
            train_end = test_start - self.gap
            if train_end < self.min_train_size:
                continue
            yield np.arange(0, train_end), np.arange(test_start, test_start + self.test_size)

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return sum(1 for _ in self.split(X if X is not None else []))
