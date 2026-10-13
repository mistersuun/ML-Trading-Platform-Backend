"""Signal selection (WS1.5): staleness, one intent per (symbol, bar), direction-aware ML confidence."""
import json

import numpy as np
import pandas as pd
import pytest

import config
from backtester import classic_backtest
from execution import OrderIntent
from signals import build_intents, latest_atr, latest_signal, ml_confidence


def frame(signals):
    idx = pd.bdate_range("2024-01-01", periods=len(signals))
    return pd.DataFrame({"Close": 10.0, "High": 10.1, "Low": 9.9, "signal": signals}, index=idx)


def test_latest_nonzero_signal_wins():
    assert latest_signal(frame([0, 0, 1, 0, -1]))[0] == -1
    assert latest_signal(frame([0, 0, -1, 0, 1]))[0] == 1
    d, bar = latest_signal(frame([1, 0, -1]))
    assert d == -1 and bar == "2024-01-03"


def test_stale_signal_returns_zero():
    assert latest_signal(frame([0, 1, 0, 0, 0]))[0] == 0  # 3 bars old
    assert latest_signal(frame([0, 1, 0, 0, 0]), max_staleness_bars=3)[0] == 1
    assert latest_signal(frame([0, 0, 1, 0, 0]))[0] == 0  # 2 bars old, default allows 1
    assert latest_signal(frame([0, 0, 0, 1, 0]))[0] == 1  # 1 bar old


def test_no_signal_cases():
    assert latest_signal(frame([0, 0, 0])) == (0, None)
    assert latest_signal(pd.DataFrame({"Close": [1.0]})) == (0, None)
    assert latest_signal(frame([np.nan, np.nan, 1.0]))[0] == 1


def test_ml_confidence_is_direction_aligned():
    assert ml_confidence(0.15, -1) == pytest.approx(0.85)
    assert ml_confidence(0.85, 1) == pytest.approx(0.85)
    assert ml_confidence(0.15, 1) == pytest.approx(0.15)  # model disagrees with the direction
    assert ml_confidence(float("nan"), 1) == 0.0 and ml_confidence(0.7, 0) == 0.0
    assert ml_confidence(1.7, 1) == 1.0 and ml_confidence(-1, -1) == 1.0


def intent(strategy, conf=0.6, status="unvalidated", direction=1, symbol="AAPL", bar="2024-06-03"):
    return OrderIntent(symbol, direction, bar, strategy, conf, status, 1.0, 100.0)


def test_one_intent_per_symbol_bar():
    out = build_intents([intent("b"), intent("a"), intent("c", symbol="MSFT"), intent("d", bar="2024-06-04")])
    assert [(i.symbol, i.signal_bar_date) for i in out] == [("AAPL", "2024-06-03"), ("MSFT", "2024-06-03"),
                                                              ("AAPL", "2024-06-04")]
    assert out[0].strategy_key == "a"  # tie on tier and confidence -> smallest strategy key


def test_highest_validation_tier_wins_then_confidence():
    out = build_intents([intent("hi_conf", 0.9), intent("validated", 0.5, "oos_validated"),
                         intent("deflated", 0.4, "deflated_validated")])
    assert out[0].strategy_key == "deflated"
    out = build_intents([intent("x", 0.5, "oos_validated"), intent("y", 0.8, "oos_validated")])
    assert out[0].strategy_key == "y"


def test_deterministic_regardless_of_input_order():
    cands = [intent("a", 0.6), intent("b", 0.6), intent("c", 0.6, direction=-1)]
    first = build_intents(cands)
    assert build_intents(list(reversed(cands))) == first
    assert first[0].direction == -1  # exits win ties


def test_all_unvalidated_candidates_stay_ineligible():
    out = build_intents([intent("a"), intent("b", symbol="MSFT")])
    assert all(i.validation_status not in config.ORDER_ELIGIBLE_STATUSES for i in out)


def test_latest_atr():
    df = frame([0] * 30)
    assert latest_atr(df, 20) == pytest.approx(0.2)
    assert latest_atr(frame([0] * 5), 20) is None


def test_profit_factor_none_in_summary_when_no_losses():
    from tests.bugs.test_backtest_bugs import NO_COST, flat_frame as ff, set_bars, with_signals
    df = ff(40, 100.0)
    set_bars(df, 7, 8, open_=100.0, close=104.5, high=105.0, low=99.5)
    set_bars(df, 8, open_=104.5, close=104.5)
    res = classic_backtest(with_signals(df, {5: 1}), "T", "p", **NO_COST)
    s = res.summary()
    assert s["profit_factor"] is None and s["profit_factor_capped"] == 10.0
    assert res.profit_factor == 10.0 and res.profit_factor_capped == 10.0
    json.dumps(s)  # serialisable, no 1e9 / inf
