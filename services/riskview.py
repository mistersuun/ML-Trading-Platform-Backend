"""Read-only view of the signal sleeve's risk state for the Risk page (D5, D10).

Everything here comes from the state DB (risk_state, equity_history, orders, signal_ledger, runs) and config: the
broker is never queried, so broker-side facts (open positions, heat, gross) are derived from the platform's own
order records and flagged where that is only an estimate."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from api.errors import ApiError
from risk_manager import kill_switch_env
from services import models as M
from state import db as state_db

DECISION_WINDOW_DAYS = 30
LATEST_DECISIONS = 20
EQUITY_HISTORY_LIMIT = 1000
_OPEN_ENTRY_STATES = ("filled", "partially_filled", "accepted", "new", "pending_new", "submitted", "held")


def open_state() -> sqlite3.Connection:
    """Initialised state DB or 503 ``state_not_initialized`` (never creates the DB)."""
    try:
        return state_db.require_initialized()
    except state_db.StateNotInitialized as e:
        raise ApiError(f"The risk state is not initialised ({e}). Run `python main.py risk init` first.",
                       {"reason": str(e)}, status_code=503, code="state_not_initialized") from e
    except sqlite3.Error as e:
        raise ApiError("The risk state database could not be read. Run `python main.py risk init` to check it.",
                       {"reason": type(e).__name__}, status_code=503, code="state_not_initialized") from e


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def latest_sleeve(conn) -> tuple[Optional[float], Optional[str]]:
    """(sleeve value, timestamp) of the newest accepted reading; falls back to risk_state.last_sleeve_equity."""
    row = conn.execute("SELECT ts, sleeve_equity FROM equity_history ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        return float(row["sleeve_equity"]), row["ts"]
    rs = conn.execute("SELECT last_sleeve_equity FROM risk_state WHERE id=1").fetchone()
    v = rs["last_sleeve_equity"] if rs else None
    return (float(v) if v else None), None


def sleeve_snapshot(conn) -> dict:
    """{value, peak, drawdown (<= 0), as_of} for the paper sleeve."""
    value, ts = latest_sleeve(conn)
    peak = conn.execute("SELECT peak_equity FROM risk_state WHERE id=1").fetchone()["peak_equity"]
    dd = None
    if value is not None and peak and peak > 0:
        dd = -max(0.0, (peak - value) / peak)
    return {"value": value, "peak": peak, "drawdown": dd, "as_of": ts}


def _ladder(value, peak) -> list[M.LadderLevel]:
    levels = [("cut", dd, mult, f"-{dd:.0%} risk x{mult:g}") for dd, mult in config.DRAWDOWN_LADDER]
    levels.append(("halt", config.DRAWDOWN_HALT_PCT, 0.0, f"-{config.DRAWDOWN_HALT_PCT:.0%} halt"))
    out = []
    for kind, dd, mult, label in levels:
        thr = peak * (1 - dd) if peak else None
        room = value - thr if (thr is not None and value is not None) else None
        out.append(M.LadderLevel(kind=kind, drawdown=dd, risk_multiplier=mult, label=label,
                                 threshold_equity=thr, room=room))
    return out


def _open_positions(conn) -> int:
    """Symbols whose newest platform order is a live / filled BUY (an estimate: protective-leg fills are not
    recorded in `orders`)."""
    rows = conn.execute("SELECT symbol, side, status FROM orders ORDER BY id").fetchall()
    last = {r["symbol"]: r for r in rows if r["side"] in ("buy", "sell")}
    return sum(1 for r in last.values() if r["side"] == "buy" and (r["status"] or "").lower() in _OPEN_ENTRY_STATES)


def _loss(start, value) -> Optional[float]:
    if not start or start <= 0 or value is None:
        return None
    return max(0.0, (start - value) / start)


def _limits(rs, value, n_open: int, now: datetime) -> list[M.LimitUse]:
    same_day = rs["day"] == now.date().isoformat()
    y, w, _ = now.isocalendar()
    same_week = rs["week"] == f"{y}-W{w:02d}"
    est = "from the platform's order records; the broker is not queried"
    flat = n_open == 0
    return [
        M.LimitUse(key="risk_per_trade", label="Risk per trade", used=config.RISK_PER_TRADE_PCT, maximum=0.01,
                   unit="fraction", note="configured setting against the 1.0% ceiling"),
        M.LimitUse(key="heat", label="Open risk to stops", used=0.0 if flat else None,
                   maximum=config.MAX_PORTFOLIO_HEAT_PCT, unit="fraction",
                   note=None if flat else "not measurable without the broker's positions"),
        M.LimitUse(key="gross", label="Gross exposure", used=0.0 if flat else None,
                   maximum=config.MAX_GROSS_EXPOSURE_PCT, unit="fraction",
                   note=None if flat else "not measurable without the broker's positions"),
        M.LimitUse(key="positions", label="Positions", used=float(n_open), maximum=float(config.MAX_OPEN_POSITIONS),
                   unit="count", note=est),
        M.LimitUse(key="orders_today", label="Orders today", used=float(rs["orders_today"] if same_day else 0),
                   maximum=float(config.MAX_ORDERS_PER_DAY), unit="count"),
        M.LimitUse(key="daily_loss", label="Daily loss",
                   used=_loss(rs["day_start_equity"], value) if same_day else (0.0 if value is not None else None),
                   maximum=config.DAILY_LOSS_STOP_PCT, unit="fraction"),
        M.LimitUse(key="weekly_loss", label="Weekly loss",
                   used=_loss(rs["week_start_equity"], value) if same_week else (0.0 if value is not None else None),
                   maximum=config.WEEKLY_LOSS_STOP_PCT, unit="fraction"),
    ]


def _json(text) -> dict:
    try:
        d = json.loads(text) if text else {}
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def _decisions(conn, now: datetime) -> list[M.DecisionRow]:
    """Order decisions of the last DECISION_WINDOW_DAYS: ledger rows plus the decisions recorded by scan runs
    (early rejections such as not_validated never reach the ledger). A scan-run decision is dropped when the
    ledger already has the same symbol/side on the same day."""
    cutoff = (now - timedelta(days=DECISION_WINDOW_DAYS)).isoformat()
    out: list[M.DecisionRow] = []
    seen = set()
    for r in conn.execute("SELECT created_at, symbol, side, status, decision_json FROM signal_ledger "
                          "WHERE created_at >= ? ORDER BY id", (cutoff,)):
        d = _json(r["decision_json"])
        reasons = [str(x) for x in (d.get("reasons") or [])]
        out.append(M.DecisionRow(ts=r["created_at"], symbol=r["symbol"], side=(r["side"] or "").lower() or None,
                                 status=d.get("status") or r["status"], reasons=reasons, source="ledger"))
        seen.add((r["created_at"][:10], r["symbol"], (r["side"] or "").lower()))
    for r in conn.execute("SELECT finished_at, started_at, summary_json FROM runs WHERE kind='scan' "
                          "AND COALESCE(finished_at, started_at) >= ? ORDER BY id", (cutoff,)):
        ts = r["finished_at"] or r["started_at"]
        for d in _json(r["summary_json"]).get("decisions") or []:
            if not isinstance(d, dict):
                continue
            side = {1: "buy", -1: "sell"}.get(d.get("direction"))
            if (ts[:10], d.get("symbol"), side) in seen:
                continue
            out.append(M.DecisionRow(ts=ts, symbol=d.get("symbol"), side=side, status=str(d.get("status") or ""),
                                     reasons=[str(x) for x in (d.get("reasons") or [])], source="scan_run"))
    return out


def _primary_reason(d: M.DecisionRow) -> str:
    return d.reasons[0].split(":")[0].strip() if d.reasons else d.status


def _reconcile(conn) -> M.ReconcileStatus:
    orphans = [f"orphan_ledger:{r['signal_key']}" for r in conn.execute(
        "SELECT signal_key FROM signal_ledger WHERE status IN ('pending','unknown') "
        "AND signal_key NOT IN (SELECT client_order_id FROM orders)")]
    return M.ReconcileStatus(
        broker_checked=False, status="mismatch" if orphans else "clean", mismatches=orphans,
        note="Checked against the platform's own records only; the broker is compared at each paper session "
             "(`python main.py risk reconcile`).")


def status(conn=None, now: Optional[datetime] = None) -> M.RiskStatusResponse:
    own = conn is None
    conn = conn or open_state()
    now = _now(now)
    try:
        try:
            rs = conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()
            if rs is None:
                raise ApiError("The risk state has no row. Run `python main.py risk init`.", None,
                               status_code=503, code="state_not_initialized")
            snap = sleeve_snapshot(conn)
            hist = [M.EquityHistoryPoint(ts=r["ts"], sleeve_equity=r["sleeve_equity"], peak=r["peak"])
                    for r in reversed(conn.execute(
                        "SELECT ts, sleeve_equity, peak FROM equity_history ORDER BY id DESC LIMIT ?",
                        (EQUITY_HISTORY_LIMIT,)).fetchall())]
            n_open = _open_positions(conn)
            decisions = _decisions(conn, now)
            reconcile = _reconcile(conn)
        except sqlite3.Error as e:
            raise ApiError("The risk state database could not be read. Run `python main.py risk init`.",
                           {"reason": type(e).__name__}, status_code=503, code="state_not_initialized") from e
    finally:
        if own:
            conn.close()
    counts = Counter(_primary_reason(d) for d in decisions)
    latest = sorted(decisions, key=lambda d: d.ts, reverse=True)[:LATEST_DECISIONS]
    value = snap["value"]
    return M.RiskStatusResponse(
        mode=config.TRADING_MODE, paper_trade_enabled=config.PAPER_TRADE_ENABLED, halted=bool(rs["halted"]),
        halt_reason=rs["halt_reason"], halted_at=rs["halted_at"],
        kill_switch=os.path.exists(config.KILL_SWITCH_FILE) or kill_switch_env(), reconcile=reconcile,
        sleeve_value=value, sleeve_as_of=snap["as_of"], configured_equity=config.SIGNAL_SLEEVE_EQUITY,
        peak=snap["peak"], drawdown=snap["drawdown"], ladder=_ladder(value, snap["peak"]),
        open_positions=n_open, max_positions=config.MAX_OPEN_POSITIONS,
        limits=_limits(rs, value, n_open, now),
        equity_history=hist, decision_window_days=DECISION_WINDOW_DAYS, decision_total=len(decisions),
        decision_reasons=[M.ReasonCount(reason=k, count=n) for k, n in
                          sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        decisions=latest)
