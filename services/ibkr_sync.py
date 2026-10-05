"""Use cases behind `python main.py ibkr sync|status` and the nightly pre-scan sync (D14). Read-only: nothing here can
place, change or cancel an order."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

import config
from account import store
from account.model import AccountSnapshot
from account.sync import IBKRUnavailable, sync_account
from services import account_view
from services import session

__all__ = ["IBKRUnavailable", "sync", "status", "sync_before_scan", "render"]


def sync(conn=None, fetch: Optional[Callable] = None) -> AccountSnapshot:
    """Fetch the account read-only and store a snapshot. Raises IBKRUnavailable / StateNotInitialized."""
    return sync_account(conn=conn, fetch=fetch)


def status(conn=None) -> Optional[AccountSnapshot]:
    """The latest stored snapshot (None when there is none or the state DB is not initialised)."""
    return store.latest_snapshot(conn)


def render(snap: AccountSnapshot, now: Optional[datetime] = None) -> str:
    blk = account_view.block_from_snapshot(snap, now)
    b = snap.base_currency
    L = [f"IBKR snapshot {snap.as_of} ({b})",
         f"  net liquidation {snap.net_liquidation:,.2f}   positions {snap.gross_positions:,.2f}   "
         f"cash {snap.cash:,.2f}",
         f"  margin loan {blk.margin_loan:,.2f}   leverage "
         + (f"{blk.leverage:.2f}x" if blk.leverage is not None else "n/a")
         + (f"   excess liquidity {snap.excess_liquidity:,.2f}" if snap.excess_liquidity is not None else "")
         + (f"   maint margin {snap.maint_margin:,.2f}" if snap.maint_margin is not None else ""),
         f"  {blk.positions} positions"]
    for p in sorted(snap.positions, key=lambda x: x.symbol):
        L.append(f"    {p.symbol:<10} {p.quantity:>12,.4f} {p.currency}")
    L += [f"  WARNING: {w}" for w in blk.warnings]
    return "\n".join(L)


def sync_before_scan(sync_fn: Optional[Callable[[], AccountSnapshot]] = None, conn=None) -> dict:
    """Nightly step 0. Never raises and never blocks the scan: on any failure it alerts and falls back to the last
    stored snapshot, else to holdings.csv. Returns {"status": "ok"|"fallback"|"skipped", "source", "message"}."""
    if sync_fn is None:
        if not config.IBKR_SYNC_ENABLED:
            return {"status": "skipped", "source": None, "message": "IBKR_SYNC_ENABLED is false"}
        sync_fn = lambda: sync(conn)       # noqa: E731
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        snap = sync_fn()
        return {"status": "ok", "source": "ibkr", "message": f"snapshot {snap.as_of}"}
    except Exception as e:             # IBKRUnavailable, StateNotInitialized, anything: the scan must still run
        last = None
        try:
            last = store.latest_snapshot(conn)
        except Exception:
            pass
        source = "last_snapshot" if last is not None else "holdings_csv"
        detail = f"{type(e).__name__}: {e}"[:200]
        msg = (f"IBKR sync failed ({detail}); using "
               + (f"the last snapshot from {last.as_of}" if last is not None else "holdings.csv") + ".")
        try:
            session.alert(msg, kind="account_sync_failed", dedup_key=f"ibkr_sync_failed:{today}")
        except Exception:
            pass
        return {"status": "fallback", "source": source, "message": msg}

