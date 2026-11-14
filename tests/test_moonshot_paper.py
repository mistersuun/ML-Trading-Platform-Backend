"""Weekly moonshot ticket (scripts/moonshot_paper.py). Synthetic prices and quotes only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import moonshot_paper as mp  # noqa: E402

SESS = pd.bdate_range("2026-01-05", "2026-01-30")          # Mon 01-05 .. Fri 01-30


def _closes(upto):
    idx = SESS[SESS <= pd.Timestamp(upto)]
    data = {s: np.full(len(idx), 100.0) for s in mp.UNIVERSE}
    data["COIN"] = 100.0 * 1.02 ** np.arange(len(idx))       # the strongest 5-day mover
    return pd.DataFrame(data, index=idx)


def test_strike_grid_and_pick():
    assert mp.call_strike(100.0) == 103.0
    assert mp.call_strike(300.0) == 310.0
    assert mp.call_strike(20.0) == 20.6 or mp.call_strike(20.0) == 21.0
    assert mp.pick(_closes("2026-01-12"))[0] == "COIN"


def test_entry_only_on_first_session_of_week_and_once():
    st = mp.new_state()
    c = _closes("2026-01-13")                                  # a Tuesday
    assert mp.wanted(c, c.index[-1], SESS, st) is None
    c = _closes("2026-01-12")                                  # Monday
    w = mp.wanted(c, c.index[-1], SESS, st)
    assert w["symbol"] == "COIN" and w["expiry"] == "2026-01-16"


def test_ticket_life_cycle_win_and_unspent_cash():
    st = mp.new_state()
    c = _closes("2026-01-12")
    w = mp.wanted(c, c.index[-1], SESS, st)
    q = [{"underlying": "COIN", "right": "C", "strike": w["strike"], "expiry": w["expiry"], "ask": 0.50}]
    mp.enter(st, "2026-01-12", w, q)
    t = st["tickets"][0]
    assert t["qty"] == 13 and t["unspent"] == pytest.approx(700 - 13 * 50.65)
    c2 = _closes("2026-01-16")
    c2.loc[pd.Timestamp("2026-01-16"), "COIN"] = w["strike"] + 5.0
    mp.settle(st, "2026-01-16", c2)
    assert t["settled"] and t["settle"] == pytest.approx(5.0)
    assert t["proceeds"] == pytest.approx(13 * 500 - 13 * 0.65 + t["unspent"])


def test_no_ask_keeps_the_ticket_uncashed():
    st = mp.new_state()
    c = _closes("2026-01-12")
    w = mp.wanted(c, c.index[-1], SESS, st)
    ev = mp.enter(st, "2026-01-12", w, [])
    assert "no ticket" in ev[0] and st["tickets"][0]["multiple"] == 1.0
