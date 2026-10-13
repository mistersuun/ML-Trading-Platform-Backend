"""Read-only IBKR sync: fetch the account through brokers.ibkr_readonly and store it as an AccountSnapshot.

This is the ONLY module outside brokers/ allowed to import brokers.ibkr_readonly (tests/test_ast_boundaries.py).
It never places or changes orders: the broker module has no such capability.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional

from account import store
from account.model import VALUED_SEC_TYPES, AccountSnapshot, SnapPosition, yfinance_symbol
from brokers import ibkr_readonly
from brokers.ibkr_readonly import IBKRUnavailable, RawAccount  # noqa: F401  (re-exported)
import instruments


def snapshot_from_raw(raw: RawAccount, now: Optional[datetime] = None) -> AccountSnapshot:
    s = raw.summary
    positions = []
    for p in raw.positions:
        sym = yfinance_symbol(p.symbol, p.exchange, p.currency, p.primary_exchange)
        positions.append(SnapPosition(
            symbol=sym, quantity=p.position, currency=p.currency, broker_symbol=p.symbol,
            exchange=p.primary_exchange or p.exchange, sec_type=p.sec_type, avg_cost=p.avg_cost,
            market_price=p.market_price, market_value=p.market_value, unrealized_pnl=p.unrealized_pnl))
        if (p.sec_type or "").upper() in VALUED_SEC_TYPES:
            instruments.register_external(sym, currency=p.currency, exchange=p.primary_exchange or p.exchange)
    cash_by = dict(raw.cash_by_currency)
    return AccountSnapshot(
        as_of=(now or datetime.now(timezone.utc)).isoformat(), base_currency=raw.base_currency,
        net_liquidation=s["NetLiquidation"], cash=s.get("TotalCashValue", sum(
            v * raw.fx_rates.get(c, 0.0) for c, v in cash_by.items())),
        gross_positions=s.get("GrossPositionValue", 0.0), excess_liquidity=s.get("ExcessLiquidity"),
        maint_margin=s.get("MaintMarginReq"), buying_power=s.get("BuyingPower"), positions=positions,
        fx_rates=dict(raw.fx_rates), cash_by_currency=cash_by, source="ibkr")


def sync_account(conn: Optional[sqlite3.Connection] = None, fetch: Optional[Callable[[], RawAccount]] = None,
                 now: Optional[datetime] = None) -> AccountSnapshot:
    """Fetch the account read-only and persist a snapshot (history is kept). Raises IBKRUnavailable.

    `conn` defaults to the initialised state DB (StateNotInitialized if it is missing). `fetch` is for tests.
    """
    from state import db as state_db
    own = conn is None
    conn = conn or state_db.require_initialized()
    try:
        raw = (fetch or ibkr_readonly.fetch_account)()
        snap = snapshot_from_raw(raw, now)
        store.save_snapshot(conn, snap)
        return snap
    finally:
        if own:
            conn.close()
