"""D18 mutation meta-test (proposal test 15): each mutant that LOWERS THE BAR is applied with monkeypatch and the
guards of tests/pooled_guards.py must catch it (at least one fails). A surviving mutant means a test is missing."""
import tempfile
from pathlib import Path

import numpy as np
import pytest

import config
import pooled_validation as PV
from stats import pooled as stats_pooled
from tests import pooled_guards as G

MUTANTS = {
    # (a) the N floor becomes the run's own count
    "a_universe_trials_is_the_runs_own_count": lambda mp: mp.setattr(PV, "pooled_universe_trials", lambda: 1),
    # (b) a run may miss any share of the universe
    "b_max_missing_is_one": lambda mp: mp.setattr(config, "POOLED_MAX_MISSING", 1.0),
    # (c) the universe hash is left out of the version
    "c_universe_hash_left_out_of_the_version": lambda mp: mp.setattr(PV, "universe_hash", lambda syms: "constant"),
    # (d) the null draws trades independently instead of by cluster
    "d_null_draws_independently": lambda mp: mp.setattr(PV, "NULL_CLUSTER_PRESERVING", False),
    # (e) T_eff is replaced by T
    "e_t_eff_is_t": lambda mp: mp.setattr(stats_pooled, "bartlett_t_eff", lambda r, q: float(len(r))),
    # (f) the leave-one-symbol-out check is removed
    "f_loso_removed": lambda mp: mp.setattr(PV, "_loso", lambda R, syms: (float(R.mean()), None)),
    # (g) the execution universe check is removed
    "g_execution_universe_check_removed": lambda mp: mp.setattr(PV, "intent_problem", lambda *a, **k: None),
    # ---- review round 1 (finding 2): gates and bypasses that used to survive
    # (h) the DSR alpha is not split with the per-symbol family (DSR_P_MAX / 2 -> DSR_P_MAX)
    "h_dsr_alpha_not_split": lambda mp: mp.setattr(config, "DSR_P_MAX", config.DSR_P_MAX * 2),
    # (i) the BH alpha is not split either (FDR_ALPHA / 2 -> FDR_ALPHA)
    "i_bh_alpha_not_split": lambda mp: mp.setattr(config, "FDR_ALPHA", config.FDR_ALPHA * 2),
    # (j) the capped replay no longer has to keep half of the uncapped Sharpe
    "j_capped_sharpe_rule_is_zero": lambda mp: mp.setattr(PV, "CAPPED_SHARPE_MIN_RATIO", 0.0),
    # (k) the 1-bar delay stress is no longer a gate
    "k_delay_gate_removed": lambda mp: mp.setattr(PV, "_cost_delay_ok", lambda cost, delay: cost > 0),
    # (l) bars after a symbol's oos_end enter the date-level series
    "l_oos_end_clip_removed": lambda mp: mp.setattr(PV, "_oos_mask", lambda idx, oos_end: np.ones(len(idx), dtype=bool)),
    # (m) the breadth requirement is 1 symbol
    "m_breadth_requirement_is_one": lambda mp: mp.setattr(PV, "_breadth_needed", lambda k: 1),
    # (n) the Var[SR] floor 1 / T_eff is removed
    "n_variance_floor_removed": lambda mp: mp.setattr(PV, "_variance_used", lambda var_floor, t_eff: var_floor),
    # (o) PSR is computed with T instead of T_eff
    "o_psr_uses_t": lambda mp: mp.setattr(stats_pooled, "psr_teff", _psr_with_t(stats_pooled.psr_teff)),
    # (p) DSR is computed with T instead of T_eff
    "p_dsr_uses_t": lambda mp: mp.setattr(stats_pooled, "dsr_teff", _dsr_with_t(stats_pooled.dsr_teff)),
    # (q) the null p-value is forced near zero
    "q_null_p_forced_near_zero": lambda mp: mp.setattr(PV, "pooled_null", lambda recs, *a, **k: PV.NullOut(
        0.0005, 0.1, 0.0, 0.05, 100, len(recs), 10)),
    # (r) the capped replay judges trades with a LOOK-AHEAD exclusion (full-sample t-stats)
    "r_capped_replay_exclusion_is_lookahead": lambda mp: mp.setattr(
        PV, "capped_replay", _lookahead(PV.capped_replay)),
    # (s) the call site passes T as T_eff to the statistics (a real call-site mutant: the statistics get T)
    "s_t_eff_passed_as_t": lambda mp: _call_site_t(mp),
    # ---- review round 2 (finding 2): the hold-out sign gate and the re-read test
    "t_holdout_sign_gate_is_minus_one": lambda mp: mp.setattr(PV, "_HOLDOUT_MIN_RETURN", -1.0),
    "u_holdout_reread_gate_removed": lambda mp: mp.setattr(stats_pooled, "stationary_bootstrap_mean_p",
                                                           lambda *a, **k: 0.0),
}


def _call_site_t(mp):
    """Wrap the call-site entry points so the statistics are priced with T = len(series) whatever the caller passes."""
    mp.setattr(stats_pooled, "psr_teff", _psr_with_t(stats_pooled.psr_teff))
    mp.setattr(stats_pooled, "dsr_teff", _dsr_with_t(stats_pooled.dsr_teff))


def _psr_with_t(real):
    return lambda returns, t_eff=None, sr_benchmark=0.0: real(returns, len(returns), sr_benchmark)


def _dsr_with_t(real):
    return lambda returns, n_trials, var_sr, t_eff=None: real(returns, n_trials, var_sr, len(returns))


def _lookahead(real):
    return lambda recs, excluded=(), **kw: real(recs, PV.excluded_symbols(recs))


def _failures(apply) -> list[str]:
    failed = []
    with pytest.MonkeyPatch.context() as mp:
        apply(mp)
        for name, guard in G.GUARDS.items():
            with tempfile.TemporaryDirectory() as d, pytest.MonkeyPatch.context() as gmp:
                try:
                    guard(Path(d), gmp)
                except AssertionError:          # only a real assertion counts as "caught"; a crash is a meta-test failure
                    failed.append(name)
                    break                        # one failing guard is enough: stop early to keep the suite fast
                except Exception as e:
                    pytest.fail(f"guard {name} crashed under the mutant ({type(e).__name__}: {e}); "
                                f"a crash is not a catch")
    return failed


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught_by_at_least_one_guard(name):
    caught = _failures(MUTANTS[name])
    assert caught, f"mutant {name} SURVIVED: no guard failed, a test is missing"
