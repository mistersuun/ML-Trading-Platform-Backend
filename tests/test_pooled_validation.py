"""D18 pooled validation: pooling arithmetic, gates, hold-out read-once, ledger, registry namespace, endpoint.

Synthetic universes with planted edges (tests/pooled_helpers.py); nothing touches real data or the network."""
import numpy as np
import pandas as pd
import pytest

import config
import pooled_validation as PV
import validation as V
from state.holdout import HoldoutStore
from tests import pooled_helpers as H
from trials.pooled_ledger import PooledLedger

NAMES = ["planted", "noise_a", "noise_b", "noise_c", "noise_d", "noise_e"]


def _patterns():
    pats = {"planted": H.planted_pattern(1)}
    for i, nm in enumerate(NAMES[1:]):
        pats[nm] = H.planted_pattern(10 + i, p=0.03, hold=2 + i)
    return pats


def _run(frames, tmp_path, patterns=None, **kw):
    kw.setdefault("ledger", PooledLedger(str(tmp_path / "t.sqlite")))
    kw.setdefault("holdout_store", HoldoutStore(str(tmp_path / "h.sqlite")))
    kw.setdefault("registry", False)
    kw.setdefault("write_components", False)
    kw.setdefault("draws", 300)
    return PV.evaluate_pooled(frames, patterns or _patterns(), **kw)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """One full pooled run on a universe where only the pattern 'planted' has an edge (shared by many tests)."""
    mp = pytest.MonkeyPatch()
    H.use_universe(mp)
    H.use_small_family(mp, NAMES)
    d = tmp_path_factory.mktemp("planted")
    try:
        out = _run(H.planted_universe(), d)
    finally:
        mp.undo()                       # the patches are only needed while the run executes
    return out, d


def _pat(out, name):
    return next(p for p in out["patterns"] if p["pattern"] == name)


# ------------------------------------------------------------------ 1. pooling arithmetic
def test_trade_rho_sums_to_the_trade_pnl_and_to_the_engine_equity_change():
    df = H.planted_frame(1)
    cand = V.executable_candidates(["planted"])[0]
    sig = H.planted_pattern(1)(df)["signal"]
    run = V._run(df, sig, cand, 0, 900, config.COMMISSION_PCT, config.SLIPPAGE_PCT)
    assert len(run.trades) > 20
    o, c = df["Open"].to_numpy(), df["Close"].to_numpy()
    eq_diff = np.diff(np.concatenate([[run.initial_capital], run.equity.to_numpy()]))
    rebuilt = np.zeros(900)
    for t in run.trades:
        rho = PV._trade_rho(o, c, t.entry_index, t.exit_index, t.pnl_pct, config.COMMISSION_PCT, config.SLIPPAGE_PCT)
        assert len(rho) == t.exit_index - t.entry_index + 1
        assert rho.sum() == pytest.approx(t.pnl_pct, abs=1e-12)
        rebuilt[t.entry_index:t.exit_index + 1] += t.notional * rho
    np.testing.assert_allclose(rebuilt, eq_diff, atol=1e-6)


def test_stop_fraction_weight_and_r_multiple_against_a_hand_computed_fixture():
    atr = np.full(40, np.nan)
    atr[30] = 2.0
    c = np.full(40, 100.0)
    assert PV._stop_frac(atr, c, 31, 2.0) == pytest.approx(0.04)            # 2 x ATR / close
    assert np.isnan(PV._stop_frac(atr, c, 0, 2.0)) and np.isnan(PV._stop_frac(atr, c, 10, 2.0))
    sd = PV.SymbolData("X", pd.DataFrame(), n_pre=40, dates=pd.bdate_range("2024-01-01", periods=40),
                       atr=atr, c=c)
    tr = V.OOSTrade(31, 35, 1, None, None, 0.02, 4, "signal", 30, 40, 2.0, 3.0)
    (rec,) = PV._recs_from_oos(sd, [tr])
    assert rec.stop_frac == pytest.approx(0.04)
    assert rec.R == pytest.approx(0.02 / 0.04)                              # pnl_pct / stop_frac = 0.5 R
    assert rec.weight == pytest.approx(config.RISK_PER_TRADE_PCT / 0.04)    # 12.5% of the sleeve
    assert rec.week == PV._week_key(sd.dates[31])


def test_entry_week_clusters_use_the_iso_week():
    assert PV._week_key(pd.Timestamp("2024-12-30")) == 202501                # ISO week 1 of 2025
    assert PV._week_key(pd.Timestamp("2024-01-05")) == PV._week_key(pd.Timestamp("2024-01-01"))
    assert PV._week_key(pd.Timestamp("2024-01-08")) != PV._week_key(pd.Timestamp("2024-01-05"))


def test_series_alignment_flat_days_zero_and_nothing_at_or_after_the_holdout(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["planted"])
    frames = H.planted_universe()
    # a symbol with a shorter history (a META-like late start) and a different calendar position
    frames["DIA"] = H.planted_frame(109, n=760, start="2022-06-09")
    out = PV.evaluate_pooled(frames, {"planted": H.planted_pattern(1)}, ledger=PooledLedger(str(tmp_path / "t.sqlite")),
                             holdout_store=HoldoutStore(str(tmp_path / "h.sqlite")), registry=False, draws=200)
    assert out["status"] == "ok" and out["universe"]["complete"]
    comps = pd.read_parquet(next((tmp_path / "trials").glob("*.components.parquet")))
    assert comps.index.max() < pd.Timestamp(config.HOLDOUT_START)          # nothing at or after the hold-out
    dia = comps["planted|DIA"]
    first_oos_dia = frames["DIA"].index[config.POOLED_WARMUP_BARS]
    assert (dia[dia.index < first_oos_dia] == 0).all()                      # no contribution before its OOS start
    assert (dia != 0).any()
    other = comps["planted|AAPL"]
    assert other.index.min() == frames["AAPL"].index[config.POOLED_WARMUP_BARS]
    series = comps.sum(axis=1)
    assert (series == 0).any() and (series != 0).any()                      # flat days are 0, as per symbol
    assert out["oos"]["start"] == str(other.index.min().date())


def test_walk_forward_uses_the_252_warmup_in_the_pooled_path_only(planted):
    out, _ = planted
    assert out["warmup_bars"] == 252 == config.POOLED_WARMUP_BARS
    assert config.WF_TRAIN_BARS == 504                                      # the per-symbol path is unchanged
    df = H.planted_frame(100)
    assert len(V.make_folds(V.holdout_position(df, config.HOLDOUT_START), 252, 126, 126)) == 5
    assert len(V.make_folds(V.holdout_position(df, config.HOLDOUT_START), 504, 126, 126)) == 3


def test_retired_pattern_is_not_evaluated_pooled_but_stays_in_n(tmp_path):
    from patterns import PATTERN_REGISTRY
    assert "macd_hist_reversal" in PATTERN_REGISTRY                         # still in the registry (per-symbol path)
    assert PV.retired_patterns() == {"macd_hist_reversal": "macd_crossover"}
    names = PV.pooled_pattern_names()
    assert "macd_hist_reversal" not in names and "macd_crossover" in names and len(names) == 21
    assert PV.pooled_universe_trials() == 22                                # retired pattern stays in the N floor


# ------------------------------------------------------------------ 2. gates and statuses on the planted universe
def test_a_planted_broad_edge_is_found_pooled_end_to_end(planted):
    out, _ = planted
    p = _pat(out, "planted")
    assert p["status"] == PV.POOLED_DEFLATED_VALIDATED and p["reasons"] == []
    assert all(p["gates"][g] for g in PV.GATE_ORDER)
    assert p["order_eligible"] is False and out["order_eligible"] is False and out["shadow_only"] is True
    assert out["funnel"]["pooled_validated"] == 1 and out["funnel"]["orders"] == 0
    f = out["funnel"]
    stages = [f[g] for g in ("patterns_tested", *PV.GATE_ORDER)]
    assert stages == sorted(stages, reverse=True)                           # each stage is a subset of the last
    assert p["t_eff"] <= out["oos"]["days"] and p["n_clusters"] >= config.POOLED_MIN_CLUSTERS
    assert p["pooled_version"] and p["trial_version"] and p["bootstrap_sharpe"]["lo"] < p["sharpe"]


def test_noise_patterns_do_not_validate_and_the_status_vocabulary_is_closed(planted):
    out, _ = planted
    for p in out["patterns"]:
        assert p["status"] in ("unvalidated", *PV.POOLED_STATUSES)
        if p["pattern"] != "planted":
            assert p["status"] == "unvalidated" and p["reasons"]


def test_pooled_statuses_are_never_order_eligible():
    assert not set(PV.POOLED_STATUSES) & set(config.ORDER_ELIGIBLE_STATUSES)
    assert config.ORDER_ELIGIBLE_STATUSES == ("deflated_validated",)
    assert config.POOLED_VALIDATION in ("off", "shadow")


def test_shadow_guard_refuses_to_run_when_a_pooled_status_became_eligible(monkeypatch, tmp_path):
    H.use_universe(monkeypatch)
    monkeypatch.setattr(config, "ORDER_ELIGIBLE_STATUSES", ("deflated_validated", PV.POOLED_DEFLATED_VALIDATED))
    out = PV.evaluate_pooled(H.planted_universe(), {"planted": H.planted_pattern(1)},
                             ledger=PooledLedger(str(tmp_path / "t.sqlite")),
                             holdout_store=HoldoutStore(str(tmp_path / "h.sqlite")), registry=False)
    assert out["status"] == PV.RUN_NOT_SHADOW_SAFE and out["patterns"] == []


def test_alpha_is_split_with_the_per_symbol_family(planted):
    out, _ = planted
    # a pooled DSR p of exactly DSR_P_MAX / 2 is the bar (not DSR_P_MAX)
    p = _pat(out, "planted")
    assert p["dsr_p"] is not None and p["dsr_p"] < config.DSR_P_MAX / 2


def test_min_trades_gate_counts_clusters_as_well_as_trades(planted):
    out, _ = planted
    for p in out["patterns"]:
        ok = p["n_trades"] >= config.POOLED_MIN_TRADES and p["n_clusters"] >= config.POOLED_MIN_CLUSTERS
        assert p["gates"]["pooled_min_trades"] is ok
    assert any(not p["gates"]["pooled_min_trades"] for p in out["patterns"])


# ------------------------------------------------------------------ 3. concentration gates (unit level)
def _rec(sym, week, R, day):
    d = pd.Timestamp("2023-01-02") + pd.Timedelta(days=day)
    return PV.TradeRec(sym, 0, 1, d, d + pd.Timedelta(days=1), R * 0.04, 1, "signal", 0, 100, 0.04, R, 0.125, week)


def _axis(n=504):
    return pd.DatetimeIndex(pd.Timestamp("2023-01-02") + pd.to_timedelta(np.arange(n), unit="D"))


def _spread(rng, syms, n_per=40, mean=0.3, days=500):
    recs = []
    for s in syms:
        for i in range(n_per):
            recs.append(_rec(s, 202300 + i, float(rng.normal(mean, 1.0)), int(rng.integers(0, days))))
    return recs


def test_concentration_passes_a_broad_edge():
    recs = _spread(np.random.default_rng(1), ["AAPL", "MSFT", "SPY", "JPM", "XOM", "GS", "CVX", "IWM", "QQQ", "DIA",
                                              "BAC", "AMZN"])
    c = PV.concentration(recs, _axis(), 126)
    assert c["ok"] and c["max_symbol_share"] <= config.POOLED_MAX_SYMBOL_SHARE


def test_a_planted_edge_in_one_symbol_only_is_concentrated():
    rng = np.random.default_rng(2)
    recs = _spread(rng, ["AAPL"], mean=1.5) + _spread(rng, ["MSFT", "SPY", "JPM", "XOM", "GS", "CVX", "IWM"], mean=-0.02)
    c = PV.concentration(recs, _axis(), 126)
    assert c["top_symbol"] == "AAPL" and not c["share_ok"] and not c["ok"]


def test_an_edge_only_in_mega_tech_fails_leave_one_cluster_out():
    rng = np.random.default_rng(3)
    mega = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "AMD", "TSLA", "META"]
    others = ["SPY", "JPM", "XOM", "GS", "CVX", "IWM", "DIA", "QQQ", "BAC"]
    recs = _spread(rng, mega, mean=0.6) + _spread(rng, others, mean=-0.15)
    c = PV.concentration(recs, _axis(), 126)
    assert c["share_ok"] and c["loso_ok"]                                   # no single name carries it ...
    assert not c["loco_ok"] and c["loco_worst_cluster"] == "mega_tech" and not c["ok"]   # ... one cluster does


def test_a_one_fold_edge_fails_leave_one_fold_out():
    rng = np.random.default_rng(4)
    syms = ["AAPL", "MSFT", "SPY", "JPM", "XOM", "GS", "CVX", "IWM"]
    recs = (_spread(rng, syms, n_per=20, mean=1.5, days=120)                 # all the edge sits in the first 126 days
            + _spread(rng, syms, n_per=60, mean=-0.05, days=500))
    for r in recs[len(syms) * 20:]:
        r.entry_date = r.entry_date + pd.Timedelta(days=130)
    c = PV.concentration(recs, _axis(), 126)
    assert c["share_ok"] and c["loso_ok"] and c["loco_ok"]
    assert not c["lofo_ok"] and not c["ok"] and c["lofo_worst_fold"] == 0


def test_loso_catches_a_barely_positive_mean_that_one_symbol_carries():
    # no symbol has 25% of the absolute P&L, but removing one flips the pooled mean R
    R = np.array([0.5] * 4 + [-0.1] * 4 + [0.02] * 3 + [-0.05] * 6)
    syms = np.array(["A"] * 4 + ["B"] * 4 + ["C"] * 3 + ["D"] * 6)
    v, s = PV._loso(R, syms)
    assert s == "A" and v < 0 < R.mean()


def test_exclusion_rule_is_weak_and_needs_three_trades():
    rng = np.random.default_rng(5)
    bad = [_rec("BAD", 1, float(x), i) for i, x in enumerate(rng.normal(-3.0, 0.5, 12))]
    mild = [_rec("MILD", 1, float(x), i) for i, x in enumerate(rng.normal(-0.05, 1.0, 12))]
    two = [_rec("TWO", 1, -9.0, 1), _rec("TWO", 1, -9.1, 2)]
    assert PV.excluded_symbols(bad + mild + two) == ["BAD"]                 # mildly negative and tiny samples trade


# ------------------------------------------------------------------ 4. capped replay
def _day_recs(n_syms, day=10, exit_day=15, stop=0.04):
    syms = ["AAPL", "MSFT", "NVDA", "AMZN", "SPY", "QQQ", "IWM", "DIA", "JPM", "GS", "BAC", "XOM", "CVX", "ZZZ1", "ZZZ2"]
    out = []
    for s in syms[:n_syms]:
        d = pd.Timestamp("2023-02-01") + pd.Timedelta(days=day)
        r = _rec(s, 1, 0.5, 0)
        r.entry_date, r.exit_date, r.stop_frac, r.weight = d, d + pd.Timedelta(days=exit_day - day), stop, 0.005 / stop
        out.append(r)
    return out


def test_capped_replay_applies_the_d5_caps_in_a_deterministic_alphabetical_order():
    recs = _day_recs(15)
    adm, info = PV.capped_replay(recs, [])
    assert info["admitted"] == len(adm) <= config.MAX_ORDERS_PER_DAY          # 5 new orders a day binds first
    chosen = [recs[i].symbol for i in adm]
    cluster_counts = {}
    for s in chosen:
        cluster_counts[PV._cluster_of(s)] = cluster_counts.get(PV._cluster_of(s), 0) + 1
    assert max(cluster_counts.values()) <= config.MAX_POSITIONS_PER_CLUSTER
    rev = list(reversed(recs))
    adm2, _ = PV.capped_replay(rev, [])
    assert sorted(rev[i].symbol for i in adm2) == sorted(chosen)              # same trade set whatever the input order
    assert sorted(chosen) == ["AAPL", "AMZN", "BAC", "CVX", "DIA"]            # alphabetical order, caps applied in it
    assert PV.capped_replay(recs, [recs[0].symbol])[1]["excluded_trades"] == 1


def test_capped_replay_open_position_cap_and_exclusions():
    recs = []
    for k in range(12):                                                      # 12 distinct days, long holds, own clusters
        r = _day_recs(1, day=10 + k, exit_day=60)[0]
        r.symbol = f"S{k:02d}"
        recs.append(r)
    adm, _ = PV.capped_replay(recs, [])
    assert len(adm) == config.MAX_OPEN_POSITIONS


# ------------------------------------------------------------------ 5. cluster-preserving null
def test_members_of_a_cluster_share_one_offset_and_stay_inside_their_fold_window():
    sds, recs = H.null_setup()
    tb = PV.null_tables(recs, sds)
    NE, VL, nval, cl, H_ = tb["NE"], tb["VL"], tb["nval"], tb["cl"], tb["H"]
    assert len(set(cl.tolist())) < len(recs)                                  # several trades per entry week
    e = PV.draw_entries(NE, VL, nval, cl, np.random.default_rng(1), 200)
    moved = 0
    for c in set(cl.tolist()):
        mem = [i for i in np.flatnonzero(cl == c) if not tb["fixed"][i]]
        if len(mem) < 2:
            continue
        offs = []
        for i in mem:
            days = sds[recs[i].symbol].dates.values.astype("datetime64[D]").astype(np.int64)
            offs.append(days[e[i]] - days[recs[i].entry])
        offs = np.array(offs)
        # calendar offsets agree across members up to the snap to the next trading day on their own calendar
        assert (offs.max(axis=0) - offs.min(axis=0)).max() <= 3
        moved += int((offs != 0).any())
    assert moved > 0
    for i, t in enumerate(recs):                                              # every draw inside its own window
        hi = max(t.w_lo, t.w_hi - 1 - int(H_[i]))
        assert (e[i] >= t.w_lo).all() and (e[i] <= max(hi, t.entry)).all()


def test_the_null_fails_closed_when_too_many_trades_cannot_share_an_offset(monkeypatch):
    sds, recs = H.null_setup()
    cc = PV._Concat(sds, sorted(sds))
    ok = PV.pooled_null(recs, sds, cc, draws=100, seed=1, key="k", comm=config.COMMISSION_PCT,
                        slip=config.SLIPPAGE_PCT, family_size=4, alpha=0.025)
    assert not ok.failed and 0 < ok.p_value <= 1 and ok.unfit_share <= config.POOLED_NULL_MAX_UNFIT
    monkeypatch.setattr(config, "POOLED_NULL_MAX_UNFIT", -1.0)
    bad = PV.pooled_null(recs, sds, cc, draws=100, seed=1, key="k", comm=config.COMMISSION_PCT,
                         slip=config.SLIPPAGE_PCT, family_size=4, alpha=0.025)
    assert bad.failed and bad.p_value == 1.0


def test_the_null_is_seeded_and_beaten_by_a_planted_edge():
    sds, recs = H.null_setup()
    cc = PV._Concat(sds, sorted(sds))
    kw = dict(draws=300, seed=3, key="k", comm=config.COMMISSION_PCT, slip=config.SLIPPAGE_PCT, family_size=4,
              alpha=0.025)
    a, b = PV.pooled_null(recs, sds, cc, **kw), PV.pooled_null(recs, sds, cc, **kw)
    assert a.p_value == b.p_value and a.null_mean == b.null_mean
    assert a.p_value <= 0.01 and a.observed > a.null_mean + 3 * a.null_std    # observed mean R far above the null


# ------------------------------------------------------------------ 6. BH family floor, T_eff, V
def test_bh_pads_a_narrowed_family_to_the_registered_size():
    p = [0.004, 0.2]
    rej_n, q_n = PV.bh_with_floor(p, 0.025, 2)
    rej_f, q_f = PV.bh_with_floor(p, 0.025, 20)
    assert q_f[0] == pytest.approx(0.004 * 20) and q_n[0] == pytest.approx(0.004 * 2 / 1)
    assert rej_n[0] and not rej_f[0]                                          # narrowing cannot lower the bar


def test_effective_bandwidth_follows_the_longest_hold(planted):
    out, _ = planted
    p = _pat(out, "planted")
    assert p["q"] == max(config.POOLED_HAC_LAGS, p["max_hold_bars"])


def test_funnel_and_pattern_payload_validate_against_the_typed_models(planted):
    from services import models as M
    out, _ = planted
    M.PooledPayload.model_validate(out)
    M.LatestPooledResult.model_validate({"kind": "pooled", "generated_at": out["generated_at"], "age_hours": 0.0,
                                         "stale": False, "payload": out})


def test_payload_is_strict_json(planted):
    import json
    out, _ = planted
    json.dumps(out, allow_nan=False)


# ------------------------------------------------------------------ 7. hold-out read once, fail closed, read cap
def test_holdout_is_read_once_per_pooled_version_and_frozen(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    frames = H.planted_universe()
    store = HoldoutStore(str(tmp_path / "h.sqlite"))
    ledger = PooledLedger(str(tmp_path / "t.sqlite"))
    first = _run(frames, tmp_path, ledger=ledger, holdout_store=store)
    p1 = _pat(first, "planted")
    assert p1["holdout_state"] == "read" and not p1["holdout_frozen"]
    assert ledger.holdout_read_versions(first["holdout_start"], "planted") == [p1["pooled_version"]]
    # one more bar of data later: the stored read is reused, never re-evaluated
    longer = {s: H.planted_frame(100 + i, n=H.N_BARS + 5) for i, s in enumerate(H.SYMBOLS10)}
    second = _run(longer, tmp_path, ledger=ledger, holdout_store=store)
    p2 = _pat(second, "planted")
    assert p2["holdout_frozen"] and p2["holdout"] == p1["holdout"] and p2["holdout_asof"] == p1["holdout_asof"]
    key = f"pooled|planted|{V.executable_candidates(['planted'])[0].key}|{p1['pooled_version']}"
    assert store.get(p1["pooled_version"], key)["result"]["n_trades"] == p1["holdout"]["n_trades"]
    # nothing else touched the per-symbol keyspace
    assert all(r[0].startswith("pooled|") for r in store._conn.execute("SELECT candidate_key FROM holdout_reads"))


def test_only_survivors_read_the_holdout(planted):
    out, d = planted
    for p in out["patterns"]:
        if p["pattern"] != "planted":
            assert p["holdout"] is None and p["holdout_state"] == "not_read"
    store = HoldoutStore(str(d / "h.sqlite"))
    assert store._conn.execute("SELECT COUNT(*) FROM holdout_reads").fetchone()[0] == 1


def test_a_flat_pooled_holdout_does_not_pass(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    monkeypatch.setattr(config, "POOLED_HOLDOUT_MIN_TRADES", 10_000)
    out = _run(H.planted_universe(), tmp_path)
    p = _pat(out, "planted")
    assert p["gates"]["holdout"] is False and "holdout_too_few_trades" in p["reasons"]
    assert p["status"] == "unvalidated"


class _BrokenStore:
    def __init__(self, fail: str):
        self.fail = fail
        self._real = HoldoutStore(":memory:")

    def get(self, v, k):
        if self.fail == "get":
            raise OSError("read failed")
        return self._real.get(v, k)

    def put(self, v, k, payload):
        if self.fail == "put":
            raise OSError("write failed")
        return self._real.put(v, k, payload)


@pytest.mark.parametrize("fail", ["get", "put", "missing"])
def test_a_holdout_store_that_cannot_be_used_fails_closed(tmp_path, monkeypatch, fail):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    store = None if fail == "missing" else _BrokenStore(fail)
    out = _run(H.planted_universe(), tmp_path, holdout_store=store)
    p = _pat(out, "planted")
    assert "holdout_store_unavailable" in p["reasons"] and p["status"] == "unvalidated"
    assert out["funnel"]["pooled_validated"] == 0


def test_second_pooled_holdout_read_of_a_pattern_under_one_holdout_start_is_refused(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    frames = H.planted_universe()
    store = HoldoutStore(str(tmp_path / "h.sqlite"))
    ledger = PooledLedger(str(tmp_path / "t.sqlite"))
    first = _run(frames, tmp_path, ledger=ledger, holdout_store=store)
    assert _pat(first, "planted")["status"] == PV.POOLED_DEFLATED_VALIDATED
    monkeypatch.setattr(config, "POOLED_HAC_LAGS", 11)                       # a gate constant: a NEW pooled version
    second = _run(frames, tmp_path, ledger=ledger, holdout_store=store)
    p = _pat(second, "planted")
    assert p["pooled_version"] != _pat(first, "planted")["pooled_version"]
    assert p["holdout_state"] == "read_cap" and "holdout_read_cap" in p["reasons"] and p["status"] == "unvalidated"
    assert len(ledger.holdout_read_versions(first["holdout_start"], "planted")) == 1
    # a disclosed re-read (an owner decision entry) allows exactly one more, counted with a Bonferroni correction
    ledger.record_disclosed_reread(first["holdout_start"], "planted", "D18-reread-1")
    third = _run(frames, tmp_path, ledger=ledger, holdout_store=store)
    p3 = _pat(third, "planted")
    assert p3["holdout_state"] == "read" and p3["holdout_reads_of_pattern"] == 2
    assert p3["holdout"]["bonferroni_k"] == 2
    assert len(ledger.holdout_read_versions(first["holdout_start"], "planted")) == 2
    monkeypatch.setattr(config, "POOLED_HAC_LAGS", 12)
    fourth = _run(frames, tmp_path, ledger=ledger, holdout_store=store)
    assert _pat(fourth, "planted")["holdout_state"] == "read_cap"


def test_a_partial_universe_never_reads_the_holdout_fresh(tmp_path, monkeypatch):
    H.use_universe(monkeypatch, H.SYMBOLS10 + ["IWM", "GS", "BAC", "CVX", "AMD", "TSLA", "META"][:0])
    H.use_small_family(monkeypatch, NAMES)
    monkeypatch.setattr(config, "POOLED_MAX_MISSING", 0.10)
    frames = H.planted_universe()
    del frames["DIA"]                                                         # 1 of 10 = 10%: partial, not incomplete
    out = _run(frames, tmp_path)
    assert out["status"] == "ok" and out["universe"]["partial"] and not out["universe"]["complete"]
    assert out["universe"]["missing"] == ["DIA"] and out["universe"]["flags"] == [PV.PARTIAL_FLAG]
    p = _pat(out, "planted")
    assert p["holdout"] is None and p["status"] == "unvalidated"
    assert "holdout_not_read_partial_universe" in p["reasons"]
    store = HoldoutStore(str(tmp_path / "h.sqlite"))
    assert store._conn.execute("SELECT COUNT(*) FROM holdout_reads").fetchone()[0] == 0


# ------------------------------------------------------------------ 8. ledger, registry namespace, components
def test_ledger_n_accumulates_across_versions_not_across_nights(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["planted"])
    pats = {"planted": H.planted_pattern(1)}
    ledger = PooledLedger(str(tmp_path / "t.sqlite"))
    kw = dict(ledger=ledger, holdout_store=HoldoutStore(":memory:"), registry=False, write_components=False, draws=100)
    a = PV.evaluate_pooled(H.planted_universe(), pats, **kw)
    n1 = ledger.count(a["holdout_start"])
    assert n1 == 2 and a["n_trials"] == 2                                     # planted + the retired macd_hist_reversal
    PV.evaluate_pooled({s: H.planted_frame(100 + i, n=H.N_BARS + 3) for i, s in enumerate(H.SYMBOLS10)}, pats, **kw)
    assert ledger.count(a["holdout_start"]) == n1                             # same version on new data: same trial
    monkeypatch.setattr(PV, "POOLING_METHOD_VERSION", "99")                    # a new version of any part adds one
    b = PV.evaluate_pooled(H.planted_universe(), pats, **kw)
    assert ledger.count(a["holdout_start"]) == n1 + 2 and b["n_trials"] == n1 + 2
    assert ledger.count("2099-01-01") == 0                                    # per HOLDOUT_START


def test_unreadable_ledger_fails_closed(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["planted"])

    class Broken(PooledLedger):
        def count(self, holdout_start):
            from trials.pooled_ledger import PooledLedgerError
            raise PooledLedgerError("disk gone")

    out = PV.evaluate_pooled(H.planted_universe(), {"planted": H.planted_pattern(1)},
                             ledger=Broken(str(tmp_path / "t.sqlite")), holdout_store=HoldoutStore(":memory:"),
                             registry=False, write_components=False, draws=100)
    assert out["status"] == PV.RUN_TRIALS_UNAVAILABLE
    assert all(p["status"] == "unvalidated" for p in out["patterns"]) and out["funnel"] is None


def test_duplicate_series_are_flagged(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["a", "b"])
    pats = {"a": H.planted_pattern(1), "b": H.planted_pattern(1)}             # identical signals
    ledger = PooledLedger(str(tmp_path / "t.sqlite"))
    out = PV.evaluate_pooled(H.planted_universe(), pats, ledger=ledger, holdout_store=HoldoutStore(":memory:"),
                             registry=False, write_components=False, draws=100)
    assert _pat(out, "a")["duplicate_of"] is None and _pat(out, "b")["duplicate_of"] == "a"
    assert {r["pattern"]: r["duplicate_of"] for r in ledger.rows(out["holdout_start"])}["b"] == "a"
    assert out["n_trials"] >= 3                                               # both are counted (conservative)


def test_pooled_trials_live_in_their_own_registry_namespace(tmp_path, monkeypatch):
    import sqlite3
    from services import trial_runs
    from trials import TrialRegistry, TrialRegistryError, load_run
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["planted"])
    out = PV.evaluate_pooled(H.planted_universe(), {"planted": H.planted_pattern(1)},
                             ledger=PooledLedger(trial_runs.trials_db_path()), holdout_store=HoldoutStore(":memory:"),
                             registry=None, draws=100)
    assert out["run_id"].startswith("pooled-")
    run = load_run(out["run_id"], db_path=trial_runs.trials_db_path())
    assert [t.symbol for t in run.trials] == ["POOLED"] and run.trials[0].pattern == "planted"
    assert run.returns_matrix().shape[1] == 1 and len(run.returns_matrix()) == out["oos"]["days"]
    comps = pd.read_parquet(tmp_path / "trials" / f"{out['run_id']}.components.parquet")
    assert set(comps.columns) == {f"planted|{s}" for s in H.SYMBOLS10}
    con = sqlite3.connect(trial_runs.trials_db_path())
    assert con.execute("SELECT COUNT(*) FROM trials WHERE run_id NOT LIKE 'pooled-%'").fetchone()[0] == 0
    with pytest.raises(TrialRegistryError):
        TrialRegistry("tech-1", db_path=trial_runs.trials_db_path(), unit="pooled")      # no mixing the namespaces
    with pytest.raises(TrialRegistryError):
        TrialRegistry("pooled-x", db_path=trial_runs.trials_db_path(), unit="symbol-ish")


# ------------------------------------------------------------------ 9. advisory PBO, V floor, narrowing
def test_pbo_and_pbo_xs_are_advisory_and_not_status_inputs(planted):
    out, _ = planted
    assert out["pbo"] is not None and out["pbo_xs"] is not None
    assert not any(k in p["gates"] for p in out["patterns"] for k in ("pbo", "pbo_xs"))


def test_variance_floor_is_the_larger_of_empirical_nightly_and_one_over_t_eff(planted):
    out, _ = planted
    for p in out["patterns"]:
        if p["n_trades"]:
            assert p["sharpe_var_used"] >= max(out["sharpe_var"] or 0.0, 1.0 / p["t_eff"]) - 1e-12


def test_a_prior_nightly_variance_floors_the_next_run(tmp_path, monkeypatch):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    prior = {"unit": "pooled", "status": "ok", "narrowed": False, "sharpe_var": 0.5, "generated_at": "2026-10-05T22:00:00+00:00"}
    out = _run(H.planted_universe(), tmp_path, prior=prior)
    assert out["sharpe_var"] >= 0.5
    assert _pat(out, "planted")["sharpe_var_used"] >= 0.5
    assert _pat(out, "planted")["gates"]["dsr"] is False                      # a huge V makes the deflation unreachable
