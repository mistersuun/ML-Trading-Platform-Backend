"""Offline tests for scripts/track_investor_portfolios.py (synthetic closes, no network)."""
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import track_investor_portfolios as tip  # noqa: E402


def S(vals, start="2026-09-01"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)), dtype=float)


def defs(portfolios, target="2026-09-03"):
    return {"inception_target": target, "start_value_usd": 10000, "portfolios": portfolios}


def P(positions):
    return {"name": "x", "basis": "b", "positions": positions}


def test_basket_value_buy_and_hold_weights():
    closes = {"SPY": S([100, 100, 100, 110, 121]), "A": S([10, 10, 10, 11, 11]), "B": S([50, 50, 50, 50, 25])}
    start = pd.Timestamp("2026-09-03")
    val, w, missing = tip.basket_value({"A": 1, "B": 1}, closes, start, closes["SPY"].index[-1])
    assert missing == [] and w == {"A": 0.5, "B": 0.5}
    # day 4: A +10%, B 0 -> 10500 ; day 5: B halves -> 5000*1.1 + 5000*0.5 = 8000 (weights drift, no rebalance)
    assert list(val.round(2)) == [10000.0, 10500.0, 8000.0]


def test_missing_ticker_renormalised_and_logged(caplog):
    closes = {"SPY": S([100, 100, 100, 100, 100]), "A": S([10, 10, 10, 12, 12])}
    d = defs({"spy": P({"SPY": 1}), "p": P({"A": 3, "ZZZ": 1})})
    with caplog.at_level("WARNING"):
        row = tip.evaluate(d, closes, pd.Timestamp("2026-09-07"))
    p = row["portfolios"]["p"]
    assert p["missing"] == ["ZZZ"] and p["weights"] == {"A": 1.0}
    assert p["value"] == pytest.approx(12000.0)
    assert p["ret_since_inception"] == pytest.approx(0.2) and p["vs_spy"] == pytest.approx(0.2)
    assert "ZZZ" in caplog.text


def test_inception_falls_back_to_latest_close_and_says_so():
    closes = {"SPY": S([100, 101, 102])}                    # last bar 2026-09-03
    d, note = tip.resolve_inception(closes, "2026-10-06", pd.Timestamp("2026-10-06"))
    assert d == pd.Timestamp("2026-09-03") and "not available yet" in note
    closes["SPY"].loc[pd.Timestamp("2026-10-06")] = 105.0
    d, note = tip.resolve_inception(closes, "2026-10-06", pd.Timestamp("2026-10-06"))
    assert d == pd.Timestamp("2026-10-06") and "not available" not in note


def test_asof_does_not_see_the_future():
    closes = {"SPY": S([100, 100, 100, 110, 121])}
    row = tip.evaluate(defs({"spy": P({"SPY": 1})}), closes, pd.Timestamp("2026-09-04"))
    assert row["date"] == "2026-09-04"
    assert row["portfolios"]["spy"]["ret_since_inception"] == pytest.approx(0.10)


def test_daily_returns_and_trailing_windows():
    idx = pd.bdate_range("2025-09-01", "2026-09-30")
    spy = pd.Series(range(100, 100 + len(idx)), index=idx, dtype=float)
    short = spy[spy.index >= "2026-08-20"]                   # no 1y history
    closes = {"SPY": spy, "N": short}
    d = defs({"spy": P({"SPY": 1}), "mix": P({"SPY": 1, "N": 1})}, target="2026-09-28")
    row = tip.evaluate(d, closes, pd.Timestamp("2026-09-30"))
    spy_row = row["portfolios"]["spy"]
    last = spy.iloc[-1]
    assert spy_row["ret_1y"] == pytest.approx(last / spy[spy.index <= idx[-1] - pd.Timedelta(days=365)].iloc[-1] - 1)
    assert spy_row["ret_1m"] == pytest.approx(last / spy[spy.index <= idx[-1] - pd.Timedelta(days=30)].iloc[-1] - 1)
    assert len(spy_row["daily_returns"]) == 2 and spy_row["ret_1d"] == pytest.approx(last / spy.iloc[-2] - 1)
    mix = row["portfolios"]["mix"]
    assert mix["cov_1y"] == pytest.approx(0.5) and mix["cov_1m"] == pytest.approx(1.0)   # N too young for 1y only


def test_append_row_idempotent_per_date(tmp_path):
    f = tmp_path / "t.jsonl"
    tip.append_row({"date": "2026-10-07", "v": 1}, f)
    tip.append_row({"date": "2026-10-06", "v": 0}, f)
    tip.append_row({"date": "2026-10-07", "v": 2}, f)         # re-run of the same date replaces it
    rows = [json.loads(x) for x in f.read_text().splitlines()]
    assert rows == [{"date": "2026-10-06", "v": 0}, {"date": "2026-10-07", "v": 2}]


def test_load_close_from_ibkr_json_and_markdown(tmp_path):
    def payload(closes):
        t = [f"2026-09-0{d}T13:30:00Z" for d in range(1, 1 + len(closes))]
        return {"time": t, "open": closes, "high": closes, "low": closes, "close": closes, "volume": [1] * len(closes)}
    (tmp_path / "SPY.json").write_text(json.dumps(payload([100.0, 101.0, 102.0])))
    (tmp_path / "AAA.investor.json").write_text(json.dumps(payload([10.0, 11.0, 12.0])))
    closes = tip.load_all(["SPY", "AAA", "NOPE"], tmp_path)
    assert sorted(closes) == ["AAA", "SPY"]
    d = defs({"spy": P({"SPY": 1}), "a": P({"AAA": 1, "NOPE": 1})}, target="2026-09-01")
    row = tip.evaluate(d, closes, pd.Timestamp("2026-09-03"))
    assert row["portfolios"]["a"]["ret_since_inception"] == pytest.approx(0.2)
    md = tip.render_md([row], d)
    assert "| x |" in md and "+20.00%" in md and "NOPE" in md
