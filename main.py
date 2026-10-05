#!/usr/bin/env python3
"""
Trading Pattern Bot v2 - command line entry point.

Pipeline: fetch -> detect -> backtest -> (Phase 2: validate) -> stress test -> alert -> order intents.
Nothing is order-eligible until a signal carries a validation_status in config.ORDER_ELIGIBLE_STATUSES,
so in Phase 1 every scan is effectively alert-only. There is no live trading mode.

Commands:
    python main.py scan [--mode technical pairs ml] [--symbol AAPL] [--market stocks]
                        [--stress AAPL ema_crossover] [--schedule --time 08:00] [--report] [--paper]
    python main.py risk init | status | halt --reason TEXT | resume --confirm | rebaseline --confirm | reconcile [--accept]
    python main.py check-llm
    python main.py rebalance --holdings holdings.csv [--contributions 500]
    python main.py backup --dest PATH

The old flat flags (python main.py --mode pairs, --paper, ...) still work and mean `scan`.
"""

import argparse
import html as html_lib
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import schedule

import config
import execution
import instruments
import signals
import validation
from alerts import (
    format_pairs_alert, format_signal_alert,
    send_alert, send_daily_summary,
)
from backtester import classic_backtest
from claude_integration import check_llm, generate_briefing
from data_fetcher import fetch_ohlcv, fetch_watchlist
from logging_setup import install_redaction
from ml_patterns import MLPatternDetector, ml_scan_candidate
from pairs_trading import scan_all_pairs
from patterns import PATTERN_REGISTRY
from risk_manager import RiskManager
from state import db as state_db
from stress_test import full_stress_test

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ML_CANDIDATE = signals.ML_CANDIDATE
UNVALIDATED = "unvalidated"  # Phase 2 upgrades this per signal; nothing is eligible before then


def check_recent_signal(df: pd.DataFrame, window: int = config.VALIDATION_WINDOW_DAYS) -> int:
    if "signal" not in df.columns or len(df) < 2:
        return 0
    recent = df.tail(window)["signal"]
    nz = recent[recent != 0].dropna()
    if nz.empty:
        return 0
    return 1 if nz.iloc[-1] > 0 else -1


# ══════════════════════════════════════════════════════════════
#  ALERTS, EXECUTION SESSION, INTENT DISPATCH
# ══════════════════════════════════════════════════════════════

def _alert(message: str, kind: str = "signal", dedup_key: Optional[str] = None) -> bool:
    """send_alert with state-DB dedup when the DB exists (alerts never need the DB to work)."""
    conn = None
    if dedup_key:
        try:
            conn = state_db.require_initialized()
        except Exception:
            conn = None
    try:
        return bool(send_alert(message, kind=kind, dedup_key=dedup_key if conn else None, conn=conn))
    finally:
        if conn is not None:
            conn.close()


def execution_enabled() -> bool:
    """True only in TRADING_MODE=paper with PAPER_TRADE_ENABLED. There is no live mode."""
    return config.TRADING_MODE == "paper" and bool(config.PAPER_TRADE_ENABLED)


def get_broker():
    """The only broker constructor used by the CLI: Alpaca PAPER, wrapped for execution.py."""
    return execution.AlpacaBroker()


@dataclass
class ExecSession:
    broker: object
    risk: RiskManager
    conn: object


def _announce_events(events: list[dict], seen: set) -> None:
    for ev in events:
        kind = ev.get("type", "halt")
        key = f"{kind}:{ev.get('at', '')}:{json.dumps(ev.get('reasons', ev.get('reason', '')), sort_keys=True)}"
        if key in seen:
            continue
        seen.add(key)
        detail = ev.get("reason") or ", ".join(ev.get("reasons", []))
        if kind == "baseline_seeded":
            _alert(f"ACCOUNT BASELINE SET [{config.TRADING_MODE}]: {detail}\nIf you did not just run "
                   f"`risk init` / `risk rebaseline --confirm`, check the paper account.", kind="halt", dedup_key=key)
            continue
        title = "TRADING HALTED" if kind == "halt" else "RECONCILIATION MISMATCH"  # UNPROTECTED arrives as a halt
        _alert(f"{title} [{config.TRADING_MODE}]: {detail}\nNo orders will be placed until resolved "
               f"(python main.py risk status).", kind=kind if kind in ("halt", "reconcile_mismatch") else "halt",
               dedup_key=key)


def _cancel_if_halted(broker, risk: RiskManager, conn) -> list:
    """CANCEL_ON_HALT: when the sleeve is halted or the kill switch is on, cancel unfilled entry orders
    (never the protective stop / take-profit legs) and alert. Errors are alerted, never raised."""
    if not config.CANCEL_ON_HALT:
        return []
    reasons = risk.check().reasons
    if not any(r == "kill_switch" or r.startswith("halted") for r in reasons):
        return []
    try:
        ids = execution.cancel_open_entries(broker, conn)
    except Exception as e:
        logger.error(f"cancel-on-halt failed ({type(e).__name__})")
        _alert(f"CANCEL ON HALT FAILED [{config.TRADING_MODE}]: {type(e).__name__}. Check open entry orders "
               f"on the paper account by hand.", kind="halt")
        return []
    if ids:
        _alert(f"CANCEL ON HALT [{config.TRADING_MODE}]: cancelled {len(ids)} unfilled entry order(s) "
               f"({', '.join(reasons)}). Protective stops were left in place.", kind="halt")
    return ids


def open_session() -> Optional[ExecSession]:
    """Prepare order routing: state DB must exist, reconcile with the broker, refresh sleeve equity.

    Returns None (alert-only run, zero broker calls) when trading is off or the state DB is missing."""
    if not execution_enabled():
        logger.info(f"Alert-only run (TRADING_MODE={config.TRADING_MODE}, "
                    f"PAPER_TRADE_ENABLED={config.PAPER_TRADE_ENABLED}): no orders, no broker calls")
        return None
    try:
        conn = state_db.require_initialized()
    except state_db.StateNotInitialized as e:
        logger.error(f"Trading refused: {e}. Run `python main.py risk init` first.")
        return None
    broker = get_broker()
    risk = RiskManager(conn)
    seen: set = set()
    try:
        rec = execution.reconcile(broker, conn)
        _announce_events(rec.events, seen)
        if rec.positions is None:  # broker unreadable: never treat it as a flat book or seed a baseline from it
            logger.error("Session setup refused: the paper account could not be read; no orders this run")
            conn.close()
            return None
        # Sleeve equity = configured dollars + realized and open P&L (account equity vs. its baseline, D10).
        _announce_events(execution.refresh_sleeve_equity(risk, broker, rec.positions), seen)
        _cancel_if_halted(broker, risk, conn)
    except Exception as e:
        logger.error(f"Session setup failed ({type(e).__name__}); no orders this run")
        conn.close()
        return None
    return ExecSession(broker, risk, conn)


def dispatch_intents(candidates: list, session: Optional[ExecSession] = None) -> list[dict]:
    """One intent per (symbol, bar) -> execution.submit_intent -> alert AFTER each Decision.

    Returns [{symbol, direction, status, reasons, mode}] for every Decision made."""
    intents = signals.build_intents(candidates)
    if not intents:
        return []
    own = session is None
    sess = session or open_session()
    if sess is None:
        for i in intents:
            logger.info(f"  alert-only: {i.symbol} {'BUY' if i.direction == 1 else 'SELL'} "
                        f"[{i.validation_status}] bar {i.signal_bar_date}")
        return []
    out, seen, cancelled_once = [], set(), False
    try:
        for i in intents:
            d = execution.submit_intent(i, broker=sess.broker, conn=sess.conn, risk_manager=sess.risk)
            side = "BUY" if i.direction == 1 else "SELL"
            _announce_events(d.events, seen)
            if d.status == "halted" and not cancelled_once:
                cancelled_once = True
                _cancel_if_halted(sess.broker, sess.risk, sess.conn)
            msg = (f"ORDER DECISION [{config.TRADING_MODE}] {side} {i.symbol} (bar {i.signal_bar_date}): "
                   f"{d.status}" + (f" - {', '.join(d.reasons)}" if d.reasons else "")
                   + (f"\nqty {d.qty} stop {d.stop_price} take-profit {d.limit_price}" if d.qty else ""))
            _alert(msg, kind="order_decision", dedup_key=f"order_decision:{execution.signal_key(i.symbol, side.lower(), i.signal_bar_date)}:{d.status}")
            logger.info(f"  order decision {i.symbol} {side}: {d.status} {d.reasons}")
            out.append({"symbol": i.symbol, "direction": i.direction, "status": d.status,
                        "reasons": list(d.reasons), "mode": config.TRADING_MODE})
    finally:
        if own:
            sess.conn.close()
    return out


# ══════════════════════════════════════════════════════════════
#  VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════

def fetch_research(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Long-history frames (config.RESEARCH_LOOKBACK_DAYS) for walk-forward / hold-out validation.

    A symbol whose long fetch fails keeps its short frame; it then fails validation for lack of history
    (never silently validated on less data)."""
    out = {}
    for sym, short in data.items():
        try:
            df = fetch_ohlcv(sym, period_days=config.RESEARCH_LOOKBACK_DAYS)
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


def _pct(x, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{digits}%}"


def _num(x, digits: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{digits}f}"


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
    """Informational tier: enough OOS trades and positive OOS Sharpe, but not validated."""
    sh = (cr.oos or {}).get("sharpe")
    return bool(sh is not None and sh > 0 and cr.n_oos_trades >= config.MIN_TRADES_OOS)


# ══════════════════════════════════════════════════════════════
#  TECHNICAL PATTERN SCAN
# ══════════════════════════════════════════════════════════════

def scan_technical(
    data: dict[str, pd.DataFrame],
    patterns: list[str] | None = None,
    run_stress: bool = False,
    paper_trade: bool = False,
    intents: list | None = None,
) -> list[dict]:
    """Scan all symbols with all technical patterns, validating every (symbol x pattern) candidate.

    `data` should hold research-length history (see fetch_research). validation.evaluate_candidates runs the
    walk-forward, fixed hold-out, random-entry null and cost stress for ALL candidates and applies
    Benjamini-Hochberg across the whole run; each intent carries that run's validation_status.
    Reported (alert + order candidate) are signals that are validated, or OOS-positive with enough OOS trades
    (labelled unvalidated, so they can never reach a broker). Alert statistics are OUT-OF-SAMPLE.

    Order candidates are appended to `intents` when given (the caller dispatches them); otherwise,
    with paper_trade, they are dispatched at the end of this scan."""
    logger.info("\n🔧 TECHNICAL PATTERN SCAN")
    logger.info("=" * 50)

    pattern_list = [p for p in (patterns or list(PATTERN_REGISTRY.keys())) if p in PATTERN_REGISTRY]
    registry = {p: PATTERN_REGISTRY[p] for p in pattern_list}
    all_triggered = []
    candidates = intents if intents is not None else []

    store = _holdout_store()
    try:
        # Validate the variant execution can trade (long-only, ATR exits) and freeze the first hold-out read.
        # Fail closed: without a working hold-out store nothing validates (reason holdout_store_unavailable).
        results = validation.evaluate_candidates(data, registry, executable_variant=True,
                                                 holdout_store=store, require_holdout_store=True)
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

    for symbol, df in data.items():
        logger.info(f"\n─── {symbol} ({len(df)} bars) ───")

        for pat_name in pattern_list:
            try:
                signals_df = registry[pat_name](df)
                recent = check_recent_signal(signals_df)
                if recent == 0:
                    continue
                cr = _best_result(results, symbol, pat_name) or validation.CandidateResult(
                    symbol=symbol, pattern=pat_name, params={})
                if not (cr.validation_status != UNVALIDATED or _oos_positive(cr)):
                    # Legacy informational tier (to be retired in Phase 3): the in-sample display heuristic.
                    # Such a signal is still labelled unvalidated, so it can never reach a broker.
                    if not classic_backtest(signals_df, symbol, pat_name).is_valid:
                        continue

                current_price = float(df["Close"].iloc[-1])
                # D12: only a signal on the LAST bar is orderable, i.e. exactly the timing the executable variant validated
                # (entry at the next open); a one-bar-late signal is alert-only.
                direction, bar_date = signals.latest_signal(signals_df, max_staleness_bars=0)
                summary = {"symbol": symbol, "pattern": pat_name, **_oos_display(cr)}
                summary["direction"] = "🟢 BUY" if recent == 1 else "🔴 SELL"
                summary["current_price"] = current_price
                summary["signal_value"] = recent
                summary["validation_status"] = cr.validation_status
                summary["signal_bar_date"] = bar_date
                summary["oos"] = cr.oos
                summary["holdout"] = cr.holdout

                if run_stress:
                    stress = full_stress_test(df, symbol, registry[pat_name], pat_name)
                    summary["stress_test"] = stress.get("assessment", {})
                    summary["monte_carlo"] = stress.get("monte_carlo", {})

                all_triggered.append(summary)

                alert_msg = format_signal_alert(summary, summary["direction"], current_price)
                alert_msg += (f"\nStats above are OUT-OF-SAMPLE (walk-forward before {config.HOLDOUT_START}, "
                              f"{cr.n_oos_trades} OOS trades; hold-out return {_pct(summary['holdout_return'])})."
                              f"\nValidation: {cr.validation_status} "
                              f"(BH-adjusted p={_num(cr.bh_adjusted_p, 3)} over {cr.n_trials} candidates"
                              + (f"; failed: {', '.join(cr.rejected_reasons)}" if cr.rejected_reasons else "")
                              + f") | Mode: {config.TRADING_MODE} (no orders without validation)")
                _alert(alert_msg, kind="signal",
                       dedup_key=f"signal:{symbol}:{pat_name}:{recent}:{bar_date or df.index[-1]}")

                if direction != 0:
                    candidates.append(execution.OrderIntent(
                        symbol=symbol, direction=direction, signal_bar_date=bar_date,
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
#  PAIRS / STAT-ARB SCAN
# ══════════════════════════════════════════════════════════════

def scan_pairs(
    data: dict[str, pd.DataFrame],
    paper_trade: bool = False,
) -> list[dict]:
    """Scan configured pairs (research-only GC/SI and CL/NG excluded). Alert only: pairs need a short leg."""
    logger.info("\n📈 PAIRS TRADING SCAN")
    logger.info("=" * 50)

    results = scan_all_pairs(data, instruments.default_pairs())
    triggered = [r for r in results if r.get("has_signal")]

    for r in triggered:
        safe = {k: (0 if v is None else v) for k, v in r.items()}   # None (no losses / no half-life) -> 0 for the template
        msg = format_pairs_alert(safe) + (
            f"\nValidation: {UNVALIDATED} (pairs are alert-only, D3). Backtest figures above are in-sample; "
            f"cointegration BH-adjusted p={_num(r.get('adj_pvalue'), 3)} over {r.get('n_tested', '?')} pairs.")
        _alert(msg, kind="signal",
               dedup_key=f"pairs:{r.get('symbol_a')}/{r.get('symbol_b')}:{r.get('signal_direction')}:"
                         f"{datetime.now(timezone.utc).date().isoformat()}")
        if paper_trade:
            logger.info(f"  pairs are never executed (long-only): {r.get('signal_direction')} "
                        f"{r['symbol_a']}/{r['symbol_b']}")

    logger.info(f"\nPairs scanned: {len(results)} | Signals: {len(triggered)}")
    return triggered


# ══════════════════════════════════════════════════════════════
#  ML PATTERN SCAN
# ══════════════════════════════════════════════════════════════

def scan_ml(
    data: dict[str, pd.DataFrame],
    paper_trade: bool = False,
    intents: list | None = None,
) -> list[dict]:
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
            bt_result = classic_backtest(oos_df, symbol, "ml_ensemble")      # OOS rows only (3 positional args)
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
        # an "ml_oos_candidate" (alert-only) until ML passes the full WS2.3 gate set (Phase 3).
        status = ML_CANDIDATE if (cand.get("oos_validated") and bh_sig) else UNVALIDATED
        logger.info(f"  {symbol}: OOS AUC={_num(auc, 3)} baseline={_num(base, 3)} status={status}")
        if not beats_baseline or e["recent"] == 0:
            continue
        try:
            direction, bar_date = signals.latest_signal(e["oos_df"])
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

def _record_run(kind: str, started: datetime, status: str, summary: dict) -> None:
    """Best-effort `runs` row (needs an initialised state DB; scans work without one)."""
    try:
        conn = state_db.require_initialized()
    except Exception:
        return
    try:
        conn.execute("INSERT INTO runs (kind, started_at, finished_at, status, summary_json) VALUES (?,?,?,?,?)",
                     (kind, started.isoformat(), datetime.now(timezone.utc).isoformat(), status,
                      json.dumps(summary, default=str)))
    except Exception as e:
        logger.warning(f"could not record run: {type(e).__name__}")
    finally:
        conn.close()


def run_full_scan(
    markets: list[str] | None = None,
    patterns: list[str] | None = None,
    symbol_filter: str | None = None,
    modes: list[str] | None = None,
    run_stress: bool = False,
    paper_trade: bool = False,
) -> dict:
    """Run the complete scan pipeline."""
    started = datetime.now(timezone.utc)
    try:
        results = _run_full_scan(markets, patterns, symbol_filter, modes, run_stress, paper_trade)
    except Exception as e:
        _record_run("scan", started, "error", {"error": type(e).__name__})
        raise
    _record_run("scan", started, "ok", {
        "technical": len(results.get("technical", [])), "pairs": len(results.get("pairs", [])),
        "ml": len(results.get("ml", [])), "decisions": results.get("decisions", []),
        "mode": config.TRADING_MODE})
    return results


def _run_full_scan(markets, patterns, symbol_filter, modes, run_stress, paper_trade) -> dict:
    modes = modes or ["technical", "pairs", "ml"]

    logger.info("\n" + "=" * 60)
    logger.info("🤖 TRADING BOT v2 — FULL SCAN")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Modes: {', '.join(modes)} | TRADING_MODE={config.TRADING_MODE}")
    logger.info("=" * 60)

    # Reconcile with the broker before looking at anything (paper mode only; else zero broker calls)
    session = open_session() if paper_trade else None
    try:
        # Fetch data
        if symbol_filter:
            data = {symbol_filter: fetch_ohlcv(symbol_filter)}
            data = {k: v for k, v in data.items() if not v.empty}
        else:
            data = fetch_watchlist(markets)

        if not data:
            logger.error("No data fetched. Check connections and symbols.")
            return {}

        logger.info(f"Data fetched for {len(data)} symbols\n")

        results = {"technical": [], "pairs": [], "ml": [], "decisions": []}
        candidates: list = []
        research = fetch_research(data) if ("technical" in modes or "ml" in modes) else {}

        if "technical" in modes:
            results["technical"] = scan_technical(research, patterns, run_stress, paper_trade, intents=candidates)
        if "pairs" in modes:
            results["pairs"] = scan_pairs(data, paper_trade)
        if "ml" in modes:
            results["ml"] = scan_ml(research, paper_trade, intents=candidates)

        if paper_trade:
            results["decisions"] = dispatch_intents(candidates, session=session)

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
                _alert(f"📝 AI ANALYSIS:\n\n{br.text}", kind="signal")
            elif not br.ok:
                _alert(f"LLM briefing failed ({br.error.value}): {br.message}", kind="briefing_failed",
                       dedup_key=f"briefing_failed:{datetime.now(timezone.utc).date().isoformat()}")

        if session is not None:
            try:
                acct = session.broker.get_account()
                logger.info(f"\n💰 Paper Account: ${acct['equity']:,.2f} (cash: ${acct['cash']:,.2f})")
                logger.info(f"📂 Open positions: {len(session.broker.get_positions())}")
            except Exception as e:
                logger.warning(f"account summary unavailable ({type(e).__name__})")

        return results
    finally:
        if session is not None:
            session.conn.close()


# ══════════════════════════════════════════════════════════════
#  REPORT GENERATION
# ══════════════════════════════════════════════════════════════

def generate_report(results: dict, output: str = "report.html"):
    """Generate a comprehensive HTML report."""
    all_signals = results.get("technical", []) + results.get("ml", [])
    pairs = results.get("pairs", [])

    html = f"""<!DOCTYPE html>
<html><head><title>Trading Bot Report — {datetime.now().strftime('%Y-%m-%d')}</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: 0 auto;
       padding: 20px; background: #0a0a0a; color: #e0e0e0; }}
h1 {{ color: #00ff88; }} h2 {{ color: #88bbff; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
th {{ background: #1a1a2e; color: #00ff88; }}
tr:hover {{ background: #1a1a2e; }}
.buy {{ color: #00ff88; }} .sell {{ color: #ff4444; }}
.section {{ background: #111; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.meta {{ color: #666; font-size: 0.85em; }}
</style></head><body>
<h1>🤖 Trading Bot v2 Report</h1>
<p class="meta">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
Technical: {len(results.get('technical', []))} |
Pairs: {len(pairs)} |
ML: {len(results.get('ml', []))}</p>
"""

    def esc(x) -> str:
        return html_lib.escape(str(x))

    if all_signals:
        html += "<h2>🔧 Technical & ML Signals (out-of-sample)</h2>"
        html += (f'<p class="meta">All statistics are walk-forward out-of-sample (before the hold-out start '
                 f'{config.HOLDOUT_START}); in-sample figures are not shown. Only <b>oos_validated</b> signals '
                 f'are order-eligible.</p><table>')
        html += ("<tr><th>Dir</th><th>Symbol</th><th>Pattern</th><th>Validation</th><th>OOS Win Rate</th>"
                 "<th>OOS PF</th><th>OOS Return</th><th>OOS Drawdown</th><th>OOS Sharpe</th>"
                 "<th>OOS Trades</th><th>Hold-out</th><th>BH q</th></tr>")
        for s in all_signals:
            cls = "buy" if "BUY" in s.get("direction", "") else "sell"
            ho, q = s.get("holdout_return"), s.get("bh_adjusted_p")
            html += f"""<tr><td class="{cls}">{esc(s.get('direction','?'))}</td>
            <td>{esc(s.get('symbol','?'))}</td><td>{esc(s.get('pattern', 'ml_ensemble'))}</td>
            <td>{esc(s.get('validation_status', UNVALIDATED))}</td>
            <td>{esc(s.get('win_rate','n/a'))}</td><td>{esc(s.get('profit_factor','n/a'))}</td>
            <td>{esc(s.get('total_return','n/a'))}</td><td>{esc(s.get('max_drawdown','n/a'))}</td>
            <td>{esc(s.get('sharpe','n/a'))}</td><td>{esc(s.get('total_trades','n/a'))}</td>
            <td>{esc(_pct(ho) if isinstance(ho, (int, float)) else 'n/a')}</td>
            <td>{esc(_num(q, 3) if isinstance(q, (int, float)) else 'n/a')}</td></tr>"""
        html += "</table>"

    if pairs:
        html += "<h2>📈 Pairs / Stat-Arb Signals (alert-only, in-sample backtest)</h2><table>"
        html += ("<tr><th>Dir</th><th>Pair</th><th>Z-Score</th><th>Half-Life</th><th>BH-adj. p</th>"
                 "<th>Win Rate</th><th>PF</th><th>Return</th><th>Sharpe</th></tr>")
        for p in pairs:
            hl = p.get("half_life")
            html += f"""<tr><td>{esc(p.get('signal_direction','?'))}</td>
            <td>{esc(p.get('symbol_a','?'))}/{esc(p.get('symbol_b','?'))}</td>
            <td>{esc(_num(p.get('current_zscore')))}</td><td>{esc(_num(hl, 1) + 'd' if hl is not None else 'n/a')}</td>
            <td>{esc(_num(p.get('adj_pvalue'), 3))}</td>
            <td>{esc(_pct(p.get('win_rate')))}</td><td>{esc(_num(p.get('profit_factor')))}</td>
            <td>{esc(_pct(p.get('total_return'), 2))}</td><td>{esc(_num(p.get('sharpe_ratio')))}</td></tr>"""
        html += "</table>"

    html += """<p class="meta">⚠️ This is a decision-support tool, not financial advice.
Past performance does not guarantee future results. Always manage risk.</p>
</body></html>"""

    with open(output, "w") as f:
        f.write(html)
    logger.info(f"Report saved to {output}")


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

SUBCOMMANDS = ("scan", "risk", "check-llm", "rebalance", "backup")


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _db_or_error():
    try:
        return state_db.require_initialized()
    except state_db.StateNotInitialized as e:
        _err(f"{e}. Run `python main.py risk init` first.")
        return None


def cmd_scan(args) -> int:
    if args.stress:
        sym, pat = args.stress
        logger.info(f"Running full stress test: {sym} / {pat}")
        df = fetch_ohlcv(sym, period_days=config.RESEARCH_LOOKBACK_DAYS)
        if df.empty:
            _err(f"No data for {sym}")
            return 1
        if pat not in PATTERN_REGISTRY:
            _err(f"Unknown pattern: {pat}")
            return 1
        report = full_stress_test(df, sym, PATTERN_REGISTRY[pat], pat)
        print(json.dumps(report, indent=2, default=str))
        return 0

    paper = bool(args.paper) or config.TRADING_MODE == "paper"

    def one_scan():
        res = run_full_scan(markets=args.market, patterns=args.pattern, symbol_filter=args.symbol,
                            modes=args.mode, paper_trade=paper)
        if args.report:
            generate_report(res)
        return res

    if args.schedule:
        def job():
            try:
                one_scan()
            except Exception as e:
                logger.error(f"Scheduled scan failed: {type(e).__name__}")
                _alert(f"⚠️ Scan failed: {type(e).__name__}", kind="halt")

        logger.info(f"Scheduling daily scan at {args.time}")
        schedule.every().day.at(args.time).do(job)
        logger.info("Bot running. Press Ctrl+C to stop.\n")
        while True:
            schedule.run_pending()
            time.sleep(60)

    one_scan()
    return 0


def cmd_risk(args) -> int:
    act = args.risk_cmd
    if act == "init":
        conn = state_db.connect()
        try:
            version = state_db.migrate(conn)
        finally:
            conn.close()
        print(f"state DB ready at {config.STATE_DB_PATH} (schema v{version})")
        print("ASSUMPTION (D10): the Alpaca PAPER account is dedicated to this sleeve. The sleeve baseline is taken "
              "from the account at the first trading session; deposits, withdrawals or manual trades there distort "
              "it (a jump > EQUITY_JUMP_HALT_PCT halts; fix with `risk rebaseline --confirm`).")
        return 0
    conn = _db_or_error()
    if conn is None:
        return 1
    try:
        rm = RiskManager(conn)
        if act == "status":
            print(json.dumps(rm.status(), indent=2, default=str))
            return 0
        if act == "halt":
            ev = rm.halt(args.reason)
            print(f"halted: {ev.get('reason')}")
            return 0
        if act == "resume":
            if not args.confirm:
                _err("resume requires --confirm (it clears the halt and resets the drawdown peak)")
                return 2
            rm.resume(confirm=True)
            print("halt cleared; the drawdown peak will re-seed on the next equity update")
            return 0
        if act == "rebaseline":
            if not args.confirm:
                _err("rebaseline requires --confirm (it re-seeds the account baseline and the drawdown peak)")
                return 2
            rm.rebaseline(confirm=True)
            print("account baseline cleared; it re-seeds (with an alert) at the next trading session")
            return 0
        if act == "reconcile":
            try:
                broker = get_broker()
                res = (execution.accept_reconciliation(broker, conn) if args.accept
                       else execution.reconcile(broker, conn))
            except Exception as e:
                _err(f"could not read the paper account ({type(e).__name__}); nothing accepted")
                return 1
            print("reconciled: OK" if res.ok else "MISMATCH:\n  " + "\n  ".join(res.mismatches))
            if args.accept and res.ok:
                print("accepted the broker's current state as known")
            elif not res.ok:
                print("review the paper account, then run `risk reconcile --accept`")
            return 0 if res.ok else 1
    finally:
        conn.close()
    return 2


def cmd_check_llm(args) -> int:
    ok, msg = check_llm()
    print(("OK: " if ok else "FAILED: ") + msg)
    return 0 if ok else 1


def cmd_backup(args) -> int:
    conn = _db_or_error()
    if conn is None:
        return 1
    try:
        state_db.backup(conn, args.dest)
    finally:
        conn.close()
    print(f"backup written to {args.dest}")
    return 0


def cmd_rebalance(args) -> int:
    import allocation

    try:
        holdings = allocation.load_holdings(args.holdings)
    except (OSError, ValueError) as e:
        _err(f"cannot read holdings: {e}")
        return 2
    symbols = sorted(set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE))
    prices: dict[str, float] = {}
    monthly: dict[str, pd.Series] = {}
    failed = []
    for s in symbols:
        try:
            df = fetch_ohlcv(s, period_days=500)
        except Exception:
            df = None
        if df is None or df.empty:
            failed.append(s)
            continue
        prices[s] = float(df["Close"].iloc[-1])
        if s in allocation.TREND_UNIVERSE:
            close = df["Close"]
            if getattr(close.index, "tz", None) is not None:
                close = close.tz_localize(None)
            monthly[s] = close.resample("ME").last().dropna().tail(14)  # 13 completed months + current
    if failed:
        _err("could not fetch prices for " + ", ".join(failed)
             + " (network or data-source failure); no proposal made")
        return 1
    try:
        proposal = allocation.propose_rebalance(holdings, prices, pd.DataFrame(monthly),
                                                contributions=args.contributions)
    except ValueError as e:
        _err(str(e))
        return 2
    print(proposal.render())
    print("\nAdvisory only: the platform never trades the core or trend sleeve. Place any trades yourself.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading Pattern Bot v2")
    sub = parser.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("scan", help="run the scan pipeline (default command)")
    sc.add_argument("--mode", nargs="+", choices=["technical", "pairs", "ml"], help="Scan modes to run")
    sc.add_argument("--symbol", type=str, help="Scan single symbol")
    sc.add_argument("--pattern", type=str, nargs="+", help="Specific patterns")
    sc.add_argument("--market", type=str, nargs="+", choices=["stocks", "crypto", "forex", "futures"])
    sc.add_argument("--stress", nargs=2, metavar=("SYMBOL", "PATTERN"),
                    help="Full stress test on symbol/pattern combo")
    sc.add_argument("--schedule", action="store_true", help="Run on schedule")
    sc.add_argument("--time", type=str, default="08:00", help="Schedule time HH:MM")
    sc.add_argument("--report", action="store_true", help="Generate HTML report")
    sc.add_argument("--paper", action="store_true",
                    help="Route order intents to the paper account (also needs TRADING_MODE=paper)")

    rk = sub.add_parser("risk", help="persisted risk state and reconciliation")
    rs = rk.add_subparsers(dest="risk_cmd", required=True)
    rs.add_parser("init", help="create/migrate the state DB (trading refuses without it)")
    rs.add_parser("status", help="show risk state")
    h = rs.add_parser("halt", help="halt all trading")
    h.add_argument("--reason", required=True)
    r = rs.add_parser("resume", help="clear a halt")
    r.add_argument("--confirm", action="store_true")
    rb_ = rs.add_parser("rebaseline", help="re-seed the account baseline after a deposit/withdrawal")
    rb_.add_argument("--confirm", action="store_true")
    rc = rs.add_parser("reconcile", help="compare the paper account with the state DB")
    rc.add_argument("--accept", action="store_true", help="accept the broker's current state as known")

    sub.add_parser("check-llm", help="verify the Anthropic key and model")

    rb = sub.add_parser("rebalance", help="print an advisory rebalance proposal (never trades)")
    rb.add_argument("--holdings", required=True, help="CSV: symbol,quantity (CASH row = dollars)")
    rb.add_argument("--contributions", type=float, default=0.0)

    bk = sub.add_parser("backup", help="back up the state DB")
    bk.add_argument("--dest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    install_redaction()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help")):
        argv = ["scan"] + argv  # legacy flat flags mean `scan`
    args = build_parser().parse_args(argv)
    return {"scan": cmd_scan, "risk": cmd_risk, "check-llm": cmd_check_llm,
            "rebalance": cmd_rebalance, "backup": cmd_backup}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
