"""Five quant paper accounts (scripts/quant_accounts.py). Invented prices only."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "quant_accounts", Path(__file__).resolve().parent.parent / "scripts" / "quant_accounts.py")
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)

IDX = pd.bdate_range("2024-01-01", periods=300)


def _panel(cols: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pd.DataFrame(cols, index=IDX, dtype=float)
    return c.copy(), c


def test_commission_minimum_and_cap():
    assert qa.commission(1, 100) == 0.35
    assert qa.commission(0.1, 10) == pytest.approx(0.01)       # 1% of a US$1 trade
    assert qa.commission(1000, 100) == pytest.approx(3.5)


def test_xs_momentum_picks_the_top_five_and_drops_old_names():
    trend = {s: np.linspace(100, 100 + 10 * k, 300) for k, s in enumerate(qa.STOCKS)}
    _, c = _panel(trend)
    out = qa.xs_momentum(c, {"AAPL": 0.2})
    winners = [s for s, w in out.items() if w > 0]
    assert sorted(winners) == sorted(qa.STOCKS[-5:]) and out["AAPL"] == 0.0


def test_trend_puts_off_sleeves_in_tbills():
    cols = {a: np.linspace(100, 120, 300) for a in qa.TREND_ASSETS}
    cols["GLD"] = np.linspace(120, 100, 300)
    cols[qa.CASH_ETF] = np.full(300, 100.0)
    _, c = _panel(cols)
    out = qa.trend(c, {})
    assert out["GLD"] == 0.0 and out[qa.CASH_ETF] == pytest.approx(0.2) and out["SPY"] == 0.2


def test_simulate_fills_next_open_and_charges_costs():
    o, c = _panel({"SPY": np.full(300, 100.0)})
    o.iloc[1:, 0] = 101.0                                   # next opens differ from the decision close
    res = qa.simulate(lambda h, w: {"SPY": 1.0}, "monthly", o, c, IDX[10], IDX[12])
    t = res["trades"][0]
    assert t["date"] == str(IDX[11].date()) and t["price"] == pytest.approx(101 * 1.0005)
    assert res["cash"] >= 0 and res["curve"].iloc[-1] < 1000


def test_rsi2_buys_a_dip_in_an_uptrend_and_exits_above_the_5_day_average():
    x = np.linspace(100, 150, 300)
    x[-3:] = [149, 146, 143]
    _, c = _panel({"SPY": x})
    assert qa.rsi2_reversion(c, {}) == {"SPY": 0.25}
    x2 = x.copy()
    x2[-1] = 160
    _, c2 = _panel({"SPY": x2})
    assert qa.rsi2_reversion(c2, {"SPY": 0.25}) == {"SPY": 0.0}


# ------------------------------------------------------------------------------------------- journal record
import json  # noqa: E402
from datetime import date  # noqa: E402

DEC = date(2024, 1, 2)
DAYS = pd.bdate_range("2024-01-02", periods=6)            # 2024-01-02 (decision) .. 2024-01-09


def _curves(vals):
    return {"a": pd.Series(vals, index=DAYS)}


def _rec(day, eq=None):
    return {"asof": day, "accounts": {"a": {"equity": eq if eq is not None else 1000.0}}}


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_nan_close_is_marked_at_the_last_available_close():
    o, c = _panel({"SPY": np.full(300, 100.0)})
    c.iloc[12, 0] = np.nan
    res = qa.simulate(lambda h, w: {"SPY": 1.0}, "monthly", o, c, IDX[10], IDX[12])
    assert res["curve"].iloc[-1] == pytest.approx(res["curve"].iloc[-2])
    assert res["curve"].iloc[-1] > 900


def test_journal_appends_new_dates_and_never_touches_old_rows(tmp_path):
    p = tmp_path / "journal.jsonl"
    old = '{"asof":"2024-01-03",  "accounts": {"a": {"equity": 1000.0}}}'      # odd spacing must survive
    p.write_text(old + "\n")
    cv = _curves([1000, 1000, 1001, 1002, 1003, 1004])
    added, revs = qa.update_journal(p, cv, lambda d: _rec(d, 1.0), DEC, today="2024-02-01")
    lines = p.read_text().splitlines()
    assert lines[0] == old and revs == []
    assert [json.loads(x)["asof"] for x in lines] == ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    assert added == ["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    assert not any("backfilled" in json.loads(x) for x in lines)


def test_journal_backfills_a_middle_date_with_the_flag(tmp_path):
    p = tmp_path / "journal.jsonl"
    _write(p, [_rec("2024-01-03", 1000.0), _rec("2024-01-05", 1001.0)])
    cv = _curves([1000, 1000, 1000, 1001, 1001, 1001])
    added, _ = qa.update_journal(p, cv, lambda d: _rec(d, 1000.0), DEC, today="2024-02-01")
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert added == ["2024-01-04", "2024-01-08", "2024-01-09"]
    assert [r["asof"] for r in rows] == ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
    assert rows[1]["backfilled"] is True and rows[1]["backfilled_on"] == "2024-02-01"
    assert "backfilled" not in rows[3] and "backfilled" not in rows[4]       # later than the last row: plain append


def test_journal_revision_is_logged_and_the_stored_row_kept(tmp_path, capsys):
    p = tmp_path / "journal.jsonl"
    _write(p, [_rec("2024-01-03", 1000.0)])
    before = p.read_text()
    cv = _curves([1000, 1005.0, 1000, 1000, 1000, 1000])
    qa.update_journal(p, cv, lambda d: _rec(d, 1000.0), DEC, today="2024-02-01")
    assert p.read_text().startswith(before)
    assert json.loads(p.read_text().splitlines()[0])["accounts"]["a"]["equity"] == 1000.0
    assert "REVISION 2024-01-03 a" in capsys.readouterr().err
    log = [json.loads(x) for x in (tmp_path / "revisions.jsonl").read_text().splitlines()]
    assert log == [{"date": "2024-01-03", "account": "a", "stored": 1000.0, "replayed": 1005.0, "accepted": False,
                    "logged_on": "2024-02-01"}]
    qa.update_journal(p, cv, lambda d: _rec(d, 1000.0), DEC, today="2024-02-02")      # same revision is not logged twice
    assert len((tmp_path / "revisions.jsonl").read_text().splitlines()) == 1


def test_journal_within_a_cent_is_not_a_revision(tmp_path):
    p = tmp_path / "journal.jsonl"
    _write(p, [_rec("2024-01-03", 1000.0)])
    qa.update_journal(p, _curves([1000, 1000.009, 1000, 1000, 1000, 1000]), lambda d: _rec(d), DEC)
    assert not (tmp_path / "revisions.jsonl").exists()


def test_journal_accept_revision_replaces_the_row_and_still_logs(tmp_path):
    p = tmp_path / "journal.jsonl"
    _write(p, [_rec("2024-01-03", 1000.0)])
    cv = _curves([1000, 1005.0, 1000, 1000, 1000, 1000])
    qa.update_journal(p, cv, lambda d: _rec(d, round(cv["a"].loc[d], 2)), DEC, accept=True, today="2024-02-01")
    assert json.loads(p.read_text().splitlines()[0])["accounts"]["a"]["equity"] == 1005.0
    assert json.loads((tmp_path / "revisions.jsonl").read_text())["accepted"] is True


def test_journal_accept_after_a_warned_run_logs_the_replacement(tmp_path):
    p = tmp_path / "journal.jsonl"
    _write(p, [_rec("2024-01-03", 1000.0)])
    cv = _curves([1000, 1005.0, 1000, 1000, 1000, 1000])
    mk = lambda d: _rec(d, round(cv["a"].loc[d], 2))
    qa.update_journal(p, cv, mk, DEC, today="2024-02-01")
    qa.update_journal(p, cv, mk, DEC, accept=True, today="2024-02-02")
    log = [json.loads(x) for x in (tmp_path / "revisions.jsonl").read_text().splitlines()]
    assert [r["accepted"] for r in log] == [False, True]
    assert json.loads(p.read_text().splitlines()[0])["accounts"]["a"]["equity"] == 1005.0
