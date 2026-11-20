"""Put sprint (scripts/sprint_paper.py). Synthetic prices and quotes only."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sprint_paper as sp  # noqa: E402

D = "2026-01-05"
DOWN = {"last": 99.0, "open": 100.0, "prior_close": 100.5}
UP = {"last": 101.0, "open": 100.0, "prior_close": 100.5}


def _q(cid, bid, ask, strike=98.0, expiry=D):
    return {"contract_id": cid, "right": "P", "strike": strike, "expiry": expiry, "bid": bid, "ask": ask}


def _snap(hhmm, spy=DOWN, quotes=(), final=False, date=D):
    return {"ts": f"{date}T{hhmm}", "final": final, "spy": spy, "quotes": list(quotes)}


def test_strike_and_entry_signal():
    assert sp.strike_for(715.80) == 712.0
    assert sp.entry_signal(DOWN) and not sp.entry_signal(UP)
    assert not sp.entry_signal({"last": 100.2, "open": 100.0, "prior_close": 100.5})


def test_entry_sizes_half_the_cash_and_waits_when_no_signal():
    st = sp.new_state()
    row = sp.check(st, _snap("10:05", UP, [_q(1, 0.95, 1.00)]))
    assert st["position"] is None and "no entry" in row["events"][0]
    sp.check(st, _snap("11:05", DOWN, [_q(1, 0.95, 1.00)]))
    assert st["position"]["qty"] == 4                       # 500 / 100.65
    assert st["cash"] == pytest.approx(1000 - 4 * 100.65)


def test_one_contract_up_to_75_percent_and_too_expensive():
    st = sp.new_state()
    sp.check(st, _snap("10:05", DOWN, [_q(1, 6.9, 7.0)]))   # 700.65 <= 750
    assert st["position"]["qty"] == 1
    st2 = sp.new_state()
    row = sp.check(st2, _snap("10:05", DOWN, [_q(1, 7.9, 8.0)]))
    assert st2["position"] is None and "does not fit" in row["events"][0]


def test_take_profit_then_reentry_at_the_same_check():
    st = sp.new_state()
    sp.check(st, _snap("10:05", DOWN, [_q(1, 0.95, 1.00)]))
    lower = {"last": 97.0, "open": 100.0, "prior_close": 100.5}
    row = sp.check(st, _snap("11:05", lower, [_q(1, 2.10, 2.20), _q(2, 0.90, 1.00, strike=96.0)]))
    assert st["trades"][0]["why"] == "take profit" and st["trades"][0]["exit"] == 2.10
    assert st["position"]["strike"] == 96.0 and st["day"]["entries"] == 2
    assert any("take profit" in e for e in row["events"])


def test_stop_blocks_reentry_that_day_and_next_day_resets():
    st = sp.new_state()
    sp.check(st, _snap("10:05", DOWN, [_q(1, 0.95, 1.00)]))
    sp.check(st, _snap("11:05", DOWN, [_q(1, 0.45, 0.50), _q(2, 0.9, 1.0)]))
    assert st["trades"][0]["why"] == "stop" and st["position"] is None and st["day"]["stopped"]
    sp.check(st, _snap("12:05", DOWN, [_q(2, 0.9, 1.0)]))
    assert st["position"] is None
    sp.check(st, _snap("10:05", DOWN, [_q(3, 0.9, 1.0, expiry="2026-01-06")], date="2026-01-06"))
    assert st["position"] is not None


def test_final_check_sells_and_no_entry_after_two_pm():
    st = sp.new_state()
    sp.check(st, _snap("14:05", DOWN, [_q(1, 0.95, 1.00)]))
    assert st["position"] is not None
    sp.check(st, _snap("15:50", DOWN, [_q(1, 0.30, 0.35)], final=True))
    assert st["position"] is None and st["trades"][0]["why"] == "final check"
    st2 = sp.new_state()
    sp.check(st2, _snap("15:05", DOWN, [_q(1, 0.95, 1.00)]))
    assert st2["position"] is None                          # 15:05 is after the last entry hour


def test_missed_final_check_is_a_total_loss_and_busted_status():
    st = sp.new_state()
    sp.check(st, _snap("10:05", DOWN, [_q(1, 0.95, 1.00)]))
    row = sp.check(st, _snap("10:05", UP, [], date="2026-01-06"))
    assert st["trades"][0]["why"] == "final check missed" and st["trades"][0]["pnl"] == -st["trades"][0]["cost"]
    st2 = sp.new_state()
    st2["cash"] = 90.0
    assert sp.check(st2, _snap("10:05", UP))["status"] == "busted"


def test_target_stops_the_account():
    st = sp.new_state()
    st["cash"] = 150_000.0
    sp.check(st, _snap("10:05", DOWN, [_q(1, 0.95, 1.00)]))
    row = sp.check(st, _snap("11:05", DOWN, [_q(1, 1.80, 1.90)]))
    assert row["status"] == "target" and st["position"] is None
