"""Scan services: technical / pairs / ML scans, the full pipeline, and the API pattern endpoints.

One copy of the logic for the CLI (`main.py scan`), the scheduler, the API and the dashboard:

* ``scan_patterns``  (API)  every pattern signal in the recency window with IN-SAMPLE display metrics.
* ``technical_candidates`` / ``scan_technical`` (CLI) the same signals, restricted to validated or OOS-positive
  candidates (the legacy in-sample 'informational' tier is retired), with the SAME in-sample metrics attached,
  so the two paths return identical candidates and metrics (tests/test_parity.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

import backtester
import config
import execution
import signals
import validation
from alerts import format_pairs_alert, format_signal_alert, send_daily_summary
from api.errors import ApiError
from claude_integration import generate_briefing
from ml_patterns import MLPatternDetector, ml_scan_candidate
from patterns import PATTERN_REGISTRY
from services import pairs as pairs_service
from services import session as sess_mod
from services import trial_runs
from services.common import clean, finite, require_data
from services.models import (LatestSignal, PatternDetectResponse, PatternScanResponse, PricePoint, ScanFailure,
                             ScanSignal, TechnicalCandidate)
from services.providers import DataProvider, default_provider
from services.session import dispatch_intents, open_session

logger = logging.getLogger(__name__)

ML_CANDIDATE = signals.ML_CANDIDATE
UNVALIDATED = "unvalidated"  # nothing is order-eligible without a Phase 2 validation status


# ══════════════════════════════════════════════════════════════
#  helpers
# ══════════════════════════════════════════════════════════════

def check_recent_signal(df: pd.DataFrame, window: int = config.VALIDATION_WINDOW_DAYS) -> int:
    if "signal" not in df.columns or len(df) < 2:
        return 0
    recent = df.tail(window)["signal"]
    nz = recent[recent != 0].dropna()
    if nz.empty:
        return 0
    return 1 if nz.iloc[-1] > 0 else -1


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}%}"


def _num(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


def list_patterns() -> dict:
    return {"patterns": list(PATTERN_REGISTRY.keys()), "count": len(PATTERN_REGISTRY)}


def fetch_research(data: dict, provider: Optional[DataProvider] = None) -> dict:
    """Long-history frames (config.RESEARCH_LOOKBACK_DAYS) for walk-forward / hold-out validation.

    A symbol whose long fetch fails keeps its short frame; it then fails validation for lack of history
    (never silently validated on less data)."""
    provider = provider or default_provider()
    out = {}
    for sym, short in data.items():
        try:
            df = provider.ohlcv(sym, config.RESEARCH_LOOKBACK_DAYS)
        except Exception as e:
            logger.warning(f"research fetch failed for {sym} ({type(e).__name__}); using the short frame")
            df = None
        if df is None or df.empty:
            logger.warning(f"no research-length history for {sym}; it cannot validate")
            df = short
        out[sym] = df
    return out


def _holdout_store():
    """Persistent first-read hold-out store; None (with an error log) if it cannot be opened."""
    try:
        from state.holdout import HoldoutStore
        return HoldoutStore()
    except Exception as e:
        logger.error(f"hold-out store unavailable ({type(e).__name__}: {e}); hold-out reads are not frozen")
        return None


def _oos_display(cr) -> dict:
    """Out-of-sample metrics of a validation.CandidateResult, formatted for alerts / the report."""
    o = cr.oos or {}
    ho = cr.holdout or {}
    return {
        "win_rate": _pct(o.get("win_rate")), "profit_factor": _num(o.get("profit_factor")),
        "total_return": _pct(o.get("total_return")), "max_drawdown": _pct(o.get("max_drawdown")),
        "sharpe": _num(o.get("sharpe")), "sortino": _num(o.get("sortino")),
        "expectancy": _num(o.get("expectancy_equity"), 4),
        "total_trades": cr.n_oos_trades, "oos_psr": cr.oos_psr, "holdout_return": ho.get("total_return"),
        "bh_adjusted_p": cr.bh_adjusted_p, "n_trials": cr.n_trials, "null_p": cr.null_p,
        "dsr": cr.dsr, "dsr_p": cr.dsr_p, "pbo": cr.pbo,
        "rejected_reasons": list(cr.rejected_reasons),
    }


def _best_result(results: list, symbol: str, pattern: str):
    """The strongest CandidateResult for (symbol, pattern): highest tier, then highest OOS PSR."""
    rank = {st: i for i, st in enumerate(signals.VALIDATION_TIERS)}
    best, best_key = None, None
    for r in results:
        if r.symbol == symbol and r.pattern == pattern:
            key = (rank.get(r.validation_status, -1), r.oos_psr if r.oos_psr is not None else -1.0)
            if best is None or key > best_key:
                best, best_key = r, key
    return best


def _oos_positive(cr) -> bool:
    """Enough OOS trades and positive OOS Sharpe, but not validated (reported as unvalidated, never orderable)."""
    sh = (cr.oos or {}).get("sharpe")
    return bool(sh is not None and sh > 0 and cr.n_oos_trades >= config.MIN_TRADES_OOS)


# ══════════════════════════════════════════════════════════════
#  one signal, in-sample display metrics (shared by CLI and API)
# ══════════════════════════════════════════════════════════════

def _now_like(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp.now(tz=ts.tz) if getattr(ts, "tz", None) is not None else pd.Timestamp.now()


def recent_direction(signals_df: pd.DataFrame, recency_days: Optional[int] = None) -> int:
    """+1 / -1 / 0. recency_days None: the last VALIDATION_WINDOW_DAYS bars (CLI default); otherwise the last
    non-zero signal when it is at most `recency_days` calendar days old (API)."""
    if recency_days is None:
        return check_recent_signal(signals_df)
    nz = signals_df[signals_df["signal"] != 0]
    if nz.empty:
        return 0
    last = nz.iloc[-1]
    if (_now_like(last.name) - last.name).days > recency_days:
        return 0
    return 1 if last["signal"] > 0 else -1


def in_sample_metrics(signals_df: pd.DataFrame, symbol: str, pattern: str) -> dict:
    """classic_backtest display metrics as fractions (non-finite -> None)."""
    bt = backtester.classic_backtest(signals_df, symbol, pattern)
    total_return = finite(bt.total_return_pct)
    return {
        "win_rate": finite(bt.win_rate),
        "profit_factor": None if bt.profit_factor_undefined else finite(bt.profit_factor),
        "sharpe": finite(bt.sharpe_ratio),
        "total_return": total_return,
        "total_return_pct": total_return,
        "max_drawdown": finite(bt.max_drawdown_pct),
        "total_trades": bt.total_trades,
        "is_valid": bt.is_valid,
    }


def _signal_fields(symbol: str, pattern: str, signals_df: pd.DataFrame) -> dict:
    nz = signals_df[signals_df["signal"] != 0]
    last = nz.iloc[-1]
    return {
        "symbol": symbol, "pattern": pattern, "signal": "BUY" if last["signal"] == 1 else "SELL",
        "signal_date": last.name.isoformat(), "days_ago": (_now_like(last.name) - last.name).days,
        "price": float(last["Close"]),
    }


def build_scan_signal(symbol: str, pattern: str, signals_df: pd.DataFrame, strict: bool = True) -> ScanSignal:
    """The ScanSignal for the last non-zero signal. strict=False: a failing in-sample backtest leaves the
    metrics None (CLI path) instead of raising (API path lists it under `failed`)."""
    fields = _signal_fields(symbol, pattern, signals_df)
    try:
        fields.update(in_sample_metrics(signals_df, symbol, pattern))
    except Exception as e:
        if strict:
            raise
        logger.warning("in-sample metrics unavailable for %s/%s (%s: %s)", symbol, pattern, type(e).__name__, e)
    return ScanSignal.model_validate(clean(fields))


# ══════════════════════════════════════════════════════════════
#  API pattern endpoints
# ══════════════════════════════════════════════════════════════

def detect_pattern(provider: DataProvider, symbol: str, pattern: str, period_days: int) -> PatternDetectResponse:
    df = require_data(provider.ohlcv(symbol, period_days), symbol)
    signals_df = PATTERN_REGISTRY[pattern](df)
    buys, sells = [], []
    for idx, row in signals_df.iterrows():
        if row.get("signal") == 1:
            buys.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})
        elif row.get("signal") == -1:
            sells.append({"date": idx.isoformat(), "price": round(float(row["Close"]), 4)})
    last_signals = signals_df[signals_df["signal"] != 0]
    latest = None
    if not last_signals.empty:
        last = last_signals.iloc[-1]
        latest = {"date": last.name.isoformat(), "signal": "BUY" if last["signal"] == 1 else "SELL",
                  "price": round(float(last["Close"]), 4)}
    return PatternDetectResponse.model_validate(clean({
        "symbol": symbol, "pattern": pattern, "total_signals": len(buys) + len(sells),
        "buys": buys, "sells": sells, "latest_signal": latest}))


def resolve_symbols(markets: Optional[list]) -> list[str]:
    markets = markets or list(config.WATCHLIST.keys())
    unknown = [m for m in markets if m not in config.WATCHLIST]
    if unknown:
        raise ApiError(f"Unknown market(s): {unknown}", {"markets": unknown, "known": sorted(config.WATCHLIST)},
                       status_code=422, code="unknown_market")
    return [s for m in markets for s in config.WATCHLIST[m]]


def scan_patterns(provider: DataProvider, markets: Optional[list] = None, patterns: Optional[list] = None,
                  recency_days: int = 30, period_days: Optional[int] = None) -> PatternScanResponse:
    """Scan the watchlist for recent pattern signals (IN-SAMPLE display statistics, status 'unvalidated').

    Anything that failed is listed in `failed`; nothing is swallowed."""
    symbols = resolve_symbols(markets)
    pattern_names = patterns or list(PATTERN_REGISTRY.keys())
    results, failed = [], []
    for sym in symbols:
        try:
            df = provider.ohlcv(sym, period_days)
        except Exception as e:
            logger.warning("pattern scan: fetch failed for %s: %s: %s", sym, type(e).__name__, e)
            failed.append(ScanFailure(symbol=sym, error=type(e).__name__, message=str(e)))
            continue
        if df.empty:
            logger.warning("pattern scan: no data for %s", sym)
            failed.append(ScanFailure(symbol=sym, error="NoData", message=f"No data for {sym}"))
            continue
        for pname in pattern_names:
            try:
                signals_df = PATTERN_REGISTRY[pname](df)
                if recent_direction(signals_df, recency_days) == 0:
                    continue
                results.append(build_scan_signal(sym, pname, signals_df))
            except Exception as e:
                logger.warning("pattern scan: %s on %s failed: %s: %s", pname, sym, type(e).__name__, e)
                failed.append(ScanFailure(symbol=sym, pattern=pname, error=type(e).__name__, message=str(e)))
    results.sort(key=lambda x: x.days_ago)
    return PatternScanResponse(count=len(results), signals=results, failed=failed)


def in_sample_scan(data: dict, patterns: Optional[list] = None, only_valid: bool = True) -> list[ScanSignal]:
    """Dashboard scan: in-sample display signals over the recent window (VALIDATION_WINDOW_DAYS bars)."""
    names = [p for p in (patterns or list(PATTERN_REGISTRY)) if p in PATTERN_REGISTRY]
    out = []
    for symbol, df in data.items():
        for pname in names:
            try:
                signals_df = PATTERN_REGISTRY[pname](df)
                if recent_direction(signals_df) == 0:
                    continue
                s = build_scan_signal(symbol, pname, signals_df)
                if only_valid and not s.is_valid:
                    continue
                out.append(s)
            except Exception as e:
                logger.debug("dashboard scan: %s on %s failed: %s", pname, symbol, e)
    return out


# ══════════════════════════════════════════════════════════════
#  TECHNICAL PATTERN SCAN (CLI)
# ══════════════════════════════════════════════════════════════

@dataclass
class _Hit:
    cand: TechnicalCandidate
    cr: object
    df: pd.DataFrame
    signals_df: pd.DataFrame
    recent: int
    direction: int
    bar_date: Optional[str]


def _sharpe_var(reg) -> Optional[float]:
    """Cross-trial variance of per-period Sharpe over the run's trials (what the DSR used), stored with the funnel
    so a single-candidate view can deflate against the same N and variance. None when it is not defined."""
    try:
        from stats import selection
        v = float(selection.sharpe_variance(reg.returns_matrix()))
        return v if v == v and abs(v) != float("inf") else None
    except Exception:
        return None


def _nightly_sharpe_var() -> Optional[float]:
    """Cross-trial Sharpe variance of the last full nightly run (floor for narrowed scans); None when unknown."""
    try:
        from results import store
        funnel = store.read_latest("funnel")
        v = finite(funnel[0].get("sharpe_var")) if funnel and isinstance(funnel[0], dict) else None
        return v if v is not None and v > 0 else None
    except Exception:
        return None


def _validate(data: dict, registry: dict, run_info: Optional[dict] = None) -> list:
    """Validate every candidate of the run against ONE trial registry (N = every evaluation, valid or not).

    The registry is built inside the guard: if it cannot be built or flushed, nothing from this scan is
    order-eligible (fail closed), exactly like a hold-out store failure. `run_info` (when given) receives
    the run id and the registry's trial count."""
    store = _holdout_store()
    try:
        reg = trial_runs.new_registry(trial_runs.new_run_id("tech"))
        if run_info is not None:
            run_info["run_id"] = reg.run_id
        # Validate the variant execution can trade (long-only, ATR exits) and freeze the first hold-out read.
        # Fail closed: without a working hold-out store nothing validates (reason holdout_store_unavailable).
        results = validation.evaluate_candidates(data, registry, executable_variant=True,
                                                 holdout_store=store, require_holdout_store=True,
                                                 trials=reg, min_trials=validation.universe_trials(),
                                                 sharpe_var_floor=_nightly_sharpe_var(), run_info=run_info)
        if run_info is not None:
            run_info["n_trials"] = int(reg.n_trials)
            run_info.setdefault("sharpe_var", _sharpe_var(reg))
    except Exception as e:
        logger.error(f"validation failed ({type(e).__name__}: {e}); nothing from this scan is order-eligible")
        results = []
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    n_valid = sum(1 for r in results if r.validation_status != UNVALIDATED)
    logger.info(f"Validated {n_valid}/{len(results)} candidates (BH alpha={config.FDR_ALPHA}, "
                f"hold-out from {config.HOLDOUT_START})")
    return results


def funnel_counts(results: list) -> dict:
    """Sequential gate funnel over every candidate a validation run tested (each stage is a subset of the last):
    tested -> enough OOS trades -> positive OOS return -> OOS PSR above the bar -> BH-significant -> survives the
    Deflated Sharpe (p < DSR_P_MAX with N = every trial of the run) -> may become an order (deflated_validated:
    also hold-out and cost-stress gates). Also reports the run's N and its advisory PBO (never a gate)."""
    tested = list(results)
    min_trades = [r for r in tested if r.n_oos_trades >= config.MIN_TRADES_OOS]
    oos_pos = [r for r in min_trades if ((r.oos or {}).get("total_return") or 0.0) > 0]
    psr = [r for r in oos_pos if r.oos_psr is not None and r.oos_psr > config.OOS_PSR_MIN]
    bh = [r for r in psr if r.bh_significant]
    dsr = [r for r in bh if r.dsr_p is not None and r.dsr_p < config.DSR_P_MAX]
    orders = [r for r in dsr if r.validation_status in config.ORDER_ELIGIBLE_STATUSES]
    return {"tested": len(tested), "min_trades": len(min_trades), "oos_positive": len(oos_pos),
            "psr": len(psr), "bh": len(bh), "dsr": len(dsr), "orders": len(orders),
            "n_trials": max((int(r.n_trials) for r in tested), default=0),
            "pbo": next((r.pbo for r in tested if r.pbo is not None), None)}


def orderable_frame(df: pd.DataFrame, bar_date: Optional[str]) -> bool:
    """May a signal on `bar_date` become an order? Not when the frame was served stale from the bar store, nor
    when the signal bar is not the last closed session (D12: entry at the NEXT open needs a current close).
    Frames without store attrs (direct callers, synthetic data) are not second-guessed here."""
    if df.attrs.get("stale"):
        return False
    last_closed = df.attrs.get("last_closed")
    if last_closed is not None and bar_date is not None:
        return pd.Timestamp(bar_date).normalize() == pd.Timestamp(last_closed).normalize()
    return True


def _technical_hits(data: dict, patterns: Optional[list], recency_days: Optional[int] = None,
                    funnel_out: Optional[dict] = None) -> list[_Hit]:
    pattern_list = [p for p in (patterns or list(PATTERN_REGISTRY.keys())) if p in PATTERN_REGISTRY]
    registry = {p: PATTERN_REGISTRY[p] for p in pattern_list}
    run_info: dict = {}
    results = _validate(data, registry, run_info)
    if funnel_out is not None:
        funnel_out.update(funnel_counts(results))
        funnel_out["run_id"] = run_info.get("run_id")
        funnel_out["sharpe_var"] = run_info.get("sharpe_var")
    hits: list[_Hit] = []
    for symbol, df in data.items():
        logger.info(f"\n─── {symbol} ({len(df)} bars) ───")
        for pat_name in pattern_list:
            try:
                signals_df = registry[pat_name](df)
                recent = recent_direction(signals_df, recency_days)
                if recent == 0:
                    continue
                cr = _best_result(results, symbol, pat_name) or validation.CandidateResult(
                    symbol=symbol, pattern=pat_name, params={})
                # Only validated or OOS-positive candidates are reported. The legacy in-sample 'informational'
                # tier (D11, retired in Phase 3) no longer lets a signal through.
                if not (cr.validation_status != UNVALIDATED or _oos_positive(cr)):
                    continue
                # D12: only a signal on the LAST bar is orderable, i.e. exactly the timing the executable variant
                # validated (entry at the next open); a one-bar-late signal is alert-only.
                direction, bar_date = signals.latest_signal(signals_df, max_staleness_bars=0)
                if direction != 0 and not orderable_frame(df, bar_date):
                    logger.warning(f"  {symbol}/{pat_name}: stale or not-current data; alert-only")
                    direction = 0
                base = build_scan_signal(symbol, pat_name, signals_df, strict=False)
                cand = TechnicalCandidate.model_validate(clean({
                    **base.model_dump(),
                    "validation_status": cr.validation_status, "signal_value": recent,
                    "signal_bar_date": bar_date, "orderable_direction": direction,
                    "oos": cr.oos, "holdout": cr.holdout, "n_oos_trades": cr.n_oos_trades,
                    "oos_psr": cr.oos_psr, "bh_adjusted_p": cr.bh_adjusted_p, "n_trials": cr.n_trials,
                    "null_p": cr.null_p, "dsr": cr.dsr, "dsr_p": cr.dsr_p, "pbo": cr.pbo,
                    "rejected_reasons": list(cr.rejected_reasons)}))
                hits.append(_Hit(cand, cr, df, signals_df, recent, direction, bar_date))
            except Exception as e:
                logger.warning(f"  {pat_name}: error — {e}")
    return hits


def technical_candidates(data: dict, patterns: Optional[list] = None,
                         recency_days: Optional[int] = None) -> list[TechnicalCandidate]:
    """Validated / OOS-positive candidates (no alerts, no orders). `data` should hold research-length history."""
    return [h.cand for h in _technical_hits(data, patterns, recency_days)]


def scan_technical(
    data: dict,
    patterns: Optional[list] = None,
    run_stress: bool = False,
    paper_trade: bool = False,
    intents: Optional[list] = None,
    recency_days: Optional[int] = None,
    candidates_out: Optional[list] = None,
    funnel_out: Optional[dict] = None,
) -> list[dict]:
    """Scan all symbols with all technical patterns, validating every (symbol x pattern) candidate.

    `data` should hold research-length history (see fetch_research). validation.evaluate_candidates runs the
    walk-forward, fixed hold-out, random-entry null and cost stress for ALL candidates and applies
    Benjamini-Hochberg across the whole run; each intent carries that run's validation_status.
    Reported (alert + order candidate) are signals that are validated, or OOS-positive with enough OOS trades
    (labelled unvalidated, so they can never reach a broker). Alert statistics are OUT-OF-SAMPLE.

    Order candidates are appended to `intents` when given (the caller dispatches them); otherwise,
    with paper_trade, they are dispatched at the end of this scan. Returns the legacy summary dicts; the typed
    TechnicalCandidate of each reported hit is appended to `candidates_out` when given (what the nightly run stores)."""
    logger.info("\n🔧 TECHNICAL PATTERN SCAN")
    logger.info("=" * 50)
    candidates = intents if intents is not None else []
    all_triggered = []

    for h in _technical_hits(data, patterns, recency_days, funnel_out):
        symbol, pat_name, cr, df = h.cand.symbol, h.cand.pattern, h.cr, h.df
        try:
            current_price = float(df["Close"].iloc[-1])
            summary = {"symbol": symbol, "pattern": pat_name, **_oos_display(cr)}
            summary["direction"] = "🟢 BUY" if h.recent == 1 else "🔴 SELL"
            summary["current_price"] = current_price
            summary["signal_value"] = h.recent
            summary["validation_status"] = cr.validation_status
            summary["signal_bar_date"] = h.bar_date
            summary["oos"] = cr.oos
            summary["holdout"] = cr.holdout

            if run_stress:
                from services import stress as stress_service
                stress = stress_service.stress_report(df, symbol, pat_name)
                summary["stress_test"] = stress.get("assessment", {})
                summary["monte_carlo"] = stress.get("monte_carlo", {})

            all_triggered.append(summary)
            if candidates_out is not None:
                candidates_out.append(h.cand)

            alert_msg = format_signal_alert(summary, summary["direction"], current_price)
            alert_msg += (f"\nStats above are OUT-OF-SAMPLE (walk-forward before {config.HOLDOUT_START}, "
                          f"{cr.n_oos_trades} OOS trades; hold-out return {_pct(summary['holdout_return'])})."
                          f"\nValidation: {cr.validation_status} "
                          f"(BH-adjusted p={_num(cr.bh_adjusted_p, 3)} over {cr.n_trials} candidates"
                          + (f"; failed: {', '.join(cr.rejected_reasons)}" if cr.rejected_reasons else "")
                          + (f"; not order-eligible: {cr.deflation_reason}"
                             + (f" (DSR p={_num(cr.dsr_p, 3)}, N={cr.n_trials})" if cr.dsr_p is not None else "")
                             if getattr(cr, "deflation_reason", None) else "")
                          + f") | Mode: {config.TRADING_MODE} (no orders without validation)")
            sess_mod._alert(alert_msg, kind="signal",
                            dedup_key=f"signal:{symbol}:{pat_name}:{h.recent}:{h.bar_date or df.index[-1]}")

            if h.direction != 0:
                candidates.append(execution.OrderIntent(
                    symbol=symbol, direction=h.direction, signal_bar_date=h.bar_date,
                    strategy_key=f"technical:{pat_name}", confidence=1.0,
                    validation_status=cr.validation_status, atr=signals.latest_atr(df), price=current_price))

            logger.info(f"  ✅ {pat_name}: {summary['direction']} [{cr.validation_status}] "
                        f"OOS Sharpe={summary['sharpe']} trades={cr.n_oos_trades}")
        except Exception as e:
            logger.warning(f"  {pat_name}: error — {e}")

    if intents is None and paper_trade:
        dispatch_intents(candidates)
    return all_triggered


# ══════════════════════════════════════════════════════════════
#  PAIRS / STAT-ARB SCAN (CLI)
# ══════════════════════════════════════════════════════════════

def scan_pairs(data: dict, paper_trade: bool = False) -> list[dict]:
    """Scan configured pairs (research-only GC/SI and CL/NG excluded). Alert only: pairs need a short leg."""
    logger.info("\n📈 PAIRS TRADING SCAN")
    logger.info("=" * 50)

    results = pairs_service.scan_pairs_data(data, trials=pairs_service.pairs_registry(data) if data else None)
    triggered = [r for r in results if r.get("has_signal")]

    for r in triggered:
        safe = {k: (0 if v is None else v) for k, v in r.items()}   # None (no losses / no half-life) -> 0 for the template
        msg = format_pairs_alert(safe) + (
            f"\nValidation: {UNVALIDATED} (pairs are alert-only, D3). Backtest figures above are walk-forward out-of-sample (formation 252, trade 63); "
            f"cointegration BH-adjusted p={_num(r.get('adj_pvalue'), 3)} over {r.get('n_tested', '?')} pairs.")
        sess_mod._alert(msg, kind="signal",
                        dedup_key=f"pairs:{r.get('symbol_a')}/{r.get('symbol_b')}:{r.get('signal_direction')}:"
                                  f"{datetime.now(timezone.utc).date().isoformat()}")
        if paper_trade:
            logger.info(f"  pairs are never executed (long-only): {r.get('signal_direction')} "
                        f"{r['symbol_a']}/{r['symbol_b']}")

    logger.info(f"\nPairs scanned: {len(results)} | Signals: {len(triggered)}")
    return triggered


# ══════════════════════════════════════════════════════════════
#  ML PATTERN SCAN (CLI)
# ══════════════════════════════════════════════════════════════

def scan_ml(data: dict, paper_trade: bool = False, intents: Optional[list] = None) -> list[dict]:
    """Run the ML models on all symbols (candidates handled as in scan_technical).

    Per symbol: ml_patterns.ml_scan_candidate runs the purged walk-forward and returns the OOS AUC, the
    baseline AUC, the latest OOS signal and the OOS backtest; the CV is run on rows BEFORE oos_start only.
    A signal is reported only when OOS AUC > baseline AUC. validation_status is at best "ml_oos_candidate"
    (NOT order-eligible): the candidate's own gates pass (AUC above baseline, >= MIN_TRADES_OOS OOS trades,
    PSR > OOS_PSR_MIN) AND Benjamini-Hochberg across the ML candidates of this run (p = 1 - OOS PSR) rejects."""
    logger.info("\n🤖 ML PATTERN SCAN")
    logger.info("=" * 50)

    candidates = intents if intents is not None else []
    evaluated = []   # one entry per symbol with an OOS evaluation

    for symbol, df in data.items():
        if len(df) < 200:
            continue
        try:
            logger.info(f"\n─── {symbol} ───")
            cand = ml_scan_candidate(df, symbol)
            oos_start = cand.get("oos_start")
            if oos_start is None or cand.get("oos_frame") is None:
                logger.info("  Skipping — not enough data for an out-of-sample evaluation")
                continue
            oos_df = cand["oos_frame"]

            # CV diagnostics on the rows before the OOS block only (feature ranking for the alert)
            train_df = df.loc[df.index < pd.Timestamp(oos_start)]
            metrics = MLPatternDetector().train(train_df)

            recent = check_recent_signal(oos_df)
            bt_result = backtester.classic_backtest(oos_df, symbol, "ml_ensemble")      # OOS rows only
            psr = getattr(bt_result, "psr", None)
            evaluated.append({"symbol": symbol, "df": df, "cand": cand, "bt": bt_result, "recent": recent,
                              "metrics": metrics, "oos_df": oos_df,
                              "p": 1.0 - psr if psr is not None else 1.0})
        except Exception as e:
            logger.warning(f"  ML scan failed for {symbol}: {e}")

    bh = {}
    if evaluated:
        rejected, q = validation.benjamini_hochberg([e["p"] for e in evaluated], config.FDR_ALPHA)
        bh = {e["symbol"]: (bool(r), float(qq)) for e, r, qq in zip(evaluated, rejected, q)}

    all_signals = []
    for e in evaluated:
        symbol, cand, bt_result, df = e["symbol"], e["cand"], e["bt"], e["df"]
        auc, base = cand.get("oos_auc"), cand.get("baseline_auc")
        beats_baseline = auc is not None and base is not None and auc > base
        bh_sig, bh_q = bh.get(symbol, (False, None))
        # ML never reaches ORDER_ELIGIBLE_STATUSES (no random-entry null, 2x-cost or fixed hold-out gate, and
        # its BH family is separate from the technical one): a candidate that clears its own gates is only
        # an "ml_oos_candidate" (alert-only) until ML passes the full WS2.3 gate set.
        status = ML_CANDIDATE if (cand.get("oos_validated") and bh_sig) else UNVALIDATED
        logger.info(f"  {symbol}: OOS AUC={_num(auc, 3)} baseline={_num(base, 3)} status={status}")
        if not beats_baseline or e["recent"] == 0:
            continue
        try:
            direction, bar_date = signals.latest_signal(e["oos_df"])
            if direction != 0 and not orderable_frame(df, bar_date):
                logger.warning(f"  {symbol}: stale or not-current data; ML signal is alert-only")
                direction = 0
            # ml_confidence in the OOS frame is p_up; the directional confidence is p_up (BUY) or 1 - p_up (SELL)
            nz = e["oos_df"][e["oos_df"]["signal"] != 0]
            if len(nz) and "ml_confidence" in nz:
                p_up = float(nz["ml_confidence"].iloc[-1])
            else:
                c0 = float(cand.get("confidence") or 0.0)
                p_up = c0 if cand.get("direction", 1) == 1 else 1.0 - c0
            conf = signals.ml_confidence(p_up, direction)
            o = cand.get("oos_backtest_summary") or {}      # numeric OOS summary from ml_scan_candidate
            signal_info = {
                "symbol": symbol,
                "direction": "🟢 BUY" if e["recent"] == 1 else "🔴 SELL",
                "confidence": f"{conf:.2f}",
                "oos_auc": f"{auc:.3f}", "baseline_auc": f"{base:.3f}", "model_accuracy": f"{auc:.3f}",
                "win_rate": _pct(o.get("win_rate")), "profit_factor": _num(o.get("profit_factor")),
                "total_return": _pct(o.get("total_return"), 2), "max_drawdown": _pct(o.get("max_drawdown"), 2),
                "sharpe": _num(o.get("sharpe")),
                "total_trades": o.get("total_trades", getattr(bt_result, "total_trades", None)),
                "oos_start": cand["oos_start"], "oos_psr": getattr(bt_result, "psr", None),
                "bh_adjusted_p": bh_q,
                "top_features": list(e["metrics"].get("top_features", {}).keys())[:5],
                "validation_status": status,
                "signal_bar_date": bar_date,
            }
            all_signals.append(signal_info)
            logger.info(f"  ✅ ML Signal: {signal_info['direction']} conf={conf:.2f} [{status}] "
                        f"OOS WR={signal_info['win_rate']} trades={signal_info['total_trades']}")

            if direction != 0:
                candidates.append(execution.OrderIntent(
                    symbol=symbol, direction=direction, signal_bar_date=bar_date,
                    strategy_key="ml:ml_ensemble", confidence=conf,
                    validation_status=status, atr=signals.latest_atr(df),
                    price=float(df["Close"].iloc[-1])))
        except Exception as ex:
            logger.warning(f"  ML scan failed for {symbol}: {ex}")

    if intents is None and paper_trade:
        dispatch_intents(candidates)
    return all_signals


# ══════════════════════════════════════════════════════════════
#  FULL SCAN
# ══════════════════════════════════════════════════════════════

def run_full_scan(
    markets: Optional[list] = None,
    patterns: Optional[list] = None,
    symbol_filter: Optional[str] = None,
    modes: Optional[list] = None,
    run_stress: bool = False,
    paper_trade: bool = False,
    provider: Optional[DataProvider] = None,
) -> dict:
    """Run the complete scan pipeline."""
    started = datetime.now(timezone.utc)
    try:
        results = _run_full_scan(markets, patterns, symbol_filter, modes, run_stress, paper_trade,
                                 provider or default_provider())
    except Exception as e:
        sess_mod.record_run("scan", started, "error", {"error": type(e).__name__})
        raise
    sess_mod.record_run("scan", started, "ok", {
        "technical": len(results.get("technical", [])), "pairs": len(results.get("pairs", [])),
        "ml": len(results.get("ml", [])), "decisions": results.get("decisions", []),
        "mode": config.TRADING_MODE})
    return results


def _run_full_scan(markets, patterns, symbol_filter, modes, run_stress, paper_trade, provider) -> dict:
    modes = modes or ["technical", "pairs", "ml"]

    logger.info("\n" + "=" * 60)
    logger.info("🤖 TRADING BOT v2 — FULL SCAN")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Modes: {', '.join(modes)} | TRADING_MODE={config.TRADING_MODE}")
    logger.info("=" * 60)

    # Reconcile with the broker before looking at anything (paper mode only; else zero broker calls)
    sess = open_session() if paper_trade else None
    try:
        if symbol_filter:
            data = {symbol_filter: provider.ohlcv(symbol_filter)}
            data = {k: v for k, v in data.items() if not v.empty}
        else:
            data = provider.watchlist(markets)

        if not data:
            logger.error("No data fetched. Check connections and symbols.")
            return {}

        logger.info(f"Data fetched for {len(data)} symbols\n")

        results = {"technical": [], "pairs": [], "ml": [], "decisions": [], "technical_candidates": [],
                   "technical_funnel": {}}
        candidates: list = []
        research = fetch_research(data, provider) if ("technical" in modes or "ml" in modes) else {}

        if "technical" in modes:
            results["technical"] = scan_technical(research, patterns, run_stress, paper_trade, intents=candidates,
                                              candidates_out=results["technical_candidates"],
                                              funnel_out=results["technical_funnel"])
        if "pairs" in modes:
            results["pairs"] = scan_pairs(data, paper_trade)
        if "ml" in modes:
            results["ml"] = scan_ml(research, paper_trade, intents=candidates)

        if paper_trade:
            results["decisions"] = dispatch_intents(candidates, session=sess)

        total = len(results["technical"]) + len(results["pairs"]) + len(results["ml"])
        logger.info("\n" + "=" * 60)
        logger.info(f"SCAN COMPLETE — {total} total signals")
        logger.info(f"  Technical: {len(results['technical'])}")
        logger.info(f"  Pairs:     {len(results['pairs'])}")
        logger.info(f"  ML:        {len(results['ml'])}")
        logger.info("=" * 60)

        send_daily_summary(results["technical"], results["pairs"], results["ml"])

        # AI briefing (advisory only; a failure raises one alert and the run continues)
        if config.ANTHROPIC_API_KEY and total > 0:
            logger.info("\n📝 Generating AI briefing...")
            br = generate_briefing(results["technical"], results["pairs"], results["ml"])
            if br.ok and br.text:
                print(f"\n{'='*50}\n📝 AI ANALYSIS\n{'='*50}\n{br.text}\n")
                sess_mod._alert(f"📝 AI ANALYSIS:\n\n{br.text}", kind="signal")
            elif not br.ok:
                sess_mod._alert(f"LLM briefing failed ({br.error.value}): {br.message}", kind="briefing_failed",
                                dedup_key=f"briefing_failed:{datetime.now(timezone.utc).date().isoformat()}")

        if sess is not None:
            try:
                acct = sess.broker.get_account()
                logger.info(f"\n💰 Paper Account: ${acct['equity']:,.2f} (cash: ${acct['cash']:,.2f})")
                logger.info(f"📂 Open positions: {len(sess.broker.get_positions())}")
            except Exception as e:
                logger.warning(f"account summary unavailable ({type(e).__name__})")

        return results
    finally:
        if sess is not None:
            sess.conn.close()
