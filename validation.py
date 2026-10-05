"""Out-of-sample validation (WS2.3): walk-forward, fixed-date hold-out, random-entry null,
cost / delay stress, Benjamini-Hochberg FDR and ``validation_status``.

Conventions
-----------
* Signals are computed ONCE on the full contiguous series (warm-up included; patterns are causal) and
  then sliced. A fold therefore never loses indicator warm-up, and its choice only depends on data
  before the fold's test window.
* A fold = train window ``[s, s+train)`` + test window ``[s+train, s+train+test)``; folds advance by
  ``step`` (must be >= ``test`` so OOS windows never overlap). Only bars strictly before ``end``
  (``config.HOLDOUT_START``) are used; the hold-out is evaluated separately and never selects anything.
* With several candidates the fold's candidate is the one with the best TRAIN-window Sharpe (rf=0, daily
  mark-to-market returns of the actual position size, see metrics.py); the chosen candidate is then traded
  from the train start through the test window with the position carried, and only the test-window
  returns are kept. With ONE candidate (no selection) a single continuous run is used.
* Random-entry null: each real OOS trade is re-drawn ``draws`` times with a uniformly random entry bar
  inside its own test window and the same direction, stop/target rules, costs and holding period (the real
  holding period for signal/end-of-data exits; for stop/target exits the median signal-exit holding period,
  at least the real one). Statistic = mean net ``pnl_pct`` per trade; p = (1 + #{null >= observed}) / (1 + draws).
* ``evaluate_candidates`` tests every (symbol x pattern x stop/target) combination as a FIXED candidate,
  applies Benjamini-Hochberg at ``config.FDR_ALPHA`` over the null p-values of ALL of them, and sets
  ``validation_status``. ``n_trials`` is recorded for deflated Sharpe (advisory value reported too).
* Executable variant (``evaluate_candidates(executable_variant=True)``): what execution can actually trade
  (D3/D5/D10) is LONG-ONLY (a -1 signal is an exit) with ATR exits (stop ``ATR_STOP_MULT`` x ATR, target
  ``TAKE_PROFIT_ATR_MULT`` x ATR, levels anchored on the signal bar's close, as execution submits them).
  That is the variant that gets validated and whose status goes on the intent.
* The random-entry null is sized to the correction: with m candidates BH needs p <= alpha/m for the best
  one, so a candidate whose p is <= alpha at the base number of draws is re-drawn with 10x more draws until
  1/(draws+1) < alpha/m (or ``MAX_NULL_DRAWS``). The null p-value can therefore resolve a real signal at scan size.
* Hold-out: ``holdout_store`` persists the FIRST hold-out evaluation per (strategy version, candidate); later
  scans gate on that stored result, so the verdict does not flip as the hold-out grows.
* Everything is seeded from ``config.SEED`` plus a stable CRC32 of the candidate key (never ``hash()``).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import zlib
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

import config
import engine
import metrics

logger = logging.getLogger(__name__)

OOS_VALIDATED = "oos_validated"
UNVALIDATED = "unvalidated"
DEFAULT_GRID = ((config.STOP_LOSS_PCT, config.TAKE_PROFIT_PCT),)
_FALLBACK_HOLD = 10
MAX_NULL_DRAWS = 200_000
_NULL_BLOCK = 5000


# ------------------------------------------------------------------ data classes
@dataclass(frozen=True)
class Candidate:
    pattern: str
    stop_loss: Optional[float] = config.STOP_LOSS_PCT
    take_profit: Optional[float] = config.TAKE_PROFIT_PCT
    stop_atr: Optional[float] = None       # ATR-multiple exits (override the percentage ones when set)
    take_atr: Optional[float] = None
    long_only: bool = False                # a -1 signal only exits a long

    @property
    def key(self) -> str:
        k = f"{self.pattern}|sl={self.stop_loss}|tp={self.take_profit}"
        if self.stop_atr is not None or self.take_atr is not None:
            k += f"|atr={self.stop_atr},{self.take_atr}"
        return k + ("|long" if self.long_only else "")

    def params(self) -> dict:
        p = {"stop_loss": self.stop_loss, "take_profit": self.take_profit}
        if self.stop_atr is not None or self.take_atr is not None:
            p.update(stop_atr=self.stop_atr, take_atr=self.take_atr)
        if self.long_only:
            p["long_only"] = True
        return p


@dataclass
class Fold:
    index: int
    train_start: int          # positions into df (end exclusive)
    train_end: int
    test_start: int
    test_end: int
    chosen: Optional[Candidate] = None
    train_sharpe: Optional[float] = None
    test_sharpe: Optional[float] = None
    n_trades: int = 0

    def describe(self, idx: pd.Index) -> dict:
        return {"index": self.index, "train_start": str(idx[self.train_start].date()),
                "test_start": str(idx[self.test_start].date()), "test_end": str(idx[self.test_end - 1].date()),
                "chosen": self.chosen.key if self.chosen else None, "train_sharpe": _r(self.train_sharpe),
                "test_sharpe": _r(self.test_sharpe), "n_trades": self.n_trades}


@dataclass
class OOSTrade:
    entry_index: int          # global position in df
    exit_index: int
    direction: int
    stop_loss: Optional[float]
    take_profit: Optional[float]
    pnl_pct: float
    bars_held: int
    exit_reason: str
    window_start: int         # fold test window [window_start, window_end)
    window_end: int
    stop_atr: Optional[float] = None
    take_atr: Optional[float] = None


@dataclass
class WalkForwardResult:
    df: pd.DataFrame = field(repr=False)
    folds: list[Fold]
    oos_returns: pd.Series
    oos_trades: list[OOSTrade]
    mode: str                              # "continuous" (one candidate) or "per_fold"
    final_candidate: Optional[Candidate]   # selected on the last `train` bars before `end`
    n_pre: int                             # bars before `end`
    commission: float
    slippage: float

    @property
    def n_oos_trades(self) -> int:
        return len(self.oos_trades)

    @property
    def total_return(self) -> float:
        r = self.oos_returns.to_numpy()
        return float(np.prod(1.0 + r) - 1.0) if len(r) else 0.0

    @property
    def oos_sharpe(self) -> float:
        return metrics.sharpe(self.oos_returns.to_numpy())

    @property
    def oos_psr(self) -> Optional[float]:
        return metrics.psr(self.oos_returns.to_numpy())

    @property
    def in_sample_sharpe(self) -> Optional[float]:
        v = [f.train_sharpe for f in self.folds if f.train_sharpe is not None]
        return float(np.mean(v)) if v else None

    @property
    def mean_trade_pnl(self) -> Optional[float]:
        return float(np.mean([t.pnl_pct for t in self.oos_trades])) if self.oos_trades else None

    def oos_windows_overlap(self) -> bool:
        w = sorted((f.test_start, f.test_end) for f in self.folds)
        return any(b[0] < a[1] for a, b in zip(w, w[1:]))


@dataclass
class NullResult:
    p_value: float
    observed: Optional[float]
    null_mean: Optional[float]
    null_std: Optional[float]
    draws: int
    n_trades: int


@dataclass
class HoldoutResult:
    start: str
    n_bars: int
    n_trades: int
    total_return: float
    sharpe: float
    returns: pd.Series = field(repr=False, default_factory=pd.Series)

    def to_dict(self) -> dict:
        return {"start": self.start, "n_bars": self.n_bars, "n_trades": self.n_trades,
                "total_return": _r(self.total_return), "sharpe": _r(self.sharpe)}


@dataclass
class CandidateResult:
    symbol: str
    pattern: str
    params: dict
    n_trials: int = 0
    n_folds: int = 0
    n_oos_bars: int = 0
    n_oos_trades: int = 0
    oos: dict = field(default_factory=dict)
    oos_psr: Optional[float] = None
    in_sample_sharpe: Optional[float] = None
    holdout: Optional[dict] = None
    holdout_frozen: bool = False              # True when the stored first read was used
    holdout_asof: Optional[str] = None        # last bar date at the time of the first read
    holdout_versions_read: Optional[int] = None   # distinct strategy versions with a stored read for this key
    null_p: float = 1.0
    null_draws: int = 0
    null_observed: Optional[float] = None
    cost_ok: bool = False
    cost_return: Optional[float] = None
    delay_ok: bool = False
    delay_return: Optional[float] = None
    bh_significant: bool = False
    bh_adjusted_p: Optional[float] = None
    deflated_sharpe: Optional[float] = None   # advisory (Phase 4 gates on it)
    validation_status: str = UNVALIDATED
    rejected_reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return _clean(asdict(self))


# ------------------------------------------------------------------ small helpers
def _r(x, nd: int = 10):
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return _r(o)
    return o


def results_to_json(results: Iterable[CandidateResult]) -> str:
    """Canonical JSON (sorted keys, no NaN): same seed + data -> byte-identical string."""
    return json.dumps([r.to_dict() for r in results], sort_keys=True, allow_nan=False, separators=(",", ":"))


def _rng(key: str, seed: int) -> np.random.Generator:
    return np.random.default_rng([int(seed), zlib.crc32(key.encode("utf-8"))])


def holdout_position(df: pd.DataFrame, start=config.HOLDOUT_START) -> int:
    """Number of bars strictly before `start` (the first hold-out bar position)."""
    if start is None:
        return len(df)
    ts = pd.Timestamp(start)
    idx = df.index
    if getattr(idx, "tz", None) is not None and ts.tzinfo is None:
        ts = ts.tz_localize(idx.tz)
    return int(idx.searchsorted(ts, side="left"))


def compute_signals(df: pd.DataFrame, patterns: dict[str, Callable], names: Optional[Iterable[str]] = None
                    ) -> dict[str, pd.Series]:
    """Run each pattern once on the FULL frame; returns int Series aligned to df.index (0 on failure)."""
    out: dict[str, pd.Series] = {}
    for name in (names if names is not None else patterns):
        try:
            s = patterns[name](df)["signal"]
            out[name] = pd.to_numeric(s, errors="coerce").reindex(df.index).fillna(0).astype(int)
        except Exception as e:  # a broken pattern must not abort a scan
            logger.warning("pattern %s failed during validation: %s", name, e)
    return out


def make_folds(n_pre: int, train: int, test: int, step: int) -> list[Fold]:
    if step < test:
        raise ValueError("step must be >= test so OOS windows do not overlap")
    folds, s, k = [], 0, 0
    while s + train + test <= n_pre:
        folds.append(Fold(k, s, s + train, s + train, s + train + test))
        s += step
        k += 1
    return folds


def _run(df, sig, cand: Candidate, lo: int, hi: int, commission: float, slippage: float):
    return engine.run_backtest(df.iloc[lo:hi], signal=sig.iloc[lo:hi], stop_loss=cand.stop_loss,
                               take_profit=cand.take_profit, commission=commission, slippage=slippage,
                               allow_short=not cand.long_only, atr_stop_mult=cand.stop_atr,
                               atr_target_mult=cand.take_atr)


def _returns(run) -> np.ndarray:
    return metrics.equity_returns(run.equity.to_numpy(), run.initial_capital)


def _select(df, sigs, candidates, lo, hi, commission, slippage):
    """Best candidate by train-window Sharpe on [lo, hi); (None, None) if no candidate trades."""
    best, best_score = None, None
    for c in candidates:
        if c.pattern not in sigs:
            continue
        run = _run(df, sigs[c.pattern], c, lo, hi, commission, slippage)
        if not run.trades:
            continue
        sc = metrics.sharpe(_returns(run))
        if best_score is None or sc > best_score:
            best, best_score = c, sc
    return best, best_score


# ------------------------------------------------------------------ walk-forward
def walk_forward(df: pd.DataFrame, candidates: Sequence[Candidate], patterns: Optional[dict] = None,
                 train: int = config.WF_TRAIN_BARS, test: int = config.WF_TEST_BARS,
                 step: int = config.WF_STEP_BARS, end=config.HOLDOUT_START,
                 signals: Optional[dict[str, pd.Series]] = None,
                 commission: float = config.COMMISSION_PCT, slippage: float = config.SLIPPAGE_PCT,
                 ) -> WalkForwardResult:
    """Walk-forward over the bars before `end`. Provide `patterns` (name -> fn) or precomputed `signals`."""
    candidates = list(candidates)
    if signals is None:
        if patterns is None:
            raise ValueError("walk_forward needs patterns or signals")
        signals = compute_signals(df, patterns, sorted({c.pattern for c in candidates}))
    n_pre = holdout_position(df, end)
    folds = make_folds(n_pre, train, test, step)
    final = None
    if len(candidates) == 1:
        mode = "continuous"
        final = candidates[0] if candidates[0].pattern in signals else None
        for f in folds:
            f.chosen = final
    else:
        mode = "per_fold"
        for f in folds:
            f.chosen, f.train_sharpe = _select(df, signals, candidates, f.train_start, f.train_end, commission, slippage)
        if n_pre >= train:
            final, _ = _select(df, signals, candidates, n_pre - train, n_pre, commission, slippage)
    res = _assemble(df, folds, signals, mode, final, n_pre, commission, slippage, cost_mult=1.0, delay=0)
    return res


def _assemble(df, folds, signals, mode, final, n_pre, commission, slippage, cost_mult, delay) -> WalkForwardResult:
    comm, slip = commission * cost_mult, slippage * cost_mult

    def sig_for(c: Candidate) -> pd.Series:
        s = signals[c.pattern]
        return s.shift(delay, fill_value=0).astype(int) if delay else s

    parts: list[pd.Series] = []
    trades: list[OOSTrade] = []
    cont_run = cont_ret = None
    if mode == "continuous" and folds and folds[0].chosen is not None:
        c = folds[0].chosen
        cont_run = _run(df, sig_for(c), c, 0, n_pre, comm, slip)
        cont_ret = _returns(cont_run)
    for f in folds:
        c = f.chosen
        n_tr = 0
        if c is None:
            r = np.zeros(f.test_end - f.test_start)
        elif mode == "continuous":
            run, allr, base = cont_run, cont_ret, 0
            r = allr[f.test_start:f.test_end]
            f.train_sharpe = _opt(metrics.sharpe(allr[f.train_start:f.train_end])) if f.train_sharpe is None else f.train_sharpe
        else:
            run = _run(df, sig_for(c), c, f.train_start, f.test_end, comm, slip)
            r = _returns(run)[f.train_end - f.train_start:]
            base = f.train_start
        if c is not None:
            for t in run.trades:
                g = t.entry_index + base
                if f.test_start <= g < f.test_end:
                    trades.append(OOSTrade(g, t.exit_index + base, t.direction, c.stop_loss, c.take_profit,
                                           t.pnl_pct, t.bars_held, t.exit_reason, f.test_start, f.test_end,
                                           c.stop_atr, c.take_atr))
                    n_tr += 1
        f.n_trades = n_tr
        f.test_sharpe = metrics.sharpe(r)
        parts.append(pd.Series(r, index=df.index[f.test_start:f.test_end]))
    oos = pd.concat(parts) if parts else pd.Series(dtype=float)
    return WalkForwardResult(df=df, folds=folds, oos_returns=oos, oos_trades=trades, mode=mode,
                             final_candidate=final, n_pre=n_pre, commission=commission, slippage=slippage)


def _opt(x):
    return float(x) if x is not None and np.isfinite(x) else None


def _replay(df, wf: WalkForwardResult, signals, cost_mult: float, delay: int) -> WalkForwardResult:
    """Re-trade the SAME fold choices with scaled costs and/or delayed signals (no re-selection)."""
    folds = [Fold(f.index, f.train_start, f.train_end, f.test_start, f.test_end, f.chosen, f.train_sharpe)
             for f in wf.folds]
    return _assemble(df, folds, signals, wf.mode, wf.final_candidate, wf.n_pre, wf.commission, wf.slippage,
                     cost_mult=cost_mult, delay=delay)


def cost_stress(df: pd.DataFrame, wf: WalkForwardResult, signals: dict[str, pd.Series],
                mult: float = config.COST_STRESS_MULT) -> WalkForwardResult:
    """Same fold choices with commission and slippage multiplied by `mult`."""
    return _replay(df, wf, signals, cost_mult=mult, delay=0)


def delay_stress(df: pd.DataFrame, wf: WalkForwardResult, signals: dict[str, pd.Series],
                 delay_bars: int = 1) -> WalkForwardResult:
    """Same fold choices with every signal delayed by `delay_bars` bars."""
    return _replay(df, wf, signals, cost_mult=1.0, delay=delay_bars)


# ------------------------------------------------------------------ hold-out
def holdout_eval(df: pd.DataFrame, candidate: Candidate, signal: pd.Series,
                 start=config.HOLDOUT_START, commission: float = config.COMMISSION_PCT,
                 slippage: float = config.SLIPPAGE_PCT, bars_per_year: int = 252) -> Optional[HoldoutResult]:
    """Trade the (already selected) candidate on the bars on/after `start`, flat at the start.

    Read it ONCE per strategy version; it must never feed back into selection. None if < 2 bars."""
    n_pre = holdout_position(df, start)
    if len(df) - n_pre < 2:
        return None
    run = _run(df, signal, candidate, n_pre, len(df), commission, slippage)
    r = _returns(run)
    return HoldoutResult(start=str(pd.Timestamp(start).date()), n_bars=len(r), n_trades=len(run.trades),
                         total_return=float(np.prod(1 + r) - 1), sharpe=metrics.sharpe(r, bars_per_year),
                         returns=pd.Series(r, index=df.index[n_pre:]))


# ------------------------------------------------------------------ random-entry null
def _simulate_random_trades(o, h, lo, c, e, d, sl, tp, H, comm, slip,
                            stop_abs=None, target_abs=None) -> np.ndarray:
    """Vectorised engine replica: one trade per element. Returns net pnl_pct per element.

    e: entry bar, d: direction, sl/tp: stop/target fractions of the fill (nan = off), H: bars to hold before
    the time exit at the open of bar e+H (>= 1). ``stop_abs`` / ``target_abs`` (optional, per element) give
    absolute levels and win over sl/tp where finite (ATR exits anchored on the signal close)."""
    n = len(o)
    f = o[e] * (1.0 + slip * d)
    stop = f * (1.0 - sl * d)
    target = f * (1.0 + tp * d)
    if stop_abs is not None:
        stop = np.where(np.isnan(stop_abs), stop, stop_abs)
    if target_abs is not None:
        target = np.where(np.isnan(target_abs), target, target_abs)
    alive = np.ones(len(e), dtype=bool)
    xp = np.zeros(len(e))
    long_ = d == 1
    hmax = int(H.max())

    def mark(mask, price):
        nonlocal alive
        m = alive & mask
        xp[m] = price[m] if isinstance(price, np.ndarray) else price
        alive = alive & ~m

    for k in range(hmax + 1):
        b = np.minimum(e + k, n - 1)
        ob, hb, lb = o[b], h[b], lo[b]
        if k >= 1:
            mark(np.where(long_, ob <= stop, ob >= stop), ob)       # gap through stop
            mark(np.where(long_, ob >= target, ob <= target), ob)   # gap through target
            mark(H == k, ob)                                        # time (signal) exit at the open
        # an entry that opens beyond a level fills at the open, otherwise at the level (as the engine)
        mark(np.where(long_, lb <= stop, hb >= stop), np.where(long_, np.minimum(ob, stop), np.maximum(ob, stop)))
        mark(np.where(long_, hb >= target, lb <= target),
             np.where(long_, np.maximum(ob, target), np.minimum(ob, target)))
        if not alive.any():
            break
    if alive.any():  # safety: close whatever is left at the last close
        xp[alive] = c[-1]
    xf = xp * (1.0 - slip * d)
    return d * (xf / f - 1.0) - comm * (1.0 + xf / f)


def random_entry_null(df: pd.DataFrame, strategy_result: WalkForwardResult, draws: int = config.NULL_DRAWS,
                      seed: int = config.SEED, key: str = "", cost_mult: float = 1.0) -> NullResult:
    """Random-entry null for the OOS trades of `strategy_result` (see module docstring).

    Draws are generated in blocks of ``_NULL_BLOCK`` (a single block, hence the historical random stream,
    for draws <= _NULL_BLOCK) so very large null sizes stay memory-bounded."""
    trades = strategy_result.oos_trades
    if not trades:
        return NullResult(1.0, None, None, None, draws, 0)
    comm = strategy_result.commission * cost_mult
    slip = strategy_result.slippage * cost_mult
    o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    n = len(df)
    sig_holds = [t.bars_held for t in trades if t.exit_reason in ("signal", "end_of_data") and t.bars_held >= 1]
    fallback = int(round(np.median(sig_holds))) if sig_holds else _FALLBACK_HOLD
    T = len(trades)
    d = np.array([t.direction for t in trades], dtype=float)
    sl = np.array([np.nan if t.stop_loss is None else t.stop_loss for t in trades])
    tp = np.array([np.nan if t.take_profit is None else t.take_profit for t in trades])
    sa = np.array([np.nan if t.stop_atr is None else t.stop_atr for t in trades])
    ta = np.array([np.nan if t.take_atr is None else t.take_atr for t in trades])
    use_atr = bool(np.isfinite(sa).any() or np.isfinite(ta).any())
    atr = engine.atr_series(df) if use_atr else None
    H = np.array([max(1, t.bars_held) if t.exit_reason in ("signal", "end_of_data")
                  else max(1, t.bars_held, fallback) for t in trades])
    wlo = np.array([t.window_start for t in trades])
    whi = np.array([min(t.window_end, n - 1) for t in trades])        # entry strictly before the last bar
    hi = np.maximum(wlo, whi - 1 - H)
    width = hi - wlo + 1
    rng = _rng(key, seed)
    null_parts = []
    for start in range(0, draws, _NULL_BLOCK):
        nb = min(_NULL_BLOCK, draws - start)
        e = wlo[:, None] + np.floor(rng.random((T, nb)) * width[:, None]).astype(int)
        Hm = np.minimum(np.broadcast_to(H[:, None], e.shape), (n - 1) - e)
        Hm = np.maximum(Hm, 1)
        e = np.minimum(e, n - 2)
        bc = lambda a: np.broadcast_to(a[:, None], e.shape).ravel()      # noqa: E731
        er = e.ravel()
        stop_abs = target_abs = None
        if use_atr:
            j = np.maximum(er - 1, 0)
            ref, a = c[j], atr[j]
            dd = bc(d)
            stop_abs = np.where(np.isnan(bc(sa)), np.nan, ref - dd * bc(sa) * a)
            target_abs = np.where(np.isnan(bc(ta)), np.nan, ref + dd * bc(ta) * a)
        pnl = _simulate_random_trades(o, h, lo, c, er, bc(d), bc(sl), bc(tp), Hm.ravel(), comm, slip,
                                      stop_abs=stop_abs, target_abs=target_abs)
        null_parts.append(pnl.reshape(T, nb).mean(axis=0))
    null_means = np.concatenate(null_parts) if null_parts else np.zeros(0)
    obs = float(np.mean([t.pnl_pct for t in trades]))
    p = (1.0 + float((null_means >= obs).sum())) / (1.0 + draws)
    return NullResult(p, obs, float(null_means.mean()), float(null_means.std()), draws, T)


def adaptive_null(df: pd.DataFrame, strategy_result: WalkForwardResult, family_size: int,
                  draws: int = config.NULL_DRAWS, seed: int = config.SEED, key: str = "",
                  alpha: float = config.FDR_ALPHA, max_draws: int = MAX_NULL_DRAWS) -> NullResult:
    """Random-entry null whose resolution matches the multiple-testing correction.

    BH can only reject a candidate with p <= alpha, and the best of m needs p <= alpha/m, but a Monte Carlo
    p-value is never below 1/(draws+1). A candidate that is still a BH contender (p <= alpha) is therefore
    re-drawn with 10x more draws until 1/(draws+1) < alpha/m or ``max_draws``. Candidates with p > alpha
    (almost all of them under the null) cost nothing extra."""
    res = random_entry_null(df, strategy_result, draws=draws, seed=seed, key=key)
    d = draws
    target = alpha / max(1, family_size)
    while res.n_trades and res.p_value <= alpha and 1.0 / (d + 1) >= target and d < max_draws:
        d = min(d * 10, max_draws)
        res = random_entry_null(df, strategy_result, draws=d, seed=seed, key=key)
    return res


# ------------------------------------------------------------------ multiple testing
def benjamini_hochberg(p_values: Sequence[float], alpha: float = config.FDR_ALPHA
                       ) -> tuple[np.ndarray, np.ndarray]:
    """BH step-up. Returns (rejected bool array, BH-adjusted p-values / q-values)."""
    p = np.asarray(p_values, dtype=float)
    m = len(p)
    if m == 0:
        return np.zeros(0, dtype=bool), np.zeros(0)
    order = np.argsort(p, kind="mergesort")
    ranked = p[order] * m / (np.arange(m) + 1)
    q_sorted = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.empty(m)
    q[order] = np.minimum(q_sorted, 1.0)
    return q <= alpha, q


# ------------------------------------------------------------------ candidate evaluation
_MODULE_HASH: dict = {}


def _module_hash(module) -> str:
    """Hash of a module's source (helpers a pattern calls live in patterns.py / features.py)."""
    name = getattr(module, "__name__", "?")
    if name not in _MODULE_HASH:
        try:
            src = inspect.getsource(module)
        except (OSError, TypeError):
            src = name
        _MODULE_HASH[name] = hashlib.sha1(src.encode("utf-8")).hexdigest()[:12]
    return _MODULE_HASH[name]


def strategy_version(pattern_fn: Optional[Callable], cand: Candidate, end, commission: float,
                     slippage: float) -> str:
    """Stable id of a strategy version: the pattern's source plus the patterns.py / features.py modules it
    draws indicators from, its exits / sides and the cost model.

    A change to any of them is a NEW version (a fresh hold-out read, by decision D11)."""
    try:
        src = inspect.getsource(pattern_fn) if pattern_fn is not None else ""
    except (OSError, TypeError):
        src = getattr(pattern_fn, "__qualname__", "") or ""
    import features
    import patterns
    mods = [_module_hash(patterns), _module_hash(features)]
    blob = json.dumps([cand.key, str(end), commission, slippage, config.EXECUTION_MODE,
                       config.MAX_POSITION_SIZE_PCT, src, mods], sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _holdout_gate(cr: CandidateResult, df: pd.DataFrame, cand: Candidate, sig: pd.Series, end,
                  commission: float, slippage: float, store, version: str, bpy: int,
                  store_required: bool = False) -> None:
    """Fill cr.holdout. With a store, the FIRST evaluation per (version, candidate) is persisted and reused.

    Fails CLOSED: a store that cannot be read or written (or is missing while ``store_required``) leaves
    ``cr.holdout`` None with reason ``holdout_store_unavailable``, so the candidate cannot validate - a broken
    store must never silently re-read the hold-out as fresh."""
    ckey = f"{cr.symbol}|{cand.key}"

    def closed(why: str, e: Exception | None = None) -> None:
        logger.error("hold-out store %s (%s)%s: candidate stays unvalidated", why, ckey, f": {e}" if e else "")
        cr.holdout, cr.holdout_frozen, cr.holdout_asof = None, False, None
        cr.rejected_reasons.append("holdout_store_unavailable")

    if store is None:
        if store_required:
            closed("missing")
            return
    else:
        try:
            got = store.get(version, ckey)
        except Exception as e:
            closed("read failed", e)
            return
        if got is not None:
            cr.holdout, cr.holdout_frozen, cr.holdout_asof = got.get("result"), True, got.get("asof")
            _count_versions(cr, store, ckey)
            return
    ho = holdout_eval(df, cand, sig, start=end, commission=commission, slippage=slippage, bars_per_year=bpy)
    cr.holdout = ho.to_dict() if ho else None
    cr.holdout_asof = str(df.index[-1].date()) if len(df) else None
    if ho is not None and store is not None:
        try:
            store.put(version, ckey, {"result": cr.holdout, "asof": cr.holdout_asof})
        except Exception as e:
            closed("write failed", e)
            return
        _count_versions(cr, store, ckey)


def _count_versions(cr: CandidateResult, store, ckey: str) -> None:
    try:
        cr.holdout_versions_read = int(store.versions_read(ckey))
    except Exception:   # informational only
        cr.holdout_versions_read = None


def _eval_one(symbol: str, df: pd.DataFrame, cand: Candidate, sigs: dict, draws: int, seed: int,
              train: int, test: int, step: int, end, commission: float, slippage: float,
              cost_mult: float, family_size: int = 1, alpha: float = config.FDR_ALPHA,
              store=None, pattern_fn: Optional[Callable] = None, bpy: int = 252,
              store_required: bool = False
              ) -> tuple[CandidateResult, np.ndarray]:
    cr = CandidateResult(symbol=symbol, pattern=cand.pattern, params=cand.params())
    wf = walk_forward(df, [cand], signals=sigs, train=train, test=test, step=step, end=end,
                      commission=commission, slippage=slippage)
    cr.n_folds = len(wf.folds)
    cr.n_oos_bars = len(wf.oos_returns)
    cr.n_oos_trades = wf.n_oos_trades
    cr.in_sample_sharpe = _opt(wf.in_sample_sharpe)
    r = wf.oos_returns.to_numpy()
    if cr.n_folds == 0:
        cr.rejected_reasons.append("insufficient_history")
        return cr, r
    init = float(config.BACKTEST_INITIAL_CAPITAL)
    cr.oos = metrics.summary(init * np.cumprod(1.0 + r), [t.pnl_pct for t in wf.oos_trades],
                             initial_capital=init, bars_per_year=bpy)
    cr.oos_psr = wf.oos_psr
    if wf.n_oos_trades == 0:     # nothing to test: p = 1, stresses are flat (no trades -> zero return)
        cr.null_p, cr.null_draws, cr.cost_return, cr.delay_return = 1.0, 0, 0.0, 0.0
    else:
        nul = adaptive_null(df, wf, family_size, draws=draws, seed=seed, key=cand.key + "|" + symbol, alpha=alpha)
        cr.null_p, cr.null_observed, cr.null_draws = nul.p_value, nul.observed, nul.draws
        cs = cost_stress(df, wf, sigs, cost_mult)
        cr.cost_return = cs.total_return
        cr.cost_ok = cs.total_return > 0
        ds = delay_stress(df, wf, sigs, 1)
        cr.delay_return = ds.total_return
        cr.delay_ok = ds.total_return > 0
    if cand.pattern in sigs:
        _holdout_gate(cr, df, cand, sigs[cand.pattern], end, commission, slippage, store,
                      strategy_version(pattern_fn, cand, end, commission, slippage), bpy, store_required)
    return cr, r


def executable_candidates(pattern_names: Iterable[str]) -> list[Candidate]:
    """The variant execution can trade (D3/D5/D10): long-only, ATR stop / target from the signal close."""
    import execution  # lazy: validation must stay importable without the order path
    return [Candidate(p, None, None, stop_atr=config.ATR_STOP_MULT, take_atr=execution.TAKE_PROFIT_ATR_MULT,
                      long_only=True) for p in pattern_names]


def evaluate_candidates(frames_by_symbol: dict[str, pd.DataFrame], patterns: dict[str, Callable],
                        grid: Sequence[tuple] = DEFAULT_GRID, *, draws: int = config.NULL_DRAWS,
                        seed: int = config.SEED, train: int = config.WF_TRAIN_BARS,
                        test: int = config.WF_TEST_BARS, step: int = config.WF_STEP_BARS,
                        end=config.HOLDOUT_START, commission: float = config.COMMISSION_PCT,
                        slippage: float = config.SLIPPAGE_PCT, cost_mult: float = config.COST_STRESS_MULT,
                        alpha: float = config.FDR_ALPHA, min_trades: int = config.MIN_TRADES_OOS,
                        psr_min: float = config.OOS_PSR_MIN, executable_variant: bool = False,
                        holdout_store=None, require_holdout_store: bool = False) -> list[CandidateResult]:
    """Walk-forward + hold-out + null + stresses for every (symbol x pattern x (stop, target)) candidate,
    then BH across ALL null p-values of the run and the final ``validation_status``.

    ``executable_variant=True`` validates only what execution can trade (long-only, ATR exits; ``grid`` is
    ignored). ``holdout_store`` (state.holdout.HoldoutStore-like: get / put) freezes the first hold-out read;
    ``require_holdout_store=True`` makes a missing / failing store fail closed (nothing validates)."""
    import instruments

    prepared = []
    for symbol in sorted(frames_by_symbol):
        df = frames_by_symbol[symbol]
        sigs = compute_signals(df, patterns)
        names = sorted(sigs)
        cands = (executable_candidates(names) if executable_variant
                 else [Candidate(p, sl, tp) for p in names for sl, tp in grid])
        prepared.append((symbol, df, sigs, cands))
    family = sum(len(c) for *_, c in prepared)
    results: list[CandidateResult] = []
    returns: list[np.ndarray] = []
    for symbol, df, sigs, cands in prepared:
        bpy = instruments.bars_per_year(symbol)
        for cand in cands:
            cr, r = _eval_one(symbol, df, cand, sigs, draws, seed, train, test, step, end, commission,
                              slippage, cost_mult, family_size=family, alpha=alpha, store=holdout_store,
                              store_required=require_holdout_store,
                              pattern_fn=patterns.get(cand.pattern), bpy=bpy)
            results.append(cr)
            returns.append(r)
    n = len(results)
    rejected, q = benjamini_hochberg([c.null_p for c in results], alpha)
    srs = np.array([metrics.sharpe(r, bars_per_year=1) if len(r) > 2 else 0.0 for r in returns])
    var_sr = float(np.var(srs, ddof=1)) if n > 1 else 0.0
    for cr, r, rej, qq, sr in zip(results, returns, rejected, q, srs):
        cr.n_trials = n
        cr.bh_significant = bool(rej)
        cr.bh_adjusted_p = float(qq)
        if len(r) > 2:
            sk, ku = metrics.skew_kurt(r)
            cr.deflated_sharpe = _opt(metrics.deflated_sharpe(float(sr), n, var_sr, len(r), sk, ku))
        _decide(cr, min_trades, psr_min)
    return results


def _decide(cr: CandidateResult, min_trades: int, psr_min: float) -> None:
    reasons = list(cr.rejected_reasons)
    if "insufficient_history" not in reasons:
        if not cr.bh_significant:
            reasons.append("not_bh_significant")
        if cr.n_oos_trades < min_trades:
            reasons.append("insufficient_oos_trades")
        if cr.oos_psr is None or cr.oos_psr <= psr_min:
            reasons.append("oos_psr_too_low")
        if cr.holdout is None:
            reasons.append("holdout_unavailable")
        elif cr.holdout["total_return"] is None or cr.holdout["total_return"] < 0:
            reasons.append("holdout_negative")
        if not cr.cost_ok:
            reasons.append("cost_stress_fails")
    cr.rejected_reasons = reasons
    cr.validation_status = OOS_VALIDATED if not reasons else UNVALIDATED
