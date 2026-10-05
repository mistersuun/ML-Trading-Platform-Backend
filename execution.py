"""
Execution chokepoint (WS1.3 / WS1.5 / WS1.6, paper only).

EVERY order reaches a broker through `submit_intent`. Nothing else may import `brokers.*`
(tests/test_ast_boundaries.py). There is no live mode: TRADING_MODE is {off, paper} and the
Alpaca client is always built with paper=True. Long-only, whole shares, equities/ETFs only.

Order of checks (any exception fails closed to a Decision, never an order):
  1 risk gate (kill switch, halt, daily/weekly loss, orders/day)   -> halted | rejected
  2 mode (paper + PAPER_TRADE_ENABLED, else dry_run: zero broker calls)
  3 intent sanity, registry executable, validation_status eligibility, broker asset tradable
  4 reconciliation of broker positions/open orders against the state DB (errors block)
  5 ledger insert, key = symbol|side|bar_date (one intent per symbol and bar, any strategy)
  6 no existing position / open order; SELL only reduces an existing long
  7 sizing (ATR risk, symbol/notional/gross/heat caps, open-position and cluster limits)
  8 bracket BUY (GTC, so the stop/take-profit legs outlive the day) / plain SELL (day), whole shares.
    A SELL exit of a bracketed long is cancel legs -> poll until they are terminal -> sell; if the
    sell then fails, a standalone GTC stop is re-placed, and if that fails the sleeve halts (UNPROTECTED).
  9 submit with a deterministic client_order_id; timeouts are resolved by looking the id up,
    never by retrying with a new id.

Price convention (reviewer amendment, to be reconciled with the backtest engine in Phase 2):
stop and take-profit are computed from the INTENT price (last completed close), not from the
actual next-open fill. A gapped entry therefore has a different risk-to-stop than planned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import config
import instruments
from brokers.alpaca import AlpacaBroker
from brokers.base import Broker, BrokerOrder, BrokerPosition, OrderSpec, validate_spec
from risk_manager import RiskManager
from state import db as state_db

logger = logging.getLogger(__name__)

_sleep = time.sleep  # injectable (tests): the poll that waits for cancelled bracket legs to go terminal
_clock = time.monotonic

MIN_PRICE = 1.0
TAKE_PROFIT_ATR_MULT = 3.0
ENTRY_ORDER_TYPE = "market"
EXIT_ORDER_TYPE = "market"
# Equities only; crypto/fx/futures are never executable. Bracket legs inherit the parent's
# time-in-force on Alpaca, so a DAY entry would leave the position without its stop from day 2:
# entries are GTC (supported for bracket orders). Plain exits are DAY.
ENTRY_TIF = "gtc"
EXIT_TIF = "day"
_STOP_TYPES = ("stop", "stop_limit", "trailing_stop")
_LEG_CLASSES = ("bracket", "oco", "oto")

DECISION_STATUSES = ("submitted", "rejected", "duplicate", "halted", "error", "dry_run")
_LEDGER_BLOCKING = ("pending", "unknown")  # a ledger row with no orders row and one of these blocks entries


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    direction: int  # 1 buy, -1 sell (exit/reduce a long only)
    signal_bar_date: str  # ISO date of the signal bar
    strategy_key: str
    confidence: float  # [0, 1]; only ever scales size down
    validation_status: str
    atr: Optional[float]
    price: float  # last completed close


@dataclass
class Decision:
    status: str
    reasons: list[str] = field(default_factory=list)
    client_order_id: Optional[str] = None
    broker_id: Optional[str] = None
    qty: Optional[int] = None
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None  # take-profit leg of the bracket
    events: list[dict] = field(default_factory=list)  # for the caller to alert on (halt, reconcile_mismatch)


@dataclass
class ReconcileResult:
    ok: bool
    mismatches: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    positions: Optional[list[BrokerPosition]] = field(default_factory=list)  # None: the broker could not be read
    open_orders: list[BrokerOrder] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────

def _utc(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def signal_key(symbol: str, side: str, bar_date: str) -> str:
    """Readable prefix + sha1(symbol|side|bar_date)[:12]. Strategy is deliberately NOT part of the key."""
    digest = hashlib.sha1(f"{symbol}|{side}|{bar_date}".encode()).hexdigest()[:12]
    return f"{symbol}-{side}-{bar_date}-{digest}"


def _side(direction: int) -> str:
    return "buy" if direction == 1 else "sell"


def _r2(x: float) -> float:
    return round(float(x) + 0.0, 2)


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _cluster_of(broker_symbol: str) -> str:
    for cand in (broker_symbol, broker_symbol.replace(".", "-")):
        try:
            return instruments.get(cand).cluster or cand
        except instruments.UnknownSymbol:
            continue
    return broker_symbol


def _is_duplicate_id_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "client_order_id" in msg and ("unique" in msg or "duplicate" in msg or "already" in msg)


def _persist_decision(conn: sqlite3.Connection, key: str, d: Decision) -> None:
    # an error after the broker call was attempted means the order's fate is unknown: keep blocking
    status = "unknown" if d.status == "error" else d.status
    conn.execute("UPDATE signal_ledger SET status=?, decision_json=? WHERE signal_key=?",
                 (status, json.dumps(asdict(d), default=str), key))


# ── reconciliation ───────────────────────────────────────────

def _accepted(conn: sqlite3.Connection) -> tuple[set, set]:
    """The LATEST accept snapshot only (it always covers the whole book). A symbol's acceptance expires once
    a platform bracket is placed on it after the snapshot, so a fresh position must have its own stop."""
    row = conn.execute("SELECT summary_json FROM runs WHERE kind='reconcile_accept' ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return set(), set()
    snap = json.loads(row[0] or "{}")
    newer = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM orders WHERE side='buy' AND order_class='bracket' "
                                        "AND id > ?", (snap.get("orders_max_id", 0),))}
    return set(snap.get("symbols", [])) - newer, set(snap.get("order_ids", []))


def _bracket_symbols(conn: sqlite3.Connection) -> set:
    return {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM orders WHERE side='buy' AND order_class='bracket'")}


def _own_orders(conn: sqlite3.Connection) -> tuple[set, list]:
    """(client/broker ids of every order this platform recorded, its bracket entries as (symbol, qty, stop, tp))."""
    ids: set = set()
    for cid, bid in conn.execute("SELECT client_order_id, broker_id FROM orders"):
        ids.update(x for x in (cid, bid) if x)
    brackets = [tuple(r) for r in conn.execute(
        "SELECT symbol, qty, stop_price, limit_price FROM orders WHERE side='buy' AND order_class='bracket'")]
    return ids, brackets


def _is_own(o: BrokerOrder, ids: set, brackets: list) -> bool:
    """Attribute an open order to this platform: its id / client id is in the orders table, or it is a leg nested
    under one of our parents, or (a leg that comes back un-nested once its parent filled, with no parent id)
    it matches symbol + qty + stop/take-profit price of a bracket we placed. Anything else is not ours."""
    if o.id in ids or (o.client_order_id and o.client_order_id in ids):
        return True
    if o.parent_id is not None:
        return o.parent_id in ids
    if o.side == "sell" and o.order_class in _LEG_CLASSES and o.qty is not None:
        px = o.stop_price if o.order_type in _STOP_TYPES else o.limit_price if o.order_type == "limit" else None
        if px is None:
            return False
        for sym, qty, stop, tp in brackets:
            want = stop if o.order_type in _STOP_TYPES else tp
            if sym == o.symbol and qty == o.qty and want and abs(want - px) < 0.005:
                return True
    return False


def _is_protective(o: BrokerOrder) -> bool:
    """A cancellable protective order: bracket leg or stop (never a market/limit exit in flight)."""
    return o.side == "sell" and (o.parent_id is not None or o.order_class in _LEG_CLASSES or o.order_type in _STOP_TYPES)


def _snapshot(broker: Broker) -> tuple[list[BrokerPosition], list[BrokerOrder]]:
    return broker.get_positions(), broker.get_open_orders()


def reconcile(broker: Broker, conn: Optional[sqlite3.Connection] = None,
              now: Optional[datetime] = None) -> ReconcileResult:
    """Compare the broker's positions and open orders with the state DB.

    A broker error is a mismatch ("broker_error"), never an empty book. Anything the DB does not
    know about (position, open order, orphaned ledger row) blocks entries until accepted via
    accept_reconciliation(). Returns an event for the caller to alert on."""
    now = _utc(now)
    conn = state_db.require_initialized(conn)
    mismatches: list[str] = []
    positions: Optional[list[BrokerPosition]] = []
    orders: list[BrokerOrder] = []
    try:
        positions, orders = _snapshot(broker)
    except Exception as e:
        positions = None  # unreadable is not a flat book: callers must not size or baseline off it
        mismatches.append(f"broker_error:{type(e).__name__}")
    else:
        acc_syms, acc_ids = _accepted(conn)
        known_syms = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM orders WHERE side='buy'")} | acc_syms
        own_ids, brackets = _own_orders(conn)
        known_ids: set = own_ids | acc_ids
        bracket_syms = _bracket_symbols(conn)
        for p in positions:
            if p.qty < 0 or p.side == "short":
                mismatches.append(f"short_position:{p.symbol}")
            elif p.qty > 0 and p.symbol not in known_syms:
                mismatches.append(f"unknown_position:{p.symbol}")
            elif p.qty > 0 and p.symbol in bracket_syms and p.symbol not in acc_syms:
                # the exit legs may expire or be cancelled by hand: a long must keep a live stop
                cover = [o for o in orders if o.symbol == p.symbol and o.side == "sell"
                         and (o.order_type in _STOP_TYPES or o.order_type == "market")]
                if not cover:
                    mismatches.append(f"missing_protective_stop:{p.symbol}")
                elif sum(o.qty or 0.0 for o in cover) + 1e-9 < p.qty:  # a 1-share stop on 100 shares is no stop
                    mismatches.append(f"insufficient_protective_stop:{p.symbol}")
        known_top = {o.id for o in orders if o.parent_id is None
                     and ((o.client_order_id in known_ids) or (o.id in known_ids))}
        for o in orders:
            if o.parent_id is not None:  # a filled parent is no longer open, so also match the DB's broker ids
                ok = o.parent_id in known_top or o.parent_id in known_ids or o.id in known_ids
            else:
                ok = o.id in known_top or _is_own(o, own_ids, brackets)
            if not ok:
                mismatches.append(f"unknown_open_order:{o.client_order_id or o.id}")
        for (key,) in conn.execute(
                "SELECT signal_key FROM signal_ledger WHERE status IN ('pending','unknown') "
                "AND signal_key NOT IN (SELECT client_order_id FROM orders)"):
            mismatches.append(f"orphan_ledger:{key}")
    events = []
    if mismatches:
        events = [{"type": "reconcile_mismatch", "reasons": mismatches, "at": now.isoformat()}]
        logger.error("reconciliation mismatch: %s", mismatches)
    return ReconcileResult(not mismatches, mismatches, events, positions, orders)


def accept_reconciliation(broker: Broker, conn: Optional[sqlite3.Connection] = None,
                          now: Optional[datetime] = None) -> ReconcileResult:
    """Owner accepts the broker's current state as known (`risk reconcile --accept`).
    Records a snapshot, abandons orphaned ledger rows. Raises if the broker cannot be read."""
    now = _utc(now)
    conn = state_db.require_initialized(conn)
    positions, orders = _snapshot(broker)  # raises on error: never accept an unread book
    snap = {"symbols": sorted({p.symbol for p in positions}),
            "order_ids": sorted({x for o in orders for x in (o.id, o.client_order_id) if x}),
            "orders_max_id": conn.execute("SELECT COALESCE(MAX(id), 0) FROM orders").fetchone()[0]}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO runs (kind, started_at, finished_at, status, summary_json) "
                     "VALUES ('reconcile_accept', ?, ?, 'ok', ?)", (now.isoformat(), now.isoformat(), json.dumps(snap)))
        conn.execute("UPDATE signal_ledger SET status='abandoned' WHERE status IN ('pending','unknown') "
                     "AND signal_key NOT IN (SELECT client_order_id FROM orders)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return reconcile(broker, conn, now)


# ── sizing ───────────────────────────────────────────────────

def _pending_buys(conn: sqlite3.Connection, open_orders: list[BrokerOrder]) -> list[tuple]:
    """Open entry orders not yet filled: (symbol, qty, intent_price|None, stop|None)."""
    out = []
    for o in open_orders:
        if o.side != "buy" or o.parent_id is not None:
            continue
        row = conn.execute("SELECT qty, intent_price, stop_price FROM orders WHERE client_order_id=? OR broker_id=?",
                           (o.client_order_id, o.id)).fetchone()
        qty = o.qty if o.qty is not None else (row["qty"] if row else None) or 0.0
        out.append((o.symbol, float(qty), row["intent_price"] if row else None, row["stop_price"] if row else None))
    return out


def _open_risk(conn: sqlite3.Connection, positions: list[BrokerPosition], pending: list[tuple] = ()) -> float:
    """Dollar risk-to-stop of open longs and pending entries. Uses the stop recorded at order time,
    else one risk unit."""
    unit = config.SIGNAL_SLEEVE_EQUITY * config.RISK_PER_TRADE_PCT
    total = 0.0
    for p in positions:
        if p.qty <= 0:
            continue
        row = conn.execute("SELECT stop_price FROM orders WHERE symbol=? AND side='buy' AND stop_price IS NOT NULL "
                           "ORDER BY id DESC LIMIT 1", (p.symbol,)).fetchone()
        if row and row[0] and p.avg_entry_price > row[0]:
            total += p.qty * (p.avg_entry_price - row[0])
        else:
            total += unit
    for _sym, qty, price, stop in pending:
        total += qty * (price - stop) if (price and stop and price > stop) else unit
    return total


def _size_buy(intent: OrderIntent, mult: float, positions: list[BrokerPosition],
              conn: sqlite3.Connection, open_orders: list[BrokerOrder] = ()) -> tuple[int, float, dict]:
    """Return (qty, stop_distance, caps). Caller guarantees atr is finite and > 0.
    Pending (unfilled) entries count against the gross and heat caps."""
    sleeve = config.SIGNAL_SLEEVE_EQUITY
    price = intent.price
    stop_distance = config.ATR_STOP_MULT * intent.atr
    risk_dollars = sleeve * config.RISK_PER_TRADE_PCT * mult
    pending = _pending_buys(conn, open_orders)
    gross = sum(abs(p.market_value) for p in positions)
    gross += sum(q * px if px else config.MAX_ORDER_NOTIONAL for _s, q, px, _st in pending)
    gross_room = max(0.0, sleeve * config.MAX_GROSS_EXPOSURE_PCT - gross)
    heat_room = max(0.0, sleeve * config.MAX_PORTFOLIO_HEAT_PCT - _open_risk(conn, positions, pending))
    caps = {
        "risk": risk_dollars / stop_distance,
        "symbol_pct": config.MAX_SYMBOL_PCT * sleeve / price,
        "order_notional": config.MAX_ORDER_NOTIONAL / price,
        "gross_room": gross_room / price,
        "heat_room": heat_room / stop_distance,
    }
    qty = math.floor(min(caps.values()) * intent.confidence)
    return max(0, int(qty)), stop_distance, caps


def refresh_sleeve_equity(rm: RiskManager, broker: Broker, positions: Optional[list[BrokerPosition]] = None,
                          now: Optional[datetime] = None) -> list[dict]:
    """Read the paper account, compute sleeve equity (configured dollars + realized and open P&L, see
    RiskManager.sleeve_equity) and feed it to the risk manager. Returns events (a new halt, a seeded baseline). A depleted
    sleeve (<= 0) halts. Broker/DB errors propagate: callers must not trade after one."""
    positions = broker.get_positions() if positions is None else positions
    acct = broker.get_account()
    pnl = sum(p.market_value - p.qty * p.avg_entry_price for p in positions)
    sleeve = rm.sleeve_equity(float(acct["equity"]), pnl, now)
    events = rm.pop_events()  # baseline seeded / equity-jump halt
    if math.isfinite(sleeve) and sleeve <= 0:
        return events + [rm.halt("sleeve equity depleted", now)]
    return events + rm.update_equity(sleeve, now)


def cancel_open_entries(broker: Broker, conn: Optional[sqlite3.Connection] = None) -> list[str]:
    """CANCEL_ON_HALT: cancel unfilled parent BUY entries this platform placed. Never touches a
    protective stop or take-profit leg (SELL, or carrying a parent). Returns the cancelled broker ids;
    broker errors propagate so the caller can alert."""
    conn = state_db.require_initialized(conn)
    ours = {r[0] for r in conn.execute("SELECT client_order_id FROM orders WHERE side='buy'")}
    cancelled: list[str] = []
    for o in broker.get_open_orders():
        if o.side == "buy" and o.parent_id is None and o.client_order_id in ours:
            broker.cancel_order(o.id)
            cancelled.append(o.id)
            conn.execute("UPDATE orders SET status='canceled' WHERE client_order_id=?", (o.client_order_id,))
    return cancelled


# ── the chokepoint ───────────────────────────────────────────

def submit_intent(intent: OrderIntent, broker: Optional[Broker] = None,
                  conn: Optional[sqlite3.Connection] = None, now: Optional[datetime] = None,
                  *, risk_manager: Optional[RiskManager] = None) -> Decision:
    """Run one intent through every gate. Never raises; any exception becomes status 'error'.

    `risk_manager` should be the instance whose update_equity() the caller refreshed this run
    (the latest equity reading is held in memory; see risk_manager.py)."""
    try:
        return _submit(intent, broker, conn, _utc(now), risk_manager)
    except Exception as e:
        logger.exception("submit_intent failed closed")
        return Decision("error", [f"exception:{type(e).__name__}"],
                        client_order_id=_safe_key(intent))


def _safe_key(intent: OrderIntent) -> Optional[str]:
    try:
        return signal_key(intent.symbol, _side(intent.direction), intent.signal_bar_date)
    except Exception:
        return None


def _submit(intent, broker, conn, now, risk_manager) -> Decision:
    rm = risk_manager or RiskManager(conn)

    # 1. risk gate
    chk = rm.check(now)
    if not chk.allowed:
        hard = any(r == "kill_switch" or r.startswith("halted") or r.startswith("state_") for r in chk.reasons)
        return Decision("halted" if hard else "rejected", list(chk.reasons), events=list(chk.events))
    events = list(chk.events)
    conn = rm.conn

    # 2. mode
    mode = config.TRADING_MODE
    if mode not in ("off", "paper"):
        return Decision("rejected", [f"invalid_trading_mode:{mode}"], events=events)
    if not (mode == "paper" and config.PAPER_TRADE_ENABLED):
        return Decision("dry_run", [f"TRADING_MODE={mode} PAPER_TRADE_ENABLED={bool(config.PAPER_TRADE_ENABLED)}"],
                        events=events)

    # 3. intent sanity, registry, validation, asset
    def reject(*reasons: str) -> Decision:
        return Decision("rejected", list(reasons), events=events)

    if intent.direction not in (1, -1) or isinstance(intent.direction, bool):
        return reject("invalid_direction")
    if not (isinstance(intent.symbol, str) and intent.symbol and intent.signal_bar_date and intent.strategy_key):
        return reject("invalid_intent")
    if not _finite(intent.confidence) or not 0.0 <= intent.confidence <= 1.0:
        return reject("invalid_confidence")
    if not _finite(intent.price) or intent.price < MIN_PRICE:
        return reject("invalid_price")
    try:
        inst = instruments.get(intent.symbol)
    except instruments.UnknownSymbol:
        return reject("unknown_symbol")
    if not inst.executable or inst.research_only or not inst.alpaca_trade_symbol:
        return reject("not_executable")
    if intent.validation_status not in config.ORDER_ELIGIBLE_STATUSES:
        return reject("not_deflated" if intent.validation_status == "oos_validated" else "not_validated")
    tsym = inst.alpaca_trade_symbol
    side = _side(intent.direction)
    key = signal_key(intent.symbol, side, intent.signal_bar_date)

    broker = broker if broker is not None else AlpacaBroker()
    if not broker.get_asset(tsym).tradable:
        return reject("asset_not_tradable")

    # 4. reconciliation (also yields the position / open-order snapshot used below)
    rec = reconcile(broker, conn, now)
    events.extend(rec.events)
    if not rec.ok:
        # A reduce-only SELL (exit of an existing long) must still run through mismatches such as a missing stop
        # (that exit is what fixes it). An unreadable book or a short position still blocks it.
        reduce_only = side == "sell" and not any(m.startswith(("broker_error", "short_position")) for m in rec.mismatches)
        if not reduce_only:
            return Decision("rejected", ["reconcile_mismatch", *rec.mismatches], client_order_id=key, events=events)
    positions, open_orders = rec.positions, rec.open_orders

    # 5. ledger: one row per (symbol, side, bar). Only 'rejected_retryable' rows may be revived.
    try:
        conn.execute("INSERT INTO signal_ledger (signal_key, strategy_key, symbol, side, bar_date, created_at, status) "
                     "VALUES (?,?,?,?,?,?, 'pending')",
                     (key, intent.strategy_key, intent.symbol, side, intent.signal_bar_date, now.isoformat()))
    except sqlite3.IntegrityError:
        cur = conn.execute("UPDATE signal_ledger SET status='pending', strategy_key=? "
                           "WHERE signal_key=? AND status='rejected_retryable'", (intent.strategy_key, key))
        if cur.rowcount != 1:
            return Decision("duplicate", ["signal_already_processed"], client_order_id=key, events=events)

    # 6-8 pre-submit work: any exception here is retryable (nothing was sent)
    cancels: list[BrokerOrder] = []
    try:
        spec, early = _plan(intent, side, tsym, key, chk.risk_multiplier, positions, open_orders, conn, cancels)
    except Exception as e:
        logger.exception("pre-submit failure")
        d = Decision("error", [f"exception:{type(e).__name__}"], client_order_id=key, events=events)
        conn.execute("UPDATE signal_ledger SET status='rejected_retryable', decision_json=? WHERE signal_key=?",
                     (json.dumps(asdict(d), default=str), key))
        return d
    if early is not None:
        early.client_order_id, early.events = key, events
        _persist_decision(conn, key, early)
        return early

    # 9. an exit first removes the position's own bracket legs (they reserve every share), waits for them to be
    #    terminal, then sells; a failed sell re-protects the position
    if cancels:
        return _protected_exit(broker, spec, cancels, positions, intent, key, tsym, conn, now, rm, events)

    # 10. submit
    d = _send(broker, spec, key, tsym, conn, now, rm, events, intent.price)
    _persist_decision(conn, key, d)
    return d


def _retryable(conn, key: str, d: Decision) -> None:
    conn.execute("UPDATE signal_ledger SET status='rejected_retryable', decision_json=? WHERE signal_key=?",
                 (json.dumps(asdict(d), default=str), key))


def _await_terminal(broker, ids: set, timeout: Optional[float] = None) -> bool:
    """Poll open orders until none of `ids` is still open (Alpaca cancels are asynchronous: a leg in
    pending_cancel still reserves its shares). Bounded; True when confirmed gone."""
    deadline = _clock() + (config.EXIT_CANCEL_WAIT_SECONDS if timeout is None else timeout)
    while True:
        try:
            if not any(o.id in ids for o in broker.get_open_orders()):
                return True
        except Exception as e:
            logger.error("open-order poll failed while waiting for cancels: %s", type(e).__name__)
        if _clock() >= deadline:
            return False
        _sleep(0.5)


def _stop_to_restore(cancels: list[BrokerOrder], conn, tsym: str, intent: OrderIntent) -> Optional[float]:
    for o in cancels:
        if o.order_type in _STOP_TYPES and o.stop_price:
            return float(o.stop_price)
    row = conn.execute("SELECT stop_price FROM orders WHERE symbol=? AND side='buy' AND stop_price IS NOT NULL "
                       "ORDER BY id DESC LIMIT 1", (tsym,)).fetchone()
    if row and row[0]:
        return float(row[0])
    if _finite(intent.atr) and intent.atr > 0:
        px = _r2(intent.price - config.ATR_STOP_MULT * intent.atr)
        return px if px > 0 else None
    return None


def _stop_live(broker, tsym: str, held: float) -> bool:
    try:
        cover = sum(o.qty or 0.0 for o in broker.get_open_orders() if o.symbol == tsym and o.side == "sell"
                    and o.order_type in _STOP_TYPES and o.status != "pending_cancel")
    except Exception:
        return False
    return cover + 1e-9 >= held


def _reprotect(broker, tsym, held, stop_px, key, conn, now, rm, events) -> list[str]:
    """After an exit failed with its legs cancelled: put a standalone GTC stop back on the held qty. If that
    fails too, halt and emit an UNPROTECTED event (the caller alerts on it)."""
    if _stop_live(broker, tsym, held):
        return ["still_protected"]
    n = conn.execute("SELECT COUNT(*) FROM orders WHERE client_order_id LIKE ?", (f"{key}-stop-%",)).fetchone()[0] + 1
    cid, err, bo = f"{key}-stop-{n}", "no_stop_price", None
    if stop_px:
        try:
            bo = broker.submit(OrderSpec(tsym, "sell", int(held), "stop", ENTRY_TIF, cid, stop_price=stop_px))
        except Exception as e:
            err = type(e).__name__
            try:
                bo = broker.get_order_by_client_id(cid)
            except Exception:
                bo = None
    if bo is not None:
        conn.execute("INSERT OR IGNORE INTO orders (client_order_id, broker_id, symbol, side, qty, order_class, "
                     "stop_price, status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, bo.id, tsym, "sell", held, "simple", stop_px, bo.status or "accepted", now.isoformat()))
        return ["standalone_stop_replaced"]
    reason = (f"UNPROTECTED {tsym}: exit failed after its stop was cancelled and the standalone stop could not be "
              f"re-placed ({err}); {held:g} sh held with no stop. Fix it by hand at the broker.")
    logger.error(reason)
    try:
        rm.halt(reason, now)
    except Exception:
        logger.exception("could not persist the UNPROTECTED halt")
    events.append({"type": "halt", "reason": reason, "at": now.isoformat()})
    return ["UNPROTECTED"]


def _protected_exit(broker, spec, cancels, positions, intent, key, tsym, conn, now, rm, events) -> Decision:
    """cancel legs -> confirm terminal -> sell -> re-protect on failure. The ledger row stays retryable (never
    'rejected') whenever the position was left without its legs."""
    held = sum(p.qty for p in positions if p.symbol == tsym and p.qty > 0)
    stop_px = _stop_to_restore(cancels, conn, tsym, intent)
    ids = {o.id for o in cancels}
    cancelled = False
    try:
        for o in cancels:
            try:
                broker.cancel_order(o.id)
            except Exception as e:
                if getattr(e, "status_code", None) in (404, 422):
                    continue  # already gone: the sibling leg was auto-cancelled, or it is terminal / pending cancel
                raise
            cancelled = True
    except Exception as e:
        logger.exception("could not cancel protective legs before exit")
        d = Decision("error", [f"cancel_protective_failed:{type(e).__name__}"], client_order_id=key, events=events)
        if cancelled:  # one leg is gone, another could not be cancelled: make sure a stop is still live
            d.reasons += _reprotect(broker, tsym, held, stop_px, key, conn, now, rm, events)
        _retryable(conn, key, d)
        return d
    settled = _await_terminal(broker, ids)
    d = _send(broker, spec, key, tsym, conn, now, rm, events, intent.price)
    d.reasons.append(f"cancelled_protective_legs:{len(cancels)}")
    if not settled:
        d.reasons.append("legs_not_confirmed_cancelled")
    if d.status in ("rejected", "error"):
        d.reasons += _reprotect(broker, tsym, held, stop_px, key, conn, now, rm, events)
    _persist_decision(conn, key, d)
    if d.status == "rejected":
        _retryable(conn, key, d)
    return d


def _plan(intent, side, tsym, key, risk_multiplier, positions, open_orders, conn, cancels):
    """Steps 6-8. Returns (OrderSpec, None) or (None, rejection Decision). A SELL appends the ids of the
    position's own bracket legs to `cancels` (the caller cancels them before selling)."""
    def rej(*reasons: str) -> tuple:
        return None, Decision("rejected", list(reasons))

    held = sum(p.qty for p in positions if p.symbol == tsym and p.qty > 0)
    sym_orders = [o for o in open_orders if o.symbol == tsym]

    if side == "sell":
        if held <= 0:
            return rej("shorts_disabled", "no_long_position")
        groups: dict[str, float] = {}
        own_ids, brackets = _own_orders(conn)
        # only orders attributable to this platform are ever cancelled; any other sell order just reserves shares
        legs = [o for o in sym_orders if _is_protective(o) and _is_own(o, own_ids, brackets)]
        leg_ids = {o.id for o in legs}
        for o in sym_orders:
            if o.side == "sell" and o.id not in leg_ids:
                if o.qty is None:
                    return rej("open_sell_order_unknown_qty")
                groups[o.parent_id or o.id] = max(groups.get(o.parent_id or o.id, 0.0), o.qty)  # OCO legs count once
        available = math.floor(held - sum(groups.values()))
        if available < 1:
            return rej("shorts_disabled", "open_sell_orders_cover_position")
        spec = OrderSpec(tsym, "sell", int(available), EXIT_ORDER_TYPE, EXIT_TIF, key)
        validate_spec(spec)
        cancels.extend(legs)
        return spec, None

    # BUY
    if held > 0:
        return rej("position_exists")
    if any(o.side == "buy" for o in sym_orders):
        return rej("open_buy_order_exists")
    if not _finite(intent.atr) or intent.atr <= 0:
        return rej("atr_invalid")
    holding = {p.symbol for p in positions if p.qty > 0} | {o.symbol for o in open_orders if o.side == "buy"}
    if len(holding) >= config.MAX_OPEN_POSITIONS:
        return rej("max_open_positions")
    cluster = _cluster_of(tsym)
    if sum(1 for s in holding if _cluster_of(s) == cluster) >= config.MAX_POSITIONS_PER_CLUSTER:
        return rej("max_positions_per_cluster", cluster)
    qty, stop_distance, caps = _size_buy(intent, risk_multiplier, positions, conn, open_orders)
    if qty < 1:
        binding = min(caps, key=caps.get)
        return rej("qty_zero", f"binding_cap:{binding}")
    stop = _r2(intent.price - stop_distance)
    tp = _r2(intent.price + TAKE_PROFIT_ATR_MULT * intent.atr)
    if stop <= 0 or not stop < intent.price < tp:
        return rej("invalid_bracket_prices", f"stop={stop} tp={tp}")
    spec = OrderSpec(tsym, "buy", qty, ENTRY_ORDER_TYPE, ENTRY_TIF, key,
                     bracket=True, stop_price=stop, take_profit_price=tp)
    try:
        validate_spec(spec)
    except ValueError as e:
        return rej(f"invalid_order:{e}")
    return spec, None


def _send(broker, spec: OrderSpec, key, tsym, conn, now, rm, events, intent_price=None) -> Decision:
    status, reasons, bo = "submitted", [], None
    try:
        bo = broker.submit(spec)
    except Exception as e:
        # Never retry with a new id. Ask the broker whether this id exists.
        dup = _is_duplicate_id_error(e)
        try:
            bo = broker.get_order_by_client_id(key)
        except Exception as e2:
            logger.error("order lookup after submit failure also failed: %s", type(e2).__name__)
            bo = None
            conn.execute("UPDATE signal_ledger SET status='unknown' WHERE signal_key=?", (key,))
            return Decision("error", [f"submit_failed:{type(e).__name__}", "lookup_failed"],
                            client_order_id=key, qty=int(spec.qty), events=events)
        code = getattr(e, "status_code", None)
        if bo is None and isinstance(code, int) and 400 <= code < 500 and code not in (408, 429):
            # definitive broker refusal (bad bracket prices, buying power): nothing exists, do not block the sleeve
            return Decision("rejected", [f"broker_rejected:{code}"], client_order_id=key, qty=int(spec.qty),
                            events=events)
        if bo is None:
            conn.execute("UPDATE signal_ledger SET status='unknown' WHERE signal_key=?", (key,))
            return Decision("error", [f"submit_failed:{type(e).__name__}", "order_not_found"],
                            client_order_id=key, qty=int(spec.qty), events=events)
        status = "duplicate" if dup else "submitted"
        reasons = ["recovered_by_client_order_id"]
    conn.execute(
        "INSERT OR IGNORE INTO orders (client_order_id, broker_id, symbol, side, qty, order_class, stop_price, "
        "limit_price, status, created_at, intent_price) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (key, bo.id, tsym, spec.side, spec.qty, "bracket" if spec.bracket else "simple",
         spec.stop_price, spec.take_profit_price, bo.status or "accepted", now.isoformat(), intent_price))
    if status == "submitted":
        rm.record_order(now)
    return Decision(status, reasons, client_order_id=key, broker_id=bo.id, qty=int(spec.qty),
                    stop_price=spec.stop_price, limit_price=spec.take_profit_price, events=events)
