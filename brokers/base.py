"""Broker Protocol and the plain-data types execution.py talks in.

Every method RAISES on a broker/transport error. Nothing here may translate an error
into an empty list or None: execution treats a failure as "unknown", never as a flat book.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

ORDER_TYPES = ("market", "limit", "stop")  # "stop": standalone protective sell stop (re-protect after a failed exit)
SIDES = ("buy", "sell")


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: float  # signed: negative means short
    side: str  # "long" | "short"
    avg_entry_price: float
    market_value: float


@dataclass(frozen=True)
class BrokerOrder:
    id: str
    client_order_id: Optional[str]
    symbol: str
    side: str  # "buy" | "sell"
    qty: Optional[float]
    status: str
    parent_id: Optional[str] = None  # set for bracket legs returned nested under their parent
    order_type: Optional[str] = None  # "market" | "limit" | "stop" | ...
    order_class: Optional[str] = None  # "simple" | "bracket" | "oco" | "oto"
    stop_price: Optional[float] = None
    limit_price: Optional[float] = None


@dataclass(frozen=True)
class BrokerAsset:
    symbol: str
    tradable: bool
    fractionable: bool = False


@dataclass(frozen=True)
class OrderSpec:
    """Broker-neutral order request. Whole shares only; bracket exits attach to buys."""
    symbol: str
    side: str
    qty: float
    order_type: str
    time_in_force: str
    client_order_id: str
    limit_price: Optional[float] = None  # entry limit (order_type == "limit")
    bracket: bool = False
    stop_price: Optional[float] = None  # bracket stop-loss leg
    take_profit_price: Optional[float] = None  # bracket take-profit leg


def validate_spec(spec: OrderSpec) -> None:
    """Raise ValueError for anything that must never reach a broker."""
    if spec.side not in SIDES:
        raise ValueError(f"invalid side {spec.side!r}")
    if spec.order_type not in ORDER_TYPES:
        raise ValueError(f"unknown order type {spec.order_type!r}")  # never fall back to market
    if not (isinstance(spec.qty, (int, float)) and math.isfinite(spec.qty)
            and spec.qty >= 1 and float(spec.qty) == int(spec.qty)):
        raise ValueError(f"qty must be a whole number of shares >= 1, got {spec.qty!r}")
    if not spec.client_order_id:
        raise ValueError("client_order_id required")
    if spec.order_type == "limit" and not (spec.limit_price and spec.limit_price > 0):
        raise ValueError("limit order needs a positive limit_price")
    if spec.order_type == "stop":
        if spec.bracket or spec.side != "sell" or not (spec.stop_price and spec.stop_price > 0):
            raise ValueError("a stop order is a standalone sell with a positive stop_price")
    if spec.bracket:
        if spec.side != "buy":
            raise ValueError("brackets are only built for long entries (shorts are disabled)")
        s, t = spec.stop_price, spec.take_profit_price
        if not (s and t and s > 0 and t > s):
            raise ValueError(f"bracket needs 0 < stop < take_profit, got stop={s} tp={t}")
        if spec.limit_price is not None and not (s < spec.limit_price < t):
            raise ValueError("limit price must lie between stop and take-profit")


@runtime_checkable
class Broker(Protocol):
    def get_account(self) -> dict: ...

    def get_positions(self) -> list[BrokerPosition]: ...

    def get_open_orders(self) -> list[BrokerOrder]: ...

    def get_asset(self, symbol: str) -> BrokerAsset: ...

    def submit(self, spec: OrderSpec) -> BrokerOrder: ...

    def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        """None only when the broker positively says the order does not exist."""
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel one open order by broker id. Raises on failure."""
        ...
