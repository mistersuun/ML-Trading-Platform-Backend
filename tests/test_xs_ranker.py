"""Cross-sectional ranker (xs_ranker.py, docs/xs-ranker/PREREG.md). Synthetic prices only."""
import numpy as np
import pandas as pd
import pytest

import xs_ranker as xs

IDX = pd.bdate_range("2020-01-01", periods=700)


def _market(n_sym=30, seed=0, planted=0.0):
    """Random walks. With `planted` > 0, a stock's daily drift is proportional to its own 63-day return rank:
    trailing winners keep winning, which a ranker should find."""
    rng = np.random.default_rng(seed)
    syms = [f"S{i:02d}" for i in range(n_sym)]
    px = np.full((len(IDX), n_sym), 50.0)
    for t in range(1, len(IDX)):
        drift = np.zeros(n_sym)
        if planted and t > 63:
            past = px[t - 1] / px[t - 64] - 1
            drift = planted * (past.argsort().argsort() / (n_sym - 1) - 0.5)
        px[t] = px[t - 1] * (1 + drift + 0.015 * rng.standard_normal(n_sym))
    close = pd.DataFrame(px, index=IDX, columns=syms)
    volume = pd.DataFrame(1e6, index=IDX, columns=syms)
    spy = pd.Series(50 * np.cumprod(1 + 0.01 * rng.standard_normal(len(IDX))), index=IDX)
    return close, volume, spy


def test_features_are_ranks_and_respect_eligibility():
    close, volume, spy = _market()
    close.iloc[:400, 0] = np.nan                  # S00 too young on day 500
    close.iloc[:, 1] = 2.0                         # S01 below US$5
    d = IDX[500]
    f = xs.features_on(close, volume, spy, d)
    assert set(f.columns) == set(xs.FEATURES)
    assert "S00" not in f.index and "S01" not in f.index
    assert ((f >= 0) & (f <= 1)).all().all()


def test_features_never_use_future_data():
    close, volume, spy = _market()
    d = IDX[500]
    a = xs.features_on(close, volume, spy, d)
    close2 = close.copy()
    close2.iloc[501:] *= 3.0                       # change everything after the date
    b = xs.features_on(close2, volume, spy, d)
    pd.testing.assert_frame_equal(a, b)


def test_walk_forward_purges_last_month_and_waits_for_history():
    close, volume, spy = _market()
    dates = xs.month_ends(IDX)
    panel = xs.build_panel(close, volume, spy, dates)
    scores = xs.walk_forward(panel, min_train_dates=6, models=("ridge",))
    panel_dates = sorted(panel["date"].unique())
    first = scores["date"].min()
    k = panel_dates.index(first)
    assert k - 1 >= 6                               # needs 6 training dates before the purged one
    assert scores["fwd"].isna().sum() == (scores["date"] == panel_dates[-1]).sum()


def test_planted_signal_is_found_and_beats_universe():
    close, volume, spy = _market(planted=0.004, seed=3)
    dates = xs.month_ends(IDX)
    panel = xs.build_panel(close, volume, spy, dates)
    scores = xs.walk_forward(panel, min_train_dates=6, models=("ridge",))
    ic = xs.ic_stats(scores, "ridge")
    assert ic["mean"] > 0.1
    books = xs.portfolios(scores, score_cols=("ridge",))
    assert books["ridge"].returns.mean() > books["universe"].returns.mean()


def test_no_signal_gives_no_reliable_ic():
    close, volume, spy = _market(seed=5)
    dates = xs.month_ends(IDX)
    panel = xs.build_panel(close, volume, spy, dates)
    scores = xs.walk_forward(panel, min_train_dates=6, models=("ridge",))
    assert abs(xs.ic_stats(scores, "ridge")["t"]) < 3


def test_portfolio_costs_and_turnover():
    scores = pd.DataFrame({
        "date": [pd.Timestamp("2021-01-29")] * 5 + [pd.Timestamp("2021-02-26")] * 5,
        "symbol": list("ABCDE") * 2,
        "fwd": [0.10, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.05],
        "x": [5, 4, 3, 2, 1, 1, 2, 3, 4, 5],
    })
    books = xs.portfolios(scores, score_cols=("x",), top_frac=0.2, cost=0.001)
    r = books["x"].returns
    assert r.iloc[0] == pytest.approx(0.10 - 0.001 * 1.0)        # buy A: 100% traded
    assert r.iloc[1] == pytest.approx(0.05 - 0.001 * 2.0)        # sell A, buy E: 200% traded
    assert books["x"].turnover.iloc[1] == pytest.approx(1.0)


def test_verdict_requires_all_three_conditions():
    idx = pd.date_range("2022-01-31", periods=24, freq="ME")
    good = xs.Book(pd.Series(0.02 + 0.01 * np.sin(np.arange(24)), index=idx), pd.Series(0.5, index=idx))
    meh = xs.Book(pd.Series(0.01 + 0.02 * np.sin(np.arange(24)), index=idx), pd.Series(0.5, index=idx))
    books = {"ml": good, "momentum": meh, "universe": meh}
    ok, lines = xs.verdict({"t": 2.5}, books)
    assert ok and len(lines) == 3
    ok2, _ = xs.verdict({"t": 1.5}, books)
    assert not ok2
