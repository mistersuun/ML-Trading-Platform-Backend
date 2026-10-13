"""Bypass-resistance guards for pooled validation (D18, proposal section 15 tests 12-15).

Each guard is a plain function ``guard(tmp: Path, mp: pytest.MonkeyPatch)`` that raises AssertionError when a way of
LOWERING THE BAR (narrowing the universe or the pattern set, re-registering a universe cheaply, trusting a stale
verdict, skipping the execution universe check, drawing the null independently, ignoring autocorrelation or the
leave-one-symbol-out check) works. ``tests/test_pooled_bypass.py`` runs them as ordinary tests;
``tests/test_pooled_mutants.py`` applies each mutant and asserts that at least one guard FAILS (a mutant that
survives means a test is missing, and the build fails)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
import pooled_validation as PV
from state.holdout import HoldoutStore
from stats import pooled as SP
from stats import selection
from tests import pooled_helpers as H
from trials.pooled_ledger import PooledLedger

# the REAL statistics, captured at import (before any mutant is applied): independent references for the guards
_REAL_MOMENTS, _REAL_PSR, _REAL_EMS = selection._moments, selection.psr, selection.expected_max_sharpe


def run_small(tmp: Path, mp, frames=None, patterns=None, universe_syms=None, family=None, **kw) -> dict:
    """evaluate_pooled on the planted universe with an isolated ledger / hold-out store (no registry, no parquet)."""
    H.use_universe(mp, universe_syms or H.SYMBOLS10)
    if family is not None:
        H.use_small_family(mp, family)
    kw.setdefault("ledger", PooledLedger(str(tmp / "t.sqlite")))
    kw.setdefault("holdout_store", HoldoutStore(str(tmp / "h.sqlite")))
    kw.setdefault("registry", False)
    kw.setdefault("write_components", False)
    kw.setdefault("draws", 100)
    return PV.evaluate_pooled(frames if frames is not None else H.planted_universe(),
                              patterns or {"planted": H.planted_pattern(1)}, **kw)


def _holdout_rows(tmp: Path) -> int:
    return HoldoutStore(str(tmp / "h.sqlite"))._conn.execute("SELECT COUNT(*) FROM holdout_reads").fetchone()[0]


# ------------------------------------------------------------------ 12. narrowing the universe
def g_subset_universe_is_incomplete(tmp, mp):
    frames = H.planted_universe()
    sub = {s: frames[s] for s in H.SYMBOLS10[:6]}                              # 4 of 10 missing
    out = run_small(tmp, mp, frames=sub, family=["planted"])
    assert out["status"] == PV.RUN_INCOMPLETE, out["status"]
    assert not out["universe"]["complete"] and len(out["universe"]["missing"]) == 4
    assert all(p["status"] == "unvalidated" for p in out["patterns"]) and out["funnel"] is None
    assert _holdout_rows(tmp) == 0                                             # an incomplete run reads no hold-out
    stale = dict(frames)
    for s in H.SYMBOLS10[:3]:                                                  # stale frames count as missing
        f = stale[s].copy()
        f.attrs["stale"] = True
        stale[s] = f
    out = run_small(tmp, mp, frames=stale, family=["planted"])
    assert out["status"] == PV.RUN_INCOMPLETE
    assert out["universe"]["missing_reasons"][H.SYMBOLS10[0]] == "stale"


def g_partial_universe_never_validates(tmp, mp):
    frames = H.planted_universe()
    del frames["DIA"]                                                          # 1 of 10: partial, still not eligible
    out = run_small(tmp, mp, frames=frames, family=["planted"])
    assert out["status"] == "ok" and out["universe"]["partial"] and not out["universe"]["complete"]
    assert all(p["status"] == "unvalidated" for p in out["patterns"])
    assert _holdout_rows(tmp) == 0


# ------------------------------------------------------------------ 13. narrowing the pattern set
def g_subset_patterns_use_the_full_n_and_family(tmp, mp):
    out = run_small(tmp, mp)                                                   # ONE pattern, the real 20-pattern floor
    p = out["patterns"][0]
    assert out["narrowed"] and out["n_trials"] >= 20 and p["n_trials"] >= 20, (out["n_trials"], p["n_trials"])
    assert out["no_nightly_variance"] and p["deflation_reason"] == "no_nightly_variance"
    assert p["gates"]["dsr"] is False and p["status"] != PV.POOLED_DEFLATED_VALIDATED
    assert p["bh_adjusted_p"] >= min(1.0, p["null_p"] * 19), (p["bh_adjusted_p"], p["null_p"])   # corrected as the family
    assert p["bh_adjusted_p"] > 10 * p["null_p"]


# ------------------------------------------------------------------ 14. re-registering a universe
def g_universe_change_costs_trials(tmp, mp):
    pats = {f"p{i}": H.planted_pattern(20 + i, p=0.05) for i in range(3)}
    ledger = PooledLedger(str(tmp / "t.sqlite"))
    kw = dict(patterns=pats, ledger=ledger, family=None)
    a = run_small(tmp, mp, **kw)
    n_a = ledger.count(a["holdout_start"])
    assert a["status"] == "ok" and n_a == 3
    swapped = [s for s in H.SYMBOLS10 if s != "DIA"] + ["IWM"]                 # a symbol swapped: a NEW universe
    frames = H.planted_universe(swapped)
    b = run_small(tmp, mp, frames=frames, universe_syms=swapped, **kw)
    assert b["status"] == "ok" and b["universe"]["hash"] != a["universe"]["hash"]
    assert ledger.count(a["holdout_start"]) == n_a + 3                          # N += number of patterns, not +1
    va = {p["pattern"]: p["pooled_version"] for p in a["patterns"]}
    vb = {p["pattern"]: p["pooled_version"] for p in b["patterns"]}
    assert all(va[k] != vb[k] for k in va)                                     # a new version: a new hold-out key
    ta = {p["pattern"]: p["trial_version"] for p in a["patterns"]}
    tb = {p["pattern"]: p["trial_version"] for p in b["patterns"]}
    assert all(ta[k] != tb[k] for k in ta)


def g_derived_universe_is_refused(tmp, mp):
    pats = {"p0": H.planted_pattern(20, p=0.05)}
    ledger = PooledLedger(str(tmp / "t.sqlite"))
    a = run_small(tmp, mp, patterns=pats, ledger=ledger)
    assert a["status"] == "ok"
    kept = [s for s in H.SYMBOLS10 if s not in ("XOM", "DIA")]                  # the previous universe minus its 'losers'
    n = ledger.count(a["holdout_start"])
    b = run_small(tmp, mp, frames=H.planted_universe(kept), patterns=pats, universe_syms=kept, ledger=ledger)
    assert b["status"] == PV.RUN_DERIVED and all(p["status"] == "unvalidated" for p in b["patterns"])
    assert ledger.count(a["holdout_start"]) == n
    other = [s for s in H.SYMBOLS10 if s not in ("XOM",)] + ["IWM"]
    assert PV.universe_hash(other) != PV.universe_hash(H.SYMBOLS10)
    mp.setattr(config, "POOLED_APPROVED_UNIVERSES", {PV.universe_hash(kept): "D18-test-approval"})
    c = run_small(tmp, mp, frames=H.planted_universe(kept), patterns=pats, universe_syms=kept, ledger=ledger)
    assert c["status"] == "ok" and ledger.count(a["holdout_start"]) == n + 1    # approved, and it still costs a trial
    mp.setattr(config, "POOLED_APPROVED_UNIVERSES", {})
    unreg = PV.evaluate_pooled(H.planted_universe(), pats, universe=["AAPL", "MSFT", "SPY", "JPM", "XOM", "TSLA", "GS", "BAC"],
                               ledger=ledger, holdout_store=HoldoutStore(":memory:"), registry=False,
                               write_components=False)
    assert unreg["status"] == PV.RUN_UNREGISTERED


# ------------------------------------------------------------------ 14c. stale / incomplete verdict never confers eligibility
def _verdict(status="pooled_deflated_validated", *, complete=True, partial=False, run_status="ok", age_h=1.0,
             symbols=None, excluded=(), uhash=None):
    syms = list(symbols if symbols is not None else H.SYMBOLS10)
    return {"unit": "pooled", "status": run_status,
            "generated_at": (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat(),
            "universe": {"hash": uhash or PV.universe_hash(syms), "symbols": syms, "complete": complete,
                         "partial": partial},
            "patterns": [{"pattern": "planted", "status": status, "excluded_symbols": list(excluded),
                          "pooled_version": "v1"}]}


def g_incomplete_or_stale_never_eligible(tmp, mp):
    import pytest
    H.use_universe(mp)
    assert PV.order_eligibility(_verdict(), "planted", "AAPL") == (False, "not_validated")     # shadow: never today
    pretend = pytest.MonkeyPatch()               # a later owner decision, simulated only while the checks are probed
    pretend.setattr(config, "ORDER_ELIGIBLE_STATUSES", ("deflated_validated", PV.POOLED_DEFLATED_VALIDATED))
    assert PV.order_eligibility(_verdict(), "planted", "AAPL") == (True, "ok")                  # control: the checks bite
    bad = {
        "incomplete status": _verdict(run_status=PV.RUN_INCOMPLETE, complete=False),
        "partial universe": _verdict(partial=True, complete=False),
        "complete flag off": _verdict(complete=False),
        "stale verdict": _verdict(age_h=config.POOLED_FALLBACK_MAX_AGE_HOURS + 1),
        "other universe hash": _verdict(uhash="deadbeefdeadbeef"),
    }
    for name, payload in bad.items():
        ok, why = PV.order_eligibility(payload, "planted", "AAPL")
        assert not ok, name
    assert PV.order_eligibility(_verdict(), "planted", "META") == (False, "pooled_symbol_not_in_universe")
    assert PV.order_eligibility(_verdict(excluded=["AAPL"]), "planted", "AAPL") == (False, "pooled_symbol_excluded")
    assert PV.order_eligibility(None, "planted", "AAPL")[0] is False
    inc_check = _verdict(run_status=PV.RUN_INCOMPLETE, complete=False)
    inc_check["last_verdict"] = {"patterns": [{"pattern": "planted", "status": "pooled_deflated_validated"}]}
    assert PV.order_eligibility(inc_check, "planted", "AAPL")[0] is False
    pretend.undo()
    # a REAL incomplete run carrying the last stored verdict for display: the fallback never confers eligibility
    prior = _verdict()
    frames = H.planted_universe()
    sub = {s: frames[s] for s in H.SYMBOLS10[:5]}
    out = run_small(tmp, mp, frames=sub, family=["planted"], prior=prior)
    assert out["status"] == PV.RUN_INCOMPLETE
    assert out["last_verdict"] and out["last_verdict"]["display_only"] and out["last_verdict"]["order_eligible"] is False
    assert out["last_verdict"]["patterns"][0]["status"] == "pooled_deflated_validated"        # shown ...
    out["generated_at"] = datetime.now(timezone.utc).isoformat()
    pretend = pytest.MonkeyPatch()
    pretend.setattr(config, "ORDER_ELIGIBLE_STATUSES", ("deflated_validated", PV.POOLED_DEFLATED_VALIDATED))
    try:
        assert PV.order_eligibility(out, "planted", "AAPL")[0] is False                       # ... never used
    finally:
        pretend.undo()
    old = _verdict(age_h=config.POOLED_FALLBACK_MAX_AGE_HOURS + 5)
    assert run_small(tmp, mp, frames=sub, family=["planted"], prior=old)["last_verdict"] is None   # too old: not shown


# ------------------------------------------------------------------ 8 / 15g. the execution chokepoint
def g_execution_checks_the_pooled_universe_and_version(tmp, mp):
    from execution import OrderIntent
    from results import store
    from tests.test_interlocks import fresh_env, go
    mp.setattr(store, "RESULTS_DIR", tmp / "results")
    H.use_universe(mp)
    uh = PV.universe_hash(H.SYMBOLS10)
    payload = _verdict()
    payload["patterns"][0]["pooled_version"] = "ver1"
    store.write_result("pooled", payload)

    def pi(symbol="AAPL", status=PV.POOLED_DEFLATED_VALIDATED, version="ver1", unit="pooled", day=0, uhash=uh):
        bar = (datetime(2024, 1, 2) + timedelta(days=day)).date().isoformat()
        return OrderIntent(symbol, 1, bar, "pooled:planted", 1.0, status, 1.0, 100.0, validation_unit=unit,
                           pooled_version=version, universe_hash=uhash)

    with fresh_env() as env:
        assert go(env, pi(day=0)).reasons == ["not_validated"]                          # shadow: the status is not eligible
        env.mp.setattr(config, "ORDER_ELIGIBLE_STATUSES", ("deflated_validated", PV.POOLED_DEFLATED_VALIDATED))
        assert go(env, pi(day=1)).status == "submitted"                                 # control: every check passes
        d = go(env, pi("META", day=2))
        assert d.status == "rejected" and d.reasons == ["pooled_symbol_not_in_universe"], d
        d = go(env, pi(version="old", day=3))
        assert d.status == "rejected" and d.reasons == ["pooled_version_mismatch"], d
        d = go(env, pi(uhash="deadbeefdeadbeef", day=4))
        assert d.status == "rejected" and d.reasons == ["pooled_version_mismatch"], d
        d = go(env, pi(unit="pooled", status="deflated_validated", symbol="MSFT", version=None, day=5))
        assert d.status == "rejected", d                                                # per-symbol status cannot ride on it
        d = go(env, pi(unit="weird", day=6))
        assert d.status == "rejected" and d.reasons == ["invalid_validation_unit"]


# ------------------------------------------------------------------ 15d / 15e / 15f. null, T_eff, LOSO
def g_null_draws_by_cluster(tmp, mp):
    sds, recs = H.null_setup()
    tb = PV.null_tables(recs, sds)
    NE, VL, nval, cl = tb["NE"], tb["VL"], tb["nval"], tb["cl"]
    assert len(set(cl.tolist())) < len(recs) // 2, "trades must share clusters (entry weeks)"
    e = PV.draw_entries(NE, VL, nval, cl, np.random.default_rng(1), 100)
    checked = 0
    for c in set(cl.tolist()):
        mem = [i for i in np.flatnonzero(cl == c) if not tb["fixed"][i]]
        if len(mem) < 2:
            continue
        offs = []
        for i in mem:
            days = sds[recs[i].symbol].dates.values.astype("datetime64[D]").astype(np.int64)
            offs.append(days[e[i]] - days[recs[i].entry])
        offs = np.array(offs)
        assert (offs.max(axis=0) - offs.min(axis=0)).max() <= 3, "members of a cluster must share one offset"
        checked += 1
    assert checked >= 5


def g_t_eff_prices_in_autocorrelation(tmp, mp):
    from stats import pooled as P
    e = np.random.default_rng(0).normal(0, 1, 3005)
    ma5 = np.convolve(e, np.ones(6) / np.sqrt(6), mode="valid")[:3000]
    assert P.bartlett_t_eff(ma5, 10) < 0.6 * len(ma5)
    out = run_small(tmp, mp, family=["planted"])                                # overlapping 3-bar holds in a real run
    p = out["patterns"][0]
    assert p["t_eff"] < 0.9 * out["oos"]["days"], (p["t_eff"], out["oos"]["days"])


def g_loso_is_applied(tmp, mp):
    pnl = [1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0, -1.0, 0.3]                     # net +0.3, carried by any positive symbol
    recs, k = [], 0
    for j, v in enumerate(pnl):
        for i in range(10):
            recs.append(PV.TradeRec(f"Z{j}", 0, 1, pd.Timestamp("2023-01-02") + pd.Timedelta(days=k % 400),
                                    pd.Timestamp("2023-01-03") + pd.Timedelta(days=k % 400), v / 10 * 0.04, 1,
                                    "signal", 0, 100, 0.04, v / 10, 0.125, 1))
            k += 1
    c = PV.concentration(recs, pd.DatetimeIndex(pd.Timestamp("2023-01-02") + pd.to_timedelta(np.arange(420), unit="D")), 126)
    assert c["share_ok"], c
    assert c["loso_min_mean_r"] < 0 < sum(r.R for r in recs), c
    assert c["loso_ok"] is False and c["ok"] is False, c


# ------------------------------------------------------------------ 16. gates bite (review round 1, finding 2)
def _ref_psr(series, t_eff, bench=0.0):
    sr, sk, ku = _REAL_MOMENTS(np.asarray(series, dtype=float))
    return _REAL_PSR(sr, t_eff, sk, ku, bench)


def _forced(tmp, mp, dsr_p, null_p, **kw):
    """A planted run whose DSR p-value and null p-value are FORCED (the gates' thresholds, not the statistics, are tested)."""
    mp.setattr(SP, "dsr_teff", lambda r, n, v, t=None: 1.0 - dsr_p)
    mp.setattr(PV, "pooled_null", lambda recs, *a, **k: PV.NullOut(null_p, 0.1, 0.0, 0.05, 100, len(recs), 10))
    return run_small(tmp, mp, family=["planted"], **kw)["patterns"][0]


def g_alpha_is_split_at_the_call_sites(tmp, mp):
    """A DSR p-value or BH-adjusted null p-value in (alpha/2, alpha) must FAIL: the per-symbol family keeps the other half."""
    lo = _forced(tmp, mp, dsr_p=0.04, null_p=0.04)
    assert abs(lo["dsr_p"] - 0.04) < 1e-9 and lo["gates"]["dsr"] is False, lo["gates"]
    assert lo["gates"]["null_bh"] is False and lo["bh_adjusted_p"] > config.FDR_ALPHA / 2.0, lo["bh_adjusted_p"]
    assert lo["status"] == PV.UNVALIDATED
    hi = _forced(tmp / "c", mp, dsr_p=0.01, null_p=0.0001)                      # control: clearly significant passes
    assert hi["gates"]["dsr"] is True and hi["gates"]["null_bh"] is True


def g_capped_replay_must_keep_half_the_sharpe(tmp, mp):
    orig = PV.capped_replay

    def thin(recs, excluded=(), **kw):               # admit one symbol's trades only: profitable but far thinner
        adm, info = orig(recs, excluded, **kw)
        keep = [i for i in adm if recs[i].symbol == "AAPL"][::10]
        return keep, {**info, "admitted": len(keep)}

    mp.setattr(PV, "capped_replay", thin)
    p = run_small(tmp, mp, family=["planted"])["patterns"][0]
    c = p["capped"]
    assert c["pooled_return"] > 0 and c["sharpe_ratio_to_uncapped"] < 0.5, c      # the 50% rule is what binds
    assert c["ok"] is False and p["gates"]["capped_replay"] is False
    assert "capped_replay_fails" in p["reasons"]


def g_one_bar_delay_is_a_gate(tmp, mp):
    import dataclasses
    from types import SimpleNamespace
    import validation as V
    orig = V._replay

    def losing_delay(df, wf, signals, cost_mult, delay):
        res = orig(df, wf, signals, cost_mult, delay)
        if delay == 1:                                # the delayed signal loses money on every trade
            return SimpleNamespace(oos_trades=[dataclasses.replace(t, pnl_pct=-abs(t.pnl_pct) - 0.01)
                                               for t in res.oos_trades])
        return res

    mp.setattr(V, "_replay", losing_delay)
    p = run_small(tmp, mp, family=["planted"])["patterns"][0]
    assert p["cost_return"] > 0 > p["delay_return"], (p["cost_return"], p["delay_return"])
    assert p["gates"]["cost_delay"] is False and "cost_or_delay_stress_fails" in p["reasons"]
    assert p["status"] == PV.UNVALIDATED


def _pattern_on(frames, symbols, salt=1):
    """The planted pattern, firing only on ``symbols`` (identified by the first close of their frame)."""
    firsts = {float(frames[s]["Close"].iloc[0]) for s in symbols}
    base = H.planted_pattern(salt)

    def fn(df):
        out = base(df)
        if float(df["Close"].iloc[0]) not in firsts:
            out["signal"] = 0
        return out
    return fn


def g_breadth_binds(tmp, mp):
    frames = H.planted_universe()
    p7 = run_small(tmp, mp, frames=frames, patterns={"planted": _pattern_on(frames, H.SYMBOLS10[:7])},
                   family=["planted"])["patterns"][0]
    assert p7["breadth"] == 7 and p7["breadth_needed"] == max(config.POOLED_BREADTH_MIN, 5) == 8, p7["breadth_needed"]
    assert p7["gates"]["breadth"] is False and "insufficient_breadth" in p7["reasons"]
    p8 = run_small(tmp / "c", mp, frames=frames, patterns={"planted": _pattern_on(frames, H.SYMBOLS10[:8])},
                   family=["planted"])["patterns"][0]
    assert p8["breadth"] == 8 and p8["gates"]["breadth"] is True                # control: exactly at the bar passes


def g_psr_dsr_and_variance_floor_use_t_eff(tmp, mp):
    """PSR / DSR are computed with T_eff at the call sites, and Var[SR] is floored at 1/T_eff (single pattern: no
    cross-sectional variance and no prior, so the floor IS the variance used)."""
    seen = {"psr": [], "dsr": [], "cap": []}
    cur_psr, cur_dsr, cur_cap = SP.psr_teff, SP.dsr_teff, PV.capped_replay

    def spy_psr(r, t=None, b=0.0):
        seen["psr"].append((np.asarray(r, dtype=float).copy(), t))
        return cur_psr(r, t, b)

    def spy_dsr(r, n, v, t=None):
        seen["dsr"].append((np.asarray(r, dtype=float).copy(), n, v, t))
        return cur_dsr(r, n, v, t)

    def spy_cap(recs, excluded=(), **kw):
        seen["cap"].append((tuple(excluded), kw))
        return cur_cap(recs, excluded, **kw)

    mp.setattr(SP, "psr_teff", spy_psr)
    mp.setattr(SP, "dsr_teff", spy_dsr)
    mp.setattr(PV, "capped_replay", spy_cap)
    # (a) the functions themselves, on a series that is NOT saturated (PSR of the planted run is ~1 either way)
    r = np.random.default_rng(3).normal(0.0006, 0.01, 500)
    assert abs(cur_psr(r, 150) - _ref_psr(r, 150)) < 1e-12 and abs(_ref_psr(r, 150) - _ref_psr(r, 500)) > 0.01
    ems = _REAL_EMS(22, 0.004)
    assert abs(cur_dsr(r, 22, 0.004, 150) - _ref_psr(r, 150, ems)) < 1e-12
    assert abs(_ref_psr(r, 150, ems) - _ref_psr(r, 500, ems)) > 0.01
    seen["psr"].clear(), seen["dsr"].clear()                                    # (a) went through the spies too
    # (b) the call sites: the planted run is evaluated with ITS T_eff (< T), and its Var[SR] floor binds
    out = run_small(tmp, mp, frames=H.planted_universe(jump=0.012), family=["planted"])
    p = out["patterns"][0]
    ser, t_psr = seen["psr"][0]
    assert abs(t_psr - p["t_eff"]) < 1e-6 and p["t_eff"] < 0.99 * len(ser), (t_psr, p["t_eff"], len(ser))
    assert abs(p["psr"] - _ref_psr(ser, p["t_eff"])) < 1e-6, (p["psr"], _ref_psr(ser, p["t_eff"]))
    _, n, v, t = seen["dsr"][0]
    assert n == p["n_trials"] and abs(t - p["t_eff"]) < 1e-6 and abs(v - p["sharpe_var_used"]) < 1e-9
    assert abs(p["dsr"] - _ref_psr(ser, p["t_eff"], _REAL_EMS(p["n_trials"], p["sharpe_var_used"]))) < 1e-6
    assert out["sharpe_var_empirical"] == 0.0                                      # var_emp < 1/T_eff ...
    assert abs(p["sharpe_var_used"] - 1.0 / max(p["t_eff"], 2.0)) < 1e-9, p["sharpe_var_used"]   # ... the floor binds
    assert seen["cap"] and all(ex == () and kw.get("causal_exclusion") is True for ex, kw in seen["cap"])


def _tr(sym, i, R, start="2023-01-02"):
    d = pd.Timestamp(start) + pd.Timedelta(days=3 * i)
    return PV.TradeRec(sym, i, i + 1, d, d + pd.Timedelta(days=1), R * 0.04, 1, "signal", 0, 10 ** 6, 0.04, R, 0.125, i)


def g_capped_replay_exclusion_is_causal(tmp, mp):
    rng = np.random.default_rng(5)
    early = [0.5, 0.7, 0.6, 0.4, 0.55, 0.65]
    late = list(-1.5 + 0.4 * rng.standard_normal(14))
    recs = [_tr("BAD", i, r) for i, r in enumerate(early + late)]
    recs += [_tr("GOOD", i, 0.4 + 0.1 * (i % 3)) for i in range(20)]
    assert "BAD" in PV.excluded_symbols(recs)                                      # full-sample t < -2: out for forward
    adm, info = PV.capped_replay(recs, (), causal_exclusion=True)
    bad = [i for i, t in enumerate(recs) if t.symbol == "BAD"]
    assert all(i in adm for i in bad[:6]), "BAD's early trades happened before anything was known against it"
    assert not all(i in adm for i in bad), "later trades are excluded once the exited record is bad enough"
    assert bad[-1] not in adm and info["excluded_trades"] >= 1 and info["exclusion"] == "causal"
    # the static (look-ahead) list would have dropped ALL of BAD, including the early winners
    static, _ = PV.capped_replay(recs, PV.excluded_symbols(recs))
    assert not any(i in static for i in bad)
    # causality property: dropping the LAST trades never changes an earlier decision
    cut = recs[:-9]
    keep_cut, _ = PV.capped_replay(cut, (), causal_exclusion=True)
    assert keep_cut == [i for i in adm if i < len(cut)] or set(keep_cut) == {i for i in adm if i < len(cut)}


def g_trade_past_oos_end_is_clipped(tmp, mp):
    dates = pd.bdate_range("2023-01-02", periods=30)
    o = np.linspace(100.0, 112.0, 30)
    c = o * 1.002
    sd = PV.SymbolData("X", pd.DataFrame(), n_pre=30, dates=dates, o=o, c=c)
    sd.oos_end = 20
    axis = dates[10:20]
    sd.axis_pos = axis.get_indexer(dates)                                          # -1 outside the OOS axis
    straddle = PV.TradeRec("X", 15, 25, dates[15], dates[25], 0.02, 10, "signal", 10, 20, 0.04, 0.5, 0.125, 1)
    after = PV.TradeRec("X", 21, 24, dates[21], dates[24], 0.02, 3, "signal", 10, 20, 0.04, 0.5, 0.125, 2)
    comm = slip = 0.0005
    comp = PV._build_components([straddle, after], {"X": sd}, axis, ["X"], comm, slip)
    rho = PV._trade_rho(o, c, 15, 25, 0.02, comm, slip)
    want = np.zeros(10)
    want[5:10] = 0.125 * rho[:5]                                                   # bars 15..19 only
    assert np.allclose(comp[:, 0], want, atol=1e-12), (comp[:, 0], want)


def g_null_is_calibrated_on_noise(tmp, mp):
    """Fast smoke version of the slow calibration test: on pure noise the null p-values are not near 0; on a planted
    edge they are."""
    syms = ("AAPL", "MSFT", "SPY", "JPM")
    ps = []
    for seed in range(6):
        sds, recs = H.null_setup(frames=H.noise_frames(syms, seed=seed), salt=seed + 1)
        out = PV.pooled_null(recs, sds, PV._Concat(sds, sorted(sds)), draws=200, seed=seed, key=f"cal{seed}",
                             comm=config.COMMISSION_PCT, slip=config.SLIPPAGE_PCT, family_size=1, alpha=0.025,
                             max_draws=200)
        ps.append(out.p_value)
    ps = np.asarray(ps)
    assert 0.2 < ps.mean() < 0.8 and (ps < 0.05).sum() <= 1, ps
    sds, recs = H.null_setup()
    edge = PV.pooled_null(recs, sds, PV._Concat(sds, sorted(sds)), draws=200, seed=1, key="edge",
                          comm=config.COMMISSION_PCT, slip=config.SLIPPAGE_PCT, family_size=1, alpha=0.025,
                          max_draws=200)
    assert edge.p_value <= 0.05


# cheapest first: the mutation meta-test stops at the first guard that fails
def _fake_holdout(total_return, returns=None, n_trades=None):
    n = n_trades if n_trades is not None else config.POOLED_HOLDOUT_MIN_TRADES + 5
    ret = returns if returns is not None else [total_return / 10.0] * 10
    return lambda sds, order, cand, end, comm, slip: {
        "start": "2026-01-01", "n_bars": len(ret), "n_trades": n, "n_symbols": 5, "total_return": float(total_return),
        "sharpe": 0.0, "returns": list(ret)}


def g_holdout_sign_is_a_gate(tmp, mp):
    """A survivor whose pooled hold-out lost money (-0.1%) with enough trades must fail the hold-out gate."""
    mp.setattr(PV, "_holdout_pooled", _fake_holdout(0.0))                     # control: flat but non-negative reaches it
    ctl = run_small(tmp / "c", mp, family=["planted"])["patterns"][0]
    assert ctl["holdout_state"] == "read" and ctl["gates"]["holdout"] is True, (ctl["gates"], ctl["reasons"])
    mp.setattr(PV, "_holdout_pooled", _fake_holdout(-0.001))
    p = run_small(tmp, mp, family=["planted"])["patterns"][0]
    assert p["holdout_state"] == "read" and p["gates"]["holdout"] is False, (p["gates"], p["reasons"])
    assert "holdout_negative" in p["reasons"] and p["status"] == PV.UNVALIDATED


def g_holdout_reread_needs_bonferroni_significance(tmp, mp):
    """A second hold-out read of a pattern (k=2) with a barely positive, noisy hold-out must fail the re-read test."""
    ledger = PooledLedger(str(tmp / "t.sqlite"))
    store = HoldoutStore(str(tmp / "h.sqlite"))
    mp.setattr(PV, "_holdout_pooled", _fake_holdout(0.001, [0.01, -0.0099] * 5))
    out1 = run_small(tmp, mp, family=["planted"], ledger=ledger, holdout_store=store)
    first, hs = out1["patterns"][0], out1["holdout_start"]
    assert first["holdout_reads_of_pattern"] == 1 and first["gates"]["holdout"] is True, first["reasons"]
    ledger.record_disclosed_reread(hs, "planted", "D-test")                        # an owner-disclosed re-read
    mp.setattr(config, "POOLED_HOLDOUT_MIN_TRADES", config.POOLED_HOLDOUT_MIN_TRADES + 1)    # a new pooled version
    mp.setattr(PV, "_holdout_pooled", _fake_holdout(0.001, [0.01, -0.0099] * 5, n_trades=config.POOLED_HOLDOUT_MIN_TRADES + 5))
    p = run_small(tmp, mp, family=["planted"], ledger=ledger, holdout_store=store)["patterns"][0]
    assert p["holdout_reads_of_pattern"] == 2 and p["holdout"]["bonferroni_k"] == 2, (p["holdout_state"], p["reasons"])
    assert p["gates"]["holdout"] is False and "holdout_reread_not_significant" in p["reasons"]
    assert p["status"] == PV.UNVALIDATED


GUARDS = {
    "holdout_sign": g_holdout_sign_is_a_gate,
    "holdout_reread": g_holdout_reread_needs_bonferroni_significance,
    "loso": g_loso_is_applied,
    "trade_past_oos_end": g_trade_past_oos_end_is_clipped,
    "causal_exclusion": g_capped_replay_exclusion_is_causal,
    "null_calibration": g_null_is_calibrated_on_noise,
    "cluster_null": g_null_draws_by_cluster,
    "execution_universe": g_execution_checks_the_pooled_universe_and_version,
    "stale_or_incomplete_verdict": g_incomplete_or_stale_never_eligible,
    "subset_universe": g_subset_universe_is_incomplete,
    "subset_patterns": g_subset_patterns_use_the_full_n_and_family,
    "t_eff": g_t_eff_prices_in_autocorrelation,
    "alpha_split": g_alpha_is_split_at_the_call_sites,
    "capped_sharpe_ratio": g_capped_replay_must_keep_half_the_sharpe,
    "delay_gate": g_one_bar_delay_is_a_gate,
    "breadth": g_breadth_binds,
    "psr_dsr_variance_floor": g_psr_dsr_and_variance_floor_use_t_eff,
    "universe_costs_trials": g_universe_change_costs_trials,
    "partial_universe": g_partial_universe_never_validates,
    "derived_universe": g_derived_universe_is_refused,
}
