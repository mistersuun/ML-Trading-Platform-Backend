from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from portfolio_backtester import backtest_weights, stats, trend_sleeve

RESEARCH = Path(__file__).resolve().parent.parent / "docs" / "research" / "data"


def _idx(n, start="2020-01"):
    return pd.period_range(start, periods=n, freq="M")


def test_constant_returns_exact_cagr():
    r = pd.DataFrame({"A": [0.01] * 24, "B": [0.01] * 24}, index=_idx(24))
    res = backtest_weights(r, {"A": 0.5, "B": 0.5})
    st = stats(res.returns)
    assert st["cagr"] == pytest.approx(1.01 ** 12 - 1, abs=1e-12)
    assert st["max_drawdown"] == 0.0
    assert st["worst_year"] == pytest.approx(1.01 ** 12 - 1, abs=1e-12)
    assert res.equity.iloc[-1] == pytest.approx(1.01 ** 24)


def test_two_asset_monthly_rebalance_hand_computed():
    r = pd.DataFrame({"A": [0.10, -0.05], "B": [0.00, 0.02]}, index=_idx(2))
    res = backtest_weights(r, {"A": 0.5, "B": 0.5}, rebalance="monthly")
    assert res.returns.iloc[0] == pytest.approx(0.05)
    assert res.returns.iloc[1] == pytest.approx(-0.015)
    assert res.equity.iloc[-1] == pytest.approx(1.05 * 0.985)
    # drift after month 1: A=0.55/1.05, B=0.5/1.05 -> back to 50/50 trades |0.5-0.5238|*2
    expected = 2 * abs(0.55 / 1.05 - 0.5)
    assert res.turnover.iloc[0] == 0.0
    assert res.turnover.iloc[1] == pytest.approx(expected)


def test_cost_bps_applied_to_turnover():
    r = pd.DataFrame({"A": [0.10, 0.0], "B": [0.0, 0.0]}, index=_idx(2))
    res = backtest_weights(r, {"A": 0.5, "B": 0.5}, cost_bps=10)
    turn = 2 * abs(0.55 / 1.05 - 0.5)
    assert res.returns.iloc[1] == pytest.approx(0.0 - turn * 10 / 1e4)


def test_bands_only_trade_when_drift_exceeds():
    # A earns 2%/mo, B 0%: drift of A weight from 50% grows slowly.
    n = 40
    r = pd.DataFrame({"A": [0.02] * n, "B": [0.0] * n}, index=_idx(n))
    res = backtest_weights(r, {"A": 0.5, "B": 0.5}, rebalance="bands", band_abs=0.05, band_rel=10)
    traded = res.turnover[res.turnover > 0]
    assert len(traded) >= 1 and len(traded) < n - 1
    # every trade month had abs drift > 5pts at its start; every no-trade month <= 5pts
    w = res.weights["A"]
    drifted_before = {}
    prev_w, prev_r = None, None
    for t in range(1, n):
        wp = res.weights["A"].iloc[t - 1]
        g = wp * 1.02 / (wp * 1.02 + (1 - wp))
        dev = abs(g - 0.5)
        if res.turnover.iloc[t] > 0:
            assert dev > 0.05
            assert w.iloc[t] == pytest.approx(0.5)
        else:
            assert dev <= 0.05 + 1e-12
    # monthly rebalancing trades every month after the first
    m = backtest_weights(r, {"A": 0.5, "B": 0.5}, rebalance="monthly")
    assert (m.turnover.iloc[1:] > 0).all()


def test_bands_relative_trigger():
    r = pd.DataFrame({"A": [0.0, 0.0], "B": [0.0, 0.0]}, index=_idx(2))
    # no drift -> never trades even with tiny bands
    res = backtest_weights(r, {"A": 0.5, "B": 0.5}, rebalance="bands", band_abs=0.001, band_rel=0.001)
    assert res.turnover.sum() == 0.0


def test_bad_weights_rejected():
    r = pd.DataFrame({"A": [0.0]}, index=_idx(1))
    with pytest.raises(ValueError):
        backtest_weights(r, {"A": 0.5})


def test_stats_sharpe_with_rf():
    s = pd.Series([0.02, 0.0, 0.01, 0.03], index=_idx(4))
    rf = pd.Series([0.005] * 4, index=s.index)
    ex = s - rf
    assert stats(s, rf)["sharpe"] == pytest.approx(ex.mean() / ex.std() * 12 ** 0.5)
    assert stats(s)["sharpe"] == pytest.approx(s.mean() / s.std() * 12 ** 0.5)


def _trend_inputs(n=40, seed=3):
    rng = np.random.default_rng(seed)
    idx = _idx(n)
    rets = pd.DataFrame({"X": rng.normal(0.005, 0.04, n), "Y": rng.normal(0.004, 0.03, n),
                         "SHY": rng.normal(0.002, 0.002, n)}, index=idx)
    # prices have one extra leading row (start level)
    pidx = pd.period_range(idx[0] - 1, periods=n + 1, freq="M")
    prices = pd.DataFrame(100 * np.vstack([np.ones(3), (1 + rets.to_numpy()).cumprod(axis=0)]),
                          index=pidx, columns=rets.columns)
    return prices, rets


def test_trend_sleeve_causality_by_truncation():
    prices, rets = _trend_inputs()
    full = trend_sleeve(prices, rets, ["X", "Y"])
    k = 25
    cut_r = rets.iloc[:k]
    cut_p = prices.loc[: cut_r.index[-1]]
    part = trend_sleeve(cut_p, cut_r, ["X", "Y"])
    pd.testing.assert_series_equal(full.iloc[:k], part, check_names=False)
    # changing a FUTURE price must not change earlier outputs
    p2 = prices.copy()
    p2.iloc[k + 3:] *= 5
    alt = trend_sleeve(p2, rets, ["X", "Y"])
    pd.testing.assert_series_equal(full.iloc[: k + 3], alt.iloc[: k + 3], check_names=False)


def test_trend_sleeve_signal_applied_next_month():
    # X trends up then crashes below its SMA at month index 20; sleeve should
    # hold cash only from the following month.
    n = 30
    idx = _idx(n)
    px = np.concatenate([np.linspace(100, 200, 21), np.full(10, 50.0)])  # 31 prices
    prices = pd.DataFrame({"X": px, "SHY": 100.0}, index=pd.period_range(idx[0] - 1, periods=n + 1, freq="M"))
    rets = pd.DataFrame({"X": 0.01, "SHY": 0.0}, index=idx)
    out = trend_sleeve(prices, rets, ["X"], lookbacks=(10,), cash="SHY")
    # price row 21 (= month index 20 in returns) is the first drop; its signal -> return month index 21
    assert out.iloc[20] == pytest.approx(0.01)   # still invested in the crash month
    assert out.iloc[21] == pytest.approx(0.0)    # in cash the month after


def _load_research():
    px = pd.read_csv(RESEARCH / "monthly_closes_raw.csv", index_col=0)
    px.index = pd.PeriodIndex(px.index, freq="M")
    dv = pd.read_csv(RESEARCH / "dividends_exdate.csv")
    dv["m"] = pd.to_datetime(dv.ex_date.astype(str)).dt.to_period("M")
    div = dv.groupby(["m", "ticker"]).dividend_usd.sum().unstack().reindex(px.index).fillna(0)
    for t in px.columns:
        if t not in div:
            div[t] = 0.0
    px, div = px.iloc[:-1], div.iloc[:-1]   # drop partial last bar, as research does
    prev = px.shift(1)
    tr = ((px + div[px.columns]) / prev - 1).iloc[1:]
    return px, tr


def test_reproduces_research_60_40_cagr():
    _, tr = _load_research()
    res = backtest_weights(tr, {"SPY": 0.6, "IEF": 0.4})
    ref = pd.read_csv(RESEARCH / "portfolio_results.csv", index_col=0)
    assert stats(res.returns)["cagr"] == pytest.approx(ref.loc["60/40 SPY/IEF", "cagr"], abs=1e-4)
    assert abs(stats(res.returns)["cagr"] - 0.0662) <= 1e-4  # 6.62% within 0.01pt
    assert stats(res.returns)["max_drawdown"] == pytest.approx(ref.loc["60/40 SPY/IEF", "mdd"], abs=1e-4)


def test_reproduces_research_faber_ivy5_with_10m_lookback():
    px, tr = _load_research()
    ivy = ["SPY", "VEA", "IEF", "VNQ", "DBC"]
    s = trend_sleeve(px, tr, ivy, lookbacks=(10,), cash="SHY")
    ref = pd.read_csv(RESEARCH / "portfolio_results.csv", index_col=0)
    assert stats(s)["cagr"] == pytest.approx(ref.loc["Faber GTAA Ivy5", "cagr"], abs=1e-4)
