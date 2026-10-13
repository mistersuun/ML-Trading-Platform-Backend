"""Pooled validation (decision D18): the unit of validation is the PATTERN, pooled across a pre-registered universe.

SHADOW ONLY. ``pooled_deflated_validated`` is deliberately NOT in ``config.ORDER_ELIGIBLE_STATUSES``: this module
computes, stores and serves pooled verdicts and never creates an order. Making a pooled status order-eligible needs a
further owner decision (D18). Design: ``docs/proposals/pooled-validation.md``; decisions: ``docs/decisions.md`` D18.

How it differs from ``validation.py`` (which it reuses and does not change)
-------------------------------------------------------------------------
* Per symbol the walk-forward is ``validation.walk_forward`` with ONE fixed candidate (continuous run) and a
  ``config.POOLED_WARMUP_BARS`` (252) warm-up instead of the 504-bar train window (D18 c: a fixed pattern is not
  fitted, so the train window would only discard data). This applies to the POOLED path only.
* Every OOS trade is expressed in R-multiples (``pnl_pct / stop_frac``, ``stop_frac = ATR_STOP_MULT x ATR / close`` at
  the signal bar) and in daily mark-to-market ``rho`` on the trade's notional; the pooled DATE-level series is
  ``r_pool[t] = sum_i w_i rho_i[t]`` with the equal-risk weight ``w_i = RISK_PER_TRADE_PCT / stop_frac_i``
  (the uncapped sleeve, a fraction of the sleeve per day; "total return" of it is the arithmetic sum).
* PSR / DSR use ``T_eff`` (Bartlett), the min-trades gate counts entry-week clusters, the random-entry null draws one
  shared calendar offset per cluster (cluster-preserving), BH and N count PATTERNS (cumulative ledger per
  ``HOLDOUT_START``, floored at the number of registered patterns), alpha is split with the per-symbol family
  (``FDR_ALPHA / 2``, ``DSR_P_MAX / 2``).
* Concentration (max symbol share, leave-one-symbol / cluster / fold out), cost stress, a GATED 1-bar delay stress,
  a capped replay under the D5 caps, and a pooled hold-out (read once per pooled version, at most once per pattern per
  ``HOLDOUT_START``) complete the gates. PBO (time CSCV) and a cross-sectional CSCV are advisory.

Fail closed everywhere: an unreadable ledger, hold-out store or registry means nothing pooled validates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

import numpy as np
import pandas as pd

import config
import engine
import validation as V
from stats import pooled as P
from stats import selection

logger = logging.getLogger(__name__)

# "2": the capped replay's exclusion rule is CAUSAL (a symbol is excluded at an entry date only from trades that had
# already exited; review round 1). A new version is a new trial: it adds to N.
POOLING_METHOD_VERSION = "2"
ASSET_SPLIT = "none"                      # the optional pattern x asset-class split is OFF (it would double N)
UNVALIDATED = "unvalidated"
POOLED_OOS_VALIDATED = "pooled_oos_validated"          # every pooled gate except the DSR. Alert-only.
POOLED_DEFLATED_VALIDATED = "pooled_deflated_validated"   # every gate. SHADOW ONLY: not order-eligible.
POOLED_STATUSES = (POOLED_OOS_VALIDATED, POOLED_DEFLATED_VALIDATED)

# run-level statuses
RUN_OK = "ok"
RUN_INCOMPLETE = "pooled_universe_incomplete"
RUN_UNREGISTERED = "pooled_universe_unregistered"
RUN_DERIVED = "pooled_universe_derived"
RUN_TRIALS_UNAVAILABLE = "pooled_trials_unavailable"
RUN_NOT_SHADOW_SAFE = "pooled_config_unsafe"
PARTIAL_FLAG = "pooled_universe_partial"

NULL_CLUSTER_PRESERVING = True            # False would draw trades independently (the design the proposal rejects)
NULL_OFFSET_DAYS = 200                    # +- calendar days of the shared random offset (a 126-bar window is ~182 days)
NULL_BLOCK = 2000
CAPPED_SHARPE_MIN_RATIO = 0.5             # the capped replay must keep at least this share of the uncapped Sharpe
GATE_ORDER = ("pooled_min_trades", "breadth", "oos_positive", "psr", "null_bh", "dsr", "concentration",
              "cost_delay", "capped_replay", "holdout")
DISCLOSURE = ("The 2025-07-01 hold-out is treated as possibly seen: per-symbol hold-out results were viewable in the app "
              "(D18 a). The pooled hold-out here is an aggregate of the same bars and is not fresh data. The clean "
              "confirmation is a forward shadow period of at least 4 weeks of nightly pooled results after 2026-10-06.")


# ------------------------------------------------------------------ universe, patterns, versions
def registered_universe() -> list[str]:
    """The pre-registered pooled universe by RULE: every executable ``WATCHLIST["stocks"]`` symbol, sorted."""
    import instruments
    out = []
    for s in config.WATCHLIST.get("stocks", []):
        try:
            inst = instruments.get(s)
        except instruments.UnknownSymbol:
            continue
        if inst.executable and not inst.research_only:
            out.append(s)
    return sorted(set(out))


def universe_hash(symbols: Sequence[str]) -> str:
    return hashlib.sha1("|".join(sorted(set(symbols))).encode("utf-8")).hexdigest()[:16]


def retired_patterns() -> dict:
    return dict(config.POOLED_RETIRED_PATTERNS)


def pooled_registry() -> dict:
    """The POOLED pattern family: ``patterns.PATTERN_REGISTRY`` plus the pre-registered research patterns
    (``patterns_prereg.PREREG_PATTERNS``, docs/research/preregistration-2026-10.md). The per-symbol registry is never
    modified, so the per-symbol scan, ``universe_trials()`` and the per-symbol ``strategy_version`` are unchanged."""
    from patterns import PATTERN_REGISTRY
    from patterns_prereg import PREREG_PATTERNS
    clash = set(PATTERN_REGISTRY) & set(PREREG_PATTERNS)
    if clash:
        raise RuntimeError(f"pre-registered pattern names clash with PATTERN_REGISTRY: {sorted(clash)}")
    return {**PATTERN_REGISTRY, **PREREG_PATTERNS}


def pooled_pattern_names(registry: Optional[dict] = None) -> list[str]:
    """Patterns that ARE evaluated pooled (the pooled family minus the retired ones), sorted."""
    reg = pooled_registry() if registry is None else registry
    ret = retired_patterns()
    return sorted(p for p in reg if p not in ret)


def pooled_universe_trials() -> int:
    """N floor: registered patterns (retired ones stay counted) x executable variants (1) x asset-class splits (1).
    A narrowed run (``--pattern P``) is deflated with this N, never with N = 1."""
    return len(pooled_registry()) * 1 * 1


def _cand(pattern: str) -> V.Candidate:
    return V.executable_candidates([pattern])[0]


def gate_constants() -> dict:
    """Every constant a pooled verdict depends on (part of the hold-out version, so changing one is a new read)."""
    keys = ("POOLED_WARMUP_BARS", "POOLED_MIN_TRADES", "POOLED_MIN_CLUSTERS", "POOLED_BREADTH_MIN",
            "POOLED_BREADTH_MIN_TRADES", "POOLED_MAX_MISSING", "POOLED_MAX_SYMBOL_SHARE", "POOLED_HAC_LAGS",
            "POOLED_HOLDOUT_MIN_TRADES", "POOLED_NULL_MAX_UNFIT", "POOLED_EXCLUDE_T", "OOS_PSR_MIN", "FDR_ALPHA",
            "DSR_P_MAX", "COST_STRESS_MULT", "WF_TEST_BARS", "WF_STEP_BARS", "NULL_DRAWS", "ATR_STOP_MULT",
            "RISK_PER_TRADE_PCT", "MAX_SYMBOL_PCT", "MAX_OPEN_POSITIONS", "MAX_POSITIONS_PER_CLUSTER",
            "MAX_ORDERS_PER_DAY", "MAX_PORTFOLIO_HEAT_PCT", "MAX_GROSS_EXPOSURE_PCT")
    return {k: getattr(config, k) for k in keys}


def _pattern_strategy_version(pattern_fn, cand: V.Candidate, end, commission: float, slippage: float) -> str:
    return V.strategy_version(pattern_fn, cand, end, commission, slippage)


def trial_version(pattern: str, pattern_fn, uhash: str, end, commission: float, slippage: float) -> str:
    """Identity of a pooled TRIAL: (pattern, executable variant params, asset split, universe hash, method version)
    plus the pattern source / ``patterns.py`` / ``features.py`` hashes. A new value adds one to N."""
    cand = _cand(pattern)
    blob = json.dumps([_pattern_strategy_version(pattern_fn, cand, end, commission, slippage), uhash, ASSET_SPLIT,
                       POOLING_METHOD_VERSION], sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def pooled_version(pattern: str, pattern_fn, uhash: str, end, commission: float, slippage: float,
                   missing: Sequence[str] = ()) -> str:
    """The pooled STRATEGY version used for the hold-out read key: the trial version inputs plus the weighting rule,
    every gate constant and the missing-symbol set (a later complete run is a new, separately counted evaluation)."""
    cand = _cand(pattern)
    blob = json.dumps([_pattern_strategy_version(pattern_fn, cand, end, commission, slippage), uhash, ASSET_SPLIT,
                       POOLING_METHOD_VERSION, "equal_risk_R", gate_constants(), sorted(missing)],
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def shadow_guard() -> None:
    """Shadow never makes anything order-eligible: refuse to run if a pooled status were order-eligible by config."""
    bad = set(POOLED_STATUSES) & set(config.ORDER_ELIGIBLE_STATUSES)
    if bad:
        raise RuntimeError(f"pooled statuses {sorted(bad)} are in ORDER_ELIGIBLE_STATUSES; shadow mode refuses to run")


def order_eligibility(payload: Optional[dict], pattern: str, symbol: str, *, now: Optional[datetime] = None
                      ) -> tuple[bool, str]:
    """May a pooled verdict make ``symbol`` order-eligible for ``pattern``? (eligible, reason).

    False today in every case: the pooled statuses are not in ``config.ORDER_ELIGIBLE_STATUSES``. The checks are the
    ones a later owner decision would rely on, and they are independent of each other: an incomplete or partial
    universe, a stale verdict, a symbol outside the universe or excluded by the pre-registered rule NEVER confers
    eligibility, whatever the stored verdict says."""
    if not isinstance(payload, dict) or payload.get("unit") != "pooled":
        return False, "no_pooled_verdict"
    uni = payload.get("universe") or {}
    if payload.get("status") != RUN_OK or not uni.get("complete") or uni.get("partial"):
        return False, RUN_INCOMPLETE
    rec = next((p for p in payload.get("patterns", []) if p.get("pattern") == pattern), None)
    if rec is None:
        return False, "pattern_not_in_pooled_run"
    if rec.get("status") not in config.ORDER_ELIGIBLE_STATUSES:
        return False, "not_validated"
    if symbol not in (uni.get("symbols") or []):
        return False, "pooled_symbol_not_in_universe"
    if symbol in (rec.get("excluded_symbols") or []):
        return False, "pooled_symbol_excluded"
    if uni.get("hash") != universe_hash(registered_universe()):
        return False, "pooled_version_mismatch"
    try:
        gen = datetime.fromisoformat(payload["generated_at"])
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
        age = ((now or datetime.now(timezone.utc)) - gen).total_seconds() / 3600.0
    except Exception:
        return False, "pooled_stale"
    if age > config.POOLED_FALLBACK_MAX_AGE_HOURS:
        return False, "pooled_stale"
    return True, "ok"


def intent_problem(symbol: str, strategy_key: str, pooled_version_: Optional[str], universe_hash_: Optional[str],
                   root=None) -> Optional[str]:
    """The extra rejection ``execution.submit_intent`` applies to a pooled intent (the only chokepoint change):
    a symbol outside the universe, a universe or version that is not the latest stored pooled verdict's, or any
    reason ``order_eligibility`` gives. Returns a rejection reason, or None."""
    from results import store
    got = store.read_latest("pooled", root)
    payload = None
    if got is not None:
        payload, gen = got
        payload = dict(payload, generated_at=gen.isoformat())
    pattern = strategy_key.split(":", 1)[1] if ":" in strategy_key else strategy_key
    if symbol not in registered_universe():
        return "pooled_symbol_not_in_universe"
    if payload is None:
        return "pooled_version_mismatch"
    rec = next((p for p in payload.get("patterns", []) if p.get("pattern") == pattern), None)
    if (universe_hash_ != (payload.get("universe") or {}).get("hash") or rec is None
            or pooled_version_ != rec.get("pooled_version")):
        return "pooled_version_mismatch"
    ok, why = order_eligibility(payload, pattern, symbol)
    return None if ok else why


# ------------------------------------------------------------------ per-symbol data
def _naive(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize(None) if idx.tz is not None else idx


@dataclass
class SymbolData:
    symbol: str
    df: pd.DataFrame = field(repr=False)
    n_pre: int = 0
    dates: pd.DatetimeIndex = field(default=None, repr=False)      # naive dates of bars [0, n_pre)
    o: np.ndarray = field(default=None, repr=False)
    h: np.ndarray = field(default=None, repr=False)
    lo: np.ndarray = field(default=None, repr=False)
    c: np.ndarray = field(default=None, repr=False)
    atr: np.ndarray = field(default=None, repr=False)              # causal ATR of the pre-hold-out bars
    sigs: dict = field(default_factory=dict, repr=False)
    test_start: int = 0                                            # first OOS bar
    oos_end: int = 0                                               # end (exclusive) of the last complete fold
    folds: list = field(default_factory=list, repr=False)
    axis_pos: np.ndarray = field(default=None, repr=False)


@dataclass(slots=True)
class TradeRec:
    symbol: str
    entry: int
    exit: int
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    pnl: float
    bars: int
    reason: str
    w_lo: int
    w_hi: int
    stop_frac: float
    R: float
    weight: float
    week: int


def _usable(df, end, warmup: int, test: int) -> tuple[bool, str]:
    if df is None or len(df) == 0:
        return False, "no_data"
    if df.attrs.get("stale"):
        return False, "stale"
    if not {"Open", "High", "Low", "Close"} <= set(df.columns):
        return False, "bad_frame"
    n_pre = V.holdout_position(df, end)
    if n_pre < warmup + test:
        return False, "insufficient_history"
    return True, "ok"


def _prepare_symbol(symbol: str, df: pd.DataFrame, end) -> SymbolData:
    n_pre = V.holdout_position(df, end)
    pre = df.iloc[:n_pre]
    return SymbolData(symbol=symbol, df=df, n_pre=n_pre, dates=_naive(pre.index),
                      o=pre["Open"].to_numpy(dtype=float), h=pre["High"].to_numpy(dtype=float),
                      lo=pre["Low"].to_numpy(dtype=float), c=pre["Close"].to_numpy(dtype=float),
                      atr=engine.atr_series(pre))


def _week_key(ts: pd.Timestamp) -> int:
    iso = ts.isocalendar()
    return int(iso[0]) * 100 + int(iso[1])


def _stop_frac(atr_arr: np.ndarray, c: np.ndarray, e: int, stop_atr: float) -> float:
    j = e - 1
    if j < 0:
        return float("nan")
    a, ref = atr_arr[j], c[j]
    return float(stop_atr * a / ref) if np.isfinite(a) and a > 0 and ref > 0 else float("nan")


def _recs_from_oos(sd: SymbolData, trades: Sequence[V.OOSTrade]) -> list[TradeRec]:
    out = []
    for t in trades:
        sf = _stop_frac(sd.atr, sd.c, t.entry_index, t.stop_atr or config.ATR_STOP_MULT)
        if not np.isfinite(sf) or sf <= 0:
            continue                                   # the engine never opens such a trade; defensive
        out.append(TradeRec(sd.symbol, t.entry_index, t.exit_index, sd.dates[t.entry_index], sd.dates[t.exit_index],
                            float(t.pnl_pct), int(t.bars_held), t.exit_reason, int(t.window_start), int(t.window_end),
                            sf, float(t.pnl_pct) / sf, config.RISK_PER_TRADE_PCT / sf,
                            _week_key(sd.dates[t.entry_index])))
    return out


def _trade_rho(o: np.ndarray, c: np.ndarray, e: int, x: int, pnl: float, comm: float, slip: float) -> np.ndarray:
    """Daily mark-to-market return on the trade's NOTIONAL for bars e..x of a long engine trade (net of costs).

    Entry bar: close vs the fill ``f = open (1 + slip)`` less the entry commission; middle bars: close-to-close over
    ``f``; exit bar: the exit fill (recovered from the trade's net pnl) vs the previous close, less the exit
    commission. The sum over the bars is exactly ``pnl`` (and equals the engine's equity change / notional)."""
    f = o[e] * (1.0 + slip)
    if x <= e:
        return np.array([pnl])
    rho = np.empty(x - e + 1)
    rho[0] = (c[e] - f) / f - comm
    if x - e > 1:
        rho[1:-1] = (c[e + 1:x] - c[e:x - 1]) / f
    xf = f * (1.0 + comm + pnl) / (1.0 - comm)
    rho[-1] = (xf - c[x - 1]) / f - comm * xf / f
    return rho


def _oos_mask(idx: np.ndarray, oos_end: int) -> np.ndarray:
    """Bars of a trade that count toward the date-level series: strictly before the symbol's ``oos_end`` (the end of
    its last complete fold). A trade entered in the last fold can be held past it (the walk-forward runs the engine
    to the hold-out); those bars never enter the series. The trade's REALISED R (null, mean R, exclusion rule) is its
    full outcome, exactly as in the per-symbol path: the two differ only for those few trades, by design."""
    return idx < oos_end


def _build_components(recs: Sequence[TradeRec], sds: dict, axis: pd.DatetimeIndex, order: list[str],
                      comm: float, slip: float, weights: Optional[Sequence[float]] = None) -> np.ndarray:
    """(T x K) daily contributions ``w_i rho_i[t]`` per symbol (only OOS bars; flat days are 0)."""
    comp = np.zeros((len(axis), len(order)))
    col = {s: i for i, s in enumerate(order)}
    for i, t in enumerate(recs):
        sd = sds[t.symbol]
        rho = _trade_rho(sd.o, sd.c, t.entry, t.exit, t.pnl, comm, slip)
        idx = np.arange(t.entry, t.exit + 1)
        keep = _oos_mask(idx, sd.oos_end)
        if not keep.any():
            continue
        w = t.weight if weights is None else weights[i]
        np.add.at(comp[:, col[t.symbol]], sd.axis_pos[idx[keep]], w * rho[keep])
    return comp


# ------------------------------------------------------------------ gates on the pooled object
def _t_stat(x: np.ndarray) -> Optional[float]:
    if len(x) < 3:
        return None
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 1e-12 else None


def excluded_symbols(recs: Sequence[TradeRec]) -> list[str]:
    """Pre-registered exclusion rule: a symbol whose own OOS mean R has a one-sided t below ``POOLED_EXCLUDE_T``
    (>= 3 trades). Deliberately weak, so it is not per-symbol selection by the back door."""
    by: dict[str, list[float]] = {}
    for t in recs:
        by.setdefault(t.symbol, []).append(t.R)
    out = []
    for s, v in sorted(by.items()):
        tt = _t_stat(np.asarray(v))
        if tt is not None and tt < config.POOLED_EXCLUDE_T:
            out.append(s)
    return out


def symbol_table(recs: Sequence[TradeRec], universe: Sequence[str], excluded: Sequence[str]) -> list[dict]:
    by: dict[str, list[float]] = {s: [] for s in universe}
    for t in recs:
        by.setdefault(t.symbol, []).append(t.R)
    tot = sum(abs(sum(v)) for v in by.values())
    rows = []
    for s in sorted(by):
        v = np.asarray(by[s])
        pnl = float(v.sum()) if len(v) else 0.0
        rows.append({"symbol": s, "n_trades": int(len(v)), "mean_r": float(v.mean()) if len(v) else None,
                     "pnl_r": pnl, "share": (abs(pnl) / tot) if tot > 0 else 0.0, "t_stat": _t_stat(v),
                     "excluded": s in excluded})
    return rows


def concentration(recs: Sequence[TradeRec], axis: pd.DatetimeIndex, fold_bars: int) -> dict:
    """Section 9.1: max symbol share of the absolute R-P&L, leave-one-symbol / cluster / fold out mean R."""
    R = np.asarray([t.R for t in recs])
    syms = np.asarray([t.symbol for t in recs])
    out = {"max_symbol_share": None, "top_symbol": None, "loso_min_mean_r": None, "loso_worst_symbol": None,
           "loco_min_mean_r": None, "loco_worst_cluster": None, "lofo_min_mean_r": None, "lofo_worst_fold": None,
           "share_ok": False, "loso_ok": False, "loco_ok": False, "lofo_ok": False, "ok": False}
    if not len(R):
        return out
    uniq = sorted(set(syms.tolist()))
    pnl = {s: float(R[syms == s].sum()) for s in uniq}
    tot = sum(abs(v) for v in pnl.values())
    top = str(max(uniq, key=lambda s: abs(pnl[s])))
    out["top_symbol"] = top
    out["max_symbol_share"] = abs(pnl[top]) / tot if tot > 0 else 1.0
    out["share_ok"] = out["max_symbol_share"] <= config.POOLED_MAX_SYMBOL_SHARE

    def worst(mask_groups: dict) -> tuple[Optional[float], Optional[str]]:
        best_v, best_k = None, None
        for k, m in mask_groups.items():
            keep = ~m
            if not keep.any():
                continue
            v = float(R[keep].mean())
            if best_v is None or v < best_v:
                best_v, best_k = v, k
        return best_v, best_k

    out["loso_min_mean_r"], out["loso_worst_symbol"] = _loso(R, syms)
    out["loso_ok"] = out["loso_min_mean_r"] is not None and out["loso_min_mean_r"] > 0
    groups = {g: np.isin(syms, members) for g, members in config.CLUSTERS.items()
              if np.isin(syms, members).any()}
    v, k = worst(groups) if groups else (float(R.mean()), None)
    out["loco_min_mean_r"], out["loco_worst_cluster"] = v, k
    out["loco_ok"] = v is not None and v > 0
    pos = axis.searchsorted(pd.DatetimeIndex([t.entry_date for t in recs]))
    fold = pos // max(1, fold_bars)
    folds = {int(f): fold == f for f in sorted(set(fold.tolist()))}
    out["lofo_min_mean_r"], fw = worst(folds)
    out["lofo_worst_fold"] = None if fw is None else int(fw)
    out["lofo_ok"] = out["lofo_min_mean_r"] is not None and out["lofo_min_mean_r"] > 0
    out["ok"] = bool(out["share_ok"] and out["loso_ok"] and out["loco_ok"] and out["lofo_ok"])
    return out


def _loso(R: np.ndarray, syms: np.ndarray) -> tuple[Optional[float], Optional[str]]:
    """Leave-one-symbol-out: the lowest pooled mean R over the universe with any single symbol removed."""
    best_v, best_s = None, None
    for s in sorted(set(syms.tolist())):
        keep = syms != s
        if not keep.any():
            continue
        v = float(R[keep].mean())
        if best_v is None or v < best_v:
            best_v, best_s = v, s
    return best_v, best_s


def _cluster_of(symbol: str) -> str:
    for g, members in config.CLUSTERS.items():
        if symbol in members:
            return g
    return symbol


class _CausalExclusion:
    """The pre-registered exclusion rule evaluated AS OF a date: a symbol is excluded at an entry date only when its
    one-sided t of mean R, over its OWN trades that had already EXITED before that date, is below
    ``POOLED_EXCLUDE_T`` (at least 3 such trades). The symbol's earlier trades count whether or not they were taken
    (a record the live rule would also have). Nothing after the entry date is used, so the replay does not select on
    outcome (the full-sample ``excluded_symbols`` is used for forward eligibility only)."""

    def __init__(self, recs: Sequence[TradeRec]):
        by: dict[str, list[tuple]] = {}
        for t in recs:
            by.setdefault(t.symbol, []).append((np.datetime64(t.exit_date, "ns"), t.R))
        self.tab = {}
        for s, v in by.items():
            v.sort(key=lambda x: x[0])
            ex = np.asarray([x[0] for x in v], dtype="datetime64[ns]")
            r = np.asarray([x[1] for x in v], dtype=float)
            self.tab[s] = (ex, np.cumsum(r), np.cumsum(r * r))

    def excluded(self, symbol: str, entry_date) -> bool:
        if symbol not in self.tab:
            return False
        ex, cs, css = self.tab[symbol]
        k = int(np.searchsorted(ex, np.datetime64(entry_date, "ns"), side="left"))   # exits strictly before the entry
        if k < 3:
            return False
        mean = cs[k - 1] / k
        var = max(0.0, (css[k - 1] - k * mean * mean) / (k - 1))
        sd = math.sqrt(var)
        if sd <= 1e-12:
            return False
        return bool(mean / (sd / math.sqrt(k)) < config.POOLED_EXCLUDE_T)


def capped_replay(recs: Sequence[TradeRec], excluded: Sequence[str] = (), *, causal_exclusion: bool = False
                  ) -> tuple[list[int], dict]:
    """Replay the trades through the D5 caps in the pre-registered tie order (entry date, then symbol alphabetical).

    A trade is admitted when, with every admitted trade that is still open on its entry date (exit date >= entry
    date, conservatively), it keeps: open positions <= MAX_OPEN_POSITIONS, positions per cluster <=
    MAX_POSITIONS_PER_CLUSTER, new orders that day <= MAX_ORDERS_PER_DAY, notional (capped at MAX_SYMBOL_PCT of the
    sleeve) gross <= MAX_GROSS_EXPOSURE_PCT and open risk-to-stop <= MAX_PORTFOLIO_HEAT_PCT.

    Excluded symbols never trade (the same rule as live). ``excluded`` is a STATIC list (a symbol is out for the whole
    sample: look-ahead when it was computed on the sample being judged; only for tests and forward eligibility).
    ``causal_exclusion=True`` applies the rule as of each entry date instead (``_CausalExclusion``): this is what the
    pooled gate uses (method version 2). Returns the admitted indices (into ``recs``) and counts."""
    static = set(excluded)
    cx = _CausalExclusion(recs) if causal_exclusion else None
    order = sorted((i for i, t in enumerate(recs) if t.symbol not in static),
                   key=lambda i: (recs[i].entry_date, recs[i].symbol, recs[i].entry))
    admitted: list[int] = []
    open_: list[int] = []
    per_day: dict = {}
    skipped = 0
    dropped = len(recs) - len(order)
    for i in order:
        t = recs[i]
        if cx is not None and cx.excluded(t.symbol, t.entry_date):
            dropped += 1
            continue
        open_ = [j for j in open_ if recs[j].exit_date >= t.entry_date]
        w = min(t.weight, config.MAX_SYMBOL_PCT)
        cl = _cluster_of(t.symbol)
        ok = (len(open_) < config.MAX_OPEN_POSITIONS
              and sum(1 for j in open_ if _cluster_of(recs[j].symbol) == cl) < config.MAX_POSITIONS_PER_CLUSTER
              and per_day.get(t.entry_date, 0) < config.MAX_ORDERS_PER_DAY
              and sum(min(recs[j].weight, config.MAX_SYMBOL_PCT) for j in open_) + w <= config.MAX_GROSS_EXPOSURE_PCT + 1e-12
              and sum(min(recs[j].weight, config.MAX_SYMBOL_PCT) * recs[j].stop_frac for j in open_)
              + w * t.stop_frac <= config.MAX_PORTFOLIO_HEAT_PCT + 1e-12)
        if ok:
            admitted.append(i)
            open_.append(i)
            per_day[t.entry_date] = per_day.get(t.entry_date, 0) + 1
        else:
            skipped += 1
    admitted.sort()
    return admitted, {"admitted": len(admitted), "skipped_by_caps": skipped, "excluded_trades": dropped,
                      "exclusion": "causal" if causal_exclusion else "static"}


def _capped_ok(cap_total: float, sh_u: float, sh_c: float) -> bool:
    """Capped replay gate: positive return, positive uncapped Sharpe, and at least ``CAPPED_SHARPE_MIN_RATIO`` of it kept."""
    return bool(cap_total > 0 and sh_u > 0 and sh_c >= CAPPED_SHARPE_MIN_RATIO * sh_u)


def _cost_delay_ok(cost_total: float, delay_total: float) -> bool:
    """Both stresses must stay positive: 2x costs AND a one-bar signal delay (the delay is a GATE)."""
    return bool(cost_total > 0 and delay_total > 0)


def _breadth_needed(k_universe: int) -> int:
    return int(max(config.POOLED_BREADTH_MIN, math.ceil(0.5 * k_universe)))


def _variance_used(var_floor: float, t_eff: float) -> float:
    """Var[SR] used for the deflation: never below 1/T_eff (a single-pattern run has no cross-sectional variance)."""
    return float(max(var_floor, 1.0 / max(t_eff, 2.0)))


def effective_q(recs: Sequence[TradeRec]) -> int:
    """Bartlett bandwidth per pattern: its longest observed hold, floored at POOLED_HAC_LAGS."""
    return int(max([config.POOLED_HAC_LAGS] + [t.bars for t in recs]))


# ------------------------------------------------------------------ cluster-preserving random-entry null
@dataclass
class NullOut:
    p_value: float
    observed: Optional[float]
    null_mean: Optional[float]
    null_std: Optional[float]
    draws: int
    n_trades: int
    n_clusters: int = 0
    unfit: int = 0
    unfit_share: float = 0.0
    failed: bool = False


class _Concat:
    """The pre-hold-out bars of every symbol in one array, so one vectorised simulation covers all of them."""

    def __init__(self, sds: dict, order: list[str]):
        self.offset: dict[str, int] = {}
        o, h, lo, c, atr = [], [], [], [], []
        pos = 0
        for s in order:
            sd = sds[s]
            self.offset[s] = pos
            o.append(sd.o), h.append(sd.h), lo.append(sd.lo), c.append(sd.c), atr.append(sd.atr)
            pos += len(sd.o)
        self.o, self.h, self.lo, self.c, self.atr = (np.concatenate(a) if a else np.zeros(0)
                                                      for a in (o, h, lo, c, atr))


def _cluster_ids(recs: Sequence[TradeRec]) -> np.ndarray:
    """Entry-week cluster of each trade (0..C-1); with NULL_CLUSTER_PRESERVING False every trade is its own."""
    if not NULL_CLUSTER_PRESERVING:
        return np.arange(len(recs))
    _, inv = np.unique([t.week for t in recs], return_inverse=True)
    return inv


def _offset_tables(recs: Sequence[TradeRec], sds: dict, H: np.ndarray, cl: np.ndarray):
    """For every trade and calendar offset: the shifted entry bar and whether it stays inside the trade's own fold
    window with room for its hold. Then per cluster the offsets valid for EVERY member; members that cannot share
    any offset are dropped one at a time (fewest valid offsets first) and kept at their real entry (never drawn
    independently)."""
    deltas = np.arange(-NULL_OFFSET_DAYS, NULL_OFFSET_DAYS + 1)
    T = len(recs)
    NE = np.zeros((T, len(deltas)), dtype=np.int64)
    VAL = np.zeros((T, len(deltas)), dtype=bool)
    for i, t in enumerate(recs):
        sd = sds[t.symbol]
        days = sd.dates.values.astype("datetime64[D]").astype(np.int64)
        target = days[t.entry] + deltas
        new = np.searchsorted(days, target, side="left")
        hi = max(t.w_lo, t.w_hi - 1 - int(H[i]))
        VAL[i] = (new >= t.w_lo) & (new <= hi) & (new < sd.n_pre)
        NE[i] = np.where(VAL[i], new, t.entry)
    fixed = np.zeros(T, dtype=bool)
    C = int(cl.max()) + 1 if T else 0
    valid_lists = []
    for c in range(C):
        mem = np.flatnonzero(cl == c)
        keep = list(mem)
        while keep:
            ok = VAL[keep].all(axis=0)
            if ok.any():
                break
            worst = min(keep, key=lambda i: (int(VAL[i].sum()), i))
            keep.remove(worst)
            fixed[worst] = True
        ok = VAL[keep].all(axis=0) if keep else np.zeros(len(deltas), dtype=bool)
        valid_lists.append(np.flatnonzero(ok) if ok.any() else np.array([NULL_OFFSET_DAYS]))
    for i in np.flatnonzero(fixed):
        NE[i, :] = recs[i].entry
    return NE, valid_lists, fixed


def null_tables(recs: Sequence[TradeRec], sds: dict) -> dict:
    """Everything the cluster-preserving null draws from: holds, cluster ids, the shifted-entry table and the valid
    offsets per cluster (padded), and which members could not fit a shared offset."""
    sig_holds = [t.bars for t in recs if t.reason in ("signal", "end_of_data") and t.bars >= 1]
    fallback = int(round(np.median(sig_holds))) if sig_holds else V._FALLBACK_HOLD
    H = np.array([max(1, t.bars) if t.reason in ("signal", "end_of_data") else max(1, t.bars, fallback)
                  for t in recs])
    cl = _cluster_ids(recs)
    NE, valid_lists, fixed = _offset_tables(recs, sds, H, cl)
    maxv = max(len(v) for v in valid_lists)
    VL = np.zeros((len(valid_lists), maxv), dtype=np.int64)
    for c, v in enumerate(valid_lists):
        VL[c, :len(v)] = v
    return {"H": H, "cl": cl, "NE": NE, "VL": VL, "nval": np.array([len(v) for v in valid_lists]), "fixed": fixed}


def draw_entries(NE: np.ndarray, VL: np.ndarray, nval: np.ndarray, cl: np.ndarray, rng: np.random.Generator,
                 nb: int) -> np.ndarray:
    """(T x nb) shifted LOCAL entry bars: each cluster draws ONE offset per draw, shared by all of its members."""
    C = len(nval)
    pick = np.floor(rng.random((C, nb)) * nval[:, None]).astype(np.int64)
    dsel = VL[np.arange(C)[:, None], pick]                           # (C, nb) offset index per cluster and draw
    return NE[np.arange(len(cl))[:, None], dsel[cl]]


POOLED_NULL_MAX_DRAWS = 100_000      # the escalation cap (1 000 -> 10 000 -> 100 000 draws, about 1 s a pattern)


def pooled_null(recs: Sequence[TradeRec], sds: dict, cc: _Concat, *, draws: int, seed: int, key: str,
                comm: float, slip: float, family_size: int, alpha: float,
                max_draws: int = POOLED_NULL_MAX_DRAWS) -> NullOut:
    """Pooled random-entry null (section 7). Statistic: pooled mean R per trade. All members of an entry-week cluster
    shift by ONE shared random calendar offset on their own symbol's bars, so the null keeps the real trades'
    cross-sectional co-movement; every draw stays inside each trade's own fold window. If more than
    ``POOLED_NULL_MAX_UNFIT`` of the trades cannot fit a shared offset the null fails closed (p = 1)."""
    T = len(recs)
    if T == 0:
        return NullOut(1.0, None, None, None, draws, 0)
    tb = null_tables(recs, sds)
    H, cl, NE, VL, nval, fixed = tb["H"], tb["cl"], tb["NE"], tb["VL"], tb["nval"], tb["fixed"]
    n_unfit = int(fixed.sum())
    obs = float(np.mean([t.R for t in recs]))
    C = len(nval)
    if n_unfit / T > config.POOLED_NULL_MAX_UNFIT:
        return NullOut(1.0, obs, None, None, 0, T, C, n_unfit, n_unfit / T, failed=True)
    off = np.array([cc.offset[t.symbol] for t in recs])
    nend = np.array([cc.offset[t.symbol] + sds[t.symbol].n_pre - 1 for t in recs])
    sa = float(config.ATR_STOP_MULT)
    ta = float(config.TAKE_PROFIT_ATR_MULT)
    dvec = np.ones(T)
    nan_ = np.full(T, np.nan)

    def run(nd: int) -> np.ndarray:
        rng = V._rng(key, seed)
        parts = []
        for start in range(0, nd, NULL_BLOCK):
            nb = min(NULL_BLOCK, nd - start)
            e = draw_entries(NE, VL, nval, cl, rng, nb) + off[:, None]   # global entry bars
            e = np.minimum(e, (nend - 1)[:, None])
            Hm = np.maximum(np.minimum(H[:, None], nend[:, None] - e), 1)
            er = e.ravel()
            bc = lambda a: np.broadcast_to(a[:, None], e.shape).ravel()   # noqa: E731
            j = np.maximum(er - 1, 0)
            ref, a = cc.c[j], cc.atr[j]
            stop_abs, target_abs = ref - sa * a, ref + ta * a
            pnl = V._simulate_random_trades(cc.o, cc.h, cc.lo, cc.c, er, bc(dvec), bc(nan_), bc(nan_), Hm.ravel(),
                                            comm, slip, stop_abs=stop_abs, target_abs=target_abs)
            sf = sa * a / ref
            r = np.where(np.isfinite(sf) & (sf > 0), pnl / np.where(sf > 0, sf, 1.0), 0.0)
            parts.append(r.reshape(T, nb).mean(axis=0))
        return np.concatenate(parts) if parts else np.zeros(0)

    def pval(null: np.ndarray, nd: int) -> float:
        return (1.0 + float((null >= obs).sum())) / (1.0 + nd)

    d = draws
    null = run(d)
    p = pval(null, d)
    while p <= alpha and d < max_draws:        # every candidate rejection is re-drawn at 10x (1k -> 10k -> 100k)
        d = min(d * 10, max_draws)
        null = run(d)
        p = pval(null, d)
    return NullOut(p, obs, float(null.mean()), float(null.std()), d, T, C, n_unfit, n_unfit / T)


def bh_with_floor(p_values: Sequence[float], alpha: float, m_floor: int) -> tuple[np.ndarray, np.ndarray]:
    """BH over the evaluated p-values PADDED with p = 1 up to ``m_floor`` hypotheses: a narrowed run is corrected for
    the whole registered family, never for fewer tests."""
    p = list(map(float, p_values))
    n = len(p)
    rej, q = V.benjamini_hochberg(p + [1.0] * max(0, int(m_floor) - n), alpha)
    return rej[:n], q[:n]


# ------------------------------------------------------------------ hold-out (pooled)
def _holdout_pooled(sds: dict, order: list[str], cand: V.Candidate, end, comm: float, slip: float) -> Optional[dict]:
    axes = []
    for s in order:
        sd = sds[s]
        if len(sd.df) - sd.n_pre >= 2:
            axes.append(_naive(sd.df.index[sd.n_pre:]))
    if not axes:
        return None
    axis = axes[0]
    for a in axes[1:]:
        axis = axis.union(a)
    series = np.zeros(len(axis))
    n_trades = 0
    traded = set()
    for s in order:
        sd = sds[s]
        if len(sd.df) - sd.n_pre < 2 or cand.pattern not in sd.sigs:
            continue
        run = V._run(sd.df, sd.sigs[cand.pattern], cand, sd.n_pre, len(sd.df), comm, slip)
        post = sd.df.iloc[sd.n_pre:]
        o, c = post["Open"].to_numpy(dtype=float), post["Close"].to_numpy(dtype=float)
        atr = engine.atr_series(post)
        pos = axis.get_indexer(_naive(post.index))
        for t in run.trades:
            sf = _stop_frac(atr, c, t.entry_index, cand.stop_atr or config.ATR_STOP_MULT)
            if not np.isfinite(sf) or sf <= 0:
                continue
            rho = _trade_rho(o, c, t.entry_index, t.exit_index, t.pnl_pct, comm, slip)
            np.add.at(series, pos[t.entry_index:t.exit_index + 1], (config.RISK_PER_TRADE_PCT / sf) * rho)
            n_trades += 1
            traded.add(s)
    return {"start": str(pd.Timestamp(end).date()), "n_bars": int(len(axis)), "n_trades": int(n_trades),
            "n_symbols": len(traded), "total_return": float(series.sum()),
            "sharpe": float(P.sharpe_annual(series)), "returns": [round(float(x), 8) for x in series]}


# ------------------------------------------------------------------ the run
def _new_run_id() -> str:
    from trials.registry import POOLED_RUN_PREFIX
    return POOLED_RUN_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _last_verdict(prior: Optional[dict], now: datetime) -> Optional[dict]:
    """Display-only fallback for an incomplete run: the last stored verdict with its data age, while it is younger
    than ``POOLED_FALLBACK_MAX_AGE_HOURS``. It is never an eligibility input."""
    if not isinstance(prior, dict) or prior.get("unit") != "pooled":
        return None
    src = prior if prior.get("status") == RUN_OK else (prior.get("last_verdict") or None)
    if not isinstance(src, dict):
        return None
    asof = src.get("generated_at") or prior.get("generated_at")
    try:
        gen = datetime.fromisoformat(asof)
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    age = (now - gen).total_seconds() / 3600.0
    if age > config.POOLED_FALLBACK_MAX_AGE_HOURS:
        return None
    pats = src.get("patterns", [])
    return {"generated_at": gen.isoformat(), "age_hours": round(age, 2), "display_only": True,
            "order_eligible": False, "universe_hash": (src.get("universe") or {}).get("hash"),
            "patterns": [{"pattern": p.get("pattern"), "status": p.get("status")} for p in pats]}


def _base_payload(run_id: str, uni: list[str], uhash: str, end, now: datetime) -> dict:
    return {"unit": "pooled", "mode": config.POOLED_VALIDATION, "shadow_only": True, "order_eligible": False,
            "status": RUN_OK, "run_id": run_id, "generated_at": now.isoformat(),
            "method_version": POOLING_METHOD_VERSION, "holdout_start": str(pd.Timestamp(end).date()),
            "warmup_bars": config.POOLED_WARMUP_BARS, "asof": None, "disclosure": DISCLOSURE,
            "universe": {"hash": uhash, "symbols": uni, "k": len(uni), "missing": [], "missing_reasons": {},
                         "complete": False, "partial": False, "flags": []},
            "narrowed": False, "n_trials": None, "n_trials_floor": pooled_universe_trials(),
            "sharpe_var": None, "pbo": None, "pbo_xs": None, "funnel": None, "patterns": [], "retired": [],
            "shadow_signals": [], "last_verdict": None, "message": None}


def _fail(payload: dict, status: str, message: str) -> dict:
    payload["status"] = status
    payload["message"] = message
    payload["universe"]["complete"] = False
    for p in payload.get("patterns", []):
        p["status"] = UNVALIDATED
    return payload


class _Unset:
    def __repr__(self) -> str:
        return "<registry not given>"


REGISTRY_UNSET = _Unset()


def evaluate_pooled(frames: dict, patterns: Optional[dict] = None, *, universe: Optional[Sequence[str]] = None,
                    end=config.HOLDOUT_START, ledger=None, holdout_store=None, registry=REGISTRY_UNSET,
                    prior: Optional[dict] = None, run_id: Optional[str] = None, draws: int = config.NULL_DRAWS,
                    seed: int = config.SEED, commission: float = config.COMMISSION_PCT,
                    slippage: float = config.SLIPPAGE_PCT, warmup: Optional[int] = None,
                    test: int = config.WF_TEST_BARS, step: int = config.WF_STEP_BARS,
                    bootstrap_draws: Optional[int] = None, xs_splits: Optional[int] = None,
                    now: Optional[datetime] = None, write_components: bool = True) -> dict:
    """Pooled validation of ``patterns`` (default: the registry minus the retired ones) over ``frames``.

    Returns a JSON-able payload (``unit="pooled"``). Never raises for data problems: they become statuses. An
    unreadable ledger / hold-out store / registry fails closed (nothing validates). ``prior`` is the last stored
    pooled payload (for the nightly Var[SR] floor and the display-only fallback). A run on a strict subset of the
    universe is ``pooled_universe_incomplete`` (or flagged partial and unable to read the hold-out); a run on a subset
    of the patterns is deflated with N >= ``pooled_universe_trials()``, corrected over the whole family and, without a
    nightly Var[SR], not deflatable (``no_nightly_variance``).

    ISOLATION: ``registry=None`` means the REAL trial registry (``state/trials.sqlite`` + ``data/trials``);
    ``registry=False`` means none. When a ledger or hold-out store is injected (a test or an ad-hoc research call), the
    registry must be given explicitly: leaving it out would quietly write the injected run into the live trial DB
    (review round 1), so it raises ``ValueError``. For research use ``evaluate_pooled_research``."""
    if registry is REGISTRY_UNSET:
        if ledger is not None or holdout_store is not None:
            raise ValueError("evaluate_pooled: a ledger or hold-out store was injected but `registry` was not given; "
                             "pass registry=False (no trial registry) or registry=None (the live one) explicitly, "
                             "or call evaluate_pooled_research")
        registry = None
    now = now or datetime.now(timezone.utc)
    warmup = config.POOLED_WARMUP_BARS if warmup is None else int(warmup)
    bdraws = config.POOLED_BOOTSTRAP_DRAWS if bootstrap_draws is None else int(bootstrap_draws)
    xsn = config.POOLED_XS_SPLITS if xs_splits is None else int(xs_splits)
    registry_patterns = pooled_registry() if patterns is None else patterns
    ret = retired_patterns()
    names = sorted(p for p in registry_patterns if p not in ret)
    registered = registered_universe()
    uni = sorted(set(universe)) if universe is not None else registered
    uhash = universe_hash(uni)
    run_id = run_id or _new_run_id()
    out = _base_payload(run_id, uni, uhash, end, now)
    out["retired"] = [{"pattern": p, "duplicate_of": d, "reason": "identical signals; retired by rule, blind to "
                       "pooled results; stays in N"} for p, d in sorted(ret.items())]
    holdout_start = out["holdout_start"]
    try:
        shadow_guard()
    except RuntimeError as e:
        return _fail(out, RUN_NOT_SHADOW_SAFE, str(e))

    if not uni:
        return _fail(out, RUN_INCOMPLETE, "the pooled universe is empty")
    approved = dict(getattr(config, "POOLED_APPROVED_UNIVERSES", {}) or {})
    if uni != registered and uhash not in approved:
        return _fail(out, RUN_UNREGISTERED, "a universe other than the registered rule universe needs its own decision "
                     "entry (config.POOLED_APPROVED_UNIVERSES) recording why it was chosen")
    own_ledger = ledger is None
    try:
        if ledger is None:
            from trials.pooled_ledger import PooledLedger
            ledger = PooledLedger()
        if uhash not in approved and ledger.derived_from(uni) is not None:
            return _fail(out, RUN_DERIVED, "this universe is a strict subset of an earlier registered universe "
                         "(narrowed after its per-symbol results were stored): refused")
    except Exception as e:
        out["last_verdict"] = _last_verdict(prior, now)
        return _fail(out, RUN_TRIALS_UNAVAILABLE, f"{type(e).__name__}: {e}"[:200])
    try:
        return V._clean(_evaluate(out, frames, registry_patterns, names, uni, uhash, end, holdout_start, ledger,
                                  holdout_store, registry, prior, draws, seed, commission, slippage, warmup, test,
                                  step, bdraws, xsn, now, write_components))
    except Exception as e:
        from trials.pooled_ledger import PooledLedgerError
        from trials.registry import TrialRegistryError
        if isinstance(e, (PooledLedgerError, TrialRegistryError)):
            logger.error("pooled validation fails closed: %s", e)
            out["last_verdict"] = _last_verdict(prior, now)
            return _fail(out, RUN_TRIALS_UNAVAILABLE, f"{type(e).__name__}: {e}"[:200])
        raise
    finally:
        if own_ledger and ledger is not None:
            ledger.close()


def evaluate_pooled_research(frames: dict, patterns: Optional[dict] = None, *, ledger=None, holdout_store=None,
                             **kw) -> dict:
    """Ad-hoc / research entry point: NEVER writes the live trial registry, the trial parquet files, the live ledger or
    the live hold-out store. A private in-memory ledger and hold-out store are used unless the caller passes its own."""
    from state.holdout import HoldoutStore
    from trials.pooled_ledger import PooledLedger
    for forbidden in ("registry", "write_components"):
        if forbidden in kw:
            raise TypeError(f"evaluate_pooled_research does not accept {forbidden!r}: it is forced off")
    own = ledger is None
    ledger = PooledLedger(":memory:") if ledger is None else ledger
    holdout_store = HoldoutStore(":memory:") if holdout_store is None else holdout_store
    try:
        return evaluate_pooled(frames, patterns, ledger=ledger, holdout_store=holdout_store, registry=False,
                               write_components=False, **kw)
    finally:
        if own:
            ledger.close()


def _evaluate(out, frames, registry_patterns, names, uni, uhash, end, holdout_start, ledger, holdout_store, registry,
              prior, draws, seed, commission, slippage, warmup, test, step, bdraws, xsn, now, write_components):
    # ---- data usability: a missing universe symbol is never dropped silently
    sds: dict[str, SymbolData] = {}
    missing: dict[str, str] = {}
    for s in uni:
        ok, why = _usable(frames.get(s), end, warmup, test)
        if ok:
            sds[s] = _prepare_symbol(s, frames[s], end)
        else:
            missing[s] = why
    out["universe"]["missing"] = sorted(missing)
    out["universe"]["missing_reasons"] = dict(sorted(missing.items()))
    frac = len(missing) / len(uni)
    if frac > config.POOLED_MAX_MISSING:
        out["universe"]["flags"] = [RUN_INCOMPLETE]
        out["last_verdict"] = _last_verdict(prior, now)
        return _fail(out, RUN_INCOMPLETE, f"{len(missing)} of {len(uni)} universe symbols are missing or unusable "
                     f"(more than {config.POOLED_MAX_MISSING:.0%}): not pooled-validatable; alert-only")
    partial = bool(missing)
    out["universe"]["partial"] = partial
    out["universe"]["complete"] = not partial
    if partial:
        out["universe"]["flags"] = [PARTIAL_FLAG]
    order = sorted(sds)
    out["asof"] = str(max(sd.df.index[-1] for sd in sds.values()).date())
    ledger.register_universe(uhash, uni)

    # ---- per-symbol signals and the walk-forward geometry (shared by every pattern)
    for s in order:
        sd = sds[s]
        sd.sigs = V.compute_signals(sd.df, registry_patterns, names)
        folds = V.make_folds(sd.n_pre, warmup, test, step)
        sd.folds = folds
        sd.test_start, sd.oos_end = folds[0].test_start, folds[-1].test_end
    axis = sds[order[0]].dates[sds[order[0]].test_start:sds[order[0]].oos_end]
    for s in order[1:]:
        sd = sds[s]
        axis = axis.union(sd.dates[sd.test_start:sd.oos_end])
    for s in order:
        sds[s].axis_pos = axis.get_indexer(sds[s].dates)
    cc = _Concat(sds, order)
    T = len(axis)
    out["oos"] = {"days": int(T), "start": str(axis[0].date()), "end": str(axis[-1].date()),
                  "bars_min": int(min(sd.n_pre for sd in sds.values())),
                  "bars_max": int(max(sd.n_pre for sd in sds.values()))}
    comm_s, slip_s = commission * config.COST_STRESS_MULT, slippage * config.COST_STRESS_MULT

    narrowed = len(names) < len(pooled_pattern_names())
    out["narrowed"] = bool(narrowed)

    # ---- phase 1: per-pattern pooled objects, stresses, null, concentration, capped replay
    res: dict[str, dict] = {}
    series: dict[str, np.ndarray] = {}
    comps: dict[str, np.ndarray] = {}
    recs_by: dict[str, list[TradeRec]] = {}
    walk: dict[str, dict] = {}
    for pat in names:
        cand = _cand(pat)
        failed = [s for s in order if pat not in sds[s].sigs]
        r = {"pattern": pat, "status": UNVALIDATED, "reasons": [], "gates": {}, "order_eligible": False}
        res[pat] = r
        if failed:
            r["reasons"].append("pattern_failed")
            r["failed_symbols"] = failed
            r["n_trades"] = 0
            continue
        recs: list[TradeRec] = []
        cost_recs: list[TradeRec] = []
        delay_recs: list[TradeRec] = []
        for s in order:
            sd = sds[s]
            wf = V.walk_forward(sd.df, [cand], signals=sd.sigs, train=warmup, test=test, step=step, end=end,
                                commission=commission, slippage=slippage)
            recs += _recs_from_oos(sd, wf.oos_trades)
            cost_recs += _recs_from_oos(sd, V._replay(sd.df, wf, sd.sigs, config.COST_STRESS_MULT, 0).oos_trades)
            delay_recs += _recs_from_oos(sd, V._replay(sd.df, wf, sd.sigs, 1.0, 1).oos_trades)
        comp = _build_components(recs, sds, axis, order, commission, slippage)
        ser = comp.sum(axis=1)
        series[pat], comps[pat], recs_by[pat] = ser, comp, recs
        cost_ser = _build_components(cost_recs, sds, axis, order, comm_s, slip_s).sum(axis=1)
        delay_ser = _build_components(delay_recs, sds, axis, order, commission, slippage).sum(axis=1)
        for t in recs:      # nothing at or after the hold-out may enter the OOS series
            assert t.entry < sds[t.symbol].oos_end <= sds[t.symbol].n_pre
        R = np.asarray([t.R for t in recs])
        weeks = [t.week for t in recs]
        n_clusters = len(set(weeks))
        by_sym = {s: sum(1 for t in recs if t.symbol == s) for s in order}
        k_br = sum(1 for v in by_sym.values() if v >= config.POOLED_BREADTH_MIN_TRADES)
        need_br = _breadth_needed(len(uni))
        q = effective_q(recs)
        t_eff = P.bartlett_t_eff(ser, q)
        excl = excluded_symbols(recs)
        adm, cap_info = capped_replay(recs, (), causal_exclusion=True)
        cap_w = [min(recs[i].weight, config.MAX_SYMBOL_PCT) for i in adm]
        cap_ser = _build_components([recs[i] for i in adm], sds, axis, order, commission, slippage,
                                    weights=cap_w).sum(axis=1)
        sh_u, sh_c = P.sharpe_annual(ser), P.sharpe_annual(cap_ser)
        conc = concentration(recs, axis, test)
        nul = pooled_null(recs, sds, cc, draws=draws, seed=seed, key=f"pooled|{cand.key}|{pat}", comm=commission,
                          slip=slippage, family_size=max(len(names), pooled_universe_trials()),
                          alpha=config.FDR_ALPHA / 2.0)
        rng = V._rng(f"pooled-boot|{pat}", seed)
        boot = P.stationary_bootstrap_sharpe(ser, bdraws, config.POOLED_BOOTSTRAP_BLOCK, rng) if len(recs) else None
        cboot = P.cluster_bootstrap_mean(R, weeks, bdraws, rng) if len(recs) else None
        psr = P.psr_teff(ser, t_eff) if len(recs) else None
        walk[pat] = {"t_eff": t_eff}
        r.update({
            "n_trades": len(recs), "n_clusters": n_clusters, "breadth": int(k_br), "breadth_needed": int(need_br),
            "mean_r": float(R.mean()) if len(R) else None,
            "pooled_return": float(ser.sum()), "sharpe": float(sh_u), "psr": psr, "t_eff": float(t_eff), "q": q,
            "null_p": nul.p_value, "null_draws": nul.draws, "null_observed": nul.observed,
            "null_clusters": nul.n_clusters, "null_unfit": nul.unfit, "null_failed": nul.failed,
            "cost_return": float(cost_ser.sum()), "delay_return": float(delay_ser.sum()),
            "concentration": conc, "excluded_symbols": excl,
            "capped": {**cap_info, "pooled_return": float(cap_ser.sum()), "sharpe": float(sh_c),
                       "sharpe_ratio_to_uncapped": float(sh_c / sh_u) if sh_u > 0 else None,
                       "max_concurrent_uncapped": int(_max_concurrent(recs)),
                       "ok": _capped_ok(float(cap_ser.sum()), float(sh_u), float(sh_c))},
            "symbols": symbol_table(recs, order, excl), "bootstrap_sharpe": boot, "bootstrap_mean_r": cboot,
            "max_hold_bars": int(max([t.bars for t in recs] + [0])),
        })
        g = r["gates"]
        g["pooled_min_trades"] = len(recs) >= config.POOLED_MIN_TRADES and n_clusters >= config.POOLED_MIN_CLUSTERS
        g["breadth"] = k_br >= need_br
        g["oos_positive"] = float(ser.sum()) > 0
        g["psr"] = psr is not None and psr > config.OOS_PSR_MIN
        g["concentration"] = bool(conc["ok"])
        g["cost_delay"] = _cost_delay_ok(float(cost_ser.sum()), float(delay_ser.sum()))
        g["capped_replay"] = bool(r["capped"]["ok"])

    evaluated = [p for p in names if "pooled_min_trades" in res[p]["gates"]]

    # ---- phase 2: ledger N, BH, Var[SR], DSR
    reg = None if registry is False else registry       # registry=False: no trial registry (unit tests only)
    reg_created = False
    if registry is None and evaluated:
        from trials import TrialRegistry
        from services import trial_runs
        reg = TrialRegistry(out["run_id"], db_path=trial_runs.trials_db_path(), unit="pooled")
        reg_created = True
    versions = {}
    dup_hash: dict[str, str] = {}
    for pat in names:
        fn = registry_patterns.get(pat)
        tv = trial_version(pat, fn, uhash, end, commission, slippage)
        versions[pat] = tv
        dup = None
        if pat in series:
            hsh = hashlib.sha1(np.round(series[pat], 12).tobytes()).hexdigest()
            if hsh in dup_hash and float(np.abs(series[pat]).sum()) > 0:
                dup = dup_hash[hsh]
            else:
                dup_hash.setdefault(hsh, pat)
        res[pat]["trial_version"] = tv
        res[pat]["duplicate_of"] = dup
        ledger.register_trial(holdout_start, tv, pattern=pat, params_hash=_params_hash(pat), universe_hash=uhash,
                              asset_class=ASSET_SPLIT, method_version=POOLING_METHOD_VERSION, run_id=out["run_id"],
                              duplicate_of=dup)
        if dup:
            ledger.set_duplicate(holdout_start, tv, dup)
    if not narrowed:       # a full run also registers the retired patterns: they stay in N
        for rp, dup_of in sorted(retired_patterns().items()):
            if rp in registry_patterns or rp in pooled_registry():
                fn = registry_patterns.get(rp) or pooled_registry().get(rp)
                ledger.register_trial(holdout_start, trial_version(rp, fn, uhash, end, commission, slippage),
                                      pattern=rp, params_hash=_params_hash(rp), universe_hash=uhash,
                                      asset_class=ASSET_SPLIT, method_version=POOLING_METHOD_VERSION,
                                      run_id=out["run_id"], duplicate_of=dup_of, retired=True)
    n_ledger = ledger.count(holdout_start)
    n_trials = max(n_ledger, pooled_universe_trials())
    out["n_trials"] = int(n_trials)
    out["n_trials_ledger"] = int(n_ledger)

    if reg is not None:
        for pat in evaluated:
            reg.record(symbol="POOLED", pattern=pat, params=_cand(pat).params(),
                       returns=pd.Series(series[pat], index=axis), strategy_version=versions[pat],
                       data_hash=_data_hash(sds, order))
        if reg_created or evaluated:
            reg.flush()
    matrix = pd.DataFrame({p: series[p] for p in evaluated}, index=axis) if evaluated else pd.DataFrame(index=axis)
    var_emp = selection.sharpe_variance(matrix) if len(evaluated) > 1 else 0.0
    prior_var = None
    if isinstance(prior, dict) and prior.get("status") == RUN_OK and not prior.get("narrowed"):
        pv = prior.get("sharpe_var")
        prior_var = float(pv) if isinstance(pv, (int, float)) and math.isfinite(pv) and pv > 0 else None
    has_floor = prior_var is not None
    var_floor = max(var_emp, prior_var or 0.0)
    out["sharpe_var"] = float(var_floor) if var_floor > 0 and np.isfinite(var_floor) else None
    out["sharpe_var_empirical"] = float(var_emp) if np.isfinite(var_emp) else None
    out["no_nightly_variance"] = bool(narrowed and not has_floor)
    if evaluated:
        rej, q = bh_with_floor([res[p]["null_p"] for p in evaluated], config.FDR_ALPHA / 2.0,
                               max(len(names), pooled_universe_trials()))
        for p, rj, qq in zip(evaluated, rej, q):
            res[p]["bh_adjusted_p"] = float(qq)
            res[p]["gates"]["null_bh"] = bool(rj) and not res[p]["null_failed"]
    for p in evaluated:
        r = res[p]
        te = walk[p]["t_eff"]
        v = _variance_used(var_floor, te)
        r["n_trials"] = int(n_trials)
        r["sharpe_var_used"] = float(v)
        d = P.dsr_teff(series[p], n_trials, v, te) if r["n_trades"] else None
        r["dsr"] = d
        r["dsr_p"] = None if d is None else float(1.0 - d)
        deflatable = not out["no_nightly_variance"]
        r["gates"]["dsr"] = bool(deflatable and r["dsr_p"] is not None and r["dsr_p"] < config.DSR_P_MAX / 2.0)
        r["deflation_reason"] = None
        if not deflatable:
            r["deflation_reason"] = "no_nightly_variance"
        elif not r["gates"]["dsr"]:
            r["deflation_reason"] = "dsr_not_significant"
    # advisory PBO (time CSCV on the (date x pooled trial) matrix) and the cross-sectional CSCV
    out["pbo"] = None
    if len(evaluated) >= 2 and T >= 2 * config.PBO_S:
        try:
            out["pbo"] = float(selection.pbo_cscv(matrix.to_numpy(), S=config.PBO_S))
        except Exception:
            out["pbo"] = None
    out["pbo_xs"] = None
    if len(evaluated) >= 2:
        arr = np.stack([comps[p] for p in evaluated], axis=2)
        xs = P.cross_sectional_cscv(arr, xsn, V._rng("pooled-xs", seed))
        out["pbo_xs"] = None if xs is None else xs["pbo_xs"]

    # ---- phase 3: the pooled hold-out for survivors only, then the final status
    pre_hold = ("pooled_min_trades", "breadth", "oos_positive", "psr", "null_bh", "concentration", "cost_delay",
                "capped_replay")
    fail_reason = {"pooled_min_trades": "insufficient_pooled_trades", "breadth": "insufficient_breadth",
                   "oos_positive": "oos_not_positive", "psr": "oos_psr_too_low", "null_bh": "not_bh_significant",
                   "concentration": "pooled_concentrated", "cost_delay": "cost_or_delay_stress_fails",
                   "capped_replay": "capped_replay_fails"}
    for p in names:
        r = res[p]
        if p not in evaluated:
            continue
        if r["null_failed"]:
            r["reasons"].append("pooled_null_unfit")
        for gname in pre_hold:
            if not r["gates"].get(gname):
                if not (gname == "null_bh" and r["null_failed"]):
                    r["reasons"].append(fail_reason[gname])
        r["pooled_version"] = pooled_version(p, registry_patterns.get(p), uhash, end, commission, slippage,
                                             sorted(missing))
        r["holdout"] = None
        r["holdout_frozen"] = False
        r["holdout_reads_of_pattern"] = None
        if r["reasons"]:
            r["gates"]["holdout"] = False
            r["holdout_state"] = "not_read"
            continue
        _holdout_gate(r, p, sds, order, end, holdout_start, ledger, holdout_store, commission, slippage, partial,
                      seed, missing)
    for p in names:
        r = res[p]
        if p not in evaluated:
            r["status"] = UNVALIDATED
            continue
        if r["reasons"]:
            r["status"] = UNVALIDATED
        elif not r["gates"]["dsr"]:
            r["status"] = POOLED_OOS_VALIDATED
        else:
            r["status"] = POOLED_DEFLATED_VALIDATED
        r["order_eligible"] = False
    out["patterns"] = [res[p] for p in names]
    out["shadow_signals"] = _shadow_signals(sds, order, res, names)
    out["funnel"] = _funnel(res, names, n_trials, out, out["run_id"], uhash)
    if write_components and comps and evaluated:
        _write_components(out["run_id"], axis, comps, evaluated, order)
    return out


def _params_hash(pattern: str) -> str:
    from trials import params_hash
    return params_hash(_cand(pattern).params())


def _data_hash(sds: dict, order: list[str]) -> str:
    from trials import data_hash
    h = hashlib.sha256()
    for s in order:
        h.update(s.encode())
        h.update(data_hash(sds[s].df).encode())
    return h.hexdigest()[:32]


def _max_concurrent(recs: Sequence[TradeRec]) -> int:
    if not recs:
        return 0
    ev = sorted([(t.entry_date, 1) for t in recs] + [(t.exit_date + pd.Timedelta(days=1), -1) for t in recs])
    cur = best = 0
    for _, d in ev:
        cur += d
        best = max(best, cur)
    return best


_HOLDOUT_MIN_RETURN = 0.0      # the pooled hold-out must not have lost money (a mutant test lowers it)


def _holdout_gate(r: dict, pat: str, sds, order, end, holdout_start, ledger, store, commission, slippage, partial,
                  seed, missing) -> None:
    """Read the pooled hold-out ONCE per pooled version (first-write-wins) and at most once per pattern per
    HOLDOUT_START; fail closed on any store / ledger failure; a partial universe never reads it fresh."""
    cand = _cand(pat)
    hv = r["pooled_version"]
    key = f"pooled|{pat}|{cand.key}|{hv}"
    reasons = r["reasons"]

    def closed(why: str, e: Optional[Exception] = None) -> None:
        logger.error("pooled hold-out %s (%s)%s: pattern stays unvalidated", why, key, f": {e}" if e else "")
        r["holdout"], r["holdout_frozen"] = None, False
        r["gates"]["holdout"] = False
        reasons.append("holdout_store_unavailable")

    if store is None:
        closed("store missing")
        return
    try:
        got = store.get(hv, key)
    except Exception as e:
        closed("read failed", e)
        return
    if got is not None:
        res_, asof, k = got.get("result"), got.get("asof"), got.get("reads_of_pattern")
        r["holdout_frozen"] = True
    else:
        if partial:
            r["gates"]["holdout"] = False
            r["holdout_state"] = "not_read_partial_universe"
            reasons.append("holdout_not_read_partial_universe")
            return
        try:
            allowed, k = ledger.read_allowed(holdout_start, pat, hv)
        except Exception as e:
            closed("ledger read failed", e)
            return
        if not allowed:
            r["gates"]["holdout"] = False
            r["holdout_state"] = "read_cap"
            r["holdout_reads_of_pattern"] = k - 1
            reasons.append("holdout_read_cap")
            return
        try:
            ledger.record_read(holdout_start, pat, hv)       # counted BEFORE the read: a crash never hides one
        except Exception as e:
            closed("ledger write failed", e)
            return
        res_ = _holdout_pooled(sds, order, cand, end, commission, slippage)
        asof = str(max(sd.df.index[-1] for sd in sds.values()).date())
        if res_ is not None:
            try:
                if not store.put(hv, key, {"result": res_, "asof": asof, "reads_of_pattern": k}):
                    got2 = store.get(hv, key)           # lost a race: the stored first read wins
                    res_, asof, k = got2["result"], got2.get("asof"), got2.get("reads_of_pattern")
                    r["holdout_frozen"] = True
            except Exception as e:
                closed("write failed", e)
                return
    r["holdout_asof"] = asof
    r["holdout_reads_of_pattern"] = k
    if res_ is None:
        r["gates"]["holdout"] = False
        r["holdout_state"] = "unavailable"
        reasons.append("holdout_unavailable")
        return
    r["holdout"] = {kk: vv for kk, vv in res_.items() if kk != "returns"}
    ok = res_["total_return"] >= _HOLDOUT_MIN_RETURN
    if not ok:
        reasons.append("holdout_negative")
    if res_["n_trades"] < config.POOLED_HOLDOUT_MIN_TRADES:
        ok = False
        reasons.append("holdout_too_few_trades")
    if ok and k and k > 1:     # a disclosed re-read: Bonferroni over every read of this pattern
        p = P.stationary_bootstrap_mean_p(res_.get("returns") or [], config.POOLED_BOOTSTRAP_DRAWS,
                                          config.POOLED_BOOTSTRAP_BLOCK, V._rng(f"pooled-ho|{pat}", seed))
        r["holdout"]["reread_p"] = p
        r["holdout"]["bonferroni_k"] = int(k)
        if p is None or p >= 0.05 / k:
            ok = False
            reasons.append("holdout_reread_not_significant")
    r["gates"]["holdout"] = bool(ok)
    r["holdout_state"] = "read"


def _funnel(res: dict, names: list[str], n_trials: int, out: dict, run_id: str, uhash: str) -> dict:
    """patterns_tested -> pooled_min_trades -> breadth -> oos_positive -> psr -> null_bh -> dsr -> concentration ->
    cost_delay -> capped_replay -> holdout -> pooled_validated (each stage a subset of the last)."""
    alive = [p for p in names]
    f = {"unit": "pooled", "patterns_tested": len(names)}
    for g in GATE_ORDER:
        alive = [p for p in alive if res[p]["gates"].get(g)]
        f[g] = len(alive)
    f["pooled_oos_validated"] = sum(1 for p in names if res[p]["status"] == POOLED_OOS_VALIDATED)
    f["pooled_validated"] = sum(1 for p in names if res[p]["status"] == POOLED_DEFLATED_VALIDATED)
    f.update({"n_trials": n_trials, "sharpe_var": out.get("sharpe_var"), "pbo": out.get("pbo"),
              "pbo_xs": out.get("pbo_xs"), "run_id": run_id, "universe_hash": uhash,
              "orders": 0, "firing_tonight": len(out.get("shadow_signals", []))})
    return f


def _shadow_signals(sds: dict, order: list[str], res: dict, names: list[str]) -> list[dict]:
    """Every pooled pattern's firing on the last bar (kind = pooled_pattern): journaled, scored, never ordered."""
    sig = []
    for p in names:
        if p not in res or "pattern_failed" in res[p].get("reasons", []):
            continue
        for s in order:
            sd = sds[s]
            ser = sd.sigs.get(p)
            if ser is None or len(ser) == 0 or int(ser.iloc[-1]) <= 0:
                continue
            sig.append({"kind": "pooled_pattern", "symbol": s, "pattern": p,
                        "signal_bar_date": str(pd.Timestamp(sd.df.index[-1]).date()),
                        "pooled_status": res[p]["status"], "stale": bool(sd.df.attrs.get("stale")),
                        "excluded": s in (res[p].get("excluded_symbols") or []), "order_eligible": False})
    return sig


def _write_components(run_id: str, axis: pd.DatetimeIndex, comps: dict, pats: list[str], order: list[str]) -> None:
    from trials import registry as _treg
    from trials.registry import TrialRegistryError
    cols = {f"{p}|{s}": comps[p][:, i] for p in pats for i, s in enumerate(order)}
    df = pd.DataFrame(cols, index=pd.DatetimeIndex(axis, name="date"))
    try:
        d = _treg.default_trials_dir()
        d.mkdir(parents=True, exist_ok=True)
        df.to_parquet(d / f"{run_id}.components.parquet")
    except Exception as e:
        raise TrialRegistryError(f"could not write the pooled components: {e}") from e
    prune_components()


def prune_components(keep: Optional[int] = None) -> list[str]:
    """Retention for the pooled per-symbol component files (about 0.8 MB per nightly run): keep the newest
    ``config.POOLED_COMPONENTS_KEEP`` ``pooled-*.components.parquet`` files and delete the older ones. Only these
    files are touched (never the trial registry's own parquet, the per-symbol runs or the ledger). Never raises."""
    removed: list[str] = []
    try:
        from trials import registry as _treg
        from trials.registry import POOLED_RUN_PREFIX
        n = config.POOLED_COMPONENTS_KEEP if keep is None else int(keep)
        files = sorted(_treg.default_trials_dir().glob(f"{POOLED_RUN_PREFIX}*.components.parquet"))
        for f in (files[:-n] if n > 0 else files):
            try:
                f.unlink()
                removed.append(f.name)
            except OSError:
                pass
    except Exception as e:
        logger.info("pooled component retention skipped (%s: %s)", type(e).__name__, e)
    return removed
