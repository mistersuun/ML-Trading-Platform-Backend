"""The `account` block shared by /api/portfolio/overview and /api/risk/status (D14): net worth, positions, margin
loan, leverage and margin headroom of the owner's account, with the leverage warning."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import config
from account.model import AccountSnapshot
from services import models as M
from services.common import finite

STALE_HOURS = 72


def leverage_warning(leverage: Optional[float], margin_loan: float, base: str) -> Optional[str]:
    if leverage is None or leverage <= config.MAX_LEVERAGE_WARN + 1e-9:
        return None
    return (f"Leverage {leverage:.2f}x is above the {config.MAX_LEVERAGE_WARN:.2f}x warning level "
            f"(margin loan {margin_loan:,.0f} {base}). Reduce the margin loan before adding new positions.")


def _age_hours(as_of: str, now: datetime) -> Optional[float]:
    try:
        t = datetime.fromisoformat(as_of)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 3600)


def block_from_values(source: str, as_of: Optional[str], base: str, net_worth: float, positions_value: float,
                      cash: float, excess_liquidity: Optional[float] = None, maint_margin: Optional[float] = None,
                      buying_power: Optional[float] = None, n_positions: Optional[int] = None,
                      now: Optional[datetime] = None) -> M.AccountBlock:
    now = now or datetime.now(timezone.utc)
    loan = max(-cash, 0.0)
    lev = positions_value / net_worth if net_worth and net_worth > 0 else None
    warnings = []
    w = leverage_warning(lev, loan, base)
    if w:
        warnings.append(w)
    age = _age_hours(as_of, now) if as_of else None
    if source == "ibkr" and age is not None and age > STALE_HOURS:
        warnings.append(f"The IBKR snapshot is {age / 24:.1f} days old; run `python main.py ibkr sync`.")
    return M.AccountBlock(
        available=True, source=source, as_of=as_of, age_hours=finite(age), base_currency=base,
        net_worth=finite(net_worth), positions_value=finite(positions_value), cash=finite(cash),
        margin_loan=finite(loan), leverage=finite(lev), max_leverage_warn=config.MAX_LEVERAGE_WARN,
        margin_headroom=finite(excess_liquidity), maint_margin=finite(maint_margin),
        buying_power=finite(buying_power), positions=n_positions, warnings=warnings)


def block_from_snapshot(snap: AccountSnapshot, now: Optional[datetime] = None) -> M.AccountBlock:
    """Block from the broker's own numbers (net liquidation, gross position value, excess liquidity)."""
    return block_from_values(snap.source, snap.as_of, snap.base_currency,
                             snap.net_liquidation, snap.gross_positions, snap.cash, snap.excess_liquidity,
                             snap.maint_margin, snap.buying_power, len([p for p in snap.positions if p.quantity]),
                             now)


def unavailable(note: str) -> M.AccountBlock:
    return M.AccountBlock(available=False, base_currency=config.BASE_CURRENCY, note=note)
