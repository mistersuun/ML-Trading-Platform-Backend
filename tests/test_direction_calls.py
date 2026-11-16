"""Direction calls (scripts/direction_calls.py). Synthetic prices and calls only."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import direction_calls as dc  # noqa: E402

IDX = pd.bdate_range("2026-01-05", periods=4)


def _file(asof, target, made_at, direction="up", conf=0.6):
    return {"asof": asof, "target": target, "made_at": made_at,
            "calls": [{"symbol": s, "direction": direction, "confidence": conf, "reason": "x"} for s in dc.UNIVERSE]}


def _closes():
    # every name: 100, 101 (up), 100 (down), 100 (unchanged)
    return pd.DataFrame({s: [100.0, 101.0, 100.0, 100.0] for s in dc.UNIVERSE}, index=IDX)


def test_validate_requires_full_universe_and_ranges():
    good = _file("2026-01-05", "2026-01-06", "2026-01-05T23:00:00Z")
    assert dc.validate(good) == []
    bad = _file("2026-01-05", "2026-01-06", "2026-01-05T23:00:00Z")
    bad["calls"] = bad["calls"][:-1]
    bad["calls"][0]["confidence"] = 0.4
    errs = dc.validate(bad)
    assert any("exactly" in e for e in errs) and any("confidence" in e for e in errs)


def test_late_files_are_excluded():
    assert not dc.is_late("2026-01-05T23:00:00Z", "2026-01-06")
    assert dc.is_late("2026-01-06T14:31:00Z", "2026-01-06")          # 09:31 New York in winter


def test_scoring_outcomes_baselines_and_ties():
    files = [_file("2026-01-05", "2026-01-06", "2026-01-05T23:00:00Z", "up"),     # right (101 > 100)
             _file("2026-01-06", "2026-01-07", "2026-01-06T23:00:00Z", "up"),     # wrong (100 < 101)
             _file("2026-01-07", "2026-01-08", "2026-01-07T23:00:00Z", "down")]   # tie -> not scored
    df = dc.score_rows(files, _closes())
    s = df[df["scored"]]
    assert len(s) == 20 and s["correct"].sum() == 10
    night2 = s[s["asof"] == "2026-01-06"]
    assert not night2["momentum_correct"].any()   # 01-06 rose (momentum says up), then 01-07 fell
    res = dc.summarize(df)
    assert res["acc"] == pytest.approx(0.5) and res["nights"] == 2 and not res["pass"]


def test_binomial_p():
    assert dc.binom_p(5, 10) == pytest.approx(0.623046875)
    assert dc.binom_p(10, 10) == pytest.approx(1 / 1024)
