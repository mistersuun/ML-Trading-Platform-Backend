"""Execution session and intent dispatch (Phase 1 safety wiring, moved verbatim from main.py).

open_session -> reconcile -> refresh sleeve equity -> cancel-on-halt; dispatch_intents routes every intent
through execution.submit_intent and alerts AFTER each Decision. Behaviour is unchanged from Phase 1.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import config
import execution
import signals
from alerts import send_alert
from risk_manager import RiskManager
from settings import RiskConfigError, validate_risk_config
from state import db as state_db

logger = logging.getLogger(__name__)


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


def risk_config_problem() -> Optional[str]:
    """None when the risk configuration is inside its safe ranges, else the message listing what is wrong."""
    try:
        validate_risk_config(config)
    except RiskConfigError as e:
        return str(e)
    return None


def open_session() -> Optional[ExecSession]:
    """Prepare order routing: state DB must exist, reconcile with the broker, refresh sleeve equity.

    Returns None (alert-only run, zero broker calls) when trading is off or the state DB is missing."""
    if not execution_enabled():
        logger.info(f"Alert-only run (TRADING_MODE={config.TRADING_MODE}, "
                    f"PAPER_TRADE_ENABLED={config.PAPER_TRADE_ENABLED}): no orders, no broker calls")
        return None
    try:
        validate_risk_config(config)
    except RiskConfigError as e:
        logger.error(f"Trading refused: {e}")
        _alert(f"Trading refused: unsafe risk configuration. {e}", kind="halt",
               dedup_key=f"badconfig:{datetime.now(timezone.utc).date().isoformat()}")
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


def record_run(kind: str, started: datetime, status: str, summary: dict) -> None:
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


def alert(message: str, kind: str = "signal", dedup_key: Optional[str] = None) -> bool:
    """Public name for the dedup-aware alert helper."""
    return _alert(message, kind=kind, dedup_key=dedup_key)
