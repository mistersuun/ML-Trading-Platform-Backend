"""Portfolio Pilot rules (pilot.py, D25). Invented prices only."""
import math

import numpy as np
import pandas as pd
import pytest

import pilot as pl

IDX = pd.date_range("2020-01-03", periods=120, freq="W-FRI")


def _frame(trends: dict, n=120, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for k, g in trends.items():
        r = g + noise * rng.standard_normal(n)
        out[k] = 100 * np.cumprod(1 + r)
    return pd.DataFrame(out, index=IDX[:n])


ALL = {s.key: 0.002 for s in pl.SLEEVES}


def test_trend_score_votes():
    up = pd.Series(np.linspace(100, 150, 60), index=IDX[:60])
    down = pd.Series(np.linspace(150, 100, 60), index=IDX[:60])
    assert pl.trend_score(up, pl.WEEKLY) == 1.0
    assert pl.trend_score(down, pl.WEEKLY) == 0.0
    assert pl.trend_score(up.iloc[:40], pl.WEEKLY) is None


def test_weights_sum_to_one_and_gate_moves_falling_satellites_to_core():
    tr = dict(ALL)
    tr["gold_miners"] = -0.004
    w, scores = pl.target_weights(_frame(tr), pl.WEEKLY)
    assert sum(w.values()) == pytest.approx(1.0)
    assert scores["gold_miners"] == 0.0 and w["gold_miners"] == 0.0
    assert w["us_equity"] > 0.26          # received part of the freed 8%
    w_nogate, _ = pl.target_weights(_frame(tr), pl.WEEKLY, gate=False)
    assert w_nogate["gold_miners"] == pytest.approx(0.08)


def test_missing_sleeve_weight_is_spread_pro_rata():
    df = _frame(ALL)
    df.loc[df.index[:100], "bonds"] = np.nan      # only 20 bars of bonds
    w, _ = pl.target_weights(df, pl.WEEKLY, gate=False)
    assert "bonds" not in w
    assert w["us_equity"] == pytest.approx(0.26 / 0.92)


def test_leverage_is_clipped_and_inverse_to_volatility():
    calm = _frame(ALL, noise=0.002)
    wild = _frame(ALL, noise=0.10)
    w, _ = pl.target_weights(calm, pl.WEEKLY)
    assert pl.target_leverage(w, calm, pl.WEEKLY)[0] == pl.LEV_MAX
    w2, _ = pl.target_weights(wild, pl.WEEKLY)
    lev, vol = pl.target_leverage(w2, wild, pl.WEEKLY)
    assert pl.LEV_MIN <= lev < 1.0 and vol > 0.15


def test_simulate_static_charges_financing_and_tracks_leverage():
    df = pd.DataFrame({"a": np.full(120, 100.0)}, index=IDX)
    r = pl.simulate(df, pl.WEEKLY, "flat", "static", IDX[0], fixed_weights={"a": 1.0}, fixed_lev=1.5)
    years = (IDX[-1] - IDX[0]).days / 365.0
    assert r.leverage.iloc[0] == pytest.approx(1.5, rel=0.01)   # entry cost trims equity a little
    assert r.financing == pytest.approx(0.5 * pl.BORROW_RATE * years, rel=0.05)
    assert r.equity.iloc[-1] < 1.0


def test_pilot_trades_only_outside_bands():
    df = _frame(ALL, noise=0.01)
    r = pl.simulate(df, pl.WEEKLY, "pilot", "pilot", IDX[60])
    m = pl.metrics(r, pl.WEEKLY)
    assert math.isfinite(m["cagr"]) and m["max_lev"] <= pl.LEV_MAX + 0.2
    assert r.traded < 60 * 2          # far less than a full rebalance every week


def test_propose_maps_tickers_and_never_sells_unmanaged():
    closes = {t: pd.Series(np.linspace(50, 60, 300), index=pd.bdate_range("2025-01-01", periods=300))
              for s in pl.SLEEVES for t in s.tsx}
    pos = {"XGD": {"qty": 200, "price": 60.0}, "VFV": {"qty": 10, "price": 60.0}, "ZZZ": {"qty": 5, "price": 10.0}}
    p = pl.propose(pos, cash=-5000.0, ticker_closes=closes)
    assert p.unmanaged == ["ZZZ"]
    xgd = next(r for r in p.rows if r["ticker"] == "XGD")
    assert xgd["action"] == "sell"            # 12k of miners is far above an 8% sleeve
    assert "ZZZ" not in {r["ticker"] for r in p.rows}
    assert "Portfolio Pilot" in pl.render(p)


def test_default_policy_is_static_fixed_leverage_and_raises_concentration_alert():
    closes = {t: pd.Series(np.linspace(50, 60, 300), index=pd.bdate_range("2025-01-01", periods=300))
              for s in pl.SLEEVES for t in s.tsx}
    pos = {"XGD": {"qty": 200, "price": 60.0}, "VFV": {"qty": 50, "price": 60.0}}
    p = pl.propose(pos, cash=-6000.0, ticker_closes=closes)
    assert p.policy["mode"] == "static" and p.leverage == pytest.approx(1.45)
    assert p.weights["gold_miners"] == pytest.approx(0.08)          # no trend gate in the static policy
    assert any("XGD" in a for a in p.alerts)
    capped = pl.propose(pos, cash=-6000.0, ticker_closes=closes, policy={"leverage": 3.0})
    assert capped.leverage == pl.LEV_MAX
