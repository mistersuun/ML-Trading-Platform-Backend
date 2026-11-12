"""Master scoreboard (scoreboard.py). Invented series only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scoreboard as sb  # noqa: E402

IDX = pd.bdate_range("2026-10-01", periods=60)


def test_bootstrap_ci_covers_a_known_mean_and_is_reproducible():
    x = np.random.default_rng(1).normal(0.002, 0.01, 200)
    r = sb.bootstrap_mean(x, n_boot=2000)
    assert r["lo"] < 0.002 < r["hi"]
    assert r == sb.bootstrap_mean(x, n_boot=2000)
    flat = sb.bootstrap_mean(np.random.default_rng(2).normal(0.0, 0.01, 200), n_boot=2000)
    assert flat["p"] > 0.05
    assert sb.bootstrap_mean(x[:5])["lo"] is None          # too few days: n/a


def test_drop_top_removes_the_two_largest_days():
    x = np.array([0.01, -0.02, 0.30, 0.02, 0.25, 0.0])
    assert sorted(sb.drop_top(x, 2)) == [-0.02, 0.0, 0.01, 0.02]
    # a result carried by two days has no edge without them
    y = np.r_[np.full(30, -0.001), 0.2, 0.2]
    assert sb.bootstrap_mean(y, n_boot=500)["mean"] > 0 > sb.bootstrap_mean(sb.drop_top(y), n_boot=500)["mean"]


def test_newey_west_alpha_t_on_a_planted_alpha():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 0.01, 250)
    y = 0.002 + 0.8 * x + rng.normal(0, 0.002, 250)
    r = sb.newey_west_ols(y, x)
    assert r["alpha"] == pytest.approx(0.002, abs=0.0006) and r["beta"] == pytest.approx(0.8, abs=0.05)
    assert r["t_alpha"] > 4
    none = sb.newey_west_ols(0.8 * x + rng.normal(0, 0.002, 250), x)
    assert abs(none["t_alpha"]) < 3
    assert sb.newey_west_ols(x, x)["t_alpha"] is None        # the benchmark itself


def test_holm_adjust_known_values():
    assert sb.holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert sb.holm_adjust([0.5, 0.9]) == pytest.approx([1.0, 1.0])
    assert sb.holm_adjust([0.001]) == [0.001]


def test_clusters_link_above_0_7_only_and_chain():
    rng = np.random.default_rng(4)
    base = pd.Series(rng.normal(0, 0.01, 40), index=IDX[:40])
    noise = lambda s: pd.Series(rng.normal(0, s, 40), index=IDX[:40])  # noqa: E731
    r = {"a": base, "b": base + noise(0.002), "c": noise(0.01), "d": base + noise(0.1)}
    groups = sb.clusters(r)
    assert ["a", "b"] in groups and ["c"] in groups and ["d"] in groups
    assert sb.clusters({"a": base[:5], "b": base[:5]}) == [["a"], ["b"]]      # too few common days to link
    assert sb.clusters({}) == []


def test_family_test_counts_a_cluster_as_one_bet():
    groups = [["a", "b"], ["c"], ["d"]]
    adj = sb.family_test({"a": 0.001, "b": 0.5, "c": 0.02, "d": None}, groups)
    assert adj["a"] == adj["b"] == pytest.approx(0.004) and adj["d"] is None
    assert adj["c"] == pytest.approx(0.02)


def test_build_returns_flags_a_missing_day_and_a_missing_benchmark_date():
    sessions = IDX[:8]
    eq = pd.Series(np.arange(100.0, 108.0), index=sessions).drop(sessions[3])
    bench = pd.Series(np.arange(50.0, 58.0), index=sessions).drop(sessions[6])
    r = sb.build_returns(eq, bench, sessions)
    assert list(r.index) == [sessions[1], sessions[2], sessions[4], sessions[5], sessions[7]]
    assert list(r["gap"]) == [False, False, True, False, True]
    assert r["pnl"].iloc[2] == 2.0 and r["arm"].iloc[2] == pytest.approx(104 / 102 - 1)


def test_pnl_share_and_effective_days():
    assert sb.top2_pnl_share([100, 100, 1, 1, 1, 1]) == pytest.approx(200 / 204)
    assert sb.top2_pnl_share([1, 2, -5, 1]) is None
    rng = np.random.default_rng(5)
    iid = rng.normal(0, 1, 300)
    sticky = np.cumsum(rng.normal(0, 1, 300)) * 0.2 + iid * 0.1
    assert sb.effective_days(iid) > 0.7 * 300 and sb.effective_days(sticky) < 60 and sb.effective_days(iid[:4]) is None


def test_promotion_needs_all_conditions():
    good = {"ci_lo_x2": 0.0004, "n_eff": 80.0}
    assert sb.promotion(good, 0.01)["promoted"]
    assert not sb.promotion(good, 0.02)["promoted"]                       # not below 0.05 / 3
    assert not sb.promotion(good, None)["promoted"]
    assert not sb.promotion({**good, "ci_lo_x2": -0.0001}, 0.01)["promoted"]
    assert not sb.promotion({**good, "ci_lo_x2": None}, 0.01)["promoted"]
    assert not sb.promotion({**good, "n_eff": 59.0}, 0.01)["promoted"]
    assert sb.promotion({**good, "n_eff": 5.0}, 0.01, trades=60)["promoted"]   # trade-based: trades count, not days
    assert not sb.promotion(good, 0.01, trades=59)["promoted"]
    assert sb.ALPHA_LOOK == pytest.approx(0.05 / 3)


def test_arm_stats_end_to_end_on_a_planted_edge_and_look_dates():
    rng = np.random.default_rng(6)
    n = 120
    idx = pd.bdate_range("2026-01-05", periods=n + 1)
    b = rng.normal(0, 0.008, n)
    a = b + 0.004 + rng.normal(0, 0.004, n)
    eq = pd.Series(1000 * np.r_[1, np.cumprod(1 + a)], index=idx)
    bn = pd.Series(100 * np.r_[1, np.cumprod(1 + b)], index=idx)
    st = sb.arm_stats(sb.build_returns(eq, bn, idx), 1000.0, eq, bn)
    assert st["n_days"] == n and st["total"] == pytest.approx(eq.iloc[-1] / 1000 - 1)
    assert st["ci_lo"] > 0 and st["ci_lo_x2"] > 0 and st["p"] < 0.001 and st["beta"] == pytest.approx(1.0, abs=0.1)
    assert sb.look_status("2026-12-31")["is_look"] and not sb.look_status("2026-11-12")["is_look"]
    assert sb.look_status("2026-11-12")["next"] == "2026-12-31"
    short = sb.arm_stats(sb.build_returns(eq[:6], bn[:6], idx), 1000.0, eq[:6], bn[:6])
    assert short["p"] is None and short["ci_lo"] is None and short["total"] is not None
