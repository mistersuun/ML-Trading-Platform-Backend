"""One scanner candidate in detail (GET /api/scanner/candidate): the executable variant of one (symbol, pattern)
validated like the nightly scan (D6, D11, D12), with the series and gate measurements the Scanner page draws.

A single candidate is its own multiple-testing family here, so its Benjamini-Hochberg q is NOT the scan's: the
BH gate reports the q the latest nightly run stored for this candidate, or says it was not in that scan."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import backtester
import config
import engine
import validation
from api.errors import InsufficientHistory
from patterns import PATTERN_REGISTRY
from results import store
from services import models as M
from services.common import cut_holdout, finite, require_data
from services.providers import DataProvider

CHART_BARS = 120
IS_METHOD = ("The same rule backtested over the whole past before the hold-out with percentage stop and target "
             "(the scan's in-sample view, ignored for decisions), restarted at the first out-of-sample date.")


class _ReadOnlyHoldout:
    """First-read hold-out store that a GET can use: returns frozen reads, never writes one."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, version, key):
        return self._inner.get(version, key)

    def put(self, version, key, payload):
        return False

    def versions_read(self, prefix):
        return self._inner.versions_read(prefix)

    def close(self):
        self._inner.close()


def _holdout_store():
    try:
        from state.holdout import HoldoutStore, default_path
        p = default_path()
        if p == ":memory:" or not Path(p).is_file():
            return None
        return _ReadOnlyHoldout(HoldoutStore(p))
    except Exception:
        return None


def _curve(returns: pd.Series) -> list[M.CurvePoint]:
    if returns is None or len(returns) == 0:
        return []
    cum = np.cumprod(1.0 + returns.to_numpy(dtype=float)) - 1.0
    return [M.CurvePoint(date=d.date().isoformat(), value=float(v)) for d, v in zip(returns.index, cum)
            if np.isfinite(v)]


def _nightly(symbol: str, pattern: str) -> M.NightlyRef:
    funnel = store.read_latest("funnel")
    tested = pbo = n_trials = None
    if funnel and isinstance(funnel[0], dict):
        tested = funnel[0].get("tested")
        pbo, n_trials = finite(funnel[0].get("pbo")), funnel[0].get("n_trials")
    got = store.read_latest("technical")
    if got is None:
        return M.NightlyRef(in_last_scan=False, tested=tested, n_trials=n_trials, pbo=pbo,
                        label="not in last scan (no nightly result yet)")
    payload, ts = got
    for row in payload if isinstance(payload, list) else []:
        if isinstance(row, dict) and row.get("symbol") == symbol and row.get("pattern") == pattern:
            q = finite(row.get("bh_adjusted_p"))
            return M.NightlyRef(in_last_scan=q is not None, generated_at=ts.isoformat(), bh_adjusted_p=q,
                                validation_status=row.get("validation_status"),
                                dsr_p=finite(row.get("dsr_p")), n_trials=n_trials, pbo=pbo,
                                tested=tested if tested is not None else row.get("n_trials"),
                                label="from the latest nightly scan" if q is not None else
                                "no stored q (only signalling candidates are kept)")
    return M.NightlyRef(in_last_scan=False, generated_at=ts.isoformat(), tested=tested, n_trials=n_trials,
                        pbo=pbo, label="no stored q (only signalling candidates are kept)")


def _dsr_gate(dsr_p, n_trials, note) -> M.Gate:
    """Deflated Sharpe: 1 - DSR must be below DSR_P_MAX, with N = every trial of the run (valid or not)."""
    name = f"Deflated Sharpe (p < {config.DSR_P_MAX:g})"
    n_txt = f"N = {n_trials} trials" if n_trials else "N unknown"
    return M.Gate(key="dsr", name=name, value=finite(dsr_p), threshold=config.DSR_P_MAX, comparator="<",
                  unit="probability", gating=True,
                  status="unavailable" if dsr_p is None else ("pass" if dsr_p < config.DSR_P_MAX else "fail"),
                  note=f"{n_txt}; {note}" if note else n_txt)


def _deflate(nightly: M.NightlyRef, oos_returns) -> tuple:
    """(dsr_p, n_trials, pbo, note): the nightly row's own DSR when it is in the last scan, else this candidate's
    OOS series deflated with the latest run's N and cross-trial Sharpe variance; else unavailable."""
    pbo, n = nightly.pbo, nightly.n_trials
    if nightly.in_last_scan and nightly.dsr_p is not None:
        return nightly.dsr_p, n, pbo, "from the latest nightly scan"
    funnel = store.read_latest("funnel")
    var = finite(funnel[0].get("sharpe_var")) if funnel and isinstance(funnel[0], dict) else None
    if n and var is not None and oos_returns is not None and len(oos_returns) > 2:
        from stats import selection
        dsr = selection.dsr_from_returns(np.asarray(oos_returns, dtype=float), int(n), var)
        if dsr is not None and np.isfinite(dsr):
            return float(1.0 - dsr), n, pbo, "this candidate deflated with the latest nightly run's N and Sharpe variance"
    return None, n, pbo, "no stored nightly run to take N from"


def _gates(cr, nightly: M.NightlyRef, dsr_p=None, n_trials=None, pbo=None, dsr_note=None) -> list[M.Gate]:
    thr_trades = float(config.MIN_TRADES_OOS)
    ho = (cr.holdout or {}).get("total_return") if cr.holdout_frozen else None
    g = [
        M.Gate(key="oos_trades", name=f"Out-of-sample trades >= {config.MIN_TRADES_OOS}", value=float(cr.n_oos_trades),
               threshold=thr_trades, comparator=">=", unit="count", gating=True,
               status="pass" if cr.n_oos_trades >= thr_trades else "fail"),
        M.Gate(key="psr", name=f"PSR above {config.OOS_PSR_MIN:g}", value=cr.oos_psr, threshold=config.OOS_PSR_MIN,
               comparator=">", unit="probability", gating=True,
               status="pass" if cr.oos_psr is not None and cr.oos_psr > config.OOS_PSR_MIN else "fail"),
    ]
    null_note = f"this candidate alone: random-entry p = {cr.null_p:.3f} (single-candidate family)"
    if nightly.in_last_scan and nightly.bh_adjusted_p is not None:
        g.append(M.Gate(key="bh", name=f"Beats random entry (q <= {config.FDR_ALPHA:g})", value=nightly.bh_adjusted_p,
                        threshold=config.FDR_ALPHA, comparator="<=", unit="probability", gating=True,
                        status="pass" if nightly.bh_adjusted_p <= config.FDR_ALPHA else "fail",
                        note=f"q from the latest nightly scan over {nightly.tested or '?'} ideas; {null_note}"))
    else:
        g.append(M.Gate(key="bh", name=f"Beats random entry (q <= {config.FDR_ALPHA:g})", value=None,
                        threshold=config.FDR_ALPHA, comparator="<=", unit="probability", gating=True,
                        status="unavailable", note=f"{nightly.label}; {null_note}"))
    g.append(_dsr_gate(dsr_p, n_trials, dsr_note))
    g.append(M.Gate(key="pbo", name="Probability of backtest overfitting (advisory)", value=finite(pbo),
                    threshold=None, comparator="<=", unit="probability", gating=False,
                    status="recorded" if pbo is not None else "unavailable",
                    note="advisory: CSCV over the whole run's trials, reported and never gated"))
    g += [
        M.Gate(key="holdout", name="Hold-out return >= 0", value=finite(ho), threshold=0.0, comparator=">=",
               unit="fraction", gating=True,
               status="pass" if ho is not None and ho >= 0 else ("fail" if cr.holdout_frozen else "unavailable"),
               note=None if ho is not None else ("hold-out unavailable (fails closed)" if cr.holdout_frozen else
                    "hold-out not read yet; the nightly scan reads it once")),
        M.Gate(key="cost", name=f"Positive at {config.COST_STRESS_MULT:g}x costs", value=finite(cr.cost_return),
               threshold=0.0, comparator=">", unit="fraction", gating=True,
               status="pass" if cr.cost_ok else "fail"),
        M.Gate(key="delay", name="+1 bar delay", value=finite(cr.delay_return), threshold=None, comparator=">",
               unit="fraction", gating=False, status="recorded", note="recorded, not gated (D11)"),
    ]
    return g


def _nightly_sharpe_var():
    from services.scan import _nightly_sharpe_var as f
    return f()


def candidate(provider: DataProvider, symbol: str, pattern: str) -> M.CandidateResponse:
    df = require_data(provider.ohlcv(symbol, config.RESEARCH_LOOKBACK_DAYS), symbol)
    fn = PATTERN_REGISTRY[pattern]
    holdout_store = _holdout_store()
    try:
        results = validation.evaluate_candidates({symbol: df}, {pattern: fn}, executable_variant=True,
                                                 holdout_store=holdout_store,
                                                 min_trials=validation.universe_trials(),
                                                 sharpe_var_floor=_nightly_sharpe_var())
    finally:
        if holdout_store is not None:
            holdout_store.close()
    if not results:
        raise InsufficientHistory(f"{pattern} produced no signals for {symbol}", {"symbol": symbol})
    cr = results[0]
    if "insufficient_history" in cr.rejected_reasons:
        raise InsufficientHistory(
            f"{symbol} has {len(df)} bars; the walk-forward needs {config.WF_TRAIN_BARS + config.WF_TEST_BARS} "
            f"before {config.HOLDOUT_START}", {"symbol": symbol, "bars": len(df)})

    # the walk-forward again (deterministic) for the OOS series and trades that CandidateResult does not carry
    sigs = validation.compute_signals(df, {pattern: fn})
    cand = validation.executable_candidates([pattern])[0]
    wf = validation.walk_forward(df, [cand], signals=sigs)
    oos_curve = _curve(wf.oos_returns)
    trade_pnls = [float(t.pnl_pct) for t in wf.oos_trades if np.isfinite(t.pnl_pct)]
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p < 0]

    # in-sample view: legacy percentage-exit backtest on bars before the hold-out
    is_curve: list[M.CurvePoint] = []
    try:
        pre = cut_holdout(df)
        bt = backtester.classic_backtest(fn(pre), symbol, pattern)
        eq = bt.equity_curve
        if oos_curve and eq is not None and len(eq):
            start = pd.Timestamp(oos_curve[0].date)
            idx = pd.DatetimeIndex(eq.index)
            eq = eq[(idx.tz_localize(None) if idx.tz is not None else idx) >= start]
            if len(eq) > 1:
                end = pd.Timestamp(oos_curve[-1].date)
                idx2 = pd.DatetimeIndex(eq.index)
                eq = eq[(idx2.tz_localize(None) if idx2.tz is not None else idx2) <= end]
                if len(eq) > 1:
                    rel = eq / float(eq.iloc[0]) - 1.0
                    is_curve = [M.CurvePoint(date=d.date().isoformat(), value=float(v))
                                for d, v in rel.items() if np.isfinite(v)]
    except Exception:
        is_curve = []

    # chart: last bars and the executable variant's trades on the full history
    run = engine.run_backtest(df, signal=sigs[pattern], stop_loss=None, take_profit=None,
                              commission=config.COMMISSION_PCT, slippage=config.SLIPPAGE_PCT, allow_short=False,
                              atr_stop_mult=cand.stop_atr, atr_target_mult=cand.take_atr)
    tail = df.iloc[-CHART_BARS:]
    first = tail.index[0]
    hold_ts = pd.Timestamp(config.HOLDOUT_START)
    if getattr(df.index, "tz", None) is not None and hold_ts.tzinfo is None:
        hold_ts = hold_ts.tz_localize(df.index.tz)
    frozen = bool(cr.holdout_frozen)
    # D11: until the hold-out is read and frozen, its sign must not leak through the reasons list.
    rejected = list(cr.rejected_reasons)
    if not frozen:
        rejected = [r for r in rejected if r != "holdout_negative"] + ["holdout_not_read"]
    markers = []
    for t in run.trades:
        exit_in = t.exit_date is not None and t.exit_date >= first
        if t.entry_date >= first or exit_in:
            hidden = bool(t.entry_date >= hold_ts) and not frozen
            markers.append(M.TradeMarker(
                entry_date=t.entry_date.isoformat(), entry_price=float(t.entry_price),
                exit_date=t.exit_date.isoformat() if (t.exit_date is not None and not hidden) else None,
                exit_price=finite(t.exit_price) if (t.exit_date is not None and not hidden) else None,
                pnl_pct=None if hidden else finite(t.pnl_pct), exit_reason=None if hidden else (t.exit_reason or None), in_holdout=bool(t.entry_date >= hold_ts)))
    bars = [M.BarOut(date=i.isoformat(), open=float(r.Open), high=float(r.High), low=float(r.Low),
                     close=float(r.Close)) for i, r in tail.iterrows()]

    last, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2]) if len(df) > 1 else None
    nightly = _nightly(symbol, pattern)
    dsr_p, n_trials, pbo, dsr_note = _deflate(nightly, wf.oos_returns)
    gates = _gates(cr, nightly, dsr_p, n_trials, pbo, dsr_note)
    failed = sum(1 for g in gates if g.gating and g.status != "pass")
    o = cr.oos or {}
    note = ("Out-of-sample = walk-forward test windows before the hold-out; the hold-out is read once per strategy "
            "version (D11). Executable variant: long-only, ATR stop and target from the signal close (D12).")
    return M.CandidateResponse(
        symbol=symbol, pattern=pattern,
        variant=f"long-only, stop {config.ATR_STOP_MULT:g}x ATR, target {cand.take_atr:g}x ATR",
        last_price=last, day_change=finite(last - prev) if prev else None,
        day_change_pct=finite(last / prev - 1.0) if prev else None, as_of=df.index[-1].date().isoformat(),
        holdout_start=config.HOLDOUT_START, holdout_frozen=bool(cr.holdout_frozen),
        oos_curve=oos_curve, in_sample_curve=is_curve, in_sample_method=IS_METHOD,
        oos_return=finite(o.get("total_return")), in_sample_return=is_curve[-1].value if is_curve else None,
        holdout_return=finite((cr.holdout or {}).get("total_return")) if frozen else None, oos_trade_returns=trade_pnls,
        n_oos_trades=cr.n_oos_trades, win_rate=finite(o.get("win_rate")),
        win_rate_ci_low=finite(o.get("win_rate_ci_low")), win_rate_ci_high=finite(o.get("win_rate_ci_high")),
        avg_win=float(np.mean(wins)) if wins else None, avg_loss=float(np.mean(losses)) if losses else None,
        gates=gates, gates_failed=failed, verdict="validated" if failed == 0 else "alert_only", nightly=nightly,
        bars=bars, trades=markers, rejected_reasons=rejected, note=note)
