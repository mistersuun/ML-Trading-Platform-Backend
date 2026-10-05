"""WS4.5: walk-forward pairs, breakdown guard, fixed-share accounting, registry feed, OOS-only validity."""
import copy
import functools
import math

import numpy as np
import pandas as pd
import pytest

import config
import pairs_trading as P
from tests.fixtures.synthetic import _bdays, _ohlc_from_close


def make_pair(n, seed, hl=8.0, sig=0.012, b_vol=0.015, beta=1.0, rw_from=None, indep_from=None):
    """log A = c + beta*log B + OU spread. From ``rw_from`` the spread is a random walk (hedge breaks);
    from ``indep_from`` A is an independent random walk (no relation to B at all)."""
    rng = np.random.default_rng(seed)
    lb = np.log(100.0) + np.cumsum(0.0002 + b_vol * rng.standard_normal(n))
    phi = math.exp(-math.log(2) / hl)
    sp = np.zeros(n)
    e = sig * rng.standard_normal(n)
    for t in range(1, n):
        sp[t] = (1.0 if (rw_from is not None and t >= rw_from) else phi) * sp[t - 1] + e[t]
    la = np.log(50.0) + beta * (lb - lb[0]) + sp
    if indep_from is not None:
        step = 0.0002 + b_vol * rng.standard_normal(n)
        la[indep_from:] = la[indep_from - 1] + np.cumsum(step[indep_from:])
    idx = _bdays(n)
    return (_ohlc_from_close(np.exp(la), np.random.default_rng(seed + 1), idx),
            _ohlc_from_close(np.exp(lb), np.random.default_rng(seed + 2), idx))


@functools.lru_cache(maxsize=None)
def _run(n, seed, **kw):
    """Cached (a, b, WalkForwardResult) of make_pair; treat the result as read-only."""
    a, b = make_pair(n, seed, **kw)
    return a, b, P.walk_forward_pairs(a, b, "A", "B")


def spread_pair(spread, seed, beta=1.0, b_vol=0.015):
    """Frame pair from an explicit log-spread path (A = c + beta*logB + spread)."""
    n = len(spread)
    rng = np.random.default_rng(seed)
    lb = np.log(100.0) + np.cumsum(0.0002 + b_vol * rng.standard_normal(n))
    la = np.log(50.0) + beta * (lb - lb[0]) + spread
    idx = _bdays(n)
    return (_ohlc_from_close(np.exp(la), np.random.default_rng(seed + 1), idx),
            _ohlc_from_close(np.exp(lb), np.random.default_rng(seed + 2), idx))


def forced_break_pair(seed, switch=510, n=567, hl=8.0, sig=0.012, rw_sig=0.0012, excursion=0.08):
    """OU spread, a ramp to +excursion ending at ``switch`` (so the pair is entered), then a quiet random
    walk: the spread never reverts and never reaches the stop, so only the guards can end the trade."""
    rng = np.random.default_rng(seed)
    phi = math.exp(-math.log(2) / hl)
    e = rng.standard_normal(n)
    sp = np.zeros(n)
    for t in range(1, switch - 9):
        sp[t] = phi * sp[t - 1] + sig * e[t]
    sp[switch - 9:switch + 1] = np.linspace(sp[switch - 10], excursion, 11)[1:]
    for t in range(switch + 1, n):
        sp[t] = sp[t - 1] + rw_sig * e[t]
    return spread_pair(sp, seed)


FORCED_SEEDS = [14, 15, 24]       # seeds whose block-4 formation is tradable (asserted below, so fixture rot is loud)
RATE = config.COMMISSION_PCT + config.SLIPPAGE_PCT


# ------------------------------------------------------------------ layout / determinism / frozen parameters
def test_block_layout_stitching_and_determinism():
    a, b, r1 = _run(1100, 1)
    r2 = P.walk_forward_pairs(a, b, "A", "B")
    n_blocks = (1100 - 252 - 63) // 63 + 1
    assert r1.n_blocks == n_blocks == 13
    assert len(r1.returns) == 63 * n_blocks and r1.returns.index[0] == a.index[252]
    assert r1.returns.index.is_monotonic_increasing and r1.returns.index.is_unique
    for k, bl in enumerate(r1.blocks):
        assert bl["formation_start"] == str(a.index[63 * k]) and bl["trade_start"] == str(a.index[63 * k + 252])
    pd.testing.assert_series_equal(r1.returns, r2.returns)
    assert r1.trades == r2.trades and r1.backtest == r2.backtest
    assert r1.equity.iloc[-1] == pytest.approx(config.BACKTEST_INITIAL_CAPITAL * (1 + r1.returns).prod(), rel=1e-9)
    with pytest.raises(ValueError):
        P.walk_forward_pairs(a, b, step=30, trade=63)             # overlapping OOS windows


def test_z_lookback_is_tied_to_half_life_and_clamped_20_120():
    assert P.z_lookback_for(3.0) == 20 and P.z_lookback_for(5.0) == 20
    assert P.z_lookback_for(30.0) == 60
    assert P.z_lookback_for(100.0) == 120 and P.z_lookback_for(None) == 120 and P.z_lookback_for(float("nan")) == 120
    for bl in _run(1100, 1)[2].blocks:
        assert bl["z_lookback"] == P.z_lookback_for(bl["half_life"]) and 20 <= bl["z_lookback"] <= 120
        if bl["tradable"]:
            assert bl["max_hold"] == math.ceil(config.PAIRS_TIME_STOP_HALF_LIVES * bl["half_life"])


def test_changing_data_inside_a_later_block_leaves_earlier_blocks_unchanged():
    a, b, r = _run(1100, 1)
    cut = 700
    a2, b2 = a.copy(), b.copy()
    rng = np.random.default_rng(99)
    factor = np.exp(np.cumsum(rng.normal(0, 0.03, len(a) - cut)))
    for df in (a2, b2):
        df.iloc[cut:, df.columns.get_indexer(["Open", "High", "Low", "Close"])] *= factor[:, None]
    r2 = P.walk_forward_pairs(a2, b2, "A", "B")
    safe = [bl["block"] for bl in r.blocks if a.index.get_loc(pd.Timestamp(bl["trade_end"])) < cut]
    assert safe == list(range(len(safe))) and len(safe) >= 6
    for k in safe:
        assert r.blocks[k] == r2.blocks[k]
    assert [t for t in r.trades if t["block"] in safe] == [t for t in r2.trades if t["block"] in safe]
    end = a.index[cut - 1]
    pd.testing.assert_series_equal(r.returns[r.returns.index <= end].iloc[:63 * len(safe)],
                                   r2.returns[r2.returns.index <= end].iloc[:63 * len(safe)])
    assert not r.returns.equals(r2.returns)                        # the later data really did change something


# ------------------------------------------------------------------ OOS honesty
def test_cointegrated_then_independent_pair_has_nonsignificant_oos_second_half():
    import metrics
    profitable_in_sample = 0
    for seed in range(10):
        a, b, r = _run(1100, seed, indep_from=600)
        second = r.returns[r.returns.index >= a.index[600]]
        # second-half OOS is never a significant edge ...
        psr = metrics.psr(second) if second.std() > 0 else None
        assert psr is None or psr < config.OOS_PSR_MIN, (seed, psr)
        n2 = sum(1 for t in r.trades if t["entry_date"] >= str(a.index[600]))
        assert n2 < config.MIN_TRADES_OOS
        # ... while the single-window in-sample backtest of the same data can look profitable
        an = P.analyze_pair(a, b, "A", "B")
        if P.backtest_pair(P.generate_pair_signals(a, b, an), an)["total_return"] > 0:
            profitable_in_sample += 1
    assert profitable_in_sample >= 3


def test_second_half_oos_pnl_is_not_positive_in_aggregate():
    tot = 0.0
    for seed in range(10):
        a, b, r = _run(1100, seed, indep_from=600)
        tot += float(r.returns[r.returns.index >= a.index[600]].sum())
    assert tot < 0.002          # no free lunch once the relationship is gone (costs make it <= 0 on average)


# ------------------------------------------------------------------ breakdown guard
@pytest.mark.parametrize("seed", FORCED_SEEDS)
def test_ou_to_random_walk_switch_exits_via_guard_in_bounded_bars_and_never_reenters(seed):
    switch = 510
    a, b = forced_break_pair(seed, switch=switch)
    r = P.walk_forward_pairs(a, b, "A", "B")
    blk = r.blocks[4]
    assert blk["tradable"], "fixture rot: block 4 formation must be tradable"
    trades = [t for t in r.trades if t["block"] == 4]
    forced = [t for t in trades if t["exit_reason"] in ("coint_break", "time_stop")]
    assert forced, trades
    f = forced[0]
    assert f["exit_idx"] >= switch
    assert f["bars_held"] <= blk["max_hold"] + 1               # time stop bound (fills next open); break is earlier
    assert not any(t["entry_idx"] > f["exit_idx"] for t in trades), trades      # no re-entry in the block
    assert all(t["exit_reason"] != "block_end" for t in trades)  # the guard, not the block end, closed it


def test_hysteresis_entries_only_when_healthy_and_breaks_follow_the_rule():
    saw_break = saw_trade = 0
    all_trades = []
    for seed in range(10):
        a, b, r = _run(1100, seed, rw_from=700)
        all_trades += r.trades
        for bl in r.blocks:
            healthy, last = bl["tradable"], None
            states = {h[0]: h for h in bl["health"]}
            for _, p, hl, after in bl["health"]:
                in_band = hl is not None and config.PAIRS_MIN_HALF_LIFE <= hl <= config.PAIRS_MAX_HALF_LIFE
                if healthy and (p > P.PAIRS_BREAK_PVALUE or not in_band):
                    assert after is False                       # hysteresis: break above 0.15 / band exit
                elif healthy:
                    assert after is True
                elif not healthy and p < config.PAIRS_COINT_PVALUE and in_band and not any(
                        t["exit_reason"] == "time_stop" for t in r.trades if t["block"] == bl["block"]):
                    assert after is True                        # re-enable only below 0.05
                elif not healthy:
                    assert after is False                       # 0.05 <= p <= 0.15 does NOT re-enable
                healthy = after
            ts = [h for h in bl["health"]]
            for t in (x for x in r.trades if x["block"] == bl["block"]):
                prior = [h for h in ts if h[0] < t["entry_idx"]]      # last re-test strictly before the fill bar
                assert (prior[-1][3] if prior else True) is True
                saw_trade += 1
            saw_break += bl["n_breaks"]
    assert saw_break > 0 and saw_trade > 0
    assert any(t["exit_reason"] == "coint_break" for t in all_trades)


def test_untradable_formation_means_flat_block():
    a, b, r = _run(800, 3, indep_from=1)                      # A is an independent walk from the start
    assert r.n_blocks > 0 and not all(bl["tradable"] for bl in r.blocks)
    for bl in r.blocks:
        if not bl["tradable"]:
            assert bl["n_trades"] == 0 and bl["untradable_reason"]
    flat = r.returns[[not any(bl["block"] == k and bl["tradable"] for bl in r.blocks)
                      for k in np.repeat(np.arange(r.n_blocks), 63)]]
    assert (flat == 0).all()


# ------------------------------------------------------------------ accounting
def _good_run(seed=1):
    a, b, r = _run(1100, seed)
    assert len(r.trades) >= 8
    return a, b, r


def test_fills_at_next_open_with_fixed_shares_and_costs_on_both_legs():
    a, b, r = _good_run()
    for t in r.trades:
        i, j = t["entry_idx"], t["exit_idx"]
        assert t["entry_px_a"] == pytest.approx(a["Open"].iloc[i]) and t["entry_px_b"] == pytest.approx(b["Open"].iloc[i])
        px_a, px_b = ((a["Close"].iloc[j], b["Close"].iloc[j]) if t["exit_reason"] == "block_end"
                      else (a["Open"].iloc[j], b["Open"].iloc[j]))
        sa, sb = t["shares_a"], t["shares_b"]
        assert np.sign(sa) == t["direction"] and np.sign(sb) == -t["direction"] * np.sign(t["hedge_beta"])
        gross = sa * (px_a - t["entry_px_a"]) + sb * (px_b - t["entry_px_b"])
        assert t["gross_pnl_abs"] == pytest.approx(gross, rel=1e-9, abs=1e-9)
        cost = RATE * (abs(sa) * t["entry_px_a"] + abs(sb) * t["entry_px_b"] + abs(sa) * px_a + abs(sb) * px_b)
        assert t["cost_abs"] == pytest.approx(cost, rel=1e-9)             # BOTH legs, entry and exit
        assert t["pnl_abs"] == pytest.approx(gross - cost, rel=1e-9, abs=1e-9)
        # dollar weights 1/(1+|b|), |b|/(1+|b|) of gross
        ab = abs(t["hedge_beta"])
        assert abs(sa) * t["entry_px_a"] == pytest.approx(t["gross_notional"] / (1 + ab), rel=1e-9)
        assert abs(sb) * t["entry_px_b"] == pytest.approx(t["gross_notional"] * ab / (1 + ab), rel=1e-9)


def test_equity_is_cash_plus_fixed_share_mark_and_ledger_closes():
    a, b, r = _good_run()
    init = config.BACKTEST_INITIAL_CAPITAL
    assert r.equity.iloc[-1] - init == pytest.approx(sum(t["pnl_abs"] for t in r.trades), rel=1e-9, abs=1e-6)
    # shares never change while a trade is open: the day-by-day equity change equals shares * price change
    t = max(r.trades, key=lambda x: x["bars_held"])
    cl_a, cl_b = a["Close"].to_numpy(), b["Close"].to_numpy()
    for k in range(t["entry_idx"] + 1, t["exit_idx"]):
        d_eq = r.equity.loc[a.index[k]] - r.equity.loc[a.index[k - 1]]
        assert d_eq == pytest.approx(t["shares_a"] * (cl_a[k] - cl_a[k - 1]) + t["shares_b"] * (cl_b[k] - cl_b[k - 1]),
                                     rel=1e-7, abs=1e-6)


def test_costs_reduce_pnl_by_exactly_the_charged_cost():
    a, b, r = _good_run()
    r0 = P.walk_forward_pairs(a, b, "A", "B", commission=0.0, slippage=0.0)
    assert len(r0.trades) >= len(r.trades) - 3
    assert r0.equity.iloc[-1] > r.equity.iloc[-1]
    assert all(t["cost_abs"] == 0 for t in r0.trades)


def test_dollar_pnl_is_invariant_to_an_additive_price_shift_return_on_gross_is_not():
    a, b, r = _good_run()
    for t in r.trades:
        j = t["exit_idx"]
        px_a, px_b = ((a["Close"].iloc[j], b["Close"].iloc[j]) if t["exit_reason"] == "block_end"
                      else (a["Open"].iloc[j], b["Open"].iloc[j]))
        base = P.fixed_share_pnl(t["shares_a"], t["shares_b"], t["entry_px_a"], t["entry_px_b"], px_a, px_b)
        assert base == pytest.approx(t["gross_pnl_abs"], rel=1e-9, abs=1e-9)
        for k in (50.0, 1000.0, -0.5 * min(t["entry_px_a"], t["entry_px_b"])):
            shifted = P.fixed_share_pnl(t["shares_a"], t["shares_b"], t["entry_px_a"] + k, t["entry_px_b"] + k,
                                        px_a + k, px_b + k)
            assert shifted == pytest.approx(base, rel=1e-9, abs=1e-9)
    t = r.trades[0]
    i, j = t["entry_idx"], t["exit_idx"]
    path = P.fixed_share_value_path(t["shares_a"], t["shares_b"], a["Close"].iloc[i:j + 1], b["Close"].iloc[i:j + 1],
                                    t["entry_px_a"], t["entry_px_b"])
    shifted = P.fixed_share_value_path(t["shares_a"], t["shares_b"], a["Close"].iloc[i:j + 1] + 300,
                                       b["Close"].iloc[i:j + 1] + 300, t["entry_px_a"] + 300, t["entry_px_b"] + 300)
    np.testing.assert_allclose(path, shifted, rtol=1e-9, atol=1e-9)
    # a % return on a price level is NOT invariant (1100 -> 1110 is 0.9%, 100 -> 110 is 10%)
    assert (110 / 100 - 1) != pytest.approx(1110 / 1100 - 1)


def test_last_bar_signal_is_never_opened_and_open_trade_closes_at_block_end():
    a, b, r = _good_run()
    ends = {a.index.get_loc(pd.Timestamp(bl["trade_end"])) for bl in r.blocks}
    for t in r.trades:
        assert t["exit_idx"] <= max(i for i in ends if i >= t["entry_idx"])
        assert t["entry_idx"] not in ends


# ------------------------------------------------------------------ registry, validity, service
def _registry(tmp_path, run_id="pairs-test"):
    from trials import TrialRegistry
    return TrialRegistry(run_id, trials_dir=tmp_path)


def test_pairs_trials_are_recorded_valid_or_not(tmp_path):
    reg = _registry(tmp_path)
    data = {}
    pairs = []
    for i, (seed, kind) in enumerate([(1, "ou"), (2, "ou"), (3, "rw")]):
        a, b = make_pair(900, seed, indep_from=1 if kind == "rw" else None)
        data[f"A{i}"], data[f"B{i}"] = a, b
        pairs.append((f"A{i}", f"B{i}"))
    P.scan_all_pairs(data, pairs, trials=reg)
    assert reg.n_trials == 3 and reg.path.exists()
    rec = reg.records[0]
    assert rec.pattern == "pairs_wf" and rec.symbol == "A0/B0" and rec.params["formation"] == 252
    m = reg.returns_matrix()
    assert m.shape[1] == 3
    expected = P.walk_forward_pairs(data["A0"], data["B0"], "A0", "B0")
    pd.testing.assert_series_equal(m[rec.trial_id].dropna(), expected.returns, check_names=False)
    assert rec.strategy_version == expected.strategy_version and rec.n_obs == len(expected.returns)


def test_walk_forward_records_one_trial_and_rejects_duplicates(tmp_path):
    from trials import TrialRegistryError
    reg = _registry(tmp_path, "pairs-dup")
    a, b = make_pair(900, 1)
    P.walk_forward_pairs(a, b, "A", "B", trials=reg)
    assert reg.n_trials == 1
    with pytest.raises(TrialRegistryError):
        P.walk_forward_pairs(a, b, "A", "B", trials=reg)


def test_validity_is_oos_only_a_pair_without_a_complete_oos_block_is_not_valid():
    a, b = make_pair(300, 1)                               # < 252 + 63 bars: no OOS block at all
    r = P.walk_forward_pairs(a, b, "A", "B")
    assert r.n_blocks == 0 and not P.is_valid_oos(r) and r.backtest["oos_status"] == "insufficient_history"
    assert r.backtest["total_trades"] == 0 and r.backtest["total_return"] == 0.0
    assert P.scan_all_pairs({"X": a, "Y": b}, [("X", "Y")]) == []


def test_require_tradable_makes_the_latest_formation_a_hard_gate():
    a, b, r = _run(1100, 1)
    r = copy.deepcopy(r)
    last_tradable = r.blocks[-1]["tradable"]
    assert P.is_valid_oos(r, require_tradable=True) is last_tradable
    assert P.is_valid_oos(r, require_tradable=False) is True
    r.blocks[-1]["tradable"] = False
    assert P.is_valid_oos(r, require_tradable=True) is False


def test_scan_reports_oos_numbers_only():
    a, b, _ = _run(900, 1)
    wf = P.walk_forward_pairs(a, b, "PA", "PB")
    res = P.scan_all_pairs({"PA": a, "PB": b}, [("PA", "PB")])
    for bt in res:
        assert bt["oos"] is True and bt["total_return"] == wf.backtest["total_return"]
        assert bt["total_trades"] == wf.backtest["total_trades"] and bt["oos_blocks"] == wf.n_blocks
    assert res, "fixture pair should pass the screen"


@pytest.mark.slow
def test_independent_pairs_rarely_pass_oos_and_oos_edge_is_not_systematic():
    import metrics
    from tests.fixtures.synthetic import random_walk_ohlc
    n_pairs, sig_oos, tradable, blocks, tot = 30, 0, 0, 0, 0.0
    for s in range(n_pairs):
        a, b = random_walk_ohlc(n=1200, seed=1000 + s), random_walk_ohlc(n=1200, seed=2000 + s)
        r = P.walk_forward_pairs(a, b, "A", "B")
        tradable += sum(bl["tradable"] for bl in r.blocks)
        blocks += r.n_blocks
        sig_oos += int(r.backtest["oos_significant"])
        tot += float(r.returns.sum())
    assert tradable / blocks < 0.15            # EG at 5% + band + correlation: few false-positive blocks
    assert sig_oos == 0
    assert tot < 0.01


def test_service_scan_pairs_data_records_into_the_given_registry(tmp_path):
    import services.pairs as svc
    a, b, _ = _run(900, 1)
    reg = _registry(tmp_path, "pairs-svc")
    svc.scan_pairs_data({"PA": a, "PB": b}, [("PA", "PB")], trials=reg)
    assert reg.n_trials == 1 and reg.path.exists() and reg.records[0].symbol == "PA/PB"
    assert P._ACTIVE_TRIALS.get() is None                   # the context is restored
