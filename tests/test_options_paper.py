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


def test_momentum_call_walks_the_ranking_until_one_is_affordable():
    qs = [_q(11, "AMD", "C", 260, "2027-01-15", 15.0, 15.5, 0.35),        # $1,550: too dear for a $500 budget
          _q(12, "MRK", "C", 110, "2027-01-15", 3.0, 3.1, 0.30),
          _q(13, "MRK", "C", 115, "2027-01-15", 1.6, 1.7, 0.20),
          _q(14, "MRK", "C", 100, "2027-01-15", 8.0, 8.2, 0.60)]           # ITM: excluded
    q, qty = op.choose_momentum_call(qs, ["AMD", "MRK"], {"AMD": 240.0, "MRK": 105.0}, ASOF, 500.0, set())
    assert q["contract_id"] == 12 and qty == 1
    assert op.choose_momentum_call(qs, ["AMD", "MRK"], {"AMD": 240.0, "MRK": 105.0}, ASOF, 500.0, {"MRK"}) is None


def test_book_m_enters_and_takes_profit_at_3x():
    closes = {"QQQ": _closes(100.0), "SPY": _closes(100.0), "MRK": _closes(105.0)}
    qs = [_q(12, "MRK", "C", 110, "2027-01-15", 3.0, 3.1, 0.30)]
    state, row = op.step(op.new_state(), ASOF, qs, closes, ["MRK"])
    M = state["books"]["M"]
    assert M["positions"][0]["contract_id"] == 12 and M["cash"] == pytest.approx(1000 - 310 - 0.65)
    nxt = "2026-10-16"
    closes = {k: _closes(v.iloc[-1], end=nxt) for k, v in closes.items()}
    state, _ = op.step(state, nxt, [_q(12, "MRK", "C", 110, "2027-01-15", 9.4, 9.6, 0.7)], closes, ["MRK"])
    assert state["books"]["M"]["closed"][0]["reason"] == "take profit (3x)"


def test_quote_without_bid_holds_the_position_instead_of_crashing():
    state, _ = _state_after_entry()
    nxt = "2026-10-16"
    closes = {"QQQ": _closes(105, end=nxt), "SPY": _closes(100.0, end=nxt)}
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", None, 2.80, 0.40),
              _q(9, "SPY", "P", 720, "2026-11-20", None, 0.45, -0.10)]
    state, row = op.step(state, nxt, quotes, closes)
    assert len(state["books"]["L"]["positions"]) == 1 and len(state["books"]["P"]["positions"]) == 1
    assert any("no usable quote" in e for e in row["events"]["L"])


def _next_day_fixture():
    state, _ = _state_after_entry()
    nxt = "2026-10-16"
    closes = {"QQQ": _closes(105, end=nxt), "SPY": _closes(100.0, end=nxt)}
    return state, nxt, closes


def test_plan_with_quotes_lists_reentry_after_a_take_profit_tonight():
    state, nxt, closes = _next_day_fixture()
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 2.70, 2.80, 0.40),
              _q(9, "SPY", "P", 720, "2026-11-20", 1.00, 1.10, -0.20)]
    base = op.plan(state, nxt, closes)
    assert base["entries"] == []
    out = op.plan(state, nxt, closes, None, quotes)
    assert [(e["book"], e["underlying"], e["right"]) for e in out["entries"]] == [("L", "QQQ", "C")]
    assert out["entries"][0]["because"] == "exit tonight: QQQ 2026-11-13 108C take profit (2x)"
    assert [c["contract_id"] for c in out["open_contracts"]] == [2, 9]


def test_plan_with_quotes_and_no_exit_adds_nothing():
    state, nxt, closes = _next_day_fixture()
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 1.20, 1.30, 0.20),
              _q(9, "SPY", "P", 720, "2026-11-20", 1.00, 1.10, -0.20)]
    assert op.plan(state, nxt, closes, None, quotes)["entries"] == op.plan(state, nxt, closes)["entries"]


def test_plan_with_quotes_marks_book_p_reentry_and_book_m_ranking():
    state, nxt, closes = _next_day_fixture()
    state["books"]["M"]["positions"] = [
        {"contract_id": 12, "underlying": "MRK", "right": "C", "strike": 110.0, "expiry": "2027-01-15", "qty": 1,
         "side": "long", "entry_price": 3.1, "entry_date": "2026-10-15", "last_mid": 3.1}]
    closes["MRK"] = _closes(105.0, end=nxt)
    closes["AMD"] = _closes(240.0, end=nxt)
    ranking = ["MRK", "AMD"]
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 1.20, 1.30, 0.20),
              _q(9, "SPY", "P", 720, "2026-11-20", 0.40, 0.45, -0.10),
              _q(12, "MRK", "C", 110, "2027-01-15", 9.4, 9.6, 0.7)]
    before = op.plan(state, nxt, closes, ranking)
    assert [e["underlyings_in_order"] for e in before["entries"] if e["book"] == "M"] == [["AMD"]]
    out = op.plan(state, nxt, closes, ranking, quotes)
    by_book = {e["book"]: e for e in out["entries"]}
    assert by_book["P"]["because"].startswith("exit tonight: SPY 2026-11-20 720P take profit")
    assert by_book["M"]["underlyings_in_order"] == ["MRK", "AMD"]
    assert by_book["M"]["because"] == "exit tonight: MRK 2027-01-15 110C take profit (3x)"


def test_plan_with_quotes_does_not_touch_the_state():
    import copy
    state, nxt, closes = _next_day_fixture()
    snap = copy.deepcopy(state)
    quotes = [_q(2, "QQQ", "C", 108, "2026-11-13", 2.70, 2.80, 0.40),
              _q(9, "SPY", "P", 720, "2026-11-20", 0.40, 0.45, -0.10)]
    op.plan(state, nxt, closes, None, quotes)
    assert state == snap
    # step on the same inputs still gives the same result as the plain path (plan has no side effects)
    s1, r1 = op.step(copy.deepcopy(state), nxt, quotes, closes)
    op.plan(state, nxt, closes, None, quotes)
    s2, r2 = op.step(copy.deepcopy(state), nxt, quotes, closes)
    assert s1 == s2 and r1 == r2
    assert s1["books"]["L"]["closed"][0]["reason"] == "take profit (2x)"
