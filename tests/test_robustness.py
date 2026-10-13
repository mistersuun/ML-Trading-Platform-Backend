"""WS4.3: signal-parameter robustness (neighbourhood grids, planted vs smooth edge, trials feed N)."""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

import config
import patterns as P
import robustness as R
from tests.test_validation import make_frame, path_walk
from trials import TrialRegistry, load_run, matrix_hash

KW = dict(train=250, test=60, step=60, end="2030-01-01")


def _reg(tmp_path, run_id="rb"):
    return TrialRegistry(run_id, trials_dir=tmp_path / "trials")


def _planted(accuracy):
    """Pattern with look-ahead edge: right next-bar direction with probability accuracy(window), else a random side.
    The uniform draws are shared across windows, so accuracy alone shapes the edge."""
    def make(df):
        rng = np.random.default_rng(99)
        u, rnd = rng.random(len(df)), rng.choice([-1, 1], len(df))
        nxt = np.sign(df["Close"].shift(-1) - df["Close"]).fillna(0).to_numpy()

        def f(d, window: int = 20):
            out = d.copy()
            out["signal"] = np.where(u[:len(d)] < accuracy(window), nxt[:len(d)], rnd[:len(d)]).astype(int)
            return out
        return f
    return make


def _run(tmp_path, accuracy, windows, seed=3):
    df = path_walk(900, seed)
    fn = _planted(accuracy)(df)
    reg = _reg(tmp_path)
    res = R.robustness("SYM", df, "plant", trials=reg, patterns={"plant": fn},
                       grid={"window": windows}, bars_per_year=252, **KW)
    return res, reg


def test_edge_at_one_exact_window_is_not_robust(tmp_path):
    res, _ = _run(tmp_path, lambda w: 0.95 if w == 20 else 0.0, [14, 17, 20, 23, 26])
    assert res.center_sharpe > 1.0
    assert res.share_positive < R.MIN_POSITIVE_SHARE
    assert not res.robust and "neighbours_not_positive" in res.reasons and "not_smooth" in res.reasons


def test_smooth_edge_is_robust(tmp_path):
    res, _ = _run(tmp_path, lambda w: 0.9 - 0.05 * abs(w - 20), [16, 18, 20, 22, 24])
    sh = [p.sharpe for p in sorted(res.points, key=lambda p: p.params["window"])]
    assert all(s > 0 for s in sh)
    assert sh[2] == max(sh) and sh[0] < sh[2] and sh[4] < sh[2]      # degrades away from the centre
    assert res.share_positive == 1.0 and res.smooth and res.robust and res.reasons == []


def test_trial_count_equals_registry_n(tmp_path):
    res, reg = _run(tmp_path, lambda w: 0.9 - 0.05 * abs(w - 20), [16, 18, 20, 22, 24])
    assert res.n_trials == len(res.points) == 5 == reg.n_trials == res.registry_n
    loaded = load_run("rb", trials_dir=tmp_path / "trials")
    assert loaded.n_trials == 5 and loaded.returns.shape[1] == 5
    assert all(t.params["sensitivity"] and "window" in t.params for t in loaded.trials)
    assert matrix_hash(loaded.returns) == matrix_hash(reg.returns_matrix())


def test_grid_trials_are_added_to_an_existing_run(tmp_path):
    df = make_frame(5, 900)
    reg = _reg(tmp_path)
    a = R.robustness("AAA", df, "ema_crossover", trials=reg, **KW)
    assert a.n_trials == 9 == reg.n_trials
    b = R.robustness("AAA", df, "donchian_breakout", trials=reg, **KW)
    assert b.n_trials == 5 and b.registry_n == reg.n_trials == 14
    assert len({r.trial_id for r in reg.records}) == 14


def test_scan_records_every_grid_point(tmp_path):
    frames = {"AAA": make_frame(1, 900), "BBB": make_frame(2, 900)}
    reg = _reg(tmp_path)
    out = R.robustness_scan(frames, ["ema_crossover", "donchian_breakout", "ichimoku_cloud"], trials=reg, **KW)
    assert [(r.symbol, r.pattern) for r in out] == [("AAA", "donchian_breakout"), ("AAA", "ema_crossover"),
                                                    ("BBB", "donchian_breakout"), ("BBB", "ema_crossover")]
    assert sum(r.n_trials for r in out) == reg.n_trials == 28
    assert all(r.registry_n == 28 for r in out)


def test_deterministic(tmp_path):
    df = make_frame(7, 900)
    a = R.robustness("AAA", df, "ema_crossover", trials=_reg(tmp_path, "a"), **KW)
    b = R.robustness("AAA", df, "ema_crossover", trials=_reg(tmp_path, "b"), **KW)
    assert a.to_dict() == b.to_dict()
    assert matrix_hash(load_run("a", trials_dir=tmp_path / "trials").returns) == \
        matrix_hash(load_run("b", trials_dir=tmp_path / "trials").returns)


def test_holdout_is_never_used(tmp_path):
    df = make_frame(2, 2350)
    kw = dict(train=250, test=60, step=60)            # end = config.HOLDOUT_START
    a = R.robustness("AAA", df, "ema_crossover", trials=_reg(tmp_path, "a"), **kw)
    df2 = df.copy()
    cut = R.V.holdout_position(df)
    df2.iloc[cut:, :4] = df2.iloc[cut:, :4].to_numpy() * 3.0
    b = R.robustness("AAA", df2, "ema_crossover", trials=_reg(tmp_path, "b"), **kw)
    assert [p.sharpe for p in a.points] == [p.sharpe for p in b.points]
    assert all(p.n_oos_bars > 0 for p in a.points)


def test_insufficient_history_is_not_robust_but_still_counted(tmp_path):
    reg = _reg(tmp_path)
    res = R.robustness("AAA", make_frame(1, 60), "ema_crossover", trials=reg, **KW)
    assert not res.robust and "insufficient_history" in res.reasons and reg.n_trials == 9


def test_bad_grids_are_rejected(tmp_path):
    df = make_frame(1, 400)
    with pytest.raises(ValueError):      # grid lacks the default
        R.robustness("A", df, "ema_crossover", trials=_reg(tmp_path), grid={"fast": [5, 6, 7]}, **KW)
    with pytest.raises(ValueError):      # no grid for the pattern
        R.robustness("A", df, "ichimoku_cloud", trials=_reg(tmp_path), **KW)
    with pytest.raises(KeyError):
        R.robustness("A", df, "nope", trials=_reg(tmp_path), **KW)


# ------------------------------------------------------------------ patterns.py grids
def test_param_grids_are_valid_and_centred_on_defaults():
    for name, g in P.PARAM_GRIDS.items():
        fn = P.PATTERN_REGISTRY[name]
        params = inspect.signature(fn).parameters
        size = int(np.prod([len(v) for v in g.values()]))
        assert 3 <= size <= 9, name
        for axis, values in g.items():
            assert axis in params and params[axis].default is not inspect.Parameter.empty, (name, axis)
            assert values == sorted(values) and len(set(values)) == len(values)
            assert params[axis].default in values, (name, axis)
            assert 0 < values.index(params[axis].default) < len(values) - 1, (name, axis)  # centre is interior


def test_exposed_defaults_keep_signals_identical():
    df = path_walk(600, 4)
    s = lambda out: out["signal"].to_numpy()
    assert (s(P.macd_crossover(df)) == s(P.macd_crossover(df, fast=12, slow=26, sign=9))).all()
    assert (s(P.sma_200_trend(df)) == s(P.sma_200_trend(df, window=200))).all()
    assert (s(P.stochastic_crossover(df)) == s(P.stochastic_crossover(df, oversold=30, overbought=70))).all()
    assert (s(P.macd_crossover(df)) != s(P.macd_crossover(df, fast=8, slow=30))).any()
    assert (s(P.sma_200_trend(df)) != s(P.sma_200_trend(df, window=100))).any()
    assert (s(P.stochastic_crossover(df)) != s(P.stochastic_crossover(df, oversold=15, overbought=85))).any()


# ------------------------------------------------------------------ statistical (slow)
@pytest.mark.slow
def test_pure_noise_is_rarely_robust(tmp_path):
    """On random walks no real pattern has an edge: the robust verdict must be rare (false-positive rate)."""
    robust = total = 0
    for seed in range(30):
        df = path_walk(900, 1000 + seed)
        out = R.robustness_scan({"S": df}, ["ema_crossover", "donchian_breakout", "rsi_reversal"],
                                trials=_reg(tmp_path, f"n{seed}"), **KW)
        robust += sum(r.robust for r in out)
        total += len(out)
    assert robust / total <= 0.15, (robust, total)     # ~9% measured (60 seeds); chance of a positive centre alone is ~50%


def test_executable_template_is_opt_in(tmp_path):
    import inspect
    assert inspect.signature(R.robustness).parameters["executable"].default is False
    df = path_walk(900, 7)
    out = R.robustness("S", df, "ema_crossover", trials=_reg(tmp_path, "ex"), executable=True, **KW)
    assert out.pattern == "ema_crossover" and out.registry_n is not None
