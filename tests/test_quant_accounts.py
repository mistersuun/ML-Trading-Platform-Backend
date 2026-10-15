"""Five quant paper accounts (scripts/quant_accounts.py). Invented prices only."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "quant_accounts", Path(__file__).resolve().parent.parent / "scripts" / "quant_accounts.py")
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)

IDX = pd.bdate_range("2024-01-01", periods=300)


def _panel(cols: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pd.DataFrame(cols, index=IDX, dtype=float)
    return c.copy(), c


def test_commission_minimum_and_cap():
    assert qa.commission(1, 100) == 0.35
    assert qa.commission(0.1, 10) == pytest.approx(0.01)       # 1% of a US$1 trade
    assert qa.commission(1000, 100) == pytest.approx(3.5)


def test_xs_momentum_picks_the_top_five_and_drops_old_names():
    trend = {s: np.linspace(100, 100 + 10 * k, 300) for k, s in enumerate(qa.STOCKS)}
    _, c = _panel(trend)
    out = qa.xs_momentum(c, {"AAPL": 0.2})
    winners = [s for s, w in out.items() if w > 0]
    assert sorted(winners) == sorted(qa.STOCKS[-5:]) and out["AAPL"] == 0.0


def test_trend_puts_off_sleeves_in_tbills():
    cols = {a: np.linspace(100, 120, 300) for a in qa.TREND_ASSETS}
    cols["GLD"] = np.linspace(120, 100, 300)
    cols[qa.CASH_ETF] = np.full(300, 100.0)
    _, c = _panel(cols)
    out = qa.trend(c, {})
    assert out["GLD"] == 0.0 and out[qa.CASH_ETF] == pytest.approx(0.2) and out["SPY"] == 0.2


def test_simulate_fills_next_open_and_charges_costs():
    o, c = _panel({"SPY": np.full(300, 100.0)})
    o.iloc[1:, 0] = 101.0                                   # next opens differ from the decision close
    res = qa.simulate(lambda h, w: {"SPY": 1.0}, "monthly", o, c, IDX[10], IDX[12])
    t = res["trades"][0]
    assert t["date"] == str(IDX[11].date()) and t["price"] == pytest.approx(101 * 1.0005)
    assert res["cash"] >= 0 and res["curve"].iloc[-1] < 1000


def test_rsi2_buys_a_dip_in_an_uptrend_and_exits_above_the_5_day_average():
    x = np.linspace(100, 150, 300)
    x[-3:] = [149, 146, 143]
    _, c = _panel({"SPY": x})
    assert qa.rsi2_reversion(c, {}) == {"SPY": 0.25}
    x2 = x.copy()
    x2[-1] = 160
    _, c2 = _panel({"SPY": x2})
    assert qa.rsi2_reversion(c2, {"SPY": 0.25}) == {"SPY": 0.0}
