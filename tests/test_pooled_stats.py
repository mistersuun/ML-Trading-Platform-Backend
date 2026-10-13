"""D18 dependence-aware statistics (stats/pooled.py): T_eff, PSR/DSR with T_eff, block bootstrap, cluster bootstrap,
cross-sectional CSCV. Offline and seeded."""
import numpy as np
import pytest

from stats import pooled as P
from stats import selection


def _ma(n: int, q: int, seed: int = 0) -> np.ndarray:
    e = np.random.default_rng(seed).normal(0, 1, n + q)
    return np.convolve(e, np.ones(q + 1) / np.sqrt(q + 1), mode="valid")[:n]


def test_t_eff_is_about_T_on_iid_input_and_never_above_T():
    rs = [np.random.default_rng(s).normal(0, 1, 2000) for s in range(20)]
    teffs = [P.bartlett_t_eff(r, 10) for r in rs]
    assert all(t <= len(r) for t, r in zip(teffs, rs))                      # never above T
    assert np.mean(teffs) > 0.9 * 2000                                      # ~T on iid input
    # exactly T for a series whose sample autocorrelations are all <= 0 (the cap, not the estimate)
    alt = np.tile([1.0, -1.0], 500)
    assert P.bartlett_t_eff(alt, 5) == len(alt)


def test_t_eff_is_strictly_smaller_on_an_ma5_process():
    r = _ma(3000, 5)
    t = P.bartlett_t_eff(r, 10)
    assert t < 0.6 * len(r)                      # theory: T / (1 + 2 sum (1 - k/6)... ) is about T / 5-6
    assert t < P.bartlett_t_eff(np.random.default_rng(1).normal(0, 1, 3000), 10)


def test_t_eff_bandwidth_matters_for_slow_holds():
    r = _ma(3000, 30, seed=3)                    # holds of ~30 bars
    assert P.bartlett_t_eff(r, 40) < P.bartlett_t_eff(r, 3)                  # a floor-of-10 bandwidth overstates T_eff


def test_t_eff_degenerate_inputs():
    assert P.bartlett_t_eff([], 10) == 0.0
    assert P.bartlett_t_eff([1.0, 2.0], 10) == 2.0
    assert P.bartlett_t_eff(np.zeros(50), 10) == 50.0


def test_psr_with_t_eff_equals_the_plain_psr_at_T_and_shrinks_with_fewer_observations():
    r = np.random.default_rng(4).normal(0.001, 0.01, 400)
    sr, sk, ku = selection._moments(r)
    assert P.psr_teff(r, len(r)) == pytest.approx(selection.psr(sr, len(r), sk, ku))
    assert P.psr_teff(r, 100) < P.psr_teff(r, 400)
    assert P.dsr_teff(r, 50, 0.002, 400) < P.psr_teff(r, 400)               # deflation lowers it
    assert P.psr_teff(np.zeros(20), 20) is None


def test_stationary_bootstrap_is_seeded_and_brackets_the_estimate():
    r = np.random.default_rng(5).normal(0.002, 0.01, 500)
    a = P.stationary_bootstrap_sharpe(r, 500, 10, np.random.default_rng(9))
    b = P.stationary_bootstrap_sharpe(r, 500, 10, np.random.default_rng(9))
    assert a == b
    sr = P.sharpe_annual(r)
    assert a["lo"] < sr < a["hi"] and a["draws"] == 500
    assert P.stationary_bootstrap_sharpe(r[:5], 100, 10, np.random.default_rng(1)) is None


def test_stationary_bootstrap_blocks_have_the_requested_mean_length():
    idx = P.stationary_bootstrap_indices(2000, 50, 10, np.random.default_rng(2))
    cont = (np.diff(idx, axis=1) % 2000) == 1                                # a block continues at the next index
    assert 0.85 < cont.mean() < 0.95                                         # 1 - 1/10
    assert idx.min() >= 0 and idx.max() < 2000


def test_cluster_bootstrap_resamples_whole_clusters():
    rng = np.random.default_rng(6)
    clusters = np.repeat(np.arange(40), 5)
    vals = np.repeat(rng.normal(0.3, 1.0, 40), 5)                            # one shared shock per cluster
    ci = P.cluster_bootstrap_mean(vals, clusters, 1000, np.random.default_rng(3))
    naive_se = vals.std(ddof=1) / np.sqrt(len(vals))
    assert (ci["hi"] - ci["lo"]) / 3.92 > 1.5 * naive_se                    # wider than treating trades as independent
    assert P.cluster_bootstrap_mean([1.0, 2.0], [1, 1], 10, np.random.default_rng(1)) is None


def test_cross_sectional_cscv_separates_a_broad_edge_from_a_one_symbol_edge():
    rng = np.random.default_rng(8)
    T, K, Pn = 300, 12, 6
    noise = rng.normal(0, 0.01, (T, K, Pn))
    broad = noise.copy()
    broad[:, :, 0] += 0.002                                                  # pattern 0 wins on every symbol
    narrow = noise.copy()
    narrow[:, 0, 0] += 0.05                                                  # pattern 0 wins on symbol 0 only
    pb = P.cross_sectional_cscv(broad, 400, np.random.default_rng(1))["pbo_xs"]
    pn = P.cross_sectional_cscv(narrow, 400, np.random.default_rng(1))["pbo_xs"]
    assert pb < 0.2 < 0.4 < pn
    assert P.cross_sectional_cscv(noise[:, :3, :], 10, np.random.default_rng(1)) is None
