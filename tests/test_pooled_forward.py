"""Forward scoring of pooled shadow signals (scripts/pooled_forward.py). Synthetic prices and signals only."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pooled_forward as pf  # noqa: E402

CAL = pd.bdate_range("2026-01-05", periods=40)


def _prices(drift_a=0.0):
    """SPY flat at 100 (open = close); AAA opens at its previous close and rises `drift_a` a day."""
    spy = pd.Series(100.0, index=CAL)
    a = pd.Series(50.0 * (1 + drift_a) ** np.arange(len(CAL)), index=CAL)
    opens = pd.DataFrame({"SPY": spy, "AAA": a.shift(1).fillna(50.0)})
    closes = pd.DataFrame({"SPY": spy, "AAA": a})
    return opens, closes


def _sig(rows):
    return pd.DataFrame([{"asof": d, "symbol": s, "pattern": p, "signal_bar_date": d} for d, s, p in rows])


def test_entry_next_open_exit_tenth_close_and_cost():
    opens, closes = _prices(0.01)
    t = pf.build_trades(_sig([(str(CAL[2].date()), "AAA", "x")]), opens, closes)
    r = t.iloc[0]
    assert r["entry_date"] == CAL[3] and r["exit_date"] == CAL[12] and r["done"]
    expect = closes.at[CAL[12], "AAA"] / opens.at[CAL[3], "AAA"] - 1 - pf.COST
    assert r["ret"] == pytest.approx(expect)
    assert r["excess"] == pytest.approx(expect)              # SPY flat


def test_dedup_same_pair_but_not_other_pattern():
    opens, closes = _prices()
    rows = [(str(CAL[i].date()), "AAA", "x") for i in range(2, 8)]      # fires 6 days running
    rows += [(str(CAL[2].date()), "AAA", "y")]                          # another pattern, same day
    rows += [(str(CAL[12].date()), "AAA", "x")]                         # entry CAL[13], after the first exit CAL[12]
    t = pf.build_trades(_sig(rows), opens, closes)
    assert len(t[t["pattern"] == "x"]) == 2 and len(t[t["pattern"] == "y"]) == 1


def test_signal_on_last_bar_is_open_and_skipped_inside_open_trade():
    opens, closes = _prices()
    rows = [(str(CAL[35].date()), "AAA", "x"), (str(CAL[-1].date()), "AAA", "x"), (str(CAL[-1].date()), "AAA", "z")]
    t = pf.build_trades(_sig(rows), opens, closes)
    assert len(t[t["pattern"] == "x"]) == 1                  # the last-bar signal falls inside the open trade
    z = t[t["pattern"] == "z"].iloc[0]
    assert not z["done"] and pd.isna(z["entry_date"])


def test_control_removes_symbol_drift():
    opens, closes = _prices(0.01)                            # AAA rises every day: every entry date looks good
    ctrl = pf.symbol_controls(opens, closes, CAL[1])         # day 0's open has no previous close
    rows = [(str(CAL[i].date()), "AAA", "x") for i in range(0, 25, 10)]
    t = pf.build_trades(_sig(rows), opens, closes)
    done = t[t["done"]]
    assert (done["excess"] > 0).all()
    assert (done["excess"] - ctrl["AAA"]).abs().max() < 1e-9   # no timing skill beyond the drift


def test_size_rule_gives_no_pass_on_small_samples():
    opens, closes = _prices(0.01)
    t = pf.build_trades(_sig([(str(CAL[i].date()), "AAA", f"p{i}") for i in range(0, 20)]), opens, closes)
    ev = pf.evaluate(t, pf.symbol_controls(opens, closes, CAL[0]))
    assert not ev["size_ok"] and not ev["pass"]


def test_cluster_test_one_sided():
    v = pd.Series([0.01, 0.02, 0.015, 0.012, 0.018, 0.011])
    w = pd.Series(["a", "b", "c", "d", "e", "f"])
    r = pf.cluster_test(v, w)
    assert r["weeks"] == 6 and r["p"] < 0.001
    r2 = pf.cluster_test(-v, w)
    assert r2["p"] > 0.99


def test_record_is_append_only_and_latest_payload_wins(tmp_path):
    d = tmp_path / "pooled"
    d.mkdir()
    def pay(name, asof, gen, n):
        (d / name).write_text(json.dumps({"payload": {"asof": asof, "status": "ok", "generated_at": gen, "run_id": name,
                                                      "shadow_signals": [{"symbol": "AAA", "pattern": "x",
                                                                          "signal_bar_date": asof}] * n}}))
    pay("20260105T1.json", "2026-01-05", "2026-01-05T23:00", 1)
    pay("20260105T2.json", "2026-01-05", "2026-01-05T23:30", 2)
    j = tmp_path / "signals.jsonl"
    assert pf.record(j, d) == ["2026-01-05"]
    pay("20260106T1.json", "2026-01-06", "2026-01-06T23:00", 3)
    pay("20260105T3.json", "2026-01-05", "2026-01-07T01:00", 9)     # a later re-run never rewrites a recorded session
    assert pf.record(j, d) == ["2026-01-06"]
    rows = [json.loads(l) for l in j.read_text().splitlines()]
    assert [len(r["signals"]) for r in rows] == [2, 3]


def test_stale_and_excluded_signals_are_dropped(tmp_path):
    j = tmp_path / "s.jsonl"
    j.write_text(json.dumps({"asof": "2026-01-05", "signals": [
        {"symbol": "AAA", "pattern": "x", "signal_bar_date": "2026-01-05"},
        {"symbol": "AAA", "pattern": "y", "signal_bar_date": "2026-01-05", "stale": True},
        {"symbol": "AAA", "pattern": "z", "signal_bar_date": "2026-01-05", "excluded": True}]}) + "\n")
    assert list(pf.load_signals(j)["pattern"]) == ["x"]
