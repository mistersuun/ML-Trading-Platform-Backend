"""Bug-pinning tests for the classic backtester (WS0.3: BT-1..BT-7, WF-1).

Each xfail(strict) test asserts the CORRECT behaviour on hand-built frames and currently
fails with AssertionError. Tests without xfail are passing regression guards.
"""
import numpy as np
import pandas as pd
import pytest

import config
from backtester import classic_backtest, walk_forward_validate
from tests.fixtures.synthetic import gap_through_stop_frame, gbm_ohlc, random_walk_ohlc

BUG = dict(strict=True, raises=AssertionError)
NO_COST = dict(commission=0.0, slippage=0.0)


# --------------------------------------------------------------------------- helpers
def flat_frame(n=40, price=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({"Open": price, "High": price * 1.005, "Low": price * 0.995,
                         "Close": price, "Volume": 1_000_000}, index=idx)


def set_bars(df, start, end=None, *, open_, close, high=None, low=None):
    """Overwrite OHLC for bars [start, end) (end=None -> to the end of the frame)."""
    sl = slice(start, end)
    df.iloc[sl, df.columns.get_loc("Open")] = open_
    df.iloc[sl, df.columns.get_loc("Close")] = close
    df.iloc[sl, df.columns.get_loc("High")] = high if high is not None else max(open_, close) * 1.005
    df.iloc[sl, df.columns.get_loc("Low")] = low if low is not None else min(open_, close) * 0.995
    return df


def with_signals(df, mapping):
    df = df.copy()
    df["signal"] = 0
    for pos, sig in mapping.items():
        df.iloc[pos, df.columns.get_loc("signal")] = sig
    return df


# --------------------------------------------------------------------------- BT-1
@pytest.mark.xfail(**BUG, reason="BT-1: entry fills at signal-bar close, not next bar open")
def test_BT_1_entry_on_next_bar_open():
    df = flat_frame(40, 100.0)
    # from bar 11 on, price is 103 (open and close); the signal bar (10) closes at 100
    set_bars(df, 11, open_=103.0, close=103.0, high=103.5, low=102.5)
    df = with_signals(df, {10: 1, 20: -1})
    res = classic_backtest(df, "T", "p", **NO_COST)
    assert res.trades, "expected at least one trade"
    t = res.trades[0]
    assert t.entry_date == df.index[11]
    assert t.entry_price == pytest.approx(103.0)


# --------------------------------------------------------------------------- BT-2
@pytest.mark.xfail(**BUG, reason="BT-2: stop gap-through fills at stop level instead of the gap open")
def test_BT_2_stop_gap_through_fills_at_open():
    df = with_signals(gap_through_stop_frame(), {5: 1})  # gap bar iloc[30] opens at 80
    res = classic_backtest(df, "T", "p", **NO_COST)
    assert res.trades, "expected a trade"
    t = res.trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_date == df.index[30]
    assert t.exit_price <= 80.0 + 1e-9  # cannot be filled at the 98 stop on a gap to 80


# --------------------------------------------------------------------------- BT-3
@pytest.mark.xfail(**BUG, reason="BT-3: equity only changes on exit, not marked to market each bar")
def test_BT_3_equity_marked_to_market_every_bar():
    n = 30
    df = flat_frame(n, 100.0)
    # slow drift down 0.05%/bar after the signal, never reaching stop (2%) or target
    closes = 100.0 * (1 - 0.0005) ** np.arange(0, n - 5)
    for k, c in enumerate(closes):
        set_bars(df, 5 + k, 6 + k, open_=c, close=c, high=c * 1.0005, low=c * 0.9995)
    df = with_signals(df, {5: 1})
    res = classic_backtest(df, "T", "p", **NO_COST)
    eq = res.equity_curve
    start = float(config.BACKTEST_INITIAL_CAPITAL)
    # position open and under water on bar 20: equity must be below starting capital
    assert float(eq.iloc[20]) < start - 1e-9
    # and strictly decreasing-ish (not flat) while the position is held
    assert eq.iloc[10:25].nunique() > 3


# --------------------------------------------------------------------------- BT-4
@pytest.mark.xfail(**BUG, reason="BT-4: bars_held computed as i - previous trade's bars_held")
def test_BT_4_bars_held_is_exit_minus_entry_index():
    df = flat_frame(60, 100.0)
    # long entered bar 5, exited by -1 signal at bar 12 (which also opens a short),
    # short exited by +1 at bar 25.
    df = with_signals(df, {5: 1, 12: -1, 25: 1})
    res = classic_backtest(df, "T", "p", **NO_COST)
    assert len(res.trades) >= 2
    for t in res.trades:
        if t.exit_date is None:
            continue
        expected = df.index.get_loc(t.exit_date) - df.index.get_loc(t.entry_date)
        assert t.bars_held == expected


# --------------------------------------------------------------------------- BT-5
def _random_entry_sharpe(seed):
    df = random_walk_ohlc(n=500, seed=seed, step=1.0, start_price=100.0)
    rng = np.random.default_rng(10_000 + seed)
    df["signal"] = rng.choice([0, 1, -1], size=len(df), p=[0.9, 0.05, 0.05])
    return classic_backtest(df, "T", "rand", **NO_COST).sharpe_ratio


@pytest.mark.xfail(**BUG, reason="BT-5: Sharpe subtracts rf/252 from sparse per-bar returns, biasing it negative")
def test_BT_5_random_walk_sharpe_near_zero(monkeypatch):
    # rf pinned to 0 so a one-line rf change cannot satisfy this; the engine itself must be unbiased.
    # (rf / cash-yield convention for the fixed engine is recorded in docs/decisions.md D9.)
    monkeypatch.setattr(config, "RISK_FREE_RATE", 0.0)
    sharpes = [_random_entry_sharpe(s) for s in range(40)]
    assert abs(float(np.mean(sharpes))) < 0.25, f"mean Sharpe {np.mean(sharpes):.2f}"


def _peek_frame(seed=7, n=400):
    df = random_walk_ohlc(n=n, seed=seed, step=1.0, start_price=100.0)
    c = df["Close"].to_numpy()
    sig = np.zeros(n, dtype=int)
    sig[:-1] = np.where(c[1:] > c[:-1], 1, -1)  # peeks at close[t+1]
    df["signal"] = sig
    return df


def test_BT_1a_canary_peeking_signal_is_profitable_today():
    """Characterization: with same-bar-close entry a look-ahead signal prints money (documents the bug)."""
    res = classic_backtest(_peek_frame(), "T", "peek", **NO_COST)
    assert res.total_return_pct > 0


@pytest.mark.xfail(**BUG, reason="BT-1b: look-ahead canary still profitable because entry is at signal-bar close")
def test_BT_1b_peeking_signal_not_profitable_with_next_bar_entry():
    res = classic_backtest(_peek_frame(), "T", "peek", **NO_COST)
    assert res.total_return_pct <= 0.10


def test_BT_5b_zero_signal_flat_curve_sharpe_is_zero():
    """Regression guard (does not reproduce today): a zero-trade flat curve has Sharpe 0."""
    df = with_signals(flat_frame(60, 100.0), {})
    res = classic_backtest(df, "T", "p")
    assert res.total_trades == 0
    assert res.sharpe_ratio == 0
    assert res.equity_curve.nunique() == 1


# --------------------------------------------------------------------------- BT-6
def test_BT_6_profit_factor_finite_and_capped_with_no_losses():
    df = flat_frame(40, 100.0)
    set_bars(df, 7, 8, open_=100.0, close=104.5, high=105.0, low=99.5)  # take-profit bar
    set_bars(df, 8, open_=104.5, close=104.5)
    df = with_signals(df, {5: 1})
    res = classic_backtest(df, "T", "p", **NO_COST)
    assert res.total_trades >= 1
    assert res.losing_trades == 0 and res.winning_trades >= 1
    assert np.isfinite(res.profit_factor)
    assert res.profit_factor <= 1000.0


# --------------------------------------------------------------------------- BT-7
@pytest.mark.xfail(**BUG, reason="BT-7: equity_curve drops the final end-of-data point / is empty for short frames")
def test_BT_7_equity_curve_covers_every_bar():
    # (a) open position force-closed at end of data: curve must end at final capital
    n = 30
    df = flat_frame(n, 100.0)
    set_bars(df, 20, open_=101.5, close=101.5, high=101.6, low=101.4)  # +1.5% by the end
    df = with_signals(df, {5: 1})
    res = classic_backtest(df, "T", "p", **NO_COST)
    assert len(res.equity_curve) == len(df)
    final_capital = config.BACKTEST_INITIAL_CAPITAL + sum(t.pnl_abs for t in res.trades)
    assert res.trades[-1].exit_reason == "end_of_data"
    assert float(res.equity_curve.iloc[-1]) == pytest.approx(final_capital)
    # (b) frames too short to backtest still return a curve aligned to the data
    short = with_signals(flat_frame(8, 100.0), {2: 1})
    assert len(classic_backtest(short, "T", "p").equity_curve) == len(short)


# --------------------------------------------------------------------------- WF-1
@pytest.mark.xfail(**BUG, reason="WF-1: pattern_func only sees the test slice; indicators are not warmed up")
def test_WF_1_walk_forward_uses_training_window_for_warmup():
    df = gbm_ohlc(400, seed=3)
    seen = []

    def pattern(frame):
        seen.append(frame.index[0])
        out = frame.copy()
        warmed = out["Close"].rolling(20).mean().notna()  # needs 20 bars of history
        out["signal"] = warmed.astype(int)
        return out

    results = walk_forward_validate(df, "T", pattern, "wf", n_splits=4, train_pct=0.7)
    assert results, "expected walk-forward folds"
    split = len(df) // 4
    train_start, train_end = 0, int(split * 0.7)
    # first fold: the pattern must have been given the training window as warm-up
    assert seen[0] <= df.index[train_end - 20]
    # and the first OOS trade must start right after the train/test boundary
    first = results[0].trades[0]
    assert first.entry_date <= df.index[train_end + 5]
