"""D18 statistical tests (proposal tests 10 and 11), slow: run nightly with ``pytest -m slow``.

Calibration: on a correlated pure-noise universe (common factor, rho = 0.6) the pooled random-entry null p-values are
uniform, the validated rate is <= alpha, and a null that draws trades INDEPENDENTLY is anti-conservative (which is why
the cluster design is needed). Power: a planted broad modest edge is found pooled and not per symbol; a planted
single-symbol edge is not found pooled (concentration)."""
import numpy as np
import pytest
from scipy import stats

import config
import pooled_validation as PV
import validation as V
from state.holdout import HoldoutStore
from tests import pooled_helpers as H
from trials.pooled_ledger import PooledLedger

pytestmark = pytest.mark.slow

SEEDS = 25
NAMES = [f"n{i}" for i in range(6)]


def _noise_p_values(tmp_path, monkeypatch, independent: bool):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, NAMES)
    if independent:
        monkeypatch.setattr(PV, "NULL_CLUSTER_PRESERVING", False)
    ps, validated = [], 0
    for seed in range(SEEDS):
        frames = H.noise_frames(common=0.6, seed=seed)
        pats = {nm: H.planted_pattern(100 * seed + i, p=0.08, hold=2 + (i % 4)) for i, nm in enumerate(NAMES)}
        out = PV.evaluate_pooled(frames, pats, ledger=PooledLedger(str(tmp_path / f"t{seed}.sqlite")),
                                 holdout_store=HoldoutStore(":memory:"), registry=False, write_components=False,
                                 draws=300, seed=seed)
        for p in out["patterns"]:
            ps.append(p["null_p"])
            validated += p["status"] == PV.POOLED_DEFLATED_VALIDATED
    return np.array(ps), validated


def test_calibration_cluster_null_is_uniform_on_correlated_noise(tmp_path, monkeypatch):
    ps, validated = _noise_p_values(tmp_path, monkeypatch, independent=False)
    assert len(ps) == SEEDS * len(NAMES)
    assert validated / len(ps) <= 0.05                                         # false-positive rate <= alpha (expected ~0)
    assert stats.kstest(ps, "uniform").pvalue > 0.01                           # uniform null p-values
    assert (ps <= 0.05).mean() <= 0.10 and (ps <= 0.01).mean() <= 0.04


def test_calibration_independent_draws_are_anti_conservative(tmp_path, monkeypatch):
    ps, _ = _noise_p_values(tmp_path, monkeypatch, independent=True)
    assert (ps <= 0.05).mean() > 0.10                                          # far more than 5% of noise 'significant'
    assert stats.kstest(ps, "uniform").pvalue < 0.01                           # non-uniform: the cluster design is needed


def _power_run(tmp_path, monkeypatch, edge_symbols, jump):
    H.use_universe(monkeypatch)
    H.use_small_family(monkeypatch, ["planted"] + NAMES[:5])
    pats = {"planted": H.planted_pattern(1)}
    for i, nm in enumerate(NAMES[:5]):
        pats[nm] = H.planted_pattern(50 + i, p=0.08, hold=2 + (i % 4))
    frames = H.planted_universe(edge_symbols=edge_symbols, jump=jump)
    out = PV.evaluate_pooled(frames, pats, ledger=PooledLedger(str(tmp_path / "t.sqlite")),
                             holdout_store=HoldoutStore(":memory:"), registry=False, write_components=False, draws=500)
    return frames, next(p for p in out["patterns"] if p["pattern"] == "planted")


def test_power_a_broad_modest_edge_is_found_pooled_and_not_per_symbol(tmp_path, monkeypatch):
    frames, p = _power_run(tmp_path, monkeypatch, H.SYMBOLS10[:6], jump=0.02)       # an edge on 60% of the symbols
    assert p["status"] in (PV.POOLED_OOS_VALIDATED, PV.POOLED_DEFLATED_VALIDATED) and p["concentration"]["ok"], p["reasons"]
    res = V.evaluate_candidates(frames, {"planted": H.planted_pattern(1)}, executable_variant=True, draws=300,
                                min_trials=PV.pooled_universe_trials() * len(H.SYMBOLS10))
    assert all(r.validation_status != "deflated_validated" for r in res)             # no symbol alone clears the bar
    assert max(r.n_oos_trades for r in res) < config.MIN_TRADES_OOS                  # (per-symbol: too few OOS trades)


def test_power_a_single_symbol_edge_is_not_found_pooled(tmp_path, monkeypatch):
    _, p = _power_run(tmp_path, monkeypatch, ["AAPL"], jump=0.08)
    assert p["status"] == "unvalidated" and "pooled_concentrated" in p["reasons"]
    assert p["concentration"]["top_symbol"] == "AAPL" and not p["concentration"]["share_ok"]
