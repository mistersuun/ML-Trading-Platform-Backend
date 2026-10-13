"""WS4.2: PSR / Deflated Sharpe (Bailey & Lopez de Prado 2014) and CSCV PBO (Bailey, Borwein, Lopez de Prado & Zhu
2015) known answers; all Sharpe ratios per period, kurtosis non-excess."""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from scipy.stats import norm

import config
import metrics
from stats import selection as sel


# ------------------------------------------------------------------ DSR / PSR (2014 paper example)
SR, VAR_SR, T = 2.5 / math.sqrt(250), 0.5 / 250, 1250      # annualised SR 2.5, 5y daily, annualised V = 0.5


def test_expected_max_sharpe_known_answer_paper_example():
    # Paper (N=100, annualised V=0.5): SR0 = 0.1132 per period (~1.79 annualised)
    assert sel.expected_max_sharpe(100, VAR_SR) == pytest.approx(0.1132, abs=1e-3)
    # Direct evaluation of SR0 = sqrt(V)[(1-g) Phi^-1(1-1/N) + g Phi^-1(1-1/(N e))]
    g = 0.5772156649015329
    ref = math.sqrt(VAR_SR) * ((1 - g) * norm.ppf(1 - 1 / 100) + g * norm.ppf(1 - 1 / (100 * math.e)))
    assert sel.expected_max_sharpe(100, VAR_SR) == pytest.approx(ref, abs=1e-12)


def test_dsr_known_answer_paper_example():
    d = sel.dsr(SR, 100, VAR_SR, T, skew=-3, kurt=10)
    assert d == pytest.approx(0.9004, abs=1e-3)                  # paper: DSR = 0.9004
    z = (SR - sel.expected_max_sharpe(100, VAR_SR)) * math.sqrt(T - 1) / math.sqrt(1 + 3 * SR + 9 / 4 * SR ** 2)
    assert d == pytest.approx(norm.cdf(z), abs=1e-12)
    assert sel.dsr_p_value(SR, 100, VAR_SR, T, -3, 10) == pytest.approx(1 - 0.9004, abs=1e-3)


def test_dsr_reduces_to_psr_with_one_trial_and_deflates_with_more():
    assert sel.dsr(SR, 1, VAR_SR, T, -3, 10) == pytest.approx(sel.psr(SR, T, -3, 10), abs=1e-12)
    assert sel.dsr(SR, 1000, VAR_SR, T, -3, 10) < sel.dsr(SR, 100, VAR_SR, T, -3, 10) < sel.dsr(SR, 10, VAR_SR, T, -3, 10)
    assert sel.dsr(SR, 100, 0.0, T) == pytest.approx(sel.psr(SR, T), abs=1e-12)    # no cross-trial spread: no deflation


def test_psr_known_values_and_agrees_with_metrics():
    assert sel.psr(0.0, 500) == pytest.approx(0.5)
    sr, t = 0.1, 250                                                # Gaussian: Phi(sr sqrt(T-1) / sqrt(1 + sr^2/2))
    assert sel.psr(sr, t) == pytest.approx(norm.cdf(sr * math.sqrt(t - 1) / math.sqrt(1 + 0.5 * sr ** 2)), abs=1e-12)
    assert sel.psr(sr, t, -1.0, 6.0, 0.02) == pytest.approx(metrics.psr_from_moments(sr, t, -1.0, 6.0, 0.02), abs=1e-12)
    assert sel.dsr(SR, 100, VAR_SR, T, -3, 10) == pytest.approx(metrics.deflated_sharpe(SR, 100, VAR_SR, T, -3, 10), abs=1e-12)
    assert sel.psr(0.1, 1) is None and sel.psr(float("nan"), 100) is None


def test_dsr_from_returns_uses_own_moments_and_rejects_degenerate_series():
    r = np.random.default_rng(config.SEED).normal(0.001, 0.01, 800)
    sr, sk, ku = sel._moments(r)
    assert sr == pytest.approx(metrics.sharpe(r, bars_per_year=1))
    assert (sk, ku) == pytest.approx(metrics.skew_kurt(r))
    assert sel.dsr_from_returns(r, 50, 0.0004) == pytest.approx(sel.dsr(sr, 50, 0.0004, 800, sk, ku), abs=1e-12)
    assert sel.dsr_from_returns(np.zeros(100), 50, 0.0004) is None and sel.dsr_from_returns([0.1, 0.2], 5, 1e-3) is None


def test_sharpe_variance_is_cross_trial_and_ignores_nan_and_short_columns():
    rng = np.random.default_rng(1)
    m = rng.normal(0.0, 0.01, (400, 6))
    srs = [metrics.sharpe(m[:, j], bars_per_year=1) for j in range(6)]
    assert sel.sharpe_variance(m) == pytest.approx(np.var(srs, ddof=1))
    m2 = np.column_stack([m, np.full(400, np.nan)])
    m2[:300, 1] = np.nan
    assert sel.sharpe_variance(m2, min_obs=150) == pytest.approx(
        np.var([srs[0], *srs[2:]], ddof=1))        # col 1 has 100 obs < 150, the NaN column none
    assert sel.sharpe_variance(m[:, :1]) == 0.0


# ------------------------------------------------------------------ PBO / CSCV
def _brute_pbo(m, S):
    """Direct transcription of the 2015 paper's algorithm (no vectorisation)."""
    L = m.shape[0] // S
    subs = [m[i * L:(i + 1) * L] for i in range(S)]
    n = m.shape[1]
    logits = []
    for js in itertools.combinations(range(S), S // 2):
        train = np.vstack([subs[i] for i in js])
        test = np.vstack([subs[i] for i in range(S) if i not in js])
        sr = lambda x: np.where(x.std(0, ddof=1) > 1e-12, x.mean(0) / np.where(x.std(0, ddof=1) > 1e-12, x.std(0, ddof=1), 1), 0)
        star = int(np.argmax(sr(train)))
        o = sr(test)
        rank = sum(o < o[star]) + 1 + 0.5 * (sum(o == o[star]) - 1)
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
    return float(np.mean(np.array(logits) <= 0)), np.array(logits)


def test_pbo_hand_computed_anti_persistent_pair_is_one():
    # S=2: strategy 0 wins block 0 and loses block 1, strategy 1 the opposite.  Each IS winner is the OOS loser:
    # rank 1 of N=2 -> w = 1/3 -> logit = ln(0.5) = -0.6931 < 0 for both combinations -> PBO = 1.
    blk = np.array([0.02, 0.0, 0.02, 0.0, 0.01, 0.0])             # block of 6 obs (has dispersion)
    a, b = blk, -blk
    m = np.column_stack([np.concatenate([a, b]), np.concatenate([b, a])])
    pbo, logits = sel.pbo_cscv(m, S=2, return_logits=True)
    assert pbo == 1.0
    assert logits == pytest.approx([math.log(0.5)] * 2, abs=1e-12)


def test_pbo_hand_computed_persistent_pair_is_zero():
    blk = np.array([0.02, 0.0, 0.02, 0.0, 0.01, 0.0])
    m = np.column_stack([np.tile(blk, 4), -np.tile(blk, 4)])        # strategy 0 dominates in every block
    pbo, logits = sel.pbo_cscv(m, S=4, return_logits=True)
    assert pbo == 0.0 and len(logits) == 6                          # C(4,2)
    assert logits == pytest.approx([math.log(2.0)] * 6, abs=1e-12)  # rank 2 of 2 -> w = 2/3


def test_pbo_matches_brute_force_reference_and_is_deterministic():
    rng = np.random.default_rng(config.SEED)
    m = rng.normal(0, 0.01, (320, 12))
    pbo, logits = sel.pbo_cscv(m, S=8, return_logits=True)
    ref, ref_logits = _brute_pbo(m, 8)
    assert pbo == pytest.approx(ref, abs=1e-12) and logits == pytest.approx(ref_logits, abs=1e-9)
    assert sel.pbo_cscv(m, S=8) == pbo
    assert len(logits) == math.comb(8, 4)


def test_pbo_default_is_config_split_and_handles_nan_and_remainder_rows():
    rng = np.random.default_rng(3)
    m = rng.normal(0, 0.01, (16 * 20 + 7, 5))
    m[5, 2] = np.nan
    v = sel.pbo_cscv(m, S=config.PBO_S)
    assert 0.0 <= v <= 1.0
    clean = np.nan_to_num(m[:320], nan=0.0)
    assert v == sel.pbo_cscv(clean, S=16)                            # NaN -> 0, trailing remainder dropped


def test_pbo_rejects_bad_inputs():
    m = np.random.default_rng(0).normal(size=(100, 4))
    with pytest.raises(ValueError):
        sel.pbo_cscv(m, S=7)
    with pytest.raises(ValueError):
        sel.pbo_cscv(m[:, :1], S=4)
    with pytest.raises(ValueError):
        sel.pbo_cscv(m[:20], S=16)


def test_pbo_logit_exactly_zero_counts_as_overfit():
    """w = rank/(N+1) = 2/4 = 0.5 -> logit 0 exactly: the boundary belongs to 'overfit' (logit <= 0)."""
    m = np.array([[0.03, 0.02, 0.01], [0.01, 0.00, -0.01],      # block 0: A > B > C
                  [0.02, 0.03, 0.01], [0.00, 0.01, -0.01]])     # block 1: B > A > C
    pbo, logits = sel.pbo_cscv(m, S=2, return_logits=True)
    assert list(logits) == [0.0, 0.0] and pbo == 1.0
