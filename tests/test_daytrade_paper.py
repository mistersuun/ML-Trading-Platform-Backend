"""Day-trading ORB paper test (scripts/daytrade_paper.py). Invented bars only."""
import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "daytrade_paper", Path(__file__).resolve().parent.parent / "scripts" / "daytrade_paper.py")
dt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dt)

DAY = date(2026, 10, 15)


def _bars(rows):
    """rows: (open, high, low, close) from 09:30, then flat 100 bars to fill the session."""
    rows = list(rows) + [(rows[-1][3],) * 4] * (78 - len(rows))
    idx = pd.date_range(f"{DAY} 09:30", periods=78, freq="5min", tz=dt.NY)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"], dtype=float)


def test_long_stopped_out_at_the_opening_low():
    b = _bars([(100, 101, 99, 100.5), (100.6, 100.8, 98.9, 99.0)])
    r = dt.simulate_day(b, 25_000)
    entry = 100.6 * 1.0001
    shares = int(min(250 / (entry - 99), 4 * 25_000 / entry))
    assert r["side"] == "long" and r["reason"] == "stop" and r["shares"] == shares
    assert r["exit"] == pytest.approx(99 * 0.9999)
    assert r["r"] < -0.99


def test_short_runs_to_end_of_day():
    b = _bars([(100, 100.5, 99.5, 99.6), (99.5, 99.6, 99.0, 99.1)])
    r = dt.simulate_day(b, 25_000)
    assert r["side"] == "short" and r["reason"] == "end of day"
    assert r["pnl"] > 0


def test_target_and_stop_in_same_bar_counts_as_stop():
    b = _bars([(100, 100.2, 99.9, 100.1), (100.1, 200, 50, 100)])
    assert dt.simulate_day(b, 25_000)["reason"] == "stop"


def test_target_hit():
    b = _bars([(100, 100.2, 99.9, 100.1), (100.1, 100.2, 100.0, 100.1), (100.1, 105, 100.1, 104)])
    r = dt.simulate_day(b, 25_000)
    assert r["reason"] == "target (10R)" and r["r"] > 5


def test_doji_and_gap_beyond_stop_mean_no_trade():
    assert dt.simulate_day(_bars([(100, 101, 99, 100), (100, 100, 100, 100)]), 25_000)["trade"] is False
    assert dt.simulate_day(_bars([(100, 101, 99, 100.5), (98, 99, 97, 98)]), 25_000)["trade"] is False


def test_leverage_cap_binds_on_a_tight_range():
    b = _bars([(100, 100.01, 99.99, 100.005), (100.0, 100.0, 100.0, 100.0)])
    r = dt.simulate_day(b, 25_000)
    assert r["shares"] * r["entry"] <= 4 * 25_000 + 1e-6


def test_step_updates_equity_once_per_session():
    bars = {s: _bars([(100, 100.5, 99.5, 99.6), (99.5, 99.6, 99.0, 99.1)]) for s in dt.SYMBOLS}
    state, row = dt.step(dt.new_state(), DAY, bars)
    assert state["books"]["QQQ"]["equity"] > 25_000 and len(state["books"]["SPY"]["trades"]) == 1
    with pytest.raises(ValueError):
        dt.step(state, DAY, bars)
