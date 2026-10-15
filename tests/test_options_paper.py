"""Options paper test rules (scripts/options_paper.py). Invented prices only."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "options_paper", Path(__file__).resolve().parent.parent / "scripts" / "options_paper.py")
op = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(op)

ASOF = "2026-10-15"


def _closes(last: float, n: int = 60, base: float = 100.0, end: str = ASOF) -> pd.Series:
    idx = pd.bdate_range(end=end, periods=n)
    vals = [base] * (n - 1) + [last]
    return pd.Series(vals, index=idx, dtype=float)


def _q(cid, und, right, strike, expiry, bid, ask, delta):
    return {"contract_id": cid, "underlying": und, "right": right, "strike": strike, "expiry": expiry,
            "bid": bid, "ask": ask, "delta": delta}


def test_signal_breakout_and_breakdown():
    assert op.signal(_closes(105)) == "C"
    assert op.signal(_closes(95)) == "P"
    rising = pd.Series(range(1, 61), index=pd.bdate_range(end=ASOF, periods=60), dtype=float)
    assert op.signal(rising) == "C"
    assert op.signal(_closes(105, n=40)) is None  # not enough history


def test_flat_series_has_no_signal():
    flat = pd.Series([100.0] * 60, index=pd.bdate_range(end=ASOF, periods=60))
    assert op.signal(flat) is None


def test_lottery_picks_largest_delta_that_fits_the_budget():
    qs = [_q(1, "QQQ", "C", 106, "2026-11-13", 2.00, 2.10, 0.30),   # $210 > budget 150
          _q(2, "QQQ", "C", 108, "2026-11-13", 1.20, 1.30, 0.20),   # fits, best delta
          _q(3, "QQQ", "C", 110, "2026-11-13", 0.60, 0.65, 0.12),
          _q(4, "QQQ", "C", 99, "2026-11-13", 6.0, 6.1, 0.6),       # ITM: excluded
          _q(5, "QQQ", "C", 108, "2026-12-18", 2.0, 2.05, 0.30)]    # wrong expiry (64 days)
    q, qty = op.choose_lottery(qs, "QQQ", "C", 105.0, ASOF, 150.0)
    assert q["contract_id"] == 2 and qty == 1


def test_lottery_rejects_wide_spreads():
    qs = [_q(1, "QQQ", "C", 108, "2026-11-13", 0.50, 1.00, 0.2)]
    assert op.choose_lottery(qs, "QQQ", "C", 105.0, ASOF, 150.0) is None


def test_put_writer_picks_delta_near_020_and_is_cash_secured():
    qs = [_q(1, "SPY", "P", 700, "2026-11-20", 3.0, 3.1, -0.15),
          _q(2, "SPY", "P", 720, "2026-11-20", 5.0, 5.1, -0.21),
          _q(3, "SPY", "P", 740, "2026-11-20", 8.0, 8.1, -0.30)]
    q, qty = op.choose_put(qs, ASOF, 100_000)
    assert q["contract_id"] == 2 and qty == 1
    assert op.choose_put(qs, ASOF, 50_000) is None


def _state_after_entry():
    closes = {"QQQ": _closes(105), "SPY": _closes(100.0)}  # QQQ call signal; SPY none
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 1.20, 1.30, 0.20),
              _q(9, "SPY", "P", 720, "2026-11-20", 1.00, 1.10, -0.20)]
    state, row = op.step(op.new_state(), ASOF, quotes, closes)
    return state, row


def test_step_enters_both_books_and_accounts_cash():
    state, row = _state_after_entry()
    L, P = state["books"]["L"], state["books"]["P"]
    assert L["positions"][0]["contract_id"] == 2
    assert L["cash"] == pytest.approx(500 - 130 - 0.65)
    assert P["cash"] == pytest.approx(100_000 + 100 - 0.65)
    assert row["books"]["L"]["equity"] == pytest.approx(500 - 130 - 0.65 + 125)
    with pytest.raises(ValueError):
        op.step(state, ASOF, [], {"QQQ": _closes(105), "SPY": _closes(100.0)})


def test_take_profit_and_put_buyback():
    state, _ = _state_after_entry()
    nxt = "2026-10-16"
    closes = {"QQQ": _closes(105, end=nxt), "SPY": _closes(100.0, end=nxt)}
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 2.70, 2.80, 0.40),
              _q(9, "SPY", "P", 720, "2026-11-20", 0.40, 0.45, -0.10)]
    state, row = op.step(state, nxt, quotes, closes)
    L, P = state["books"]["L"], state["books"]["P"]
    assert L["closed"][0]["reason"] == "take profit (2x)"
    assert L["closed"][0]["pnl"] == pytest.approx((2.70 - 1.30) * 100 - 1.30)
    assert P["closed"][0]["reason"].startswith("take profit")
    assert P["closed"][0]["pnl"] == pytest.approx((1.00 - 0.45) * 100 - 1.30)


def test_expired_position_settles_at_intrinsic():
    state = op.new_state()
    state["books"]["L"]["positions"].append(
        {"contract_id": 7, "underlying": "SPY", "right": "C", "strike": 95.0, "expiry": "2026-10-14", "qty": 1,
         "side": "long", "entry_price": 1.0, "entry_date": "2026-09-20", "last_mid": 1.0})
    state["books"]["L"]["cash"] = 399.35
    closes = {"QQQ": _closes(100.0), "SPY": _closes(100.0)}
    state, _ = op.step(state, ASOF, [], closes)
    t = state["books"]["L"]["closed"][0]
    assert t["exit_price"] == 5.0 and t["reason"] == "expired (intrinsic)"


def test_plan_lists_open_contracts_and_wanted_entries():
    state, _ = _state_after_entry()
    out = op.plan(state, ASOF, {"QQQ": _closes(105), "SPY": _closes(95)})
    assert [c["contract_id"] for c in out["open_contracts"]] == [2, 9]
    assert [(e["book"], e["underlying"], e["right"]) for e in out["entries"]] == [("L", "SPY", "P")]
