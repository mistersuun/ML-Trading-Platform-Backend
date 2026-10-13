import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import allocation as al
from allocation import CORE_TARGETS, TREND_UNIVERSE, load_holdings, propose_rebalance, target_weights

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2024, 6, 15)
PRICES = {s: 100.0 for s in set(CORE_TARGETS) | set(TREND_UNIVERSE)}


@pytest.fixture(autouse=True)
def _us_profile(monkeypatch):
    """These tests are written against the D4 US-ticker table; the platform default is now 'cad' (D14)."""
    import config
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")


def _closes(n=30, start="2022-01", drift=0.01, cols=TREND_UNIVERSE):
    idx = pd.period_range(start, periods=n, freq="M")
    return pd.DataFrame({c: 100 * (1 + drift) ** np.arange(n) for c in cols}, index=idx)


def _on_target(total=1_000_000.0, scores=None):
    w = target_weights(scores)
    return {s: x * total / 100.0 for s, x in w.items()}   # price 100


def test_targets_sum_to_one_and_constants():
    assert sum(CORE_TARGETS.values()) == pytest.approx(1.0)
    assert al.CORE_PCT + al.TREND_SLEEVE_PCT == pytest.approx(1.0)
    assert sum(target_weights({a: 0.0 for a in TREND_UNIVERSE}).values()) == pytest.approx(1.0)


def test_in_band_no_trades():
    h = _on_target()
    h["VTI"] *= 1.05   # small drift, well inside band
    p = propose_rebalance(h, PRICES, _closes(), as_of=AS_OF)
    assert p.trades == []


def test_overweight_sleeve_sold_to_target():
    w = target_weights()
    w["VTI"] += 0.08                       # 8 pts over target; others shrink pro rata
    rest = 1.0 - w["VTI"]
    old_rest = 1.0 - target_weights()["VTI"]
    w = {s: (x if s == "VTI" else x * rest / old_rest) for s, x in w.items()}
    h = {s: x * 1_000_000.0 / 100.0 for s, x in w.items()}
    p = propose_rebalance(h, PRICES, _closes(), as_of=AS_OF)
    row = next(r for r in p.rows if r.symbol == "VTI")
    assert row.out_of_band and row.trade_shares < 0
    new_w = (row.value + row.trade_value) / (p.total_value)
    assert new_w == pytest.approx(row.target, abs=0.002)
    # proceeds are redeployed into underweights, never into the overweight
    assert all(r.trade_shares > 0 for r in p.trades if r.symbol != "VTI")


def test_contributions_buy_underweight_first_whole_shares():
    h = _on_target()
    h["VEA"] *= 0.8                          # underweight but inside band
    p = propose_rebalance(h, PRICES, _closes(), contributions=5_000.0, as_of=AS_OF)
    buys = {r.symbol: r for r in p.trades}
    assert "VEA" in buys and buys["VEA"].trade_shares > 0
    assert all(r.trade_shares > 0 for r in p.trades)       # no sells
    assert all(float(r.trade_shares).is_integer() for r in p.trades)
    spent = sum(r.trade_value for r in p.trades)
    assert spent <= 5_000.0 + 1e-9 and p.leftover_cash == pytest.approx(5_000.0 - spent)


def test_cash_row_funds_buys_and_missing_price_errors():
    h = {"CASH": 10_000.0}
    p = propose_rebalance(h, PRICES, None, as_of=AS_OF)
    assert sum(r.trade_value for r in p.trades) <= 10_000.0
    with pytest.raises(ValueError):
        propose_rebalance(h, {"VTI": 100.0}, None, as_of=AS_OF)


def test_trend_signal_causality():
    full = _closes(40, drift=0.0)
    full.iloc[:, :] = 100 + 5 * np.sin(np.arange(40))[:, None] + np.arange(40)[:, None] * 0.3
    as_of = date(2024, 3, 10)       # last completed month = 2024-02
    base, m = al.trend_signals(full[full.index < pd.Period("2024-03", "M")], as_of)
    assert m == "2024-02"
    future = full.copy()
    future.iloc[full.index >= pd.Period("2024-03", "M")] *= 0.1   # crash after as_of
    assert al.trend_signals(future, as_of)[0] == base
    # partial current month is ignored too
    cur = al.trend_signals(full, as_of)
    assert cur == (base, m)


def test_trend_scores_downtrend_off_uptrend_on():
    up = al.trend_signals(_closes(30, drift=0.02), AS_OF)[0]
    down = al.trend_signals(_closes(30, drift=-0.02), AS_OF)[0]
    assert all(v == 1.0 for v in up.values()) and all(v == 0.0 for v in down.values())
    # signal off moves trend weight into T-bills
    t = target_weights(down)
    assert t["SPY"] == 0.0 and t["SGOV"] > CORE_TARGETS["SGOV"] * al.CORE_PCT


def test_render_no_nan_and_load_holdings(tmp_path):
    f = tmp_path / "h.csv"
    f.write_text("symbol,quantity\nVTI,100\nCASH,2500\nXYZ,3\n")
    h = load_holdings(f)
    assert h == {"VTI": 100.0, "CASH": 2500.0, "XYZ": 3.0}
    p = propose_rebalance(h, PRICES, _closes(), contributions=100.0, as_of=AS_OF)
    txt = p.render()
    assert "nan" not in txt.lower() and "no orders" in txt and "XYZ" in txt
    assert "nan" not in propose_rebalance(h, PRICES, None, as_of=AS_OF).render().lower()


def test_module_does_not_import_execution():
    import ast
    mods = set()
    for n in ast.walk(ast.parse((ROOT / "allocation.py").read_text())):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert not mods & {"execution", "brokers", "paper_trader"}


def test_long_history_synthetic(tmp_path):
    sys.path.insert(0, str(ROOT))
    from scripts import long_history_backtest as lh
    out = tmp_path / "r.md"
    assert lh.main(["--synthetic", "--out", str(out)]) == 0
    txt = out.read_text()
    assert "core+trend" in txt and "2022" in txt and "nan" not in txt.lower()
