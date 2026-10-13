"""Known-answer tests for metrics.py (WS2.2)."""
import json
import math

import numpy as np
import pytest
from scipy.stats import norm

import metrics


def test_sharpe_known_value_rf_zero():
    r = np.array([0.01, -0.005, 0.02, 0.0, 0.01])
    expected = r.mean() / r.std(ddof=1) * math.sqrt(252)
    assert metrics.sharpe(r) == pytest.approx(expected)
    assert metrics.sharpe(r, bars_per_year=365) == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(365))


def test_sharpe_zero_variance_and_short_are_zero():
    assert metrics.sharpe(np.zeros(50)) == 0.0
    assert metrics.sharpe(np.full(50, 0.001)) == 0.0
    assert metrics.sharpe([]) == 0.0 and metrics.sharpe([0.01]) == 0.0
    assert metrics.sortino(np.zeros(10)) == 0.0


def test_sortino_downside_deviation_over_all_observations():
    r = np.array([0.02, -0.01, 0.03, -0.02, 0.0, 0.01])
    dd = math.sqrt(np.mean(np.minimum(r, 0) ** 2))   # divides by N, not by the number of losers
    assert dd == pytest.approx(math.sqrt((0.01 ** 2 + 0.02 ** 2) / 6))
    assert metrics.sortino(r) == pytest.approx(r.mean() / dd * math.sqrt(252))
    assert metrics.sortino(np.array([0.01, 0.02, 0.03])) == 0.0   # no downside -> defined as 0, not inf


def test_cagr_and_calmar():
    r = np.full(252, 0.0004)
    assert metrics.cagr(r) == pytest.approx(1.0004 ** 252 - 1)
    r2 = np.array([0.1, -0.2, 0.05, 0.05] * 63)
    eq = np.cumprod(1 + r2)
    mdd, _ = metrics.max_drawdown(np.concatenate([[1.0], eq]))
    assert metrics.calmar(r2, np.concatenate([[1.0], eq])) == pytest.approx(metrics.cagr(r2) / abs(mdd))
    assert metrics.calmar(np.full(10, 0.01)) is None            # no drawdown -> undefined
    assert metrics.cagr(np.array([-1.0, 0.1])) == -1.0          # wiped out


def test_max_drawdown_and_duration():
    eq = np.array([100, 110, 99, 105, 90, 95, 111, 120.0])
    mdd, dur = metrics.max_drawdown(eq)
    assert mdd == pytest.approx(90 / 110 - 1)
    assert dur == 4                                              # bars 2..5 under the 110 peak
    assert metrics.max_drawdown(np.array([1.0, 2.0, 3.0])) == (0.0, 0)


def test_psr_matches_closed_form_and_skew_kurtosis_matter():
    sr, T = 0.1, 500
    base = metrics.psr_from_moments(sr, T, 0.0, 3.0)
    assert base == pytest.approx(norm.cdf(sr * math.sqrt(T - 1) / math.sqrt(1 + 0.5 * sr ** 2)))
    assert metrics.psr_from_moments(sr, T, -1.0, 3.0) < base      # negative skew lowers PSR
    assert metrics.psr_from_moments(sr, T, 0.0, 10.0) < base      # fat tails lower PSR
    assert metrics.psr_from_moments(0.0, T) == pytest.approx(0.5)  # benchmark 0, SR 0
    assert metrics.psr_from_moments(sr, 1) is None


def test_psr_from_returns_sane():
    rng = np.random.default_rng(1)
    good = rng.normal(0.002, 0.01, 1000)
    bad = rng.normal(-0.002, 0.01, 1000)
    assert metrics.psr(good) > 0.95 and metrics.psr(bad) < 0.05
    assert metrics.psr(np.zeros(100)) is None


def test_deflated_sharpe_known_answer_bailey_lopez_de_prado_2014():
    # Paper example: annualised SR 2.5 over 5y of daily data (T=1250), skew -3, kurtosis 10,
    # N=100 trials with annualised cross-trial Sharpe variance 0.5.  Per-period units: /sqrt(250), /250.
    sr, var_sr, T = 2.5 / math.sqrt(250), 0.5 / 250, 1250
    sr0 = metrics.expected_max_sharpe(100, var_sr)
    assert sr0 == pytest.approx(0.1132, abs=5e-4)                 # paper: SR0 ~ 0.1132 (per period)
    dsr = metrics.deflated_sharpe(sr, 100, var_sr, T, skew=-3, kurt=10)
    assert dsr == pytest.approx(0.9004, abs=2e-3)                 # paper: DSR ~ 0.90
    # independent recomputation from the formula
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(1 + 3 * sr + 9 / 4 * sr ** 2)
    assert dsr == pytest.approx(norm.cdf(z))
    # one trial -> no deflation; more trials -> lower DSR
    assert metrics.deflated_sharpe(sr, 1, var_sr, T, -3, 10) == pytest.approx(metrics.psr_from_moments(sr, T, -3, 10))
    assert metrics.deflated_sharpe(sr, 1000, var_sr, T, -3, 10) < dsr < metrics.deflated_sharpe(sr, 10, var_sr, T, -3, 10)


def test_wilson_ci_known_values():
    lo, hi = metrics.wilson_ci(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert metrics.wilson_ci(0, 0) == (None, None)
    lo, hi = metrics.wilson_ci(0, 20)
    assert lo == 0.0 and 0 < hi < 0.2
    lo, hi = metrics.wilson_ci(20, 20)
    assert hi == 1.0 and lo > 0.8


def test_profit_factor_none_without_losses():
    assert metrics.profit_factor([0.1, 0.2]) is None
    assert metrics.profit_factor([]) is None
    assert metrics.profit_factor([0.3, -0.1, -0.1]) == pytest.approx(1.5)
    assert metrics.profit_factor([-0.1]) == 0.0


def test_expectancy_notional_and_equity_scale():
    e = metrics.expectancy([0.04, -0.02], [200.0, -100.0], [10_000.0, 10_200.0])
    assert e["expectancy_notional"] == pytest.approx(0.01)
    assert e["expectancy_equity"] == pytest.approx(np.mean([200 / 10_000, -100 / 10_200]))
    assert metrics.expectancy([])["expectancy_notional"] is None


@pytest.mark.parametrize("equity,pnls", [
    ([100_000.0] * 20, []),                                            # zero trades
    ([100_000.0] + list(np.linspace(100_000, 101_000, 19)), [0.02]),   # all win
    ([100_000.0] + list(np.linspace(100_000, 99_000, 19)), [-0.02]),   # all loss
    ([], []),                                                          # empty
])
def test_summary_is_finite_or_none_and_json_safe(equity, pnls):
    s = metrics.summary(equity, pnls, [p * 5000 for p in pnls], [100_000.0] * len(pnls), initial_capital=100_000.0)
    json.dumps(s, allow_nan=False)
    for v in s.values():
        assert v is None or (isinstance(v, (int, float)) and math.isfinite(v))
    if not pnls:
        assert s["win_rate"] is None and s["profit_factor"] is None and s["sharpe"] == 0.0
