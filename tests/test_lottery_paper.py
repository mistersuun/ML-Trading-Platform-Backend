"""Short-dated SPY option accounts (scripts/lottery_paper.py). Synthetic prices and quotes only."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lottery_paper as lp  # noqa: E402

IDX = pd.bdate_range("2026-01-05", periods=25)


def _closes(last=100.0, trend_up=True):
    base = [90.0 + i * 0.4 for i in range(24)] if trend_up else [110.0 - i * 0.4 for i in range(24)]
    return pd.Series(base + [last], index=IDX)


def test_strikes_and_direction():
    assert lp.strike_for("C", 754.37, 0.003) == 757.0
    assert lp.strike_for("P", 754.37, 0.003) == 752.0
    assert lp.strike_for("C", 754.37, 0.005) == 759.0
    assert lp.strike_for("P", 754.37, 0.005) == 750.0
    up, down = _closes(100.0, True), _closes(100.0, False)
    assert lp.right_for(lp.ACCOUNTS["Y2"], up) == "C" and lp.right_for(lp.ACCOUNTS["Y2"], down) == "P"
    assert lp.right_for(lp.ACCOUNTS["Y1"], down) == "C"


def _quotes(c, nxt, ask=1.0):
    out = []
    for k, w in lp.wanted(c, nxt, lp.new_state()).items():
        out.append({"contract_id": 1, "right": w["right"], "strike": w["strike"], "expiry": w["expiry"],
                    "bid": ask - 0.05, "ask": ask})
    return out


def test_entry_sizes_and_settlement_win_and_loss():
    c = _closes(100.0)
    nxt = pd.Timestamp("2026-02-09")
    st = lp.new_state()
    row = lp.step(st, str(IDX[-1].date()), c, nxt, _quotes(c, nxt))
    y1, y3 = st["accounts"]["Y1"], st["accounts"]["Y3"]
    assert y1["positions"][0]["qty"] == 1                   # 20% of 1,000 = 200 -> 1 x (100 + 0.65)
    assert y3["positions"][0]["qty"] == 9                   # all-in: 1,000 / 100.65
    assert row["accounts"]["Y1"]["equity"] == pytest.approx(1000 - 0.65)
    # next session: SPY closes at 103 -> the 101 call (Y1) pays 2.00, the 101 call (Y3, 0.5% -> 101) pays 2.00
    c2 = pd.concat([c, pd.Series([103.0], index=[nxt])])
    lp.step(st, str(nxt.date()), c2, pd.Timestamp("2026-02-10"), [])
    t = st["accounts"]["Y1"]["trades"][0]
    assert t["settle"] == pytest.approx(2.0) and t["pnl"] == pytest.approx(200 - 0.65 - 100.65)
    # a losing session for the all-in account busts it
    st2 = lp.new_state()
    lp.step(st2, str(IDX[-1].date()), c, nxt, _quotes(c, nxt))
    c3 = pd.concat([c, pd.Series([99.0], index=[nxt])])
    lp.step(st2, str(nxt.date()), c3, pd.Timestamp("2026-02-10"), [])
    assert st2["accounts"]["Y3"]["busted"] and st2["accounts"]["Y3"]["cash"] < lp.BUST


def test_busted_account_never_trades_again_and_dates_must_increase():
    c = _closes(100.0)
    nxt = pd.Timestamp("2026-02-09")
    st = lp.new_state()
    st["accounts"]["Y3"]["busted"] = True
    lp.step(st, str(IDX[-1].date()), c, nxt, _quotes(c, nxt))
    assert not st["accounts"]["Y3"]["positions"]
    with pytest.raises(SystemExit):
        lp.step(st, str(IDX[-1].date()), c, nxt, [])


def test_missing_ask_means_no_trade():
    c = _closes(100.0)
    nxt = pd.Timestamp("2026-02-09")
    st = lp.new_state()
    q = _quotes(c, nxt)
    for x in q:
        x["ask"] = None
    row = lp.step(st, str(IDX[-1].date()), c, nxt, q)
    assert all("no ask" in row["events"][k][0] for k in ("Y1", "Y2", "Y3"))


def test_one_contract_minimum_up_to_forty_percent():
    c = _closes(100.0)
    nxt = pd.Timestamp("2026-02-09")
    st = lp.new_state()
    lp.step(st, str(IDX[-1].date()), c, nxt, _quotes(c, nxt, ask=3.0))     # 300.65: above 20%, below 40%
    assert st["accounts"]["Y1"]["positions"][0]["qty"] == 1
    st2 = lp.new_state()
    lp.step(st2, str(IDX[-1].date()), c, nxt, _quotes(c, nxt, ask=4.5))    # 450.65: above 40%
    assert not st2["accounts"]["Y1"]["positions"]
