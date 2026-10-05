"""In-memory fake of the alpaca TradingClient surface used by paper_trader.py."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional


class FakeBroker:
    """Mimics TradingClient: get_account, get_all_positions, get_orders, submit_order,
    close_position, get_asset. Every call is recorded in `self.calls` as (method, args, kwargs).
    """

    def __init__(self, equity: float = 100_000.0, cash: float = 100_000.0,
                 buying_power: Optional[float] = None, daytrade_count: int = 0):
        self.account = SimpleNamespace(
            equity=str(equity), cash=str(cash),
            buying_power=str(buying_power if buying_power is not None else cash * 2),
            portfolio_value=str(equity), daytrade_count=daytrade_count,
        )
        self.positions: list[Any] = []
        self.orders: list[Any] = []
        self.calls: list[tuple[str, tuple, dict]] = []
        self.assets: dict[str, Any] = {}
        self._raises: dict[str, BaseException] = {}

    # -- failure injection ---------------------------------------------------
    def raise_on(self, method: str, exc: Optional[BaseException] = None) -> None:
        """Make `method` raise `exc` (default ConnectionError)."""
        self._raises[method] = exc or ConnectionError(f"FakeBroker: {method} failed")

    def timeout_on(self, method: str) -> None:
        self._raises[method] = TimeoutError(f"FakeBroker: {method} timed out")

    def clear_failures(self) -> None:
        self._raises.clear()

    def _record(self, method: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((method, args, kwargs))
        if method in self._raises:
            raise self._raises[method]

    def calls_to(self, method: str) -> list[tuple[str, tuple, dict]]:
        return [c for c in self.calls if c[0] == method]

    # -- TradingClient surface ----------------------------------------------
    def get_account(self):
        self._record("get_account", (), {})
        return self.account

    def get_all_positions(self):
        self._record("get_all_positions", (), {})
        return list(self.positions)

    def get_orders(self, *args, **kwargs):
        self._record("get_orders", args, kwargs)
        return list(self.orders)

    def get_asset(self, symbol_or_asset_id):
        self._record("get_asset", (symbol_or_asset_id,), {})
        return self.assets.get(symbol_or_asset_id) or SimpleNamespace(
            symbol=symbol_or_asset_id, tradable=True, shortable=True, fractionable=True, status="active")

    def submit_order(self, order_data=None, *args, **kwargs):
        self._record("submit_order", (order_data, *args), kwargs)
        order = SimpleNamespace(
            id=uuid.uuid4(), symbol=getattr(order_data, "symbol", None), qty=getattr(order_data, "qty", None),
            side=getattr(order_data, "side", None), type=type(order_data).__name__, status="accepted",
            request=order_data,
        )
        self.orders.append(order)
        return order

    def close_position(self, symbol_or_asset_id, *args, **kwargs):
        self._record("close_position", (symbol_or_asset_id, *args), kwargs)
        self.positions = [p for p in self.positions if p.symbol != symbol_or_asset_id]
        return SimpleNamespace(id=uuid.uuid4(), symbol=symbol_or_asset_id, status="accepted")

    # -- helpers -------------------------------------------------------------
    def add_position(self, symbol: str, qty: float, price: float, side: str = "long") -> None:
        mv = qty * price
        self.positions.append(SimpleNamespace(
            symbol=symbol, qty=str(qty), side=side, market_value=str(mv), unrealized_pl="0",
            unrealized_plpc="0", current_price=str(price), avg_entry_price=str(price)))


def install_fake_broker(monkeypatch, broker: Optional[FakeBroker] = None, enable_trading: bool = True) -> FakeBroker:
    """Install `broker` as paper_trader's client (module global `_client`)."""
    import config
    import paper_trader

    broker = broker or FakeBroker()
    monkeypatch.setattr(paper_trader, "_client", broker)
    if enable_trading:
        monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
        monkeypatch.setattr(config, "TRADING_MODE", "paper")
    return broker
