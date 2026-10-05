"""
Risk Manager - persisted, fail-closed gate for the signal sleeve (WS1.4).

State (halt flag, peak, day/week baselines, order count) lives in state.db's
`risk_state` singleton row, so a restart never clears a halt. Sizing lives in
execution, not here. The caller computes sleeve equity (SIGNAL_SLEEVE_EQUITY plus
realised+unrealised P&L of signal-sleeve positions) and feeds it to update_equity().

Events (dicts) are returned, never sent: this module does not import alerts.
"""

import logging
import math
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import config
from state import db as state_db

logger = logging.getLogger(__name__)

_EPS = 1e-9


@dataclass
class RiskCheck:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    risk_multiplier: float = 0.0
    events: list[dict] = field(default_factory=list)  # e.g. a halt that just fired; caller alerts on it


def kill_switch_env() -> bool:
    return os.getenv("KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on")


def _utc(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _day_key(now: datetime) -> str:
    return now.date().isoformat()


def _week_key(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


class RiskManager:
    """Persisted portfolio-level risk gate. All state is in the DB except the latest
    equity reading, which the caller must refresh with update_equity() each run."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        self.conn = conn
        self._equity: Optional[float] = None

    # ── internals ────────────────────────────────────────────

    def _db(self) -> sqlite3.Connection:
        """Initialized connection or raise StateNotInitialized."""
        self.conn = state_db.require_initialized(self.conn)
        return self.conn

    def _row(self, conn) -> sqlite3.Row:
        return conn.execute("SELECT * FROM risk_state WHERE id = 1").fetchone()

    def _roll(self, conn, row, now: datetime, equity: Optional[float]) -> None:
        """UTC-day / ISO-week rollover. Baselines need an equity reading; a rollover
        without one only resets the order counter."""
        day, week = _day_key(now), _week_key(now)
        if row["day"] != day:
            conn.execute(
                "UPDATE risk_state SET day=?, orders_today=0, day_start_equity=? WHERE id=1",
                (day, equity),
            )
        elif row["day_start_equity"] is None and equity is not None:
            conn.execute("UPDATE risk_state SET day_start_equity=? WHERE id=1", (equity,))
        if row["week"] != week:
            conn.execute(
                "UPDATE risk_state SET week=?, week_start_equity=? WHERE id=1", (week, equity)
            )
        elif row["week_start_equity"] is None and equity is not None:
            conn.execute("UPDATE risk_state SET week_start_equity=? WHERE id=1", (equity,))

    def _halt_event(self, reason: str, now: datetime) -> dict:
        return {"type": "halt", "reason": reason, "at": now.isoformat()}

    def _set_halt(self, conn, reason: str, now: datetime) -> Optional[dict]:
        """Persist a halt. Returns an event only if this call newly halted."""
        cur = conn.execute(
            "UPDATE risk_state SET halted=1, halt_reason=?, halted_at=? WHERE id=1 AND halted=0",
            (reason, now.isoformat()),
        )
        if cur.rowcount:
            logger.error("TRADING HALTED: %s", reason)
            return self._halt_event(reason, now)
        return None

    @staticmethod
    def _drawdown(peak, equity) -> float:
        """Positive fraction below peak (0 if unknown)."""
        if not peak or equity is None or peak <= 0:
            return 0.0
        return max(0.0, (peak - equity) / peak)

    def _drawdown_halt(self, conn, now: datetime) -> Optional[dict]:
        row = self._row(conn)
        dd = self._drawdown(row["peak_equity"], self._equity)
        if not row["halted"] and dd + _EPS >= config.DRAWDOWN_HALT_PCT:
            return self._set_halt(conn, f"drawdown {dd:.1%} >= halt {config.DRAWDOWN_HALT_PCT:.0%}", now)
        return None

    # ── public API ───────────────────────────────────────────

    @property
    def current_drawdown(self) -> float:
        """Sleeve drawdown from peak as a negative fraction (0 when unknown)."""
        try:
            row = self._row(self._db())
        except (state_db.StateNotInitialized, sqlite3.Error):
            return 0.0
        return -self._drawdown(row["peak_equity"], self._equity)

    def update_equity(self, sleeve_equity: float, now: Optional[datetime] = None) -> list[dict]:
        """Persist peak and day/week baselines. Returns events (a new halt) for the caller to alert on."""
        now = _utc(now)
        if (isinstance(sleeve_equity, bool) or not isinstance(sleeve_equity, (int, float))
                or not math.isfinite(sleeve_equity) or sleeve_equity <= 0):
            # a bad reading must never seed or poison the peak/baselines: refuse it, keep the old reading
            self._equity = None
            raise ValueError(f"invalid sleeve equity {sleeve_equity!r}: must be finite and > 0")
        conn = self._db()
        events: list[dict] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._equity = float(sleeve_equity)
            row = self._row(conn)
            self._roll(conn, row, now, self._equity)
            peak = row["peak_equity"]
            if peak is None or self._equity > peak:
                conn.execute("UPDATE risk_state SET peak_equity=? WHERE id=1", (self._equity,))
            ev = self._drawdown_halt(conn, now)
            if ev:
                events.append(ev)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return events

    def sleeve_equity(self, account_equity: float, open_pnl: float) -> float:
        """Sleeve equity = SIGNAL_SLEEVE_EQUITY + (paper account equity - baseline), so realized
        losses persist after a stop fills. The baseline (account equity minus open P&L) is captured
        the first time this is called; the paper account must be dedicated to the sleeve (D10)."""
        if not (math.isfinite(account_equity) and math.isfinite(open_pnl)):
            raise ValueError("non-finite account equity or open P&L")
        conn = self._db()
        row = self._row(conn)
        base = row["account_baseline"]
        if base is None:
            base = account_equity - open_pnl
            conn.execute("UPDATE risk_state SET account_baseline=? WHERE id=1 AND account_baseline IS NULL", (base,))
        return config.SIGNAL_SLEEVE_EQUITY + (account_equity - base)

    def record_order(self, now: Optional[datetime] = None) -> None:
        now = _utc(now)
        conn = self._db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            self._roll(conn, self._row(conn), now, self._equity)
            conn.execute("UPDATE risk_state SET orders_today = orders_today + 1 WHERE id=1")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def halt(self, reason: str, now: Optional[datetime] = None) -> dict:
        """Manual/programmatic halt. Returns the halt event (existing halt keeps its original reason)."""
        now = _utc(now)
        conn = self._db()
        ev = self._set_halt(conn, reason, now)
        return ev or self._halt_event(self._row(conn)["halt_reason"] or reason, now)

    def resume(self, confirm: bool = False) -> None:
        """Clear a halt. Requires explicit confirmation. The peak is reset to the current
        equity (or re-seeded by the next update_equity) so the old drawdown cannot re-halt at once."""
        if not confirm:
            raise ValueError("resume requires confirm=True")
        conn = self._db()
        conn.execute(
            "UPDATE risk_state SET halted=0, halt_reason=NULL, halted_at=NULL, peak_equity=? WHERE id=1",
            (self._equity,),
        )
        logger.warning("Trading halt cleared by operator")

    def check(self, now: Optional[datetime] = None) -> RiskCheck:
        """May a NEW entry be taken now? Fails closed on any state problem."""
        now = _utc(now)
        reasons: list[str] = []
        events: list[dict] = []

        if os.path.exists(config.KILL_SWITCH_FILE) or kill_switch_env():
            reasons.append("kill_switch")

        try:
            conn = self._db()
            row = self._row(conn)
            if row["halted"]:
                reasons.append(f"halted: {row['halt_reason']}")
            elif self._equity is None:
                reasons.append("equity_not_updated")
            else:
                ev = self._drawdown_halt(conn, now)
                if ev:
                    events.append(ev)
                    reasons.append(f"halted: {ev['reason']}")

            if not reasons or all(r == "kill_switch" for r in reasons):
                same_day = row["day"] == _day_key(now)
                same_week = row["week"] == _week_key(now)
                eq = self._equity
                if eq is not None:
                    d0 = row["day_start_equity"] if same_day else eq
                    w0 = row["week_start_equity"] if same_week else eq
                    if d0 and (d0 - eq) / d0 + _EPS >= config.DAILY_LOSS_STOP_PCT:
                        reasons.append("daily_loss_stop")
                    if w0 and (w0 - eq) / w0 + _EPS >= config.WEEKLY_LOSS_STOP_PCT:
                        reasons.append("weekly_loss_stop")
                orders = row["orders_today"] if same_day else 0
                if orders >= config.MAX_ORDERS_PER_DAY:
                    reasons.append("max_orders_per_day")

            mult = 1.0
            dd = self._drawdown(row["peak_equity"], self._equity)
            for threshold, m in config.DRAWDOWN_LADDER:
                if dd + _EPS >= threshold:
                    mult = min(mult, m)
        except state_db.StateNotInitialized:
            reasons.append("state_not_initialized")
            mult = 0.0
        except sqlite3.Error as e:
            logger.error("risk state unreadable: %s", e)
            reasons.append("state_error")
            mult = 0.0

        if reasons:
            return RiskCheck(False, reasons, 0.0, events)
        return RiskCheck(True, [], mult, events)

    def status(self) -> dict:
        try:
            row = self._row(self._db())
        except (state_db.StateNotInitialized, sqlite3.Error) as e:
            return {"initialized": False, "error": str(e), "halted": True}
        return {
            "initialized": True,
            "halted": bool(row["halted"]),
            "halt_reason": row["halt_reason"],
            "halted_at": row["halted_at"],
            "peak_equity": row["peak_equity"],
            "equity": self._equity,
            "drawdown": self.current_drawdown,
            "day": row["day"],
            "day_start_equity": row["day_start_equity"],
            "week": row["week"],
            "week_start_equity": row["week_start_equity"],
            "orders_today": row["orders_today"],
            "kill_switch": os.path.exists(config.KILL_SWITCH_FILE) or kill_switch_env(),
        }
