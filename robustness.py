"""WS4.3: signal-parameter robustness.

A strategy whose edge lives at one exact parameter value (EMA 9/21 works, 8/21 and 10/21 do not) is a
selection artefact. For a pattern with a neighbourhood grid (``patterns.PARAM_GRIDS``; the centre is the
pattern's default) every grid point is run through the SAME walk-forward validation on the bars before the
hold-out (``config.HOLDOUT_START``; the hold-out is never touched) and its out-of-sample Sharpe measured.

Verdict (``robust``) needs ALL of
* a centre OOS Sharpe of at least ``MIN_CENTER_SHARPE``,
* ``share_positive`` >= ``MIN_POSITIVE_SHARE``: the share of the neighbours (every non-centre grid point)
  with positive OOS Sharpe,
* smooth degradation: the immediate neighbours of the centre (one grid step along one axis) keep a median of
  at least ``MIN_MEDIAN_RETENTION`` of the centre Sharpe and none flips sign (``MIN_WORST_RETENTION``).
  A cliff next to the centre is the signature of a planted / overfitted edge.

Every grid point, centre included, is recorded in the trial registry (WS4.1) with its OOS return series, so
the sensitivity run counts in N exactly like any other trial and ``RobustnessResult.n_trials`` is the number of
trials this run added. The robustness verdict is advisory evidence; it does not alter validation gates.
"""
from __future__ import annotations

import inspect
import itertools
import logging
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

import config
import metrics
import validation as V
from patterns import PARAM_GRIDS, PATTERN_REGISTRY

logger = logging.getLogger(__name__)

MIN_CENTER_SHARPE = 0.5         # annualised OOS Sharpe floor for the centre (a bare >0 passes ~half of noise)
MIN_POSITIVE_SHARE = 0.6        # share of neighbours with positive OOS Sharpe
MIN_MEDIAN_RETENTION = 0.5      # median(neighbour Sharpe / centre Sharpe) over immediate neighbours
MIN_WORST_RETENTION = 0.0       # no immediate neighbour may flip the sign of the edge
MAX_GRID_POINTS = 81


@dataclass
class GridPoint:
    params: dict
    index: tuple                    # position on each axis (axis order = result.axes)
    is_center: bool
    adjacent: bool                  # one grid step from the centre along a single axis
    sharpe: float
    n_oos_trades: int
    n_oos_bars: int
    total_return: float
    failed: bool = False            # signals could not be computed (still a counted trial)
    retention: Optional[float] = None


@dataclass
class RobustnessResult:
    symbol: str
    pattern: str
    axes: list
    center_params: dict
    points: list = field(default_factory=list)
    n_trials: int = 0               # trials this run recorded (== grid size)
    registry_n: int = 0             # registry N after the run
    center_sharpe: Optional[float] = None
    n_neighbours: int = 0
    n_positive_neighbours: int = 0
    share_positive: Optional[float] = None
    median_retention: Optional[float] = None
    min_retention: Optional[float] = None
    smooth: bool = False
    robust: bool = False
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return V._clean({k: ([p.__dict__ for p in v] if k == "points" else v) for k, v in self.__dict__.items()})


def _defaults(fn: Callable, axes: Iterable[str]) -> dict:
    sig = inspect.signature(fn).parameters
    out = {}
    for a in axes:
        if a not in sig or sig[a].default is inspect.Parameter.empty:
            raise ValueError(f"{fn.__name__} has no defaulted parameter {a!r}")
        out[a] = sig[a].default
    return out


def _center_index(name: str, axis: str, values: list, default) -> int:
    for i, v in enumerate(values):
        if v == default:
            return i
    raise ValueError(f"grid for {name}.{axis} {values} does not contain the default {default}")


def _fmt(params: dict) -> str:
    return ",".join(f"{k}={params[k]}" for k in sorted(params))


def robustness(symbol: str, df: pd.DataFrame, pattern: str, *, trials,
               patterns: Optional[dict] = None, grid: Optional[dict] = None,
               candidate: Optional[V.Candidate] = None,
               train: int = config.WF_TRAIN_BARS, test: int = config.WF_TEST_BARS,
               step: int = config.WF_STEP_BARS, end=config.HOLDOUT_START,
               commission: float = config.COMMISSION_PCT, slippage: float = config.SLIPPAGE_PCT,
               bars_per_year: Optional[int] = None, min_positive_share: float = MIN_POSITIVE_SHARE,
               min_median_retention: float = MIN_MEDIAN_RETENTION,
               min_worst_retention: float = MIN_WORST_RETENTION,
               min_center_sharpe: float = MIN_CENTER_SHARPE, flush: bool = True,
               executable: bool = False) -> RobustnessResult:
    """Neighbourhood evaluation of ``pattern`` on ``df`` (pre-hold-out walk-forward). ``trials`` is a
    ``trials.TrialRegistry``; every grid point is recorded in it (a registry failure raises).

    ``executable=True`` evaluates the variant the order gate validates (long-only, ATR exits) instead of the
    default percentage-exit long/short template. NOT the default: the verdict thresholds were calibrated on the
    default template and the executable variant's pure-noise "robust" rate measured 16/90 (17.8%) against the
    15% bound of test_pure_noise_is_rarely_robust, so its verdict is not yet usable as order evidence (D15)."""
    from trials import data_hash as _data_hash
    patterns = PATTERN_REGISTRY if patterns is None else patterns
    if pattern not in patterns:
        raise KeyError(f"unknown pattern {pattern!r}")
    grid = PARAM_GRIDS.get(pattern) if grid is None else grid
    if not grid:
        raise ValueError(f"pattern {pattern!r} has no parameter grid")
    fn = patterns[pattern]
    axes = sorted(grid)
    center = _defaults(fn, axes)
    cidx = tuple(_center_index(pattern, a, list(grid[a]), center[a]) for a in axes)
    size = int(np.prod([len(grid[a]) for a in axes]))
    if size > MAX_GRID_POINTS:
        raise ValueError(f"grid of {size} points exceeds {MAX_GRID_POINTS}")
    if bars_per_year is None:
        import instruments
        bars_per_year = instruments.bars_per_year(symbol)
    template = candidate or (V.executable_candidates([pattern])[0] if executable else V.Candidate(pattern))
    dhash = _data_hash(df)

    res = RobustnessResult(symbol=symbol, pattern=pattern, axes=axes, center_params=center)
    for idx in itertools.product(*(range(len(grid[a])) for a in axes)):
        params = {a: grid[a][i] for a, i in zip(axes, idx)}
        dist = [abs(i - c) for i, c in zip(idx, cidx)]
        is_center, adjacent = sum(dist) == 0, sum(dist) == 1
        cand = replace(template, pattern=pattern)
        failed = False
        sigs = V.compute_signals(df, {pattern: partial(fn, **params)}, [pattern])
        if pattern not in sigs:
            failed = True
            oos = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
            n_tr, n_f = 0, 0
        else:
            wf = V.walk_forward(df, [cand], signals=sigs, train=train, test=test, step=step, end=end,
                                commission=commission, slippage=slippage)
            oos, n_tr, n_f = wf.oos_returns, wf.n_oos_trades, len(wf.folds)
        r = oos.to_numpy()
        sh = float(metrics.sharpe(r, bars_per_year=bars_per_year)) if len(r) > 2 else 0.0
        tot = float(np.prod(1.0 + r) - 1.0) if len(r) else 0.0
        trials.record(symbol=symbol, pattern=pattern,
                      params={**params, **cand.params(), "sensitivity": True}, returns=oos,
                      strategy_version=V.strategy_version(fn, replace(cand, pattern=f"{pattern}[{_fmt(params)}]"),
                                                          end, commission, slippage),
                      data_hash=dhash)
        res.points.append(GridPoint(params, idx, is_center, adjacent, sh, n_tr, len(r), tot, failed))
        if n_f == 0 and not failed and "insufficient_history" not in res.reasons:
            res.reasons.append("insufficient_history")
    if flush:
        trials.flush()
    res.n_trials = len(res.points)
    res.registry_n = trials.n_trials
    _judge(res, min_positive_share, min_median_retention, min_worst_retention, min_center_sharpe)
    return res


def _judge(res: RobustnessResult, min_share: float, min_median: float, min_worst: float,
           min_center: float = MIN_CENTER_SHARPE) -> None:
    centre = next(p for p in res.points if p.is_center)
    nbrs = [p for p in res.points if not p.is_center]
    res.center_sharpe = centre.sharpe
    res.n_neighbours = len(nbrs)
    res.n_positive_neighbours = sum(p.sharpe > 0 for p in nbrs)
    res.share_positive = res.n_positive_neighbours / len(nbrs) if nbrs else None
    if centre.sharpe > 0:
        for p in nbrs:
            p.retention = p.sharpe / centre.sharpe
        ret = [p.retention for p in nbrs if p.adjacent]
        res.median_retention = float(np.median(ret)) if ret else None
        res.min_retention = float(min(ret)) if ret else None
        res.smooth = bool(ret) and res.median_retention >= min_median and res.min_retention >= min_worst
    reasons = res.reasons
    if "insufficient_history" not in reasons:
        if centre.sharpe < min_center:
            reasons.append("center_sharpe_low")
        if res.share_positive is None or res.share_positive < min_share:
            reasons.append("neighbours_not_positive")
        if centre.sharpe > 0 and not res.smooth:
            reasons.append("not_smooth")
    res.robust = not reasons


def robustness_scan(frames_by_symbol: dict, pattern_names: Optional[Iterable[str]] = None, *, trials,
                    patterns: Optional[dict] = None, **kw) -> list[RobustnessResult]:
    """``robustness`` for every (symbol x pattern with a grid), sorted by symbol then pattern; one flush."""
    patterns = PATTERN_REGISTRY if patterns is None else patterns
    names = sorted(n for n in (pattern_names if pattern_names is not None else patterns)
                   if n in PARAM_GRIDS and n in patterns)
    out = []
    for symbol in sorted(frames_by_symbol):
        for name in names:
            out.append(robustness(symbol, frames_by_symbol[symbol], name, trials=trials, patterns=patterns,
                                  flush=False, **kw))
    trials.flush()
    for r in out:
        r.registry_n = trials.n_trials
    return out
