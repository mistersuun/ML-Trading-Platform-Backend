"""Manager's book (scripts/manager_book.py). Synthetic prices, quotes and decisions only."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import manager_book as mb  # noqa: E402

D1, D2, D3 = "2026-01-05", "2026-01-06", "2026-01-07"


def _closes(spy=(100.0, 100.0, 100.0)):
    idx = pd.to_datetime([D1, D2, D3])
    return {s: pd.Series(list(spy), index=idx) for s in mb.UNIVERSE}


def _q(cid, bid, ask, strike=101.0, right="C", expiry="2026-01-16", und="SPY", hi=None):
    return {"contract_id": cid, "underlying": und, "right": right, "strike": strike, "expiry": expiry, "bid": bid,
            "ask": ask, "session_high": hi}


def _buy(qty=1, strike=101.0, right="C", expiry="2026-01-16", und="SPY", tp=None, stop=None):
    return {"action": "buy", "underlying": und, "right": right, "strike": strike, "expiry": expiry, "qty": qty,
            "take_profit": tp, "stop": stop, "thesis": "t"}


def test_buy_at_ask_and_risk_limits():
    st = mb.new_state()
    row = mb.step(st, {"asof": D1, "actions": [_buy(qty=1)]}, [_q(1, 1.40, 1.50)], _closes())
    assert st["positions"][0]["cost"] == pytest.approx(150.65) and row["open"] == 1
    assert row["equity"] == pytest.approx(1000 - 150.65 + 140)          # marked at the bid
    st2 = mb.new_state()
    row2 = mb.step(st2, {"asof": D1, "actions": [_buy(qty=2)]}, [_q(1, 1.40, 1.50)], _closes())
    assert not st2["positions"] and "above 20%" in row2["events"][0]     # 301.30 > 20% of 1,000
    st3 = mb.new_state()
    row3 = mb.step(st3, {"asof": D1, "actions": [_buy(qty=1)]}, [_q(1, 2.80, 2.90)], _closes())
    assert len(st3["positions"]) == 1                                    # one contract up to 30% is allowed
    st4 = mb.new_state()
    row4 = mb.step(st4, {"asof": D1, "actions": [_buy(qty=1)]}, [_q(1, 3.10, 3.20)], _closes())
    assert not st4["positions"]


def test_one_per_underlying_two_new_a_night_and_total_cost_cap():
    st = mb.new_state()
    quotes = [_q(1, 1.4, 1.5), _q(2, 1.4, 1.5, strike=102.0), _q(3, 1.4, 1.5, und="QQQ"), _q(4, 1.4, 1.5, und="AMD")]
    acts = [_buy(), _buy(strike=102.0), _buy(und="QQQ"), _buy(und="AMD")]
    row = mb.step(st, {"asof": D1, "actions": acts}, quotes, _closes())
    assert [p["underlying"] for p in st["positions"]] == ["SPY", "QQQ"]
    assert any("one position per underlying" in e for e in row["events"])
    assert any("at most 2 new trades" in e for e in row["events"])


def test_take_profit_order_fills_at_limit_on_session_high_and_stop_at_bid():
    st = mb.new_state()
    mb.step(st, {"asof": D1, "actions": [_buy(tp=3.0, stop=0.7)]}, [_q(1, 1.40, 1.50)], _closes())
    row = mb.step(st, {"asof": D2, "actions": []}, [_q(1, 2.0, 2.1, hi=3.4)], _closes())
    assert st["closed"][0]["exit"] == 3.0 and st["closed"][0]["reason"] == "take-profit order"
    assert st["closed"][0]["pnl"] == pytest.approx(300 - 0.65 - 150.65)
    st2 = mb.new_state()
    mb.step(st2, {"asof": D1, "actions": [_buy(tp=3.0, stop=0.7)]}, [_q(1, 1.40, 1.50)], _closes())
    mb.step(st2, {"asof": D2, "actions": []}, [_q(1, 0.6, 0.7, hi=1.6)], _closes())
    assert st2["closed"][0]["reason"] == "stop" and st2["closed"][0]["exit"] == 0.6


def test_take_profit_never_fills_on_the_entry_night():
    st = mb.new_state()
    mb.step(st, {"asof": D1, "actions": [_buy(tp=1.45)]}, [_q(1, 1.40, 1.50, hi=9.9)], _closes())
    assert len(st["positions"]) == 1


def test_expiry_settles_at_intrinsic_and_manager_sell():
    st = mb.new_state()
    mb.step(st, {"asof": D1, "actions": [_buy(strike=99.0, right="P", expiry=D2)]},
            [_q(1, 1.0, 1.1, strike=99.0, right="P", expiry=D2)], _closes())
    mb.step(st, {"asof": D2, "actions": []}, [], _closes(spy=(100.0, 96.0, 96.0)))
    assert st["closed"][0]["exit"] == pytest.approx(3.0) and st["closed"][0]["reason"] == "expiry"
    st2 = mb.new_state()
    mb.step(st2, {"asof": D1, "actions": [_buy()]}, [_q(1, 1.4, 1.5)], _closes())
    mb.step(st2, {"asof": D2, "actions": [{"action": "sell", "contract_id": 1, "reason": "thesis broken"}]},
            [_q(1, 1.9, 2.0)], _closes())
    assert st2["closed"][0]["exit"] == 1.9 and st2["cash"] == pytest.approx(1000 - 150.65 + 190 - 0.65)


def test_no_trade_is_recorded_and_dates_must_increase():
    st = mb.new_state()
    row = mb.step(st, {"asof": D1, "actions": []}, [], _closes())
    assert row["events"] == ["manager: no trade tonight"] and row["equity"] == 1000.0
    with pytest.raises(SystemExit):
        mb.step(st, {"asof": D1, "actions": []}, [], _closes())
