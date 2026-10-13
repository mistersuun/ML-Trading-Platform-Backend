"""Alpaca adapter. PAPER ONLY: TradingClient is always built with paper=True (decisions D1-D9:
no live trading mode exists). `AlpacaBroker` wraps any object with the alpaca-py TradingClient
surface, so tests wrap FakeBroker with it."""
from __future__ import annotations

import logging
from typing import Any, Optional

import config
from brokers.base import (Broker, BrokerAsset, BrokerOrder, BrokerPosition, OrderSpec,
                          validate_spec)

logger = logging.getLogger(__name__)


def build_trading_client(api_key: Optional[str] = None, secret_key: Optional[str] = None):
    """Create the alpaca-py TradingClient. paper=True is hard-coded; there is no parameter for it."""
    from alpaca.trading.client import TradingClient

    key = api_key or config.ALPACA_API_KEY
    secret = secret_key or config.ALPACA_SECRET_KEY
    if not key or not secret:
        raise RuntimeError("Alpaca credentials not configured")
    return TradingClient(key, secret, paper=True)


def _enum_text(v: Any) -> str:
    v = getattr(v, "value", v)
    return str(v).lower().split(".")[-1]


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)


_TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected", "replaced", "done_for_day", "suspended"}


def _to_order(o: Any, parent_id: Optional[str] = None) -> BrokerOrder:
    otype = getattr(o, "order_type", None) or getattr(o, "type", None)
    oclass = getattr(o, "order_class", None)
    return BrokerOrder(
        id=str(o.id),
        client_order_id=getattr(o, "client_order_id", None),
        symbol=str(o.symbol),
        side=_enum_text(o.side),
        qty=_opt_float(getattr(o, "qty", None)),
        status=_enum_text(getattr(o, "status", "")),
        parent_id=parent_id,
        order_type=None if otype is None else _enum_text(otype),
        order_class=None if oclass is None else _enum_text(oclass),
        stop_price=_opt_float(getattr(o, "stop_price", None)),
        limit_price=_opt_float(getattr(o, "limit_price", None)),
    )


class AlpacaBroker:
    """Broker Protocol implementation over a TradingClient-like object. Errors propagate."""

    def __init__(self, client: Any = None):
        self._client = client
        self._assets: dict[str, BrokerAsset] = {}  # per-instance (= per run) asset cache

    @property
    def client(self):
        if self._client is None:
            self._client = build_trading_client()
        return self._client

    def get_account(self) -> dict:
        a = self.client.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash), "buying_power": float(a.buying_power),
                "portfolio_value": float(getattr(a, "portfolio_value", a.equity)),
                "day_trade_count": getattr(a, "daytrade_count", None)}

    def get_positions(self) -> list[BrokerPosition]:
        out = []
        for p in self.client.get_all_positions():
            side = _enum_text(p.side)
            qty = float(p.qty)
            if side == "short" and qty > 0:
                qty = -qty
            out.append(BrokerPosition(str(p.symbol), qty, side, float(p.avg_entry_price), float(p.market_value)))
        return out

    def get_open_orders(self) -> list[BrokerOrder]:
        kwargs: dict = {}
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest
            kwargs["filter"] = GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True, limit=500)
        except ImportError:  # pragma: no cover - alpaca-py is a hard dependency
            pass
        out: list[BrokerOrder] = []
        for o in self.client.get_orders(**kwargs):
            parent = _to_order(o)
            # a filled parent can come back with its still-open legs nested: keep only the open ones
            if parent.status not in _TERMINAL:
                out.append(parent)
            for leg in getattr(o, "legs", None) or []:
                lo = _to_order(leg, parent_id=parent.id)
                if lo.status not in _TERMINAL:
                    out.append(lo)
        return out

    def get_asset(self, symbol: str) -> BrokerAsset:
        if symbol not in self._assets:
            a = self.client.get_asset(symbol)
            self._assets[symbol] = BrokerAsset(
                str(a.symbol), bool(a.tradable) and _enum_text(getattr(a, "status", "active")) == "active",
                bool(getattr(a, "fractionable", False)))
        return self._assets[symbol]

    def _request(self, spec: OrderSpec):
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (LimitOrderRequest, MarketOrderRequest, StopLossRequest,
                                             StopOrderRequest, TakeProfitRequest)
        validate_spec(spec)
        common: dict = dict(
            symbol=spec.symbol, qty=int(spec.qty),
            side=OrderSide.BUY if spec.side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce(spec.time_in_force), client_order_id=spec.client_order_id)
        if spec.bracket:
            common.update(order_class=OrderClass.BRACKET,
                          take_profit=TakeProfitRequest(limit_price=spec.take_profit_price),
                          stop_loss=StopLossRequest(stop_price=spec.stop_price))
        if spec.order_type == "market":
            return MarketOrderRequest(**common)
        if spec.order_type == "stop":
            return StopOrderRequest(stop_price=spec.stop_price, **common)
        return LimitOrderRequest(limit_price=spec.limit_price, **common)

    def submit(self, spec: OrderSpec) -> BrokerOrder:
        return _to_order(self.client.submit_order(self._request(spec)))

    def cancel_order(self, order_id: str) -> None:
        self.client.cancel_order_by_id(order_id)

    def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        try:
            return _to_order(self.client.get_order_by_client_id(client_order_id))
        except Exception as e:
            if getattr(e, "status_code", None) == 404:
                return None
            raise


_: type[Broker] = AlpacaBroker  # structural check for type checkers
