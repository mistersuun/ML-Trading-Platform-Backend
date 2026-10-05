"""Bug-pinning tests for pairs trading and stress testing (WS0.3: PR-1..PR-4, ST-1..ST-3).

xfail(strict) tests assert the CORRECT behaviour and currently fail with AssertionError.
"""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import config
from backtester import BacktestResult, Trade
from pairs_trading import analyze_pair, backtest_pair, generate_pair_signals
from stress_test import detect_regimes, monte_carlo_analysis, stress_test_regimes
from tests.fixtures.synthetic import gbm_ohlc, ou_pair, random_walk_ohlc
from tests.helpers_causal import assert_causal

BUG = dict(strict=True, raises=AssertionError)


def _shift_prices(df, add=0.0, mul=1.0):
    out = df.copy()
    for c in ("Open", "High", "Low", "Close"):
        out[c] = out[c] * mul + add
    return out


def _pair_backtest(a, b, analysis):
    sig = generate_pair_signals(a, b, analysis)
    return backtest_pair(sig, analysis, commission=0.0)


@pytest.fixture(scope="module")
def pair_case():
    a, b = ou_pair(n=500, seed=7)
    analysis = analyze_pair(a, b, "A", "B")
    base = _pair_backtest(a, b, analysis)
    assert base.get("total_trades", 0) >= 3, "fixture must produce trades"
    return a, b, analysis, base


# ------------------------------------------------------------------ PR-1
def test_PR_1a_scaling_both_prices_leaves_returns_unchanged(pair_case):
    """Regression guard: dividing by |entry spread| happens to be scale invariant today."""
    a, b, analysis, base = pair_case
    k = 7.0
    analysis_k = analyze_pair(_shift_prices(a, mul=k), _shift_prices(b, mul=k), "A", "B")
    scaled = _pair_backtest(_shift_prices(a, mul=k), _shift_prices(b, mul=k), analysis_k)
    assert scaled["total_trades"] == base["total_trades"]
    assert scaled["total_return"] == pytest.approx(base["total_return"], rel=1e-6, abs=1e-9)


@pytest.mark.xfail(reason="BUG-PR-1: pairs P&L divided by |entry spread| (+1e-10), blowing up when the spread is near zero", **BUG)
def test_PR_1b_near_zero_entry_spread_gives_bounded_pnl(pair_case):
    _, _, analysis, _ = pair_case
    n = 40
    idx = pd.bdate_range("2024-01-01", periods=n)
    spread = np.zeros(n)
    spread[10:] = 0.5  # a 0.5 move on ~100-dollar legs, entered from a ~zero spread
    signal = np.zeros(n)
    signal[5:10] = 1
    df = pd.DataFrame({"spread": spread, "zscore": 0.0, "signal": signal}, index=idx)
    res = backtest_pair(df, analysis, commission=0.0)
    assert res["total_trades"] == 1
    assert abs(res["total_return"]) <= 1.0, f"total_return {res['total_return']:.3g}"


# ------------------------------------------------------------------ PR-2
@pytest.mark.xfail(reason="BUG-PR-2: hedge ratio / z-score use full-sample statistics (look-ahead)", **BUG)
def test_PR_2_pair_signals_causal_under_truncation():
    a, b = ou_pair(n=420, seed=11)

    def pipeline(x, y):
        analysis = analyze_pair(x, y, "A", "B")
        return generate_pair_signals(x, y, analysis)[["spread", "zscore", "signal"]]

    assert_causal(pipeline, (a, b), T=300, n_future=120)


# ------------------------------------------------------------------ PR-3 / PR-4
def _no_constants(name):
    raise AssertionError(f"non-finite JSON constant {name} in response")


@pytest.fixture
def client():
    import server
    return TestClient(server.app, raise_server_exceptions=False)


@pytest.mark.xfail(reason="BUG-PR-3: /api/pairs/analyze emits Infinity half_life -> invalid JSON / 500", **BUG)
def test_PR_3_pairs_analyze_json_valid_with_infinite_half_life(client, patch_fetch):
    # Independent random walks (seeds chosen so AR(1) theta >= 0): half_life == inf
    patch_fetch.overrides["RW1"] = random_walk_ohlc(n=300, seed=14)
    patch_fetch.overrides["RW2"] = random_walk_ohlc(n=300, seed=114)
    r = client.post("/api/pairs/analyze", json={"symbol_a": "RW1", "symbol_b": "RW2", "period_days": 300})
    assert r.status_code == 200, f"got {r.status_code}"
    body = json.loads(r.text, parse_constant=_no_constants)
    assert "error" not in body
    assert body["half_life"] is None or np.isfinite(body["half_life"])


@pytest.mark.xfail(reason="BUG-PR-4: /api/pairs/scan returns np.bool_ / inf and 500s", **BUG)
def test_PR_4_pairs_scan_does_not_500_on_numpy_types(client, patch_fetch, monkeypatch):
    a, b = ou_pair(n=400, seed=7)
    patch_fetch.overrides["PA"] = a
    patch_fetch.overrides["PB"] = b
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB")])
    monkeypatch.setattr(config, "PAIRS_LOOKBACK", 400)
    r = client.post("/api/pairs/scan")
    assert r.status_code == 200, f"got {r.status_code}"
    body = json.loads(r.text, parse_constant=_no_constants)
    assert body["count"] >= 1


# ------------------------------------------------------------------ ST-1..ST-3
@pytest.mark.xfail(reason="BUG-ST-1: detect_regimes uses full-sample rank(pct=True) (look-ahead)", **BUG)
def test_ST_1_regimes_causal():
    df = gbm_ohlc(n=700, seed=21)
    assert_causal(detect_regimes, df, T=500, n_future=150)


@pytest.mark.xfail(reason="BUG-ST-2: regime tests run the pattern on non-contiguous stitched slices", **BUG)
def test_ST_2_regime_tests_use_contiguous_slices():
    df = gbm_ohlc(n=1000, seed=5)
    pos = {ts: i for i, ts in enumerate(df.index)}
    seen = []

    def spy_pattern(x):
        seen.append(x.index)
        return x.assign(signal=0)

    stress_test_regimes(df, "SYN", spy_pattern, "spy")
    assert len(seen) >= 1, "pattern was never run"
    for idx in seen:
        p = np.array([pos[t] for t in idx])
        assert (np.diff(p) == 1).all(), "pattern run on a non-contiguous stitched slice"


@pytest.mark.xfail(reason="BUG-ST-3: Monte Carlo compounds full pnl_pct, ignoring position size", **BUG)
def test_ST_3_monte_carlo_dispersion_reflects_position_size():
    f = config.MAX_POSITION_SIZE_PCT
    pnl = 0.10
    n = 20
    ts = pd.bdate_range("2024-01-01", periods=n)
    trades = [Trade(entry_date=ts[i], exit_date=ts[i], pnl_pct=pnl if i % 2 == 0 else -pnl) for i in range(n)]
    res = BacktestResult(symbol="X", pattern_name="p", trades=trades, total_trades=n)
    np.random.seed(0)
    mc = monte_carlo_analysis(res, n_simulations=500)
    # Each trade risks fraction f of equity, so the best possible total return is (1+f*pnl)^n - 1.
    best = (1 + f * pnl) ** n - 1
    worst = (1 - f * pnl) ** n - 1
    assert mc["return_ci_high"] <= best + 1e-9
    assert mc["return_ci_low"] >= worst - 1e-9
