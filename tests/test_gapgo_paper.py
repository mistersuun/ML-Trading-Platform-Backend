"""Gap-and-go paper account (scripts/gapgo_paper.py). Invented bars only."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "scripts"))
_spec = importlib.util.spec_from_file_location("gapgo_paper", _root / "scripts" / "gapgo_paper.py")
gg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gg)

CAND = {"symbol": "XYZ", "gap": 0.08, "prev_close": 92.0, "adv_shares": 1_000_000, "adv_dollar": 9e7}


def _bars(rows, vol0=100_000):
    rows = list(rows) + [(rows[-1][3],) * 4] * (78 - len(rows))
    idx = pd.date_range("2026-10-15 09:30", periods=78, freq="5min", tz=gg.dtp.NY)
    df = pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"], dtype=float)
    df["Volume"] = 10_000.0
    df.iloc[0, df.columns.get_loc("Volume")] = vol0
    return df


def test_breakout_half_at_2r_then_breakeven():
    b = _bars([(100, 101, 99, 100.8), (100.9, 101.2, 100.8, 101.1), (101.1, 105.5, 101, 105), (105, 105, 100.5, 101)])
    r = gg.simulate_trade(b, CAND, 5_000)
    assert r["trade"] and r["entry_time"] == "09:35"
    assert [e["why"] for e in r["exits"]] == ["half at 2R", "breakeven stop"]
    assert r["pnl"] > 0


def test_filters_red_bar_low_volume_and_no_breakout():
    assert gg.simulate_trade(_bars([(100, 101, 99, 99.5), (99.5, 102, 99, 101)]), CAND, 5_000)["reason"].startswith("first")
    assert "opening volume" in gg.simulate_trade(_bars([(100, 101, 99, 100.5), (100.5, 102, 100, 101)], vol0=10),
                                                 CAND, 5_000)["reason"]
    assert "no breakout" in gg.simulate_trade(_bars([(100, 101, 99, 100.5), (100.5, 100.9, 100, 100.6)]),
                                              CAND, 5_000)["reason"]


def test_stop_loses_about_one_r_and_size_is_capped():
    b = _bars([(100, 101, 99, 100.8), (101.0, 101.3, 98.5, 98.8)])
    r = gg.simulate_trade(b, CAND, 5_000)
    assert r["exits"][0]["why"] == "stop" and r["r"] == pytest.approx(-1.0, abs=0.15)
    assert r["shares"] * r["entry"] <= 0.5 * 5_000 + 1e-6
