"""In-memory fake of the alpaca TradingClient surface (wrap with brokers.alpaca.AlpacaBroker)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Optional


class FakeAPIError(Exception):
    """Stand-in for alpaca.common.exceptions.APIError (has .status_code)."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class FakeBroker:
    """Mimics TradingClient: get_account, get_all_positions, get_orders, submit_order,
    close_position, get_asset. Every call is recorded in `self.calls` as (method, args, kwargs).
    """

    def __init__(self, equity: float = 100_000.0, cash: float = 100_000.0,
                 buying_power: Optional[float] = None, daytrade_count: int = 0,
                 async_cancel: bool = False, enforce_held_qty: bool = False, oco_cascade: bool = False):
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
        self._raise_after: dict[str, BaseException] = {}
        # Realism switches (all off by default, so existing tests are unchanged):
        # async_cancel: cancel_order_by_id leaves the order in "pending_cancel" (still open, still reserving
        #   shares) until tick(); cancelling it again is a 422, as on Alpaca.
        # enforce_held_qty: a SELL larger than position qty minus the qty reserved by open sell orders (legs of
        #   one OCO/bracket group reserve once) is refused with a 422 "insufficient qty available".
        # oco_cascade: cancelling one bracket leg also cancels its sibling (a second cancel then 404s).
        self.async_cancel = async_cancel
        self.enforce_held_qty = enforce_held_qty
        self.oco_cascade = oco_cascade

    # -- failure injection ---------------------------------------------------
    def raise_on(self, method: str, exc: Optional[BaseException] = None) -> None:
        """Make `method` raise `exc` (default ConnectionError)."""
        self._raises[method] = exc or ConnectionError(f"FakeBroker: {method} failed")

    def timeout_on(self, method: str) -> None:
        self._raises[method] = TimeoutError(f"FakeBroker: {method} timed out")

    def timeout_after_accept(self, method: str = "submit_order") -> None:
        """`method` performs its effect, then raises TimeoutError (the response was lost)."""
        self._raise_after[method] = TimeoutError(f"FakeBroker: {method} timed out after accept")

    def clear_failures(self) -> None:
        self._raises.clear()
        self._raise_after.clear()

    def _record(self, method: str, args: tuple, kwargs: dict) -> None:
        self.calls.append((method, args, kwargs))
        if method in self._raises:
            raise self._raises[method]

    def calls_to(self, method: str) -> list[tuple[str, tuple, dict]]:
        return [c for c in self.calls if c[0] == method]

    # -- async-cancel / reservation model --------------------------------------
    def tick(self) -> None:
        """Settle every pending_cancel order (top-level or nested leg): it is gone from now on."""
        for o in list(self.orders):
            for leg in list(o.legs or []):
                if leg.status == "pending_cancel":
                    o.legs.remove(leg)
            if o.status == "pending_cancel":
                self.orders.remove(o)

    def _all_orders(self):
        for o in self.orders:
            yield o
            yield from (o.legs or [])

    def reserved_qty(self, symbol: str) -> float:
        groups: dict = {}
        for o in self._all_orders():
            if o.symbol == symbol and str(o.side).lower().endswith("sell") and o.status not in ("canceled", "filled", "expired"):
                g = getattr(o, "oco_group", None) or str(o.id)
                groups[g] = max(groups.get(g, 0.0), float(o.qty or 0))
        return sum(groups.values())

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
        cid = getattr(order_data, "client_order_id", None)
        if cid and any(o.client_order_id == cid for o in self.orders):
            raise FakeAPIError("client_order_id must be unique", 422)
        sym = getattr(order_data, "symbol", None)
        if self.enforce_held_qty and str(getattr(getattr(order_data, "side", ""), "value", getattr(order_data, "side", ""))).lower() == "sell":
            held = sum(float(p.qty) for p in self.positions if p.symbol == sym)
            if float(order_data.qty) > held - self.reserved_qty(sym) + 1e-9:
                raise FakeAPIError(f"insufficient qty available for order (requested {order_data.qty}, "
                                   f"available {held - self.reserved_qty(sym):g})", 422)
        oclass = getattr(order_data, "order_class", None)
        legs = None
        group = str(uuid.uuid4())
        if oclass is not None and "bracket" in str(getattr(oclass, "value", oclass)).lower():
            tp, sl = getattr(order_data, "take_profit", None), getattr(order_data, "stop_loss", None)
            legs = [self._leg(order_data.symbol, order_data.qty, "limit", tp.limit_price if tp else None, group=group),
                    self._leg(order_data.symbol, order_data.qty, "stop", sl.stop_price if sl else None, group=group)]
        order = SimpleNamespace(
            id=uuid.uuid4(), client_order_id=cid, symbol=getattr(order_data, "symbol", None),
            qty=getattr(order_data, "qty", None), side=getattr(order_data, "side", None),
            type=type(order_data).__name__, order_class="bracket" if legs else "simple", status="accepted",
            legs=legs, request=order_data, stop_price=getattr(order_data, "stop_price", None),
            limit_price=getattr(order_data, "limit_price", None),
            order_type="market" if "Market" in type(order_data).__name__
            else "stop" if "Stop" in type(order_data).__name__ else "limit",
        )
        self.orders.append(order)
        if "submit_order" in self._raise_after:
            raise self._raise_after["submit_order"]
        return order

    def get_order_by_client_id(self, client_order_id: str):
        self._record("get_order_by_client_id", (client_order_id,), {})
        for o in self.orders:
            if o.client_order_id == client_order_id:
                return o
        raise FakeAPIError("order not found", 404)

    def close_position(self, symbol_or_asset_id, *args, **kwargs):
        self._record("close_position", (symbol_or_asset_id, *args), kwargs)
        self.positions = [p for p in self.positions if p.symbol != symbol_or_asset_id]
        return SimpleNamespace(id=uuid.uuid4(), symbol=symbol_or_asset_id, status="accepted")

    def cancel_order_by_id(self, order_id):
        self._record("cancel_order_by_id", (order_id,), {})
        oid = str(order_id)
        target = next((o for o in self._all_orders() if str(o.id) == oid), None)
        if target is None:
            raise FakeAPIError("order not found", 404)
        if target.status == "pending_cancel":
            raise FakeAPIError("order is already pending cancel", 422)
        victims = [target]
        group = getattr(target, "oco_group", None)
        if self.oco_cascade and group:
            victims += [o for o in self._all_orders() if o is not target and getattr(o, "oco_group", None) == group]
        for v in victims:
            if self.async_cancel:
                v.status = "pending_cancel"
            else:
                self._remove(v)
        return None

    def _remove(self, victim) -> None:
        if victim in self.orders:
            self.orders.remove(victim)
        for o in self.orders:
            if victim in (o.legs or []):
                o.legs.remove(victim)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _leg(symbol, qty, order_type, price, status="new", group=None):
        return SimpleNamespace(
            id=uuid.uuid4(), client_order_id=str(uuid.uuid4()), symbol=symbol, qty=qty, side="sell",
            type=order_type, order_type=order_type, order_class="bracket", status=status, legs=None,
            limit_price=price if order_type == "limit" else None,
            stop_price=price if order_type == "stop" else None, oco_group=group)

    def add_filled_bracket(self, client_order_id: str, symbol: str, qty: float, stop: float, tp: float,
                           nested: bool = True, broker_id=None) -> SimpleNamespace:
        """A bracket whose entry already filled and whose exit legs are open (adds the position too).
        nested=True: the filled parent is returned with its legs (as get_orders(nested=True) can);
        nested=False: only the open legs come back, top-level, as separate orders."""
        group = str(uuid.uuid4())
        legs = [self._leg(symbol, qty, "limit", tp, group=group), self._leg(symbol, qty, "stop", stop, group=group)]
        parent = SimpleNamespace(
            id=broker_id or uuid.uuid4(), client_order_id=client_order_id, symbol=symbol, qty=qty, side="buy",
            type="market", order_type="market", order_class="bracket", status="filled",
            legs=legs if nested else None, request=None)
        self.orders.append(parent)
        if not nested:
            self.orders.extend(legs)
        entry = (stop + tp) / 2
        if not any(p.symbol == symbol for p in self.positions):
            self.add_position(symbol, qty, entry)
        return parent

    def add_position(self, symbol: str, qty: float, price: float, side: str = "long") -> None:
        mv = qty * price
        self.positions.append(SimpleNamespace(
            symbol=symbol, qty=str(qty), side=side, market_value=str(mv), unrealized_pl="0",
            unrealized_plpc="0", current_price=str(price), avg_entry_price=str(price)))
