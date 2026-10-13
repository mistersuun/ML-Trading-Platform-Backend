"""Golden / closed-form tests for engine.py and the classic_backtest compatibility wrapper (WS2.2)."""
import json

import numpy as np
import pandas as pd
import pytest

import config
import engine
import metrics
from backtester import BacktestResult, Trade, classic_backtest

C0 = float(config.BACKTEST_INITIAL_CAPITAL)
SIZE = config.MAX_POSITION_SIZE_PCT
SLIP, COM = 0.001, 0.0005


def make(rows, signals):
    idx = pd.bdate_range("2024-01-01", periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 1_000_000
    df["signal"] = 0
    for i, s in signals.items():
        df.iloc[i, df.columns.get_loc("signal")] = s
    return df


def flat(n, p=100.0, signals=None):
    return make([[p, p, p, p]] * n, signals or {})


# ------------------------------------------------------------------ 8-bar golden fixture
GOLD_ROWS = [
    [100, 101, 99, 100],     # 0  long signal (acted on at bar 1 open)
    [100, 101, 99, 100],     # 1  entry at open
    [100, 101, 99.5, 100.5], # 2
    [90, 91, 89, 90.5],      # 3  gaps 10% below the stop -> fills at the open
    [90, 90, 90, 90],        # 4  short signal
    [90, 91, 88, 89],        # 5  short entry at open
    [89, 89.5, 86, 86.5],    # 6  low through target (86.3136) -> target fill
    [86.5, 86.5, 86.5, 86.5],
]


def test_golden_8_bar_fixture():
    df = make(GOLD_ROWS, {0: 1, 4: -1})
    run = engine.run_backtest(df, commission=COM, slippage=SLIP, stop_loss=0.02, take_profit=0.04)
    t1, t2 = run.trades
    # --- trade 1: long, entry = next open * (1 + slip), stop gap fills at open (adverse slippage)
    assert (t1.entry_index, t1.exit_index) == (1, 3)
    assert t1.entry_date == df.index[1] != df.index[0]          # never the signal bar
    assert t1.entry_price == pytest.approx(100 * (1 + SLIP))
    assert t1.exit_reason == "stop_loss"
    assert t1.exit_price == pytest.approx(90 * (1 - SLIP))       # NOT the 98.098 stop level
    notional1 = C0 * SIZE
    units1 = notional1 / t1.entry_price
    pnl1 = units1 * (t1.exit_price - t1.entry_price) - COM * notional1 - COM * units1 * t1.exit_price
    assert t1.pnl_abs == pytest.approx(pnl1)
    assert t1.pnl_pct == pytest.approx(pnl1 / notional1)
    assert t1.bars_held == 2
    # --- trade 2: short, entry fill sells below the open, target fills at target (buy slippage)
    cash2 = C0 + pnl1
    assert (t2.entry_index, t2.exit_index) == (5, 6) and t2.direction == -1
    assert t2.entry_price == pytest.approx(90 * (1 - SLIP))
    tgt = t2.entry_price * 0.96
    assert t2.exit_reason == "take_profit"
    assert t2.exit_price == pytest.approx(tgt * (1 + SLIP))
    notional2 = cash2 * SIZE
    units2 = notional2 / t2.entry_price
    pnl2 = units2 * (t2.entry_price - t2.exit_price) - COM * notional2 - COM * units2 * t2.exit_price
    assert t2.pnl_abs == pytest.approx(pnl2)
    # --- equity: cash + MTM at each close
    eq = run.equity.to_numpy()
    assert len(eq) == len(df) == 8
    assert eq[0] == pytest.approx(C0)
    entry_cost = COM * notional1
    assert eq[1] == pytest.approx(C0 - entry_cost + units1 * (100 - t1.entry_price))
    assert eq[2] == pytest.approx(C0 - entry_cost + units1 * (100.5 - t1.entry_price))
    assert eq[3] == pytest.approx(C0 + pnl1)                      # closed on bar 3
    assert eq[4] == pytest.approx(C0 + pnl1)
    assert eq[5] == pytest.approx(cash2 - COM * notional2 + units2 * (t2.entry_price - 89))
    assert eq[6] == pytest.approx(cash2 + pnl2)
    assert eq[7] == pytest.approx(cash2 + pnl2)
    assert list(run.positions) == [0, 1, 1, 0, 0, -1, 0, 0]


def test_close_mode_is_explicit_opt_in_and_fills_at_signal_close():
    rows = [[100, 100, 100, 100]] * 3 + [[100, 100, 100, 102]] * 2 + [[102, 102, 102, 102]] * 5
    df = make(rows, {3: 1})
    assert config.EXECUTION_MODE == "next_open"
    nxt = engine.run_backtest(df, commission=0, slippage=SLIP)
    cls = engine.run_backtest(df, commission=0, slippage=SLIP, execution="close")
    assert nxt.execution == "next_open" and cls.execution == "close"
    assert nxt.trades[0].entry_index == 4 and nxt.trades[0].entry_price == pytest.approx(100 * (1 + SLIP))
    assert cls.trades[0].entry_index == 3 and cls.trades[0].entry_price == pytest.approx(102 * (1 + SLIP))
    with pytest.raises(ValueError):
        engine.run_backtest(df, execution="same_bar")


def test_last_bar_signal_is_never_executed():
    res = engine.run_backtest(flat(12, signals={11: 1}))
    assert res.trades == []


def test_target_gap_fills_at_open_and_target_fills_at_level_otherwise():
    # gap up through the 4% target: fill at the open
    rows = [[100, 100, 100, 100]] * 3 + [[110, 111, 109, 110]] * 3
    r = engine.run_backtest(make(rows, {0: 1}), commission=0, slippage=0)
    assert r.trades[0].exit_reason == "take_profit" and r.trades[0].exit_price == pytest.approx(110.0)
    # intrabar touch without a gap: fill at the target level exactly
    rows = [[100, 100, 100, 100]] * 3 + [[101, 105, 100, 104]] * 3
    r = engine.run_backtest(make(rows, {0: 1}), commission=0, slippage=0)
    assert r.trades[0].exit_price == pytest.approx(104.0)


def test_pessimistic_tie_break_stop_first():
    rows = [[100, 100, 100, 100]] * 3 + [[100, 110, 90, 100]] * 3  # both 98 stop and 104 target touched
    r = engine.run_backtest(make(rows, {0: 1}), commission=0, slippage=0)
    assert r.trades[0].exit_reason == "stop_loss" and r.trades[0].exit_price == pytest.approx(98.0)


def test_stop_checked_on_entry_bar_after_open_entry():
    rows = [[100, 100, 100, 100]] * 2 + [[100, 101, 97, 99]] + [[99, 99, 99, 99]] * 3
    r = engine.run_backtest(make(rows, {1: 1}), commission=0, slippage=0)
    t = r.trades[0]
    assert t.entry_index == t.exit_index == 2 and t.bars_held == 0 and t.exit_price == pytest.approx(98.0)


def test_short_stop_gap_up_fills_at_open():
    rows = [[100, 100, 100, 100]] * 3 + [[110, 111, 109, 110]] * 3
    r = engine.run_backtest(make(rows, {0: -1}), commission=0, slippage=0)
    t = r.trades[0]
    assert t.direction == -1 and t.exit_reason == "stop_loss" and t.exit_price == pytest.approx(110.0)
    assert t.pnl_pct == pytest.approx(-0.10)


def test_flip_is_two_fills_at_next_open_with_costs():
    rows = [[100, 100, 100, 100]] * 3 + [[100, 100, 100, 100]] + [[101, 101.5, 100.5, 101]] * 6
    df = make(rows, {1: 1, 4: -1})
    r = engine.run_backtest(df, commission=COM, slippage=SLIP, stop_loss=None, take_profit=None)
    t1, t2 = r.trades[:2]
    assert (t1.exit_index, t2.entry_index) == (5, 5)             # both at the t+1 open
    assert t1.exit_reason == "signal" and t1.exit_price == pytest.approx(101 * (1 - SLIP))
    assert t2.direction == -1 and t2.entry_price == pytest.approx(101 * (1 - SLIP))
    assert t2.exit_reason == "end_of_data" and t2.entry_index == 5 and t2.exit_index == len(df) - 1
    # end-of-data close pays slippage + commission: buy back above the last close
    assert t2.exit_price == pytest.approx(101 * (1 + SLIP))


def test_same_side_signal_ignored_while_in_position():
    r = engine.run_backtest(flat(20, signals={1: 1, 5: 1, 8: 1}), stop_loss=None, take_profit=None)
    assert len(r.trades) == 1 and r.trades[0].exit_reason == "end_of_data"


# ------------------------------------------------------------------ closed-form equity
def test_buy_and_hold_equity_closed_form_no_costs():
    rng = np.random.default_rng(config.SEED)
    n = 60
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    opn = np.concatenate([[100.0], close[:-1]])
    df = make(np.column_stack([opn, np.maximum(opn, close) * 1.0005, np.minimum(opn, close) * 0.9995, close]), {0: 1})
    run = engine.run_backtest(df, commission=0, slippage=0, stop_loss=None, take_profit=None)
    p0 = opn[1]
    expected = C0 * (1 + SIZE * (close / p0 - 1))
    assert run.equity.iloc[0] == pytest.approx(C0)
    np.testing.assert_allclose(run.equity.to_numpy()[1:], expected[1:], rtol=1e-12)
    assert len(run.equity) == n


def test_buy_and_hold_with_costs_matches_closed_form_minus_costs():
    n = 30
    close = 100 + np.arange(n) * 0.2
    opn = np.concatenate([[100.0], close[:-1]])
    df = make(np.column_stack([opn, close + 0.1, opn - 0.1, close]), {0: 1})
    run = engine.run_backtest(df, commission=COM, slippage=SLIP, stop_loss=None, take_profit=None)
    fill = opn[1] * (1 + SLIP)
    notional = C0 * SIZE
    units = notional / fill
    for t in range(1, n - 1):
        assert run.equity.iloc[t] == pytest.approx(C0 - COM * notional + units * (close[t] - fill))
    # final point includes the end-of-data close-out (sell slippage + exit commission)
    xfill = close[-1] * (1 - SLIP)
    assert run.equity.iloc[-1] == pytest.approx(C0 - COM * notional + units * (xfill - fill) - COM * units * xfill)
    assert run.equity.iloc[-1] == pytest.approx(C0 + run.trades[0].pnl_abs)


def test_bars_held_equals_exit_minus_entry_index():
    df = flat(60, signals={5: 1, 12: -1, 25: 1})
    res = classic_backtest(df, "T", "p", commission=0, slippage=0)
    assert len(res.trades) >= 3
    for t in res.trades:
        assert t.bars_held == t.exit_index - t.entry_index
        assert t.bars_held == df.index.get_loc(t.exit_date) - df.index.get_loc(t.entry_date)


# ------------------------------------------------------------------ wrapper / summary safety
def test_flat_zero_trade_run_sharpe_is_zero_not_nan():
    res = classic_backtest(flat(60), "T", "p")
    assert res.total_trades == 0 and res.sharpe_ratio == 0.0 and res.sortino_ratio == 0.0
    assert np.isfinite(res.sharpe_ratio) and len(res.equity_curve) == 60
    json.dumps(res.numeric_summary(), allow_nan=False)
    json.dumps(res.summary(), allow_nan=False)


def test_flat_run_with_a_trade_has_zero_variance_sharpe_zero():
    df = flat(40, signals={3: 1})
    res = classic_backtest(df, "T", "p", commission=0, slippage=0)
    assert res.total_trades == 1 and res.sharpe_ratio == 0.0


def _wins_frame(win: bool):
    px = 104.5 if win else 95.0
    rows = [[100, 100, 100, 100]] * 6 + [[100, max(100, px), min(100, px), px]] + [[px, px, px, px]] * 20
    return make(rows, {3: 1})


@pytest.mark.parametrize("win", [True, False])
def test_summary_json_safe_all_win_and_all_loss(win):
    res = classic_backtest(_wins_frame(win), "T", "p", commission=0, slippage=0)
    assert res.total_trades == 1 and (res.winning_trades == 1) == win
    ns = res.numeric_summary()
    s = json.dumps(ns, allow_nan=False)
    assert all(v is None or isinstance(v, (int, float)) for v in json.loads(s).values())
    json.dumps(res.summary(), allow_nan=False)
    if win:
        assert ns["profit_factor"] is None and res.profit_factor_undefined
        assert res.profit_factor == 10.0
    else:
        assert ns["profit_factor"] == 0.0 and ns["win_rate"] == 0.0


def test_backtest_result_is_compat_and_has_new_fields():
    res = classic_backtest(flat(30, signals={3: 1}), "T", "p")
    assert isinstance(res, BacktestResult) and isinstance(res.trades[0], Trade)
    assert res.trades[0].entry_index == 4 and res.trades[0].exit_index == 29
    for f in ("total_trades", "win_rate", "profit_factor", "sharpe_ratio", "max_drawdown_pct", "equity_curve"):
        assert hasattr(res, f)
    assert res.execution == "next_open"


def test_is_valid_is_a_display_heuristic_gated_by_min_trades_oos():
    res = BacktestResult("T", "p", total_trades=config.MIN_TRADES_OOS - 1, win_rate=0.9, profit_factor=3, sharpe_ratio=2)
    assert not res.is_valid
    res.total_trades = config.MIN_TRADES_OOS
    assert res.is_valid


def test_short_frame_returns_aligned_flat_curve():
    res = classic_backtest(flat(8, signals={2: 1}), "T", "p")
    assert len(res.equity_curve) == 8 and res.total_trades == 0


# ------------------------------------------------------------------ slow statistical check
def _path_consistent_walks(n_walks, n_bars, sub=8, step=1.0, p0=100.0, seed=config.SEED):
    """Random walks whose high/low come from a sub-bar path (so stop/target touches are unbiased)."""
    rng = np.random.default_rng(seed)
    inc = rng.normal(0, step / np.sqrt(sub), (n_walks, n_bars, sub))
    path = p0 + np.cumsum(inc.reshape(n_walks, -1), axis=1)
    path = np.maximum(np.concatenate([np.full((n_walks, 1), p0), path], axis=1), 1.0).reshape(n_walks, -1)
    return path, sub


@pytest.mark.slow
def test_random_walk_random_entries_mean_sharpe_near_zero_1000_walks():
    n_walks, n_bars = 1000, 250
    path, sub = _path_consistent_walks(n_walks, n_bars)
    sig_rng = np.random.default_rng(config.SEED + 1)
    idx = pd.bdate_range("2020-01-01", periods=n_bars)
    sharpes = []
    for w in range(n_walks):
        p = path[w]
        seg = np.stack([p[k * sub:(k + 1) * sub + 1] for k in range(n_bars)])
        df = pd.DataFrame({"Open": seg[:, 0], "High": seg.max(axis=1), "Low": seg.min(axis=1), "Close": seg[:, -1]}, index=idx)
        sig = sig_rng.choice([0, 1, -1], size=n_bars, p=[0.9, 0.05, 0.05])
        run = engine.run_backtest(df, signal=pd.Series(sig, index=idx), commission=0, slippage=0)
        sharpes.append(metrics.sharpe(metrics.equity_returns(run.equity.to_numpy(), run.initial_capital)))
    mean = float(np.mean(sharpes))
    assert abs(mean) < 0.2, f"mean Sharpe {mean:.3f}"
