"""Persistence of AccountSnapshot rows (state DB table account_snapshots, migration 006) and the holdings.csv fallback.

History is kept (rows are only ever inserted). Reading never creates the DB: a missing or un-migrated state DB just
means "no snapshot", so the services fall back to holdings.csv.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import allocation
import config
import instruments
from account.model import AccountSnapshot, SnapPosition
from state import db as state_db

_COLS = ("as_of, source, base_currency, net_liquidation, cash, gross_positions, leverage, excess_liquidity, "
         "maint_margin, buying_power, fx_json, positions_json, cash_json")


def save_snapshot(conn: sqlite3.Connection, snap: AccountSnapshot) -> int:
    lev = snap.leverage
    cur = conn.execute(
        f"INSERT INTO account_snapshots ({_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (snap.as_of, snap.source, snap.base_currency, snap.net_liquidation, snap.cash, snap.gross_positions, lev,
         snap.excess_liquidity, snap.maint_margin, snap.buying_power, json.dumps(snap.fx_rates),
         json.dumps([p.__dict__ for p in snap.positions]), json.dumps(snap.cash_by_currency)))
    snap.id = cur.lastrowid
    return int(cur.lastrowid)


def _row_to_snapshot(r) -> AccountSnapshot:
    return AccountSnapshot(
        id=r["id"], as_of=r["as_of"], source=r["source"], base_currency=r["base_currency"],
        net_liquidation=r["net_liquidation"], cash=r["cash"], gross_positions=r["gross_positions"],
        excess_liquidity=r["excess_liquidity"], maint_margin=r["maint_margin"], buying_power=r["buying_power"],
        fx_rates=json.loads(r["fx_json"] or "{}"), cash_by_currency=json.loads(r["cash_json"] or "{}"),
        positions=[SnapPosition(**p) for p in json.loads(r["positions_json"] or "[]")])


def latest_snapshot(conn: Optional[sqlite3.Connection] = None) -> Optional[AccountSnapshot]:
    """Newest stored snapshot, or None (no snapshot yet, or the state DB is missing / not migrated)."""
    own = conn is None
    try:
        conn = conn or state_db.require_initialized()
    except (state_db.StateNotInitialized, sqlite3.Error):
        return None
    try:
        r = conn.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        snap = _row_to_snapshot(r)
        for p in snap.positions:      # broker-reported symbols are known (non-executable) instruments in this process
            instruments.register_external(p.symbol, p.currency, p.exchange)
        return snap
    except sqlite3.Error:
        return None
    finally:
        if own:
            conn.close()


def history(conn: sqlite3.Connection, limit: int = 365) -> list[AccountSnapshot]:
    """Snapshots newest first."""
    rows = conn.execute("SELECT * FROM account_snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def snapshot_from_holdings(path=None, prices: Optional[dict[str, float]] = None,
                           fx_rates: Optional[dict[str, float]] = None, base_currency: Optional[str] = None,
                           now: Optional[datetime] = None) -> AccountSnapshot:
    """The holdings.csv fallback, in the same shape as a broker snapshot.

    `prices` are native-currency prices per symbol and `fx_rates` currency -> base. Without prices the positions
    carry quantities only and the money fields are computed from whatever is priced (net liquidation = priced
    positions + cash). CASH may be negative (margin loan); a `currency` column defaults to the base currency.
    """
    base = (base_currency or config.BASE_CURRENCY).upper()
    p = path if path is not None else config.HOLDINGS_CSV
    holdings, ccy = allocation.load_holdings_ex(p, default_currency=base)
    prices = prices or {}
    fx = {base: 1.0, **{k.upper(): v for k, v in (fx_rates or {}).items()}}
    cash = float(holdings.get(allocation.CASH_KEY, 0.0))
    positions, gross = [], 0.0
    for s, q in sorted(holdings.items()):
        if s == allocation.CASH_KEY or not q:
            continue
        c = ccy.get(s, base)
        px = prices.get(s)
        mv = q * px if px is not None else None
        rate = fx.get(c)
        if mv is not None and rate is not None:
            gross += mv * rate
        positions.append(SnapPosition(symbol=s, quantity=q, currency=c, broker_symbol=s, market_price=px,
                                      market_value=mv))
    net = gross + cash
    return AccountSnapshot(as_of=(now or datetime.now(timezone.utc)).isoformat(), base_currency=base,
                           net_liquidation=net, cash=cash, gross_positions=gross, positions=positions,
                           fx_rates=fx, cash_by_currency={base: cash}, source="holdings_csv")
