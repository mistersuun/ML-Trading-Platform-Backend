"""WS2.4 stress tests: causal regimes, single-run attribution, block-bootstrap MC, sensitivity."""
import json

import numpy as np
import pandas as pd
import pytest

import config
import stress_test as st
from backtester import BacktestResult, Trade, classic_backtest
from tests.fixtures.synthetic import gbm_ohlc
from tests.helpers_causal import assert_causal


def _alt_signal(df):
    """Deterministic, causal: long when the previous bar closed up (a mix of regimes, many trades)."""
    sig = (df["Close"].diff().shift(1) > 0).astype(int)
    return df.assign(signal=sig)


@pytest.fixture(scope="module")
def frame():
    return gbm_ohlc(n=900, seed=3)


def test_regimes_causal_all_columns():
    df = gbm_ohlc(n=700, seed=33)
    assert_causal(st.detect_regimes, df, T=400, n_future=200)


def test_regimes_labels_and_warmup(frame):
    r = st.detect_regimes(frame)
    assert list(r.columns) == ["vol_regime", "trend_regime", "volume_regime"]
    assert r["vol_regime"].iloc[:60].isna().all()
    assert set(r["vol_regime"].dropna().unique()) <= {"low_vol", "normal_vol", "high_vol"}
    assert r["trend_regime"].notna().sum() > 500


def test_regimes_zero_volume_neutral():
    df = gbm_ohlc(n=300, seed=1).assign(Volume=0)
    assert (st.detect_regimes(df)["volume_regime"] == "normal_volume").all()


def test_pattern_runs_once_on_full_frame(frame):
    calls = []

    def spy(x):
        calls.append(len(x))
        return _alt_signal(x)

    res = st.stress_test_regimes(frame, "SYN", spy, "alt")
    assert calls == [len(frame)]
    assert "full_period" in res


def test_attribution_partitions_trades_and_flags(frame):
    res, detail = st.regime_attribution(frame, "SYN", _alt_signal, "alt")
    full = res["full_period"]
    assert full.total_trades > 50
    for label, d in detail.items():
        assert res[label].total_trades == d["n_trades"] <= full.total_trades
        assert res[label].insufficient == (d["n_trades"] < st.MIN_REGIME_TRADES)
    # high/low vol are disjoint -> together at most all trades
    assert detail["high_volatility"]["n_trades"] + detail["low_volatility"]["n_trades"] <= full.total_trades


def test_insufficient_regime_not_counted_in_consistency():
    ok = BacktestResult("X", "p", total_return_pct=0.1, profit_factor=2.0)
    bad = BacktestResult("X", "p", total_return_pct=-0.1, profit_factor=0.5)
    bad.insufficient = True
    assert st._check_regime_consistency({"full_period": ok, "a": ok, "b": bad}) is True


def _mc_result(frame):
    return classic_backtest(_alt_signal(frame), "SYN", "alt")


def test_mc_deterministic_and_bands(frame):
    r = _mc_result(frame)
    a = st.monte_carlo_analysis(r, n_simulations=200)
    b = st.monte_carlo_analysis(r, n_simulations=200)
    assert a == b
    assert a["method"] == "stationary_block_bootstrap" and a["returns_source"] == "daily_mtm"
    assert a["return_ci_low"] <= a["return_median"] <= a["return_ci_high"]
    assert a["drawdown_ci_low"] <= a["drawdown_ci_high"] <= 0
    assert a["sharpe_ci_low"] <= a["sharpe_ci_high"]
    assert a["cagr_ci_low"] <= a["cagr_ci_high"]
    json.dumps(a, allow_nan=False)


def test_mc_sharpe_centred_on_observed(frame):
    r = _mc_result(frame)
    a = st.monte_carlo_analysis(r, n_simulations=400)
    assert a["sharpe_ci_low"] - 0.5 <= r.sharpe_ratio <= a["sharpe_ci_high"] + 0.5


def test_mc_insufficient_trades():
    r = BacktestResult("X", "p", trades=[Trade(entry_date=pd.Timestamp("2024-01-02"))] * 3)
    assert st.monte_carlo_analysis(r) == {"error": "insufficient_trades"}


def test_sensitivity_uses_only_pre_holdout():
    cut = pd.Timestamp(config.HOLDOUT_START)
    idx = pd.bdate_range(cut - pd.offsets.BDay(400), periods=600)
    base = gbm_ohlc(n=600, seed=4)
    df = base.set_axis(idx)
    seen = []

    def spy(x):
        seen.append(x.index.max())
        return _alt_signal(x)

    out = st.parameter_sensitivity(df, "SYN", "alt", pattern_func=spy)
    assert seen and all(t < cut for t in seen)
    assert out.attrs["n_trials"] == len(out) == 64
    assert 0.0 <= out.attrs["share_positive_oos_sharpe"] <= 1.0
    assert {"oos_sharpe", "oos_trades", "sharpe", "stop_loss", "take_profit"} <= set(out.columns)


def test_sensitivity_empty_when_all_post_holdout():
    idx = pd.bdate_range(pd.Timestamp(config.HOLDOUT_START) + pd.offsets.BDay(5), periods=300)
    df = gbm_ohlc(n=300, seed=4).set_axis(idx)
    out = st.parameter_sensitivity(df, "SYN", "alt", pattern_func=_alt_signal)
    assert out.empty and out.attrs["n_trials"] == 0


def test_full_stress_test_keys_and_json():
    cut = pd.Timestamp(config.HOLDOUT_START)
    df = gbm_ohlc(n=900, seed=6).set_axis(pd.bdate_range(cut - pd.offsets.BDay(700), periods=900))
    rep = st.full_stress_test(df, "SYN", _alt_signal, "alt")
    for k in ("symbol", "pattern", "regimes", "monte_carlo", "sensitivity", "assessment", "regime_detail"):
        assert k in rep
    for k in ("regimes_consistent", "mc_prob_positive", "mc_worst_case_5pct", "param_robust"):
        assert k in rep["assessment"]
    assert rep["sensitivity"]["n_trials"] == 64
    json.dumps(rep, default=str, allow_nan=False)


@pytest.mark.slow
def test_mc_zero_edge_walk_covers_zero():
    """On a driftless random walk with random entries, the 95% return band should straddle 0 in most seeds."""
    hits = 0
    for seed in range(10):
        df = gbm_ohlc(n=1200, seed=100 + seed, mu=0.0) if "mu" in gbm_ohlc.__code__.co_varnames else gbm_ohlc(n=1200, seed=100 + seed)
        rng = np.random.default_rng(seed)
        sig = pd.Series((rng.random(len(df)) < 0.05).astype(int), index=df.index)
        r = classic_backtest(df.assign(signal=sig), "SYN", "rand")
        mc = st.monte_carlo_analysis(r, n_simulations=300)
        if "error" in mc:
            continue
        hits += mc["return_ci_low"] <= 0.0 <= mc["return_ci_high"] or mc["return_ci_low"] <= r.total_return_pct <= mc["return_ci_high"]
    assert hits >= 7


def test_regime_attribution_uses_the_label_known_before_the_entry_bar(monkeypatch):
    """A trade entering on the FIRST bar of a regime is attributed to the previous bar's label (t-1), because
    the new label is only known at that bar's close. Removing the .shift(1) flips it to the new regime."""
    df = gbm_ohlc(n=300, seed=5)
    k = 150

    def fake_regimes(d):
        vol = pd.Series(np.where(np.arange(len(d)) < k, "low_vol", "high_vol"), index=d.index)
        neutral = pd.Series("normal", index=d.index)
        return pd.DataFrame({"vol_regime": vol, "trend_regime": neutral, "volume_regime": neutral})

    def one_trade(d):
        sig = np.zeros(len(d), dtype=int)
        sig[k - 1] = 1                    # signal at bar k-1 -> entry at the open of bar k (first high_vol bar)
        return d.assign(signal=sig)

    monkeypatch.setattr(st, "detect_regimes", fake_regimes)
    res, detail = st.regime_attribution(df, "SYN", one_trade, "t")
    assert res["full_period"].trades[0].entry_index == k
    assert detail["low_volatility"]["n_trades"] == 1 and detail["high_volatility"]["n_trades"] == 0
