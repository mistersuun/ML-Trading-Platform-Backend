"""Dashboard helpers survive the Phase 2 return shapes (None half-life / profit factor, insufficient data)."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

import config
import dashboard_fmt as D
from backtester import BacktestResult
from tests.fixtures.synthetic import gbm_ohlc, ou_pair


def test_fmt_handles_none_nan_inf_and_text():
    assert D.fmt(None) == "n/a" and D.fmt(float("nan")) == "n/a" and D.fmt(float("inf")) == "n/a"
    assert D.fmt("x") == "n/a" and D.fmt(0.1234, ".1%") == "12.3%" and D.fmt(12.34, ".0f", "d") == "12d"


def test_scanned_pair_texts_with_none_fields():
    t = D.scanned_pair_texts({"current_zscore": 2.1, "half_life": None, "profit_factor": None, "win_rate": None})
    assert t == {"zscore": "2.10", "win_rate": "n/a", "half_life": "n/a", "profit_factor": "n/a"}


def test_pair_metric_texts_with_none_half_life():
    a = SimpleNamespace(coint_pvalue=0.01, correlation=0.8, half_life=None, current_zscore=1.2,
                        hedge_ratio=1.5, adf_pvalue=None)
    t = D.pair_metric_texts(a)
    assert t["half_life"] == "n/a" and t["adf_p"] == "n/a" and t["coint_p"] == "0.0100"


def test_safe_analyze_pair_warns_below_minimum_overlap_instead_of_raising():
    a, b = ou_pair(n=config.PAIRS_MIN_ALIGNED_BARS - 1, seed=3)
    analysis, _, _, warn = D.safe_analyze_pair(a, b, "A", "B")
    assert analysis is None and str(config.PAIRS_MIN_ALIGNED_BARS) in warn
    a, b = ou_pair(n=400, seed=3, half_life=8.0)
    analysis, ea, eb, warn = D.safe_analyze_pair(a, b, "A", "B")
    assert warn is None and analysis is not None and len(ea) == len(eb) == 400


def test_regime_profitability_skips_insufficient_regimes():
    good = BacktestResult("X", "p", total_return_pct=0.1)
    bad = BacktestResult("X", "p", total_return_pct=-0.1)
    thin = BacktestResult("X", "p", total_return_pct=0.5)
    thin.insufficient = True
    assert D.regime_profitability({"full_period": good, "a": good, "b": bad, "c": thin}) == (1, 2)
    assert D.regime_profitability({}) == (0, 0)


def test_dashboard_module_imports_helpers():
    import ast
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath("dashboard.py").read_text()
    ast.parse(src)
    assert "from dashboard_fmt import" in src
