"""Mover sprint (scripts/mover_paper.py). Synthetic prices and quotes only."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import mover_paper as mp  # noqa: E402

D = "2026-01-05"
FLAT = {"last": 100.0, "open": 100.0, "prior_close": 100.0}
PRICES = {"SPY": dict(FLAT), "NVDA": {"last": 94.0, "open": 99.0, "prior_close": 100.0},     # -6%: 2.0x
          "AAPL": {"last": 102.0, "open": 100.5, "prior_close": 100.0},                       # +2%: 1.2x
          "TSLA": {"last": 97.0, "open": 96.0, "prior_close": 100.0}}                         # below close, above open


def _q(cid, und, right, strike, bid, ask, expiry="2026-01-09"):
    return {"contract_id": cid, "underlying": und, "right": right, "strike": strike, "expiry": expiry, "bid": bid,
            "ask": ask}


def _snap(hhmm, kind="regular", prices=None, quotes=(), date=D):
    return {"ts": f"{date}T{hhmm}", "kind": kind, "prices": PRICES if prices is None else prices,
            "quotes": list(quotes)}


def test_rank_needs_a_typical_day_beyond_open_and_close():
    r = mp.rank(PRICES)
    assert [m["symbol"] for m in r] == ["NVDA", "AAPL"]
    assert r[0]["right"] == "P" and r[0]["strike_level"] == pytest.approx(94 * 0.985)
    assert r[1]["right"] == "C" and r[1]["strike_level"] == pytest.approx(102 * 1.0085, abs=0.01)


def test_order_then_fill_on_a_later_quote_and_size():
    st = mp.new_state()
    row = mp.check(st, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00), _q(2, "NVDA", "P", 93.0, 1.2, 1.3)]))
    assert st["pending"]["contract_id"] == 1 and st["position"] is None      # 93 is above the level 92.59
    assert "order: buy NVDA" in row["events"][0]
    mp.check(st, _snap("10:10", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 1.0, 1.1)]))
    assert st["position"] is None and st["pending"] is not None              # too early: 5 minutes
    mp.check(st, _snap("10:22", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 1.15, 1.20)]))
    assert st["pending"] is None and st["position"]["entry"] == 1.20 and st["position"]["qty"] == 4
    assert st["cash"] == pytest.approx(1000 - 4 * 120.65) and st["day"]["entries"] == 1


def test_expensive_first_choice_walks_out_then_to_the_next_mover():
    st = mp.new_state()
    quotes = [_q(1, "NVDA", "P", 92.0, 7.9, 8.0), _q(2, "NVDA", "P", 91.0, 6.9, 7.0),
              _q(3, "AAPL", "C", 103.0, 0.5, 0.6)]
    mp.check(st, _snap("10:05", quotes=quotes))
    assert st["pending"]["contract_id"] == 2                                 # one contract up to 75% of cash
    st2 = mp.new_state()
    mp.check(st2, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 7.9, 8.0), _q(3, "AAPL", "C", 103.0, 0.5, 0.6)]))
    assert st2["pending"]["underlying"] == "AAPL" and st2["pending"]["right"] == "C"


def test_stale_order_is_cancelled_and_final_cancels_too():
    st = mp.new_state()
    mp.check(st, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    row = mp.check(st, _snap("11:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    assert "cancelled" in row["events"][0] and st["day"]["entries"] == 0
    assert st["pending"] is not None and st["pending"]["ts"].endswith("11:05")   # a new order at the same check
    row = mp.check(st, _snap("15:50", "final", quotes=[]))
    assert st["pending"] is None and st["position"] is None


def test_take_profit_stop_and_final_exit():
    st = mp.new_state()
    mp.check(st, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st, _snap("10:22", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    row = mp.check(st, _snap("11:05", prices={"SPY": FLAT}, quotes=[_q(1, "NVDA", "P", 92.0, 2.05, 2.15)]))
    assert st["trades"][0]["why"] == "take profit" and st["trades"][0]["exit"] == 2.05
    assert st["trades"][0]["reason"].startswith("NVDA -6.0%")
    st2 = mp.new_state()
    mp.check(st2, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st2, _snap("10:22", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st2, _snap("11:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.45, 0.50)]))
    assert st2["trades"][0]["why"] == "stop" and st2["day"]["stopped"] and st2["pending"] is None
    st3 = mp.new_state()
    mp.check(st3, _snap("14:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st3, _snap("14:22", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st3, _snap("15:50", "final", quotes=[_q(1, "NVDA", "P", 92.0, 0.70, 0.80)]))
    assert st3["trades"][0]["why"] == "final check" and st3["trades"][0]["exit"] == 0.70


def test_no_order_after_two_pm_and_at_most_two_entries():
    st = mp.new_state()
    mp.check(st, _snap("15:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    assert st["pending"] is None
    st2 = mp.new_state()
    st2["day"] = {"date": D, "entries": 2, "stopped": False}
    mp.check(st2, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    assert st2["pending"] is None


def test_missed_final_is_a_total_loss_and_bust():
    st = mp.new_state()
    mp.check(st, _snap("10:05", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st, _snap("10:22", "fill", quotes=[_q(1, "NVDA", "P", 92.0, 0.95, 1.00)]))
    mp.check(st, _snap("10:05", prices={"SPY": FLAT}, date="2026-01-06"))
    assert st["trades"][0]["why"] == "final check missed" and st["trades"][0]["pnl"] == -st["trades"][0]["cost"]
    st2 = mp.new_state()
    st2["cash"] = 90.0
    assert mp.check(st2, _snap("10:05", prices={"SPY": FLAT}))["status"] == "busted"
