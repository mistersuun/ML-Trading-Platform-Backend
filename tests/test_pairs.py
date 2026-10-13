"""WS2.6 pairs correctness: causal spread stats, two-leg engine, statistics, routes."""
import json
import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import config
import pairs_trading as P
from tests.fixtures.synthetic import _bdays, _ohlc_from_close, ou_pair, random_walk_ohlc


def _analysis(beta=1.0, hl=10.0, bpy=252):
    return SimpleNamespace(symbol_a="A", symbol_b="B", hedge_ratio=beta, half_life=hl, correlation=0.9,
                           coint_pvalue=0.01, bars_per_year=bpy, non_executable=False)


def _hand_signals(pa, pb, signal, beta=1.0):
    n = len(pa)
    return pd.DataFrame({
        "price_a": pa, "price_b": pb, "spread": 0.0, "zscore": 0.0, "signal": signal,
        "hedge_beta": np.where(np.asarray(signal) != 0, beta, np.nan),
    }, index=pd.bdate_range("2024-01-01", periods=n))


def _gross_example(shift=0.0, mul=1.0):
    n = 30
    pa = np.full(n, 100.0)
    pa[10:] = 110.0
    pb = np.full(n, 100.0)
    sig = np.zeros(n, dtype=int)
    sig[5:10] = 1       # entered at bar 5's close, exits at bar 10 (earns bar 10's +10% on A)
    return _hand_signals(pa * mul + shift, pb * mul + shift, sig)


# ------------------------------------------------------------------ two-leg engine, closed forms
def test_gross_example_returns_five_percent_on_gross():
    bt = P.backtest_pair(_gross_example(), _analysis(), commission=0.0, slippage=0.0, time_stop=False)
    assert bt["total_trades"] == 1
    assert bt["trades"][0]["pnl_pct"] == pytest.approx(0.05, abs=1e-12)   # 0.5 * 10% + 0.5 * 0
    assert bt["trades"][0]["bars_held"] == 5
    assert bt["total_return"] == pytest.approx(config.MAX_POSITION_SIZE_PCT * 0.05, rel=1e-9)


def test_return_on_gross_invariant_to_price_scale_not_to_additive_shift():
    """Multiplying both price series by k leaves returns unchanged. ADDING +1000 does not: A from
    1100 to 1110 is a 0.909% move, so the correct return on gross is 0.5*0.909% (reviewer amendment)."""
    base = P.backtest_pair(_gross_example(), _analysis(), commission=0.0, slippage=0.0, time_stop=False)
    scaled = P.backtest_pair(_gross_example(mul=1000.0), _analysis(), commission=0.0, slippage=0.0, time_stop=False)
    assert scaled["trades"][0]["pnl_pct"] == pytest.approx(base["trades"][0]["pnl_pct"], rel=1e-12)
    shifted = P.backtest_pair(_gross_example(shift=1000.0), _analysis(), commission=0.0, slippage=0.0, time_stop=False)
    assert shifted["trades"][0]["pnl_pct"] == pytest.approx(0.5 * (1110 / 1100 - 1), rel=1e-12)


def test_pnl_is_never_divided_by_spread_level():
    """Same price path, spread column scaled to ~0 / huge: with prices present, result is identical."""
    a = _gross_example()
    b = a.copy()
    b["spread"] = 1e-9
    ra = P.backtest_pair(a, _analysis(), commission=0.0, slippage=0.0)
    rb = P.backtest_pair(b, _analysis(), commission=0.0, slippage=0.0)
    assert ra["total_return"] == rb["total_return"]


def test_costs_charged_on_both_legs_entry_and_exit():
    rate = 0.001 + 0.0005
    bt = P.backtest_pair(_gross_example(), _analysis(), commission=0.001, slippage=0.0005, time_stop=False)
    # gross 5% less entry and exit turnover of 1.0 of gross each
    expected = (1 - rate) * 1.05 * (1 - rate) - 1
    # entry cost lands on bar 5, return on bar 10, exit cost on bar 10 (same bar)
    expected = (1 - rate) * (1 + 0.05 - rate) - 1
    assert bt["trades"][0]["pnl_pct"] == pytest.approx(expected, rel=1e-12)


def test_beta_weighting_and_short_spread():
    n = 30
    pa = np.full(n, 100.0)
    pb = np.full(n, 100.0)
    pb[10:] = 110.0                     # B +10%
    sig = np.zeros(n, dtype=int)
    sig[5:10] = 1                       # long spread: long A, short B with beta 2
    df = _hand_signals(pa, pb, sig, beta=2.0)
    bt = P.backtest_pair(df, _analysis(beta=2.0), commission=0.0, slippage=0.0, time_stop=False)
    assert bt["trades"][0]["pnl_pct"] == pytest.approx(-(2 / 3) * 0.10, abs=1e-12)  # w_b = |b|/(1+|b|)
    df_s = _hand_signals(pa, pb, -sig, beta=2.0)
    bt_s = P.backtest_pair(df_s, _analysis(beta=2.0), commission=0.0, slippage=0.0, time_stop=False)
    assert bt_s["trades"][0]["pnl_pct"] == pytest.approx((2 / 3) * 0.10, abs=1e-12)


def test_open_trade_marked_at_end_and_last_bar_entry_ignored():
    n = 30
    pa = np.linspace(100, 130, n)
    pb = np.full(n, 100.0)
    sig = np.zeros(n, dtype=int)
    sig[20:] = 1                         # still open at the end
    bt = P.backtest_pair(_hand_signals(pa, pb, sig), _analysis(), commission=0.0, slippage=0.0, time_stop=False)
    assert bt["total_trades"] == 1 and bt["trades"][0]["exit_reason"] == "end_of_data"
    assert bt["trades"][0]["pnl_pct"] == pytest.approx(np.prod(1 + 0.5 * (pa[21:] / pa[20:-1] - 1)) - 1, rel=1e-9)  # daily-rebalanced
    sig2 = np.zeros(n, dtype=int)
    sig2[-1] = 1                         # signal on the final bar is never executed
    bt2 = P.backtest_pair(_hand_signals(pa, pb, sig2), _analysis(), commission=0.0, slippage=0.0)
    assert bt2["total_trades"] == 0 and bt2["total_return"] == 0.0


def test_time_stop_exits_after_k_half_lives_and_blocks_reentry():
    n = 120
    pa = np.full(n, 100.0)
    pb = np.full(n, 100.0)
    sig = np.zeros(n, dtype=int)
    sig[10:100] = 1                      # raw signal never exits
    bt = P.backtest_pair(_hand_signals(pa, pb, sig), _analysis(hl=5.0), commission=0.0, slippage=0.0)
    assert bt["total_trades"] == 1
    t = bt["trades"][0]
    assert t["exit_reason"] == "time_stop"
    assert t["bars_held"] == math.ceil(config.PAIRS_TIME_STOP_HALF_LIVES * 5.0)


def test_json_safe_with_no_losses_or_no_trades():
    ok = P.backtest_pair(_gross_example(), _analysis(), commission=0.0, slippage=0.0)
    json.dumps(ok, allow_nan=False)
    assert ok["profit_factor"] is None            # no losing trade -> None, never inf
    n = 30
    flat = _hand_signals(np.full(n, 100.0), np.full(n, 100.0), np.zeros(n, dtype=int))
    json.dumps(P.backtest_pair(flat, _analysis()), allow_nan=False)


# ------------------------------------------------------------------ causal spread stats
def _logs(n=420, seed=11):
    a, b = ou_pair(n=n, seed=seed)
    return np.log(a["Close"]), np.log(b["Close"])


def test_spread_stats_truncation_bit_identical():
    la, lb = _logs()
    T = 300
    full = P.compute_spread_stats(la, lb)
    rng = np.random.default_rng(0)
    la2, lb2 = la.copy(), lb.copy()
    la2.iloc[T:] = la2.iloc[T:] + rng.normal(0, 0.5, len(la) - T)     # wreck everything after T
    lb2.iloc[T:] = lb2.iloc[T:] + rng.normal(0, 0.5, len(lb) - T)
    other = P.compute_spread_stats(la2, lb2)
    trunc = P.compute_spread_stats(la.iloc[:T], lb.iloc[:T])
    for col in ("beta", "alpha", "spread", "sigma", "z"):
        np.testing.assert_array_equal(full[col].iloc[:T].to_numpy(), trunc[col].to_numpy())
        np.testing.assert_array_equal(full[col].iloc[:T].to_numpy(), other[col].iloc[:T].to_numpy())


def test_spread_stats_uses_window_ending_at_t_minus_1():
    la, lb = _logs(n=120)
    w = config.PAIRS_ROLLING_WINDOW
    st = P.compute_spread_stats(la, lb)
    t = 90
    x, y = lb.iloc[t - w:t].to_numpy(), la.iloc[t - w:t].to_numpy()
    b, a = np.polyfit(x, y, 1)
    assert st["beta"].iloc[t] == pytest.approx(b, rel=1e-9)
    assert st["alpha"].iloc[t] == pytest.approx(a, rel=1e-9)
    resid = y - (a + b * x)
    assert st["z"].iloc[t] == pytest.approx((la.iloc[t] - a - b * lb.iloc[t]) / resid.std(ddof=2), rel=1e-9)
    assert st["z"].iloc[:w].isna().all()


def test_signals_do_not_use_full_sample_statistics():
    a, b = ou_pair(n=420, seed=11)
    an = P.analyze_pair(a, b, "A", "B")
    s1 = P.generate_pair_signals(a, b, an)
    an2 = SimpleNamespace(**{**an.__dict__, "hedge_ratio": 99.0, "half_life": 1.0})
    s2 = P.generate_pair_signals(a, b, an2)
    pd.testing.assert_frame_equal(s1, s2)


# ------------------------------------------------------------------ statistics
def test_half_life_none_unless_phi_in_unit_interval():
    rng = np.random.default_rng(1)
    rw = pd.Series(np.cumsum(rng.normal(size=400)))
    assert P.compute_half_life(rw + 0) is None or 0 < P.compute_half_life(rw) < math.inf
    explosive = pd.Series(1.02 ** np.arange(300) + rng.normal(0, 1e-3, 300))
    assert P.compute_half_life(explosive) is None
    alt = pd.Series((-0.5) ** np.arange(60) + rng.normal(0, 1e-3, 60))   # phi < 0
    assert P.compute_half_life(alt) is None
    assert P.compute_half_life(pd.Series([1.0, 2.0])) is None


def test_analyze_pair_requires_min_aligned_bars():
    a, b = ou_pair(n=config.PAIRS_MIN_ALIGNED_BARS - 1, seed=3)
    with pytest.raises(P.InsufficientData):
        P.analyze_pair(a, b, "A", "B")


def test_analyze_pair_ou_is_cointegrated_and_symmetric_pvalue():
    a, b = ou_pair(n=500, seed=7)
    ab = P.analyze_pair(a, b, "A", "B")
    ba = P.analyze_pair(b, a, "B", "A")
    assert ab.coint_pvalue == pytest.approx(ba.coint_pvalue, rel=1e-12)
    assert ab.is_cointegrated == ba.is_cointegrated
    assert ab.half_life is not None and 5 < ab.half_life < 20
    assert ab.correlation > 0.5
    json.dumps(ab.__dict__, allow_nan=False)


def test_scan_applies_bh_across_all_pairs_scanned():
    data, pairs = {}, []
    for i in range(6):
        data[f"X{i}"] = random_walk_ohlc(n=300, seed=100 + i, step=1.0)
        data[f"Y{i}"] = random_walk_ohlc(n=300, seed=200 + i, step=1.0)
        pairs.append((f"X{i}", f"Y{i}"))
    a, b = ou_pair(n=500, seed=21)
    data["PA"], data["PB"] = a, b
    pairs.append(("PA", "PB"))
    res = P.scan_all_pairs(data, pairs)
    assert all(r["n_tested"] == 7 for r in res)
    assert all(r["adj_pvalue"] >= r["coint_pvalue"] for r in res)
    assert {(r["symbol_a"], r["symbol_b"]) for r in res} <= {("PA", "PB")}


# ------------------------------------------------------------------ scan z == backtest last z, routes
@pytest.fixture
def client():
    import server
    return TestClient(server.app, raise_server_exceptions=False)


def _strict(text):
    def bad(name):
        raise AssertionError(f"non-finite JSON constant {name}")
    return json.loads(text, parse_constant=bad)


def _good_pair(seed):
    """An OU pair that survives max-p Engle-Granger (found deterministically)."""
    for s in range(seed, seed + 40):
        a, b = ou_pair(n=500, seed=s)
        an = P.analyze_pair(a, b, "A", "B")
        if an.is_cointegrated and an.half_life and 5 <= an.half_life <= 120:
            return a, b
    raise AssertionError("no cointegrated fixture found")


def test_scan_z_equals_backtest_last_z_and_stats(client, patch_fetch, monkeypatch):
    a, b = _good_pair(1)
    patch_fetch.overrides["PA"], patch_fetch.overrides["PB"] = a, b
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB")])
    r = client.post("/api/pairs/scan")
    assert r.status_code == 200
    body = _strict(r.text)
    assert body["count"] == 1
    z_scan = body["pairs"][0]["current_zscore"]
    st = P.compute_spread_stats(np.log(a["Close"]), np.log(b["Close"]))
    assert z_scan == pytest.approx(st["z"].iloc[-1], rel=1e-9)
    an = P.analyze_pair(a, b, "PA", "PB")
    sig = P.generate_pair_signals(a, b, an)
    assert P.backtest_pair(sig, an)["current_zscore"] == pytest.approx(z_scan, rel=1e-12)
    assert isinstance(body["pairs"][0]["has_signal"], bool)


def test_analyze_route_plain_types_fractions_and_none_half_life(client, patch_fetch):
    patch_fetch.overrides["RW1"] = random_walk_ohlc(n=300, seed=14)
    patch_fetch.overrides["RW2"] = random_walk_ohlc(n=300, seed=114)
    r = client.post("/api/pairs/analyze", json={"symbol_a": "RW1", "symbol_b": "RW2", "period_days": 300})
    assert r.status_code == 200
    body = _strict(r.text)
    assert body["half_life"] is None or math.isfinite(body["half_life"])
    for k in ("total_return", "max_drawdown", "total_trades", "win_rate", "sharpe_ratio"):
        assert k in body["backtest"]
    assert abs(body["backtest"]["total_return"]) < 1.0       # a fraction, not a percent
    zs = [d["zscore"] for d in body["spread_data"] if "zscore" in d]
    assert zs and body["current_zscore"] == pytest.approx(zs[-1], abs=1e-5)


def test_analyze_route_insufficient_overlap(client, patch_fetch):
    patch_fetch.overrides["S1"] = random_walk_ohlc(n=100, seed=1)
    patch_fetch.overrides["S2"] = random_walk_ohlc(n=100, seed=2)
    body = client.post("/api/pairs/analyze", json={"symbol_a": "S1", "symbol_b": "S2", "period_days": 100}).json()
    assert "error" in body


# ------------------------------------------------------------------ exits
def _regime_shift_pair(ramp, jump=0.03, n=400, seed=5, shift_at=300):
    """OU spread; at `shift_at` the spread jumps by `jump` then ramps by `ramp` per bar, forever."""
    rng = np.random.default_rng(seed)
    lb = np.log(100) + np.cumsum(rng.normal(0, 0.01, n))
    sp = np.zeros(n)
    phi = 0.9
    for t in range(1, n):
        sp[t] = phi * sp[t - 1] + rng.normal(0, 0.005)
    k = np.arange(n) - shift_at
    sp = sp + np.where(k >= 0, jump + ramp * np.maximum(k, 0), 0.0)
    la = np.log(50) + lb + sp
    idx = _bdays(n)
    return (_ohlc_from_close(np.exp(la), np.random.default_rng(seed + 1), idx),
            _ohlc_from_close(np.exp(lb), np.random.default_rng(seed + 2), idx))


def test_drifting_spread_exits_via_frozen_z_stop_while_rolling_z_stays_below_stop():
    a, b = _regime_shift_pair(ramp=0.0015, jump=0.03)
    sig = P.generate_pair_signals(a, b)
    after = sig.iloc[300:]
    entered = after.index[after["signal"] != 0]
    assert len(entered) > 0, "the jump must trigger an entry"
    stops = after[after["exit_reason"] == "stop_z"]
    assert len(stops) >= 1
    first_exit = stops.index[0]
    held = sig.loc[entered[0]:first_exit]
    assert held["zscore"].abs().max() < config.PAIRS_ZSCORE_STOP          # rolling z never reaches the stop
    assert held["z_frozen"].abs().iloc[-1] > config.PAIRS_ZSCORE_STOP     # the frozen z does
    an = P.analyze_pair(a, b, "A", "B")
    bt = P.backtest_pair(sig, an)
    assert "stop_z" in bt["exit_reasons"] or "time_stop" in bt["exit_reasons"]


# ------------------------------------------------------------------ registry: crypto / executability
def _crypto_pair(n=400, seed=9):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D")           # includes weekends
    lb = np.log(2000) + np.cumsum(rng.normal(0, 0.03, n))
    sp = np.zeros(n)
    for t in range(1, n):
        sp[t] = 0.9 * sp[t - 1] + rng.normal(0, 0.01)
    la = np.log(30000) + 1.1 * (lb - lb[0]) + sp
    return (_ohlc_from_close(np.exp(la), np.random.default_rng(1), idx),
            _ohlc_from_close(np.exp(lb), np.random.default_rng(2), idx))


def test_btc_eth_keeps_weekend_bars_uses_365_and_is_non_executable():
    a, b = _crypto_pair()
    an = P.analyze_pair(a, b, "BTC-USD", "ETH-USD")
    assert an.n_obs == 400 and an.bars_per_year == 365
    assert an.non_executable and "crypto" in an.non_executable_reason
    sig = P.generate_pair_signals(a, b, an)
    assert (sig.index.dayofweek >= 5).any()
    bt = P.backtest_pair(sig, an)
    assert bt["bars_per_year"] == 365 and bt["non_executable"] is True
    # the annualised Sharpe really uses 365
    eq_ret = bt["sharpe_ratio"]
    bt252 = P.backtest_pair(sig, an, bars_per_year=252)
    if bt["sharpe_ratio"] != 0:
        assert bt["sharpe_ratio"] / bt252["sharpe_ratio"] == pytest.approx(math.sqrt(365 / 252), rel=1e-9)


def test_equity_pair_is_executable_label():
    a, b = ou_pair(n=300, seed=7)
    assert not P.analyze_pair(a, b, "AAPL", "MSFT").non_executable
    assert P.analyze_pair(a, b, "AAPL", "EURUSD=X").non_executable


def test_scan_default_pairs_exclude_research_only(monkeypatch):
    import instruments
    assert ("GC=F", "SI=F") not in instruments.default_pairs()
    seen = []
    monkeypatch.setattr(P, "analyze_pair", lambda *a, **k: seen.append(a[2:4]) or (_ for _ in ()).throw(P.InsufficientData("x")))
    data = {s: ou_pair(n=300)[0] for pair in config.PAIRS for s in pair}
    P.scan_all_pairs(data)
    assert ("GC=F", "SI=F") not in seen and ("CL=F", "NG=F") not in seen and seen


# ------------------------------------------------------------------ slow statistical tests
@pytest.mark.slow
def test_ou_half_life_recovered_within_25_percent():
    phi = 0.95
    true_hl = -math.log(2) / math.log(phi)
    errs = []
    for seed in range(20):
        rng = np.random.default_rng(config.SEED + seed)
        n = 1000
        lb = np.log(100) + np.cumsum(rng.normal(0, 0.01, n))
        sp = np.zeros(n)
        for t in range(1, n):
            sp[t] = phi * sp[t - 1] + rng.normal(0, 0.01)
        la = np.log(60) + 1.2 * lb + sp
        hl = P.compute_half_life(pd.Series(la - 1.2 * lb))
        # also through the full-sample cointegrating regression, as analyze_pair does
        idx = _bdays(n)
        an = P.analyze_pair(_ohlc_from_close(np.exp(la), rng, idx), _ohlc_from_close(np.exp(lb), rng, idx), "A", "B")
        assert an.half_life is not None
        errs.append(abs(an.half_life / true_hl - 1))
        assert abs(hl / true_hl - 1) < 0.5
    assert np.median(errs) < 0.25
    assert np.mean(np.array(errs) < 0.25) >= 0.8


@pytest.mark.slow
def test_independent_random_walks_accepted_at_most_five_percent_after_bh():
    data, pairs = {}, []
    for i in range(150):
        rng = np.random.default_rng(config.SEED + i)
        idx = _bdays(300)
        for tag in ("X", "Y"):
            close = np.exp(np.log(100) + np.cumsum(rng.normal(0, 0.01, 300)))
            data[f"{tag}{i}"] = _ohlc_from_close(close, rng, idx)
        pairs.append((f"X{i}", f"Y{i}"))
    res = P.scan_all_pairs(data, pairs)
    assert len(res) / len(pairs) <= 0.05


@pytest.mark.slow
def test_swap_gives_same_is_cointegrated():
    for seed in range(30):
        a, b = ou_pair(n=400, seed=seed) if seed % 2 else (random_walk_ohlc(n=400, seed=seed),
                                                            random_walk_ohlc(n=400, seed=seed + 500))
        x = P.analyze_pair(a, b, "A", "B")
        y = P.analyze_pair(b, a, "B", "A")
        assert x.is_cointegrated == y.is_cointegrated
        assert x.coint_pvalue == pytest.approx(y.coint_pvalue, rel=1e-9)
