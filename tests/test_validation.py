"""WS2.3: walk-forward, hold-out, random-entry null, stresses, BH-FDR and validation_status."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
import engine
import signals as sigmod
import validation as V
from tests.fixtures.synthetic import gbm_ohlc


# ------------------------------------------------------------------ helpers
def path_walk(n_bars: int, seed: int, start="2017-01-02", sub=8, step=1.0, p0=100.0) -> pd.DataFrame:
    """Random walk whose open == previous close and whose high/low come from a sub-bar path."""
    rng = np.random.default_rng(seed)
    inc = rng.normal(0, step / np.sqrt(sub), n_bars * sub)
    path = np.maximum(p0 + np.concatenate([[0.0], np.cumsum(inc)]), 1.0)
    seg = np.stack([path[k * sub:(k + 1) * sub + 1] for k in range(n_bars)])
    idx = pd.bdate_range(start, periods=n_bars)
    return pd.DataFrame({"Open": seg[:, 0], "High": seg.max(axis=1), "Low": seg.min(axis=1),
                         "Close": seg[:, -1], "Volume": 1e6}, index=idx)


def ema_pattern(fast: int, slow: int):
    def f(df):
        out = df.copy()
        ef, es = out["Close"].ewm(span=fast, adjust=False).mean(), out["Close"].ewm(span=slow, adjust=False).mean()
        up, dn = ef > es, ef < es
        out["signal"] = 0
        out.loc[up & ~up.shift(1, fill_value=False), "signal"] = 1
        out.loc[dn & ~dn.shift(1, fill_value=False), "signal"] = -1
        return out
    return f


def momentum_pattern(win: int):
    def f(df):
        out = df.copy()
        out["signal"] = np.sign(out["Close"] - out["Close"].shift(win)).fillna(0).astype(int)
        return out
    return f


PATTERNS = {"ema_5_20": ema_pattern(5, 20), "ema_10_40": ema_pattern(10, 40),
            "mom_10": momentum_pattern(10), "mom_30": momentum_pattern(30)}
GRID = ((0.02, 0.04), (0.03, None))


def make_frame(seed=1, n=2350):
    return path_walk(n, seed)   # 2017-01 .. early 2026: ~130 bars on/after HOLDOUT_START


# ------------------------------------------------------------------ folds / holdout
def test_folds_do_not_overlap_and_end_before_holdout():
    df = make_frame()
    wf = V.walk_forward(df, [V.Candidate("ema_5_20")], PATTERNS)
    assert wf.folds and not wf.oos_windows_overlap()
    assert all(f.test_end <= wf.n_pre for f in wf.folds)
    assert df.index[wf.n_pre - 1] < pd.Timestamp(config.HOLDOUT_START) <= df.index[wf.n_pre]
    assert len(wf.oos_returns) == sum(f.test_end - f.test_start for f in wf.folds)
    assert wf.oos_returns.index.is_unique and wf.oos_returns.index.is_monotonic_increasing
    with pytest.raises(ValueError):
        V.make_folds(1000, 200, 100, 50)


def test_holdout_data_never_changes_walk_forward():
    df = make_frame(2)
    cands = [V.Candidate(p) for p in ("ema_5_20", "mom_10")]
    a = V.walk_forward(df, cands, PATTERNS)
    df2 = df.copy()
    cut = V.holdout_position(df)
    df2.iloc[cut:, :4] = df2.iloc[cut:, :4].to_numpy() * 3.0
    b = V.walk_forward(df2, cands, PATTERNS)
    assert a.oos_returns.equals(b.oos_returns)
    assert [f.chosen for f in a.folds] == [f.chosen for f in b.folds]
    assert a.final_candidate == b.final_candidate


def test_fold_choice_invariant_to_test_window_data():
    df = gbm_ohlc(n=700, seed=5)
    cands = [V.Candidate(p, sl, tp) for p in ("ema_5_20", "ema_10_40", "mom_10") for sl, tp in GRID]
    kw = dict(train=250, test=60, step=60, end=None)
    a = V.walk_forward(df, cands, PATTERNS, **kw)
    ts = a.folds[0].test_start
    df2 = df.copy()
    rng = np.random.default_rng(0)
    noise = np.exp(rng.normal(0, 0.05, len(df2) - ts))[:, None]
    df2.iloc[ts:, :4] = df2.iloc[ts:, :4].to_numpy() * noise      # scramble the fold-0 test window and later
    b = V.walk_forward(df2, cands, PATTERNS, **kw)
    assert a.folds[0].chosen == b.folds[0].chosen
    assert a.folds[0].train_sharpe == pytest.approx(b.folds[0].train_sharpe)
    assert not a.oos_returns.equals(b.oos_returns)                 # the change really mattered for OOS


def test_oos_trades_enter_inside_test_windows_and_returns_are_daily_mtm():
    df = make_frame(3)
    wf = V.walk_forward(df, [V.Candidate("mom_10")], PATTERNS)
    for t in wf.oos_trades:
        assert t.window_start <= t.entry_index < t.window_end
    assert wf.n_oos_trades > 0 and len(wf.oos_returns) > 100


# ------------------------------------------------------------------ BH
def test_benjamini_hochberg_known_answer():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
    rej, q = V.benjamini_hochberg(p, 0.05)
    assert list(rej) == [True, True] + [False] * 8
    assert q[0] == pytest.approx(0.01) and q[1] == pytest.approx(0.04)
    assert q[2] == pytest.approx(0.042 * 10 / 5) or q[2] <= 0.21
    assert np.all(np.diff(q) >= -1e-12)
    assert V.benjamini_hochberg([], 0.05)[0].size == 0
    assert V.benjamini_hochberg([0.04], 0.05)[0][0]


# ------------------------------------------------------------------ null machinery
def test_vector_simulation_matches_engine_on_stop_target_exits():
    df = path_walk(400, 11, step=1.5)
    o, h, lo, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    rng = np.random.default_rng(3)
    checked = 0
    for e in rng.integers(2, 300, 60):
        d = int(rng.choice([1, -1]))
        sig = np.zeros(len(df), dtype=int)
        sig[e - 1] = d
        run = engine.run_backtest(df, signal=pd.Series(sig, index=df.index), stop_loss=0.03, take_profit=0.05)
        t = run.trades[0]
        if t.exit_reason not in ("stop_loss", "take_profit") or t.entry_index != e:
            continue
        sim = V._simulate_random_trades(o, h, lo, c, np.array([e]), np.array([float(d)]), np.array([0.03]),
                                        np.array([0.05]), np.array([len(df) - 1 - e]),
                                        config.COMMISSION_PCT, config.SLIPPAGE_PCT)
        assert sim[0] == pytest.approx(t.pnl_pct, rel=1e-9, abs=1e-12)
        checked += 1
    assert checked >= 15


def test_null_returns_p_one_without_trades_and_is_deterministic():
    df = make_frame(4)
    wf = V.walk_forward(df, [V.Candidate("mom_30")], PATTERNS)
    a = V.random_entry_null(df, wf, draws=200, seed=7, key="k")
    b = V.random_entry_null(df, wf, draws=200, seed=7, key="k")
    assert a == b and 0 < a.p_value <= 1
    empty = V.WalkForwardResult(df, [], pd.Series(dtype=float), [], "continuous", None, 0, 0.001, 0.0005)
    assert V.random_entry_null(df, empty, draws=10).p_value == 1.0


# ------------------------------------------------------------------ planted signal
def _planted_pattern(df_full):
    nxt = np.sign(df_full["Close"].shift(-1) - df_full["Close"]).fillna(0).astype(int)

    def f(df):
        out = df.copy()
        out["signal"] = nxt.reindex(df.index).fillna(0).astype(int)
        return out
    return f


def test_planted_one_bar_ahead_signal_is_validated_and_dies_with_delay():
    df = make_frame(5)
    pats = {"planted": _planted_pattern(df), "ema_5_20": PATTERNS["ema_5_20"]}
    res = V.evaluate_candidates({"SYN": df}, pats, ((0.02, 0.04),), draws=300)
    by = {r.pattern: r for r in res}
    pl = by["planted"]
    assert pl.null_p < 0.01, pl.null_p
    assert pl.validation_status == "deflated_validated", pl.rejected_reasons
    assert pl.n_trials == 2 and pl.n_oos_trades >= config.MIN_TRADES_OOS
    assert by["ema_5_20"].validation_status == "unvalidated"
    # +5 bars of delay: the edge (and its significance) is gone
    sigs = V.compute_signals(df, pats)
    wf = V.walk_forward(df, [V.Candidate("planted")], signals=sigs)
    d5 = V.delay_stress(df, wf, sigs, 5)
    assert V.random_entry_null(df, d5, draws=300, key="d5").p_value > 0.05
    assert V.delay_stress(df, wf, sigs, 1).total_return < wf.total_return
    assert V.cost_stress(df, wf, sigs).total_return < wf.total_return


# ------------------------------------------------------------------ determinism / status gating
def test_same_seed_gives_byte_identical_json():
    df = make_frame(6, n=1500)
    kw = dict(draws=60, end="2025-01-01")
    a = V.results_to_json(V.evaluate_candidates({"A": df, "B": make_frame(7, 1500)}, PATTERNS, GRID, **kw))
    b = V.results_to_json(V.evaluate_candidates({"A": df, "B": make_frame(7, 1500)}, PATTERNS, GRID, **kw))
    assert a == b and "NaN" not in a
    c = V.results_to_json(V.evaluate_candidates({"A": df, "B": make_frame(7, 1500)}, PATTERNS, GRID, seed=1, **kw))
    assert c != a


def _ok_result(**kw) -> V.CandidateResult:
    base = dict(symbol="S", pattern="p", params={}, n_folds=3, n_oos_trades=40, oos_psr=0.99,
                holdout={"total_return": 0.01}, null_p=0.001, cost_ok=True, bh_significant=True)
    base.update(kw)
    return V.CandidateResult(**base)


@pytest.mark.parametrize("change,reason", [
    ({"bh_significant": False}, "not_bh_significant"),
    ({"n_oos_trades": 29}, "insufficient_oos_trades"),
    ({"oos_psr": 0.95}, "oos_psr_too_low"),
    ({"oos_psr": None}, "oos_psr_too_low"),
    ({"holdout": {"total_return": -0.001}}, "holdout_negative"),
    ({"holdout": None}, "holdout_unavailable"),
    ({"cost_ok": False}, "cost_stress_fails"),
])
def test_validation_status_requires_every_gate(change, reason):
    ok = _ok_result()
    V._decide(ok, config.MIN_TRADES_OOS, config.OOS_PSR_MIN)
    assert ok.validation_status == "oos_validated" and ok.rejected_reasons == []
    bad = _ok_result(**change)
    V._decide(bad, config.MIN_TRADES_OOS, config.OOS_PSR_MIN)
    assert bad.validation_status == "unvalidated" and reason in bad.rejected_reasons


def test_short_history_is_unvalidated_with_reason():
    df = path_walk(300, 1)
    (r,) = V.evaluate_candidates({"S": df}, {"ema_5_20": PATTERNS["ema_5_20"]}, draws=20)
    assert r.validation_status == "unvalidated" and "insufficient_history" in r.rejected_reasons


def test_bh_is_applied_across_all_candidates_in_a_run():
    df = make_frame(8, n=1500)
    res = V.evaluate_candidates({"A": df}, PATTERNS, GRID, draws=60, end="2025-01-01")
    assert len(res) == len(PATTERNS) * len(GRID) and all(r.n_trials == len(res) for r in res)
    rej, q = V.benjamini_hochberg([r.null_p for r in res])
    assert [r.bh_significant for r in res] == list(rej)
    assert all(r.bh_adjusted_p is not None for r in res)


# ------------------------------------------------------------------ signals.py helpers
def test_signals_helpers_pick_highest_tier():
    rs = [_ok_result(symbol="A", pattern="x", validation_status="unvalidated"),
          _ok_result(symbol="A", pattern="x", validation_status="oos_validated"),
          {"symbol": "B", "pattern": "x", "validation_status": "unvalidated"}]
    assert sigmod.best_validation_status(rs, "A", "x") == "oos_validated"
    assert sigmod.best_validation_status(rs, "C", "x") == "unvalidated"
    assert sigmod.validation_index(rs) == {("A", "x"): "oos_validated", ("B", "x"): "unvalidated"}


# ------------------------------------------------------------------ slow
@pytest.mark.slow
def test_random_walks_100_seeds_no_edge_after_selection_and_bh():
    cands = [V.Candidate(p, sl, tp) for p in PATTERNS for sl, tp in GRID]
    ins, oos, n_validated, n_total, n_runs_with_any = [], [], 0, 0, 0
    for seed in range(100):
        df = make_frame(1000 + seed)
        sigs = V.compute_signals(df, PATTERNS)
        wf = V.walk_forward(df, cands, signals=sigs, commission=0.0, slippage=0.0)  # frictionless: isolates selection bias
        ins.append(wf.in_sample_sharpe)
        oos.append(wf.oos_sharpe)
        res = V.evaluate_candidates({"RW": df}, PATTERNS, GRID, draws=200)
        v = sum(r.validation_status in ("oos_validated", "deflated_validated") for r in res)
        n_validated += v
        n_total += len(res)
        n_runs_with_any += v > 0
    print(f"in-sample {np.mean(ins):.3f} oos {np.mean(oos):.3f} validated {n_validated}/{n_total} runs {n_runs_with_any}")
    assert np.mean(ins) > 0.3                       # selection finds "edge" in-sample
    assert abs(np.mean(oos)) < 0.2                  # but it is noise out of sample
    assert n_validated / n_total < 0.05
    assert n_runs_with_any / 100 < 0.05
