"""WS4.2: Deflated-Sharpe order gate (deflated_validated) + advisory PBO."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
import validation as V
from stats import selection as sel
from tests.test_execution import env, go, mk, submits  # noqa: F401  (fixture + helpers)
from tests.test_validation import PATTERNS, _ok_result, _planted_pattern, make_frame
from trials import TrialRegistry


# ------------------------------------------------------------------ config
def test_only_deflated_validated_is_order_eligible():
    assert config.ORDER_ELIGIBLE_STATUSES == ("deflated_validated",)
    assert "oos_validated" not in config.ORDER_ELIGIBLE_STATUSES
    assert config.DSR_P_MAX == 0.05 and config.PBO_S == 16


# ------------------------------------------------------------------ status decision
def _decide(**kw):
    r = _ok_result(**kw)
    V._decide(r, config.MIN_TRADES_OOS, config.OOS_PSR_MIN)
    return r


def test_status_is_deflated_only_when_oos_validated_and_dsr_p_below_threshold():
    assert _decide(dsr=0.99, dsr_p=0.01).validation_status == "deflated_validated"
    assert _decide(dsr=0.96, dsr_p=0.04999).validation_status == "deflated_validated"
    for p in (0.05, 0.2, None):                                   # boundary is strict; undefined DSR never passes
        r = _decide(dsr=None if p is None else 1 - p, dsr_p=p)
        assert r.validation_status == "oos_validated" and r.rejected_reasons == []
    bad = _decide(dsr=0.99, dsr_p=0.01, cost_ok=False)          # DSR can never rescue a failed OOS gate
    assert bad.validation_status == "unvalidated" and "cost_stress_fails" in bad.rejected_reasons


def test_candidate_result_records_dsr_fields_in_to_dict():
    d = _ok_result(dsr=0.97, dsr_p=0.03, pbo=0.4, n_trials=12).to_dict()
    assert (d["dsr"], d["dsr_p"], d["pbo"], d["n_trials"]) == (0.97, 0.03, 0.4, 12)


# ------------------------------------------------------------------ end to end with the registry
def test_planted_edge_is_deflated_validated_with_registry_n_and_reports_pbo(tmp_path):
    df = make_frame(5)
    pats = {"planted": _planted_pattern(df), "ema_5_20": PATTERNS["ema_5_20"]}
    reg = TrialRegistry("dsr_run", trials_dir=tmp_path / "t")
    # 40 extra recorded trials (e.g. other symbols of the same run): N must come from the registry, not len(results)
    rng = np.random.default_rng(config.SEED)
    idx = pd.bdate_range("2019-01-01", periods=400)
    for i in range(40):
        reg.record(symbol=f"X{i}", pattern="noise", params={"i": i}, returns=pd.Series(rng.normal(0, 0.01, 400), idx),
                   strategy_version="v", data_hash="h")
    res = V.evaluate_candidates({"SYN": df}, pats, ((0.02, 0.04),), draws=300, trials=reg)
    by = {r.pattern: r for r in res}
    pl = by["planted"]
    assert reg.n_trials == 42 and all(r.n_trials == 42 for r in res)
    assert pl.dsr is not None and pl.dsr_p == pytest.approx(1 - pl.dsr, abs=1e-9)
    assert pl.dsr_p < config.DSR_P_MAX and pl.validation_status == "deflated_validated", pl.rejected_reasons
    assert by["ema_5_20"].validation_status == "unvalidated"
    assert all(r.pbo is None or 0.0 <= r.pbo <= 1.0 for r in res)    # advisory only: never gates (planted still passes)
    plain = V.evaluate_candidates({"SYN": df}, pats, ((0.02, 0.04),), draws=300)     # no registry: N = len(results)
    assert all(r.n_trials == 2 for r in plain)


def test_oos_validated_but_not_deflated_when_dsr_p_fails(monkeypatch):
    df = make_frame(5)
    pats = {"planted": _planted_pattern(df)}
    monkeypatch.setattr(config, "DSR_P_MAX", 0.0)                 # unreachable (strict <): OOS gates pass, deflation does not
    pl = V.evaluate_candidates({"SYN": df}, pats, ((0.02, 0.04),), draws=300)[0]
    assert pl.rejected_reasons == [] and pl.validation_status == "oos_validated"
    assert pl.validation_status not in config.ORDER_ELIGIBLE_STATUSES


# ------------------------------------------------------------------ execution (FakeBroker)
def test_oos_validated_intent_is_rejected_not_deflated(env):  # noqa: F811
    d = go(env, mk(validation_status="oos_validated"))
    assert (d.status, d.reasons) == ("rejected", ["not_deflated"])
    assert env.fake.calls == []                                   # no broker contact


def test_deflated_validated_intent_is_submitted_and_unvalidated_keeps_old_reason(env):  # noqa: F811
    assert go(env, mk(validation_status="deflated_validated")).status == "submitted"
    assert len(submits(env)) == 1
    assert go(env, mk(validation_status="unvalidated", symbol="MSFT")).reasons == ["not_validated"]


# ------------------------------------------------------------------ slow: pure-noise selection bias
T_SIM, N_SIM = 750, 780


def _noise(seed):
    return np.random.default_rng(config.SEED + seed).normal(0.0, 0.01, (T_SIM, N_SIM))


def _best_dsr_p(m):
    sr = m.mean(0) / m.std(0, ddof=1)
    j = int(sr.argmax())
    _, sk, ku = sel._moments(m[:, j])
    return sel.dsr_p_value(float(sr[j]), m.shape[1], sel.sharpe_variance(m), m.shape[0], sk, ku)


@pytest.mark.slow
def test_noise_best_sharpe_looks_real_but_is_not_deflated_significant():
    runs, raw_hit, signif = 30, 0, 0
    for k in range(runs):
        m = _noise(k)
        sr_ann = (m.mean(0) / m.std(0, ddof=1)).max() * np.sqrt(252)
        raw_hit += sr_ann > 0.3
        signif += _best_dsr_p(m) < config.DSR_P_MAX
    assert raw_hit / runs > 0.9                    # selection bias: the best of 780 zero-mean series always looks good
    assert signif / runs <= 0.10                   # ...and the Deflated Sharpe sees through it (>= 90% not significant)


@pytest.mark.slow
def test_pbo_of_pure_noise_is_about_one_half():
    pbos = [sel.pbo_cscv(_noise(100 + k), S=config.PBO_S) for k in range(10)]
    assert abs(float(np.mean(pbos)) - 0.5) <= 0.1, pbos


@pytest.mark.slow
def test_planted_strong_series_stays_significant_among_780_noise_trials():
    """The planted series' annual Sharpe is 4.0.  A 1.5 annual Sharpe over 750 days (t ~ 2.6) CANNOT survive the
    deflation for N = 780 (SR0 ~ 0.117/period > 1.5/sqrt(252) = 0.094); that case is asserted separately as the
    statistically correct outcome."""
    runs = 20
    hits = {1.5: 0, 4.0: 0}
    for k in range(runs):
        rng = np.random.default_rng(config.SEED + 500 + k)
        for annual in hits:
            m = _noise(200 + k)
            m[:, 0] = rng.normal(annual / np.sqrt(252) * 0.01, 0.01, T_SIM)
            sr = m[:, 0].mean() / m[:, 0].std(ddof=1)
            _, sk, ku = sel._moments(m[:, 0])
            p = sel.dsr_p_value(float(sr), N_SIM, sel.sharpe_variance(m), T_SIM, sk, ku)
            hits[annual] += p < config.DSR_P_MAX
    assert hits[4.0] / runs >= 0.8
    assert hits[1.5] / runs <= 0.2


# ------------------------------------------------------------------ narrowed scans cannot bypass the deflation
def test_one_trial_run_cannot_be_deflated_validated():
    """Var[SR] = 0 with a single trial makes DSR == PSR; that must never reach deflated_validated."""
    df = make_frame(5)
    pl = V.evaluate_candidates({"SYN": df}, {"planted": _planted_pattern(df)}, ((0.02, 0.04),), draws=300,
                               min_trials=V.universe_trials())[0]
    assert pl.rejected_reasons == [] and pl.validation_status == "oos_validated"
    assert pl.deflation_reason == "dsr_variance_unavailable" and pl.n_trials == V.universe_trials()
    assert pl.validation_status not in config.ORDER_ELIGIBLE_STATUSES


def test_variance_floor_with_universe_n_deflates_a_narrowed_run():
    df = make_frame(5)
    kw = dict(draws=300, min_trials=V.universe_trials())
    pl = V.evaluate_candidates({"SYN": df}, {"planted": _planted_pattern(df)}, ((0.02, 0.04),),
                               sharpe_var_floor=1.0, **kw)[0]
    assert pl.n_trials == V.universe_trials() and pl.dsr_p is not None
    assert pl.oos_psr is not None and pl.dsr_p > (1.0 - pl.oos_psr) + 1e-9       # the deflation actually bit


def _two_near_identical(df):
    base = _planted_pattern(df)

    def twin(d):
        out = base(d)
        sig = out["signal"].copy()
        sig.iloc[::40] = 0                             # near-identical trial: slightly lower Sharpe, tiny variance
        out["signal"] = sig
        return out
    return {"planted": base, "planted_twin": twin}


def _scan_validate(monkeypatch, tmp_path, pats, floor):
    from services import scan
    from state.holdout import HoldoutStore
    monkeypatch.setattr(scan, "_holdout_store", lambda: HoldoutStore(tmp_path / "ho.sqlite"))
    monkeypatch.setattr(scan, "_nightly_sharpe_var", lambda: floor)
    info: dict = {}
    return scan._validate({"SYN": make_frame(5)}, pats, info), info


def test_narrowed_two_trial_run_without_nightly_variance_is_never_deflated_validated(monkeypatch, tmp_path):
    """scan --symbol X --pattern P1 P2 on a fresh install: Var[SR] from 2 similar trials is ~0, so SR0 ~ 0."""
    df = make_frame(5)
    res, _ = _scan_validate(monkeypatch, tmp_path, _two_near_identical(df), None)
    assert len(res) == 2
    assert all(r.validation_status != "deflated_validated" for r in res)
    top = [r for r in res if r.validation_status == "oos_validated"]
    assert top and all(r.deflation_reason == "no_nightly_variance" for r in top)


def test_nightly_variance_floor_reaches_the_order_path(monkeypatch, tmp_path):
    df = make_frame(5)
    pats = _two_near_identical(df)
    res_none, _ = _scan_validate(monkeypatch, tmp_path, pats, None)
    (tmp_path / "b").mkdir()
    res_big, info = _scan_validate(monkeypatch, tmp_path / "b", pats, 1.0)
    p_none = {r.pattern: r for r in res_none}["planted"]
    p_big = {r.pattern: r for r in res_big}["planted"]
    assert p_none.deflation_reason == "no_nightly_variance"
    assert p_big.dsr_p is not None and p_big.dsr_p > 1.0 - p_big.oos_psr + 1e-9
    assert p_big.validation_status == "oos_validated" and p_big.deflation_reason == "dsr_not_significant"
    assert info["sharpe_var"] >= 1.0                  # the funnel stores the effective (floored) variance


def test_nightly_sharpe_var_reads_the_latest_funnel(monkeypatch):
    from results import store
    from services import scan
    monkeypatch.setattr(store, "read_latest", lambda name: [{"sharpe_var": 0.003}])
    assert scan._nightly_sharpe_var() == 0.003
    monkeypatch.setattr(store, "read_latest", lambda name: [{"sharpe_var": None}])
    assert scan._nightly_sharpe_var() is None


def test_scan_validate_one_symbol_one_pattern_never_deflated_validated(monkeypatch, tmp_path):
    """scan --symbol X --pattern P builds a 1-trial registry: the order path must not see deflated_validated."""
    df = make_frame(5)
    res, _ = _scan_validate(monkeypatch, tmp_path, {"planted": _planted_pattern(df)}, None)
    assert len(res) == 1
    assert res[0].validation_status == "oos_validated"
    assert res[0].deflation_reason == "dsr_variance_unavailable"
    assert res[0].n_trials == V.universe_trials()


def test_oos_validated_failing_only_the_deflation_records_why(monkeypatch):
    df = make_frame(5)
    monkeypatch.setattr(config, "DSR_P_MAX", 0.0)
    pl = V.evaluate_candidates({"SYN": df}, {"planted": _planted_pattern(df)}, ((0.02, 0.04),), draws=300,
                               sharpe_var_floor=1e-4)[0]
    assert pl.validation_status == "oos_validated" and pl.deflation_reason == "dsr_not_significant"
    assert pl.rejected_reasons == []


# ------------------------------------------------------------------ PBO: calendar handling
def test_run_pbo_ignores_weekend_and_prehistory_gaps():
    rng = np.random.default_rng(config.SEED)
    idx = pd.date_range("2020-01-01", periods=300, freq="D")
    eq = pd.DataFrame(rng.normal(0, 0.01, (300, 4)), index=idx)
    eq = eq[idx.dayofweek < 5].reindex(idx)          # weekday-only columns: weekends are NaN, not flat days
    crypto = pd.DataFrame(rng.normal(0, 0.01, (300, 1)), index=idx, columns=[9])
    m = pd.concat([eq, crypto], axis=1)
    got = V._run_pbo(m)
    full = eq.dropna()
    assert got == V._opt(sel.pbo_cscv(full.to_numpy(), S=config.PBO_S))
