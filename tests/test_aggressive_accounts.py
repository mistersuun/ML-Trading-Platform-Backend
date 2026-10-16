"""Aggressive paper accounts (scripts/aggressive_accounts.py). Invented prices only."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "scripts"))
_spec = importlib.util.spec_from_file_location("aggressive_accounts", _root / "scripts" / "aggressive_accounts.py")
ag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ag)

IDX = pd.bdate_range("2024-01-01", periods=300)


def test_leveraged_trend_switches_on_the_200_day_line_and_holds_otherwise():
    up = pd.DataFrame({"QQQ": np.linspace(100, 150, 300), "TQQQ": 1.0, "SGOV": 100.0}, index=IDX)
    assert ag.leveraged_trend(up, {}) == {"TQQQ": 1.0, "SGOV": 0.0}
    assert ag.leveraged_trend(up, {"TQQQ": 1.0}) == {}
    down = up.assign(QQQ=np.linspace(150, 100, 300))
    assert ag.leveraged_trend(down, {"TQQQ": 1.0}) == {"TQQQ": 0.0, "SGOV": 1.0}


def test_top1_momentum_holds_only_the_best_stock():
    cols = {s: np.linspace(100, 100 + k, 300) for k, s in enumerate(ag.qa.STOCKS)}
    c = pd.DataFrame(cols, index=IDX)
    out = ag.top1_momentum(c, {"AAPL": 1.0})
    assert out[ag.qa.STOCKS[-1]] == 1.0 and out["AAPL"] == 0.0


def test_bootstrap_flat_returns_never_reach_10x():
    b = ag.bootstrap(pd.Series(np.zeros(500)), n=200)
    assert b["p10x"] == 0 and b["median"] == 1.0
