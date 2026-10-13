"""AccountSnapshot: the owner's broker account at one moment, in one shape whatever its source.

Money is in ``base_currency`` unless a field says otherwise. ``cash`` is the base-currency cash total and is NEGATIVE
when the account is on margin (the margin loan is ``-cash``). ``leverage = gross_positions / net_liquidation``.
Positions carry the yfinance symbol (``VFV.TO``) so the bar store and the allocation code can use them directly.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

# IBKR exchange -> yfinance suffix. Anything not listed (NYSE, NASDAQ, ARCA, AMEX, BATS, IEX, PINK ...) is a plain US symbol.
_SUFFIX = {"TSE": ".TO", "TSX": ".TO", "VENTURE": ".V", "TSXV": ".V", "NEO": ".NE", "NEOE": ".NE", "AEQLIT": ".NE"}
_SMART = {"", "SMART"}


def yfinance_symbol(symbol: str, exchange: str = "", currency: str = "", primary_exchange: str = "") -> str:
    """Broker symbol -> yfinance symbol. TSE/TSX -> SYMBOL.TO, VENTURE -> .V, US exchanges -> plain.

    A share class written with a dot ('CGL.C') or a space ('BRK B') becomes a dash before the suffix ('CGL-C.TO',
    'BRK-B'). A SMART-routed CAD contract with no primary exchange is treated as TSX.
    """
    sym = (symbol or "").strip().upper().replace(" ", "-").replace(".", "-")
    ex = (primary_exchange or "").strip().upper()
    if ex in _SMART:
        ex = (exchange or "").strip().upper()
    if ex in _SMART and (currency or "").upper() == "CAD":
        ex = "TSE"
    return sym + _SUFFIX.get(ex, "")


# Position types valued as shares in the views. Options, futures and the like (contract counts, not shares) are kept
# in the snapshot but never mapped to a stock symbol; an empty sec_type (CSV, older rows) counts as a stock.
VALUED_SEC_TYPES = frozenset({"STK", "ETF", "FUND", ""})


def _num(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class SnapPosition:
    symbol: str                       # yfinance symbol
    quantity: float
    currency: str
    broker_symbol: str = ""
    exchange: str = ""
    sec_type: str = ""
    avg_cost: Optional[float] = None  # per unit, position currency
    market_price: Optional[float] = None
    market_value: Optional[float] = None   # position currency
    unrealized_pnl: Optional[float] = None


@dataclass
class AccountSnapshot:
    as_of: str                        # ISO-8601 UTC
    base_currency: str
    net_liquidation: float
    cash: float                       # base currency, negative = margin loan
    gross_positions: float            # base currency
    excess_liquidity: Optional[float] = None
    maint_margin: Optional[float] = None
    buying_power: Optional[float] = None
    positions: list[SnapPosition] = field(default_factory=list)
    fx_rates: dict[str, float] = field(default_factory=dict)       # currency -> units of base per 1 unit
    cash_by_currency: dict[str, float] = field(default_factory=dict)
    source: str = "ibkr"              # ibkr | holdings_csv
    id: Optional[int] = None

    @property
    def leverage(self) -> Optional[float]:
        if not self.net_liquidation or self.net_liquidation <= 0:
            return None
        return self.gross_positions / self.net_liquidation

    @property
    def margin_loan(self) -> float:
        return max(-self.cash, 0.0)

    def fx(self, currency: str) -> Optional[float]:
        """Units of base currency per 1 unit of `currency` (1.0 for the base currency itself)."""
        c = (currency or self.base_currency).upper()
        if c == self.base_currency:
            return 1.0
        return self.fx_rates.get(c)

    def valued_positions(self) -> list[SnapPosition]:
        return [p for p in self.positions if p.quantity and (p.sec_type or "").upper() in VALUED_SEC_TYPES]

    def unvalued_positions(self) -> list[SnapPosition]:
        """Held positions of a type that is not valued as shares (options, futures, ...)."""
        return [p for p in self.positions if p.quantity and (p.sec_type or "").upper() not in VALUED_SEC_TYPES]

    def holdings(self) -> dict[str, float]:
        """allocation-style holdings: {yfinance symbol: quantity, 'CASH': base-currency cash (may be negative)}.
        Only stocks, ETFs and funds: derivative contracts are not shares and are left out."""
        out: dict[str, float] = {}
        for p in self.valued_positions():
            if p.quantity:
                out[p.symbol] = out.get(p.symbol, 0.0) + p.quantity
        out["CASH"] = self.cash
        return out

    def currencies(self) -> dict[str, str]:
        return {p.symbol: p.currency for p in self.valued_positions()}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["leverage"] = self.leverage
        d["margin_loan"] = self.margin_loan
        return d

    @staticmethod
    def positions_from(rows) -> list[SnapPosition]:
        return [SnapPosition(**r) for r in rows]
