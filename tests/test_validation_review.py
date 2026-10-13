"""Review round: executable variant (long-only + ATR exits), null resolution vs BH, hold-out read-once."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
import engine
import instruments
import signals as sigmod
import validation as V
from state.holdout import HoldoutStore
from tests.test_validation import PATTERNS, make_frame, path_walk


def _sig(df, **at):
    s = np.zeros(len(df), dtype=int)
    for i, v in at.items():
        s[int(i[1:])] = v
    return pd.Series(s, index=df.index)


# ------------------------------------------------------------------ engine: long-only and ATR exits
def test_long_only_minus_one_closes_long_and_never_opens_short():
    df = path_walk(120, 3)
    sig = _sig(df, s30=1, s40=-1, s41=-1, s60=-1, s70=1)
    both = engine.run_backtest(df, signal=sig, stop_loss=None, take_profit=None)
    lo = engine.run_backtest(df, signal=sig, stop_loss=None, take_profit=None, allow_short=False)
    assert any(t.direction == -1 for t in both.trades)
    assert all(t.direction == 1 for t in lo.trades)
    assert (lo.positions >= 0).all()
    first = lo.trades[0]
    assert first.entry_index == 31 and first.exit_index == 41 and first.exit_reason == "signal"
    assert lo.trades[1].entry_index == 71        # the -1 at bars 60 / 41 opened nothing


def test_atr_series_matches_latest_atr():
    df = path_walk(200, 4)
    a = engine.atr_series(df)
    for t in (20, 21, 57, 199):
        assert a[t] == pytest.approx(sigmod.latest_atr(df.iloc[:t + 1]), rel=1e-12)
    assert np.isnan(a[:19]).all()


def test_atr_exits_are_anchored_on_the_signal_close_like_execution():
    df = path_walk(150, 5, step=1.5)
    k = 60
    sig = _sig(df, **{f"s{k}": 1})
    run = engine.run_backtest(df, signal=sig, atr_stop_mult=config.ATR_STOP_MULT, atr_target_mult=3.0,
                              allow_short=False, stop_loss=None, take_profit=None)
    t = run.trades[0]
    ref, atr = float(df["Close"].iloc[k]), sigmod.latest_atr(df.iloc[:k + 1])
    stop, target = ref - config.ATR_STOP_MULT * atr, ref + 3.0 * atr
    o, h, lo = df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()
    assert t.entry_index == k + 1
    if t.exit_reason == "stop_loss":
        assert t.exit_price == pytest.approx(min(stop, o[t.exit_index]) * (1 - config.SLIPPAGE_PCT))
    elif t.exit_reason == "take_profit":
        assert t.exit_price == pytest.approx(max(target, o[t.exit_index]) * (1 - config.SLIPPAGE_PCT))
    # no earlier bar touched either level
    for j in range(t.entry_index, t.exit_index):
        assert lo[j] > stop and h[j] < target


def test_atr_entry_is_skipped_when_atr_undefined():
    df = path_walk(100, 6)
    run = engine.run_backtest(df, signal=_sig(df, s5=1), atr_stop_mult=2.0, atr_target_mult=3.0)
    assert run.trades == []


def test_null_simulator_replicates_engine_atr_exits():
    df = path_walk(400, 11, step=1.5)
    o, h, lo, c = (df[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    atr = engine.atr_series(df)
    rng = np.random.default_rng(3)
    checked = 0
    for e in rng.integers(30, 300, 60):
        sig = np.zeros(len(df), dtype=int)
        sig[e - 1] = 1
        run = engine.run_backtest(df, signal=pd.Series(sig, index=df.index), atr_stop_mult=1.0,
                                  atr_target_mult=1.5, allow_short=False, stop_loss=None, take_profit=None)
        t = run.trades[0]
        if t.exit_reason not in ("stop_loss", "take_profit"):
            continue
        sim = V._simulate_random_trades(
            o, h, lo, c, np.array([e]), np.array([1.0]), np.array([np.nan]), np.array([np.nan]),
            np.array([len(df) - 1 - e]), config.COMMISSION_PCT, config.SLIPPAGE_PCT,
            stop_abs=np.array([c[e - 1] - 1.0 * atr[e - 1]]), target_abs=np.array([c[e - 1] + 1.5 * atr[e - 1]]))
        assert sim[0] == pytest.approx(t.pnl_pct, rel=1e-9, abs=1e-12)
        checked += 1
    assert checked >= 15


# ------------------------------------------------------------------ executable variant
def _short_edge_pattern(df_full, fwd=5, thr=0.03):
    """-1 before a drop of more than `thr` over the next `fwd` bars (peeks); +1 only on the very last bar."""
    fut = df_full["Close"].shift(-fwd) / df_full["Close"] - 1
    sig = pd.Series(np.where(fut < -thr, -1, 0), index=df_full.index)
    sig.iloc[-1] = 1

    def f(df):
        out = df.copy()
        out["signal"] = sig.reindex(df.index).fillna(0).astype(int)
        return out
    return f


def test_short_only_edge_is_not_validated_for_the_executable_long_only_variant():
    df = make_frame(21)
    pats = {"short_edge": _short_edge_pattern(df)}
    # one trial has no cross-trial variance (DSR == PSR, cannot deflate): supply a nightly-style variance floor
    both = V.evaluate_candidates({"S": df}, pats, ((0.02, 0.04),), draws=200, sharpe_var_floor=1e-4)[0]
    assert both.validation_status == "deflated_validated", both.rejected_reasons     # edge is real, via shorts
    exe = V.evaluate_candidates({"S": df}, pats, draws=200, executable_variant=True)[0]
    assert exe.validation_status == "unvalidated" and exe.n_oos_trades == 0
    assert exe.params["long_only"] is True and exe.params["stop_atr"] == config.ATR_STOP_MULT


def test_scan_technical_reports_nothing_for_short_only_edge(monkeypatch):
    """D11 (Phase 3): the legacy in-sample tier that used to let this through as an 'unvalidated' BUY is retired;
    a long-only executable variant with no OOS evidence is simply not reported (and so never order-eligible)."""
    import main
    from services import pairs as pairs_svc, report as report_svc, scan as scan_svc, session as session_svc
    import backtester, data_fetcher
    df = make_frame(21)
    monkeypatch.setattr(scan_svc, "PATTERN_REGISTRY", {"short_edge": _short_edge_pattern(df)})
    monkeypatch.setattr(session_svc, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan_svc, "_holdout_store", lambda: HoldoutStore(":memory:"))
    intents: list = []
    got = scan_svc.scan_technical({"S": df}, intents=intents)
    assert intents == [] and got == []


def test_scan_technical_requests_executable_variant_and_store(monkeypatch):
    import main
    from services import pairs as pairs_svc, report as report_svc, scan as scan_svc, session as session_svc
    import backtester, data_fetcher
    seen = {}

    def fake(frames, patterns, *a, **k):
        seen.update(k)
        return []

    monkeypatch.setattr(scan_svc.validation, "evaluate_candidates", fake)
    monkeypatch.setattr(session_svc, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan_svc, "_holdout_store", lambda: "STORE")
    scan_svc.scan_technical({}, intents=[])
    assert seen["executable_variant"] is True and seen["holdout_store"] == "STORE" and seen["require_holdout_store"] is True


# ------------------------------------------------------------------ null resolution vs the BH family
def _sparse_planted(df_full, fwd=5, thr=0.03):
    fut = df_full["Close"].shift(-fwd) / df_full["Close"] - 1
    sig = pd.Series(np.where(fut > thr, 1, np.where(fut < -thr, -1, 0)), index=df_full.index)

    def f(df):
        out = df.copy()
        out["signal"] = sig.reindex(df.index).fillna(0).astype(int) if df is df_full else 0
        return out
    return f


def _calls(monkeypatch):
    calls = []
    orig = V.random_entry_null

    def spy(df, res, draws=0, **k):
        calls.append(draws)
        return orig(df, res, draws=draws, **k)

    monkeypatch.setattr(V, "random_entry_null", spy)
    return calls


def test_null_escalates_draws_for_bh_contenders_until_floor_below_alpha_over_m(monkeypatch):
    df = make_frame(5)
    sigs = V.compute_signals(df, {"p": _sparse_planted(df)})
    wf = V.walk_forward(df, [V.Candidate("p")], signals=sigs)
    calls = _calls(monkeypatch)
    r = V.adaptive_null(df, wf, family_size=100, draws=50, key="k")
    assert calls == [50, 500, 5000]                 # 1/5001 < 0.05/100: stop escalating
    assert r.draws == 5000 and r.p_value < 5e-4


def test_null_does_not_escalate_for_non_contenders(monkeypatch):
    df = make_frame(8, n=1500)
    sigs = V.compute_signals(df, PATTERNS)
    wf = V.walk_forward(df, [V.Candidate("mom_10")], signals=sigs, end="2025-01-01")
    calls = _calls(monkeypatch)
    r = V.adaptive_null(df, wf, family_size=780, draws=100, key="x", seed=3)
    assert r.p_value > config.FDR_ALPHA and calls == [100]


def test_null_blocks_keep_the_single_block_stream():
    df = make_frame(5)
    sigs = V.compute_signals(df, {"p": _sparse_planted(df)})
    wf = V.walk_forward(df, [V.Candidate("p")], signals=sigs)
    a = V.random_entry_null(df, wf, draws=300, key="z")
    b = V.random_entry_null(df, wf, draws=300, key="z")
    assert a.p_value == b.p_value and a.null_mean == b.null_mean


@pytest.mark.slow
def test_bh_can_reject_one_planted_candidate_among_780():
    """39 symbols x 20 patterns: the BH rank-1 threshold is 0.05/780 = 6.4e-5, below the 1/1001 floor of a
    1000-draw null. The adaptive null must resolve a real edge with true p << 1e-5."""
    df = make_frame(5)
    planted = _sparse_planted(df, thr=0.012)
    pats = {f"zero_{i:02d}": (lambda d: d.assign(signal=0)) for i in range(19)}
    pats["planted"] = planted
    frames = {"P0": df}
    for k in range(1, 39):
        frames[f"S{k:02d}"] = df.iloc[:2300].copy()       # a different object: the planted pattern stays flat
    res = V.evaluate_candidates(frames, pats, ((0.02, 0.04),))
    assert len(res) == 780 and all(r.n_trials == 780 for r in res)
    top = [r for r in res if r.bh_significant]
    assert [(r.symbol, r.pattern) for r in top] == [("P0", "planted")]
    assert top[0].null_draws >= 100_000 and top[0].null_p < 6.4e-5
    assert top[0].validation_status == "deflated_validated", top[0].rejected_reasons


# ------------------------------------------------------------------ hold-out: read once per strategy version
def test_holdout_store_is_first_write_wins():
    st = HoldoutStore(":memory:")
    assert st.get("v1", "k") is None
    assert st.put("v1", "k", {"result": {"total_return": 0.1}}) is True
    assert st.put("v1", "k", {"result": {"total_return": -0.9}}) is False
    assert st.get("v1", "k") == {"result": {"total_return": 0.1}}
    assert st.get("v2", "k") is None and st.versions_read("k") == 1


def test_second_scan_does_not_change_a_stored_holdout_verdict():
    df = make_frame(5)
    pats = {"p": _sparse_planted(df)}
    st = HoldoutStore(":memory:")
    kw = dict(draws=60, holdout_store=st)
    first = V.evaluate_candidates({"S": df}, pats, ((0.02, 0.04),), **kw)[0]
    assert first.holdout is not None and first.holdout_frozen is False
    # later scan: same pre-hold-out data, hold-out bars now very different (moved / grown)
    rng = np.random.default_rng(1)
    mod = df.copy()
    ho = V.holdout_position(df)
    mod.iloc[ho:, :4] = mod.iloc[ho:, :4].to_numpy() * rng.uniform(0.9, 1.1, size=(len(df) - ho, 1))
    mod["High"] = mod[["Open", "High", "Low", "Close"]].max(axis=1)
    mod["Low"] = mod[["Open", "High", "Low", "Close"]].min(axis=1)
    second = V.evaluate_candidates({"S": mod}, {"p": _sparse_planted(mod)}, ((0.02, 0.04),), **kw)[0]
    assert second.holdout_frozen is True
    assert second.holdout == first.holdout and second.holdout_asof == first.holdout_asof
    # without a store the verdict is recomputed from the new bars
    fresh = V.evaluate_candidates({"S": mod}, {"p": _sparse_planted(mod)}, ((0.02, 0.04),), draws=60)[0]
    assert fresh.holdout != first.holdout and not fresh.holdout_frozen


def test_changed_strategy_is_a_new_version_with_a_new_read():
    df = make_frame(5, n=1500)
    st = HoldoutStore(":memory:")
    a = V.evaluate_candidates({"S": df}, {"p": PATTERNS["mom_10"]}, ((0.02, 0.04),), draws=40, end="2025-01-01",
                              holdout_store=st)[0]
    b = V.evaluate_candidates({"S": df}, {"p": PATTERNS["mom_10"]}, ((0.03, 0.04),), draws=40, end="2025-01-01",
                              holdout_store=st)[0]
    assert not a.holdout_frozen and not b.holdout_frozen     # different exits -> different candidate key


def test_bars_per_year_uses_registry():
    assert instruments.bars_per_year("SPY") == 252
    assert instruments.bars_per_year("NOT-A-REAL-SYMBOL") == 252
    crypto = [i for i in instruments.all_instruments() if i.asset_class == "crypto"]
    if crypto:
        assert instruments.bars_per_year(crypto[0].id) == 365


def test_legacy_walk_forward_test_windows_do_not_overlap():
    import backtester
    df = make_frame(4, n=1200)
    res = backtester.walk_forward_validate(df, "SYN", PATTERNS["ema_5_20"], "ema", n_splits=4)
    assert len(res) >= 2
    spans = [(r.equity_curve.index[0], r.equity_curve.index[-1]) for r in res]
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:]))


def test_classic_backtest_and_monte_carlo_annualise_crypto_with_365():
    import backtester
    import stress_test
    crypto = [i.id for i in instruments.all_instruments() if i.asset_class == "crypto"]
    if not crypto:
        pytest.skip("no crypto instrument in the registry")
    df = make_frame(4, n=800)
    df = PATTERNS["ema_5_20"](df)
    a = backtester.classic_backtest(df, crypto[0], "p")
    b = backtester.classic_backtest(df, "SPY", "p")
    assert a.bars_per_year == 365 and b.bars_per_year == 252
    assert a.sharpe_ratio == pytest.approx(b.sharpe_ratio * (365 / 252) ** 0.5, rel=1e-9)
    assert stress_test.monte_carlo_analysis(a, n_simulations=20).get("n_simulations") == 20


# ------------------------------------------------------------------ hold-out store fails closed
class _BrokenStore:
    def __init__(self, get_raises=False, put_raises=False):
        self.get_raises, self.put_raises = get_raises, put_raises

    def get(self, version, key):
        if self.get_raises:
            raise RuntimeError("database is locked")
        return None

    def put(self, version, key, payload):
        if self.put_raises:
            raise RuntimeError("database is locked")
        return True

    def versions_read(self, prefix):
        return 0


def _planted_run(**kw):
    df = make_frame(5)
    return V.evaluate_candidates({"S": df}, {"p": _sparse_planted(df)}, ((0.02, 0.04),), draws=60, **kw)[0]


@pytest.mark.parametrize("store", [_BrokenStore(get_raises=True), _BrokenStore(put_raises=True)])
def test_failing_holdout_store_fails_closed(store):
    cr = _planted_run(holdout_store=store)
    assert cr.validation_status == "unvalidated" and cr.holdout is None and not cr.holdout_frozen
    assert "holdout_store_unavailable" in cr.rejected_reasons


def test_missing_required_holdout_store_fails_closed_but_offline_use_still_works():
    cr = _planted_run(holdout_store=None, require_holdout_store=True)
    assert cr.validation_status == "unvalidated" and "holdout_store_unavailable" in cr.rejected_reasons
    offline = _planted_run()
    assert offline.holdout is not None and "holdout_store_unavailable" not in offline.rejected_reasons


def test_scan_technical_with_unopenable_store_never_validates_and_closes_the_store(monkeypatch):
    import main
    from services import pairs as pairs_svc, report as report_svc, scan as scan_svc, session as session_svc
    import backtester, data_fetcher
    df = make_frame(21)
    monkeypatch.setattr(scan_svc, "PATTERN_REGISTRY", {"short_edge": _short_edge_pattern(df)})
    monkeypatch.setattr(session_svc, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan_svc, "_holdout_store", lambda: None)
    seen = []
    real = scan_svc.validation.evaluate_candidates

    def spy(*a, **k):
        out = real(*a, **k)
        seen.extend(out)
        return out
    monkeypatch.setattr(scan_svc.validation, "evaluate_candidates", spy)
    intents = []
    scan_svc.scan_technical({"S": df}, intents=intents)
    # the real validation ran and failed closed: every candidate unvalidated with the store reason, no order intent
    assert seen and all(r.validation_status == "unvalidated" and "holdout_store_unavailable" in r.rejected_reasons
                        for r in seen)
    assert intents == []
    st = HoldoutStore(":memory:")
    monkeypatch.setattr(scan_svc, "_holdout_store", lambda: st)
    scan_svc.scan_technical({"S": df}, intents=[])
    with pytest.raises(Exception):
        st.get("v", "k")                    # closed in a finally


# ------------------------------------------------------------------ strategy version
def _pat_a(df):
    return df.assign(signal=0)


def _pat_b(df):
    s = 0
    return df.assign(signal=s)


def test_strategy_version_tracks_pattern_source_and_modules(monkeypatch):
    cand = V.Candidate("p")
    va = V.strategy_version(_pat_a, cand, "2025-01-01", 0.0, 0.0)
    assert va == V.strategy_version(_pat_a, cand, "2025-01-01", 0.0, 0.0)
    assert va != V.strategy_version(_pat_b, cand, "2025-01-01", 0.0, 0.0)
    monkeypatch.setitem(V._MODULE_HASH, "patterns", "changed-helper")
    assert va != V.strategy_version(_pat_a, cand, "2025-01-01", 0.0, 0.0)


def test_same_source_across_store_instances_is_frozen_and_changed_body_is_a_new_read(tmp_path):
    df = make_frame(5)
    base = _sparse_planted(df)

    def pat_one(d):
        return base(d)

    def pat_two(d):
        out = base(d)
        return out

    path = str(tmp_path / "ho.sqlite")
    run = lambda fn: V.evaluate_candidates({"S": df}, {"p": fn}, ((0.02, 0.04),), draws=40,   # noqa: E731
                                           holdout_store=HoldoutStore(path))[0]
    first = run(pat_one)
    assert not first.holdout_frozen and first.holdout_versions_read == 1
    again = run(pat_one)
    assert again.holdout_frozen and again.holdout_versions_read == 1
    changed = run(pat_two)                    # same candidate key, different pattern body
    assert not changed.holdout_frozen and changed.holdout_versions_read == 2


# ------------------------------------------------------------------ null replicates the executable engine
def _single_entry_wf(df, direction, long_only):
    k = 60
    sig = np.zeros(len(df), dtype=int)
    sig[k] = direction
    run = engine.run_backtest(df, signal=pd.Series(sig, index=df.index), stop_loss=None, take_profit=None,
                              atr_stop_mult=config.ATR_STOP_MULT, atr_target_mult=3.0,
                              allow_short=not long_only, commission=config.COMMISSION_PCT,
                              slippage=config.SLIPPAGE_PCT)
    t = run.trades[0]
    assert t.entry_index == k + 1 and t.direction == direction
    ot = V.OOSTrade(t.entry_index, t.exit_index, t.direction, None, None, t.pnl_pct, t.bars_held, t.exit_reason,
                    t.entry_index, t.entry_index + 1, config.ATR_STOP_MULT, 3.0)
    wf = V.WalkForwardResult(df=df, folds=[], oos_returns=pd.Series(dtype=float), oos_trades=[ot], mode="continuous",
                             final_candidate=None, n_pre=len(df), commission=config.COMMISSION_PCT,
                             slippage=config.SLIPPAGE_PCT)
    return wf, t


@pytest.mark.parametrize("direction,long_only", [(1, True), (-1, False)])
def test_random_entry_null_with_one_possible_entry_equals_the_engine_trade(direction, long_only):
    """Window width 1 => every draw is the engine's entry: a null that peeks (ATR / reference from the entry
    bar) or flips directions cannot match the engine pnl."""
    df = path_walk(150, 5, step=1.5)
    wf, t = _single_entry_wf(df, direction, long_only)
    res = V.random_entry_null(df, wf, draws=20, key="one")
    assert res.null_mean == pytest.approx(t.pnl_pct, rel=1e-9, abs=1e-12)
    assert res.null_std == pytest.approx(0.0, abs=1e-12)
