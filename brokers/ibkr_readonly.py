"""Read-only IBKR account access (IB Gateway / TWS via ib_async). decisions.md D14.

This module is written so it can READ the account and nothing else. The layers, strongest first:

1. **IB Gateway "Read-Only API" setting (authoritative).** With it on, the broker itself rejects orders, whatever
   this code does (README). Switch it on.
2. **Static bans (tests/test_ibkr_readonly.py, tests/test_ast_boundaries.py).** Every source file is parsed: no order,
   modify, cancel, exercise, what-if or FA-change API name anywhere outside tests, no ``ib_async`` import outside
   this file, no dynamic ``getattr`` in this file, and inside this file the raw IB object may only be used for
   ``connect``, ``isConnected``, ``disconnect``, ``RequestTimeout`` and the five read methods below.
3. **Allow-list wrapper (best effort).** Past connect, the IB object is reached only through ``ReadOnlyIB``, which
   has the five read methods and raises ``ReadOnlyViolation`` for any other attribute. It holds a token, not the IB
   object. Python introspection can still get around a wrapper written in Python; it stops accidents, not a
   determined caller.
4. ``connect(..., readonly=True)``. NOTE: in ib_async 2.1.0 this flag only skips the ``reqOpenOrders`` /
   ``reqCompletedOrders`` sync at startup; it does NOT make the library refuse ``placeOrder``. It is not a defence.

The IB object never leaves this module: callers get plain slotted dataclasses of primitives.

Timeouts: ``connect(timeout=)`` covers only the connect. ib_async's ``RequestTimeout`` defaults to 0 (wait forever),
so it is set to ``timeout`` right after connect, and the whole fetch also runs under an overall deadline in a worker
thread, so a gateway that accepts the socket but never answers cannot hang the caller. Timeouts, refused connections
and incomplete answers raise ``IBKRUnavailable`` so callers can fall back to the last snapshot. The connection is
always closed.
"""
from __future__ import annotations

import asyncio
import itertools
import math
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import config

# The only IB methods reachable through the wrapper.
ALLOWED_READ_METHODS = frozenset({"accountSummary", "accountValues", "portfolio", "managedAccounts", "isConnected"})
# The only attributes of the raw IB object this file may touch (enforced by tests/test_ibkr_readonly.py).
ALLOWED_RAW_IB_ATTRS = frozenset({"connect", "isConnected", "disconnect", "RequestTimeout"})
SUMMARY_TAGS = ("NetLiquidation", "TotalCashValue", "GrossPositionValue", "BuyingPower", "ExcessLiquidity",
                "MaintMarginReq")


class IBKRUnavailable(RuntimeError):
    """IB Gateway could not be reached, timed out, or returned an incomplete account."""


class ReadOnlyViolation(AttributeError):
    """Something other than an allowed read method was requested from the read-only wrapper."""


# Private registry: the wrapper holds only a token, so no closure or slot of a ReadOnlyIB contains the IB object.
_LIVE: dict[int, object] = {}
_TOKENS = itertools.count(1)


def _live(token: int):
    try:
        return _LIVE[token]
    except KeyError:
        raise ReadOnlyViolation("the read-only IBKR connection is closed") from None


class ReadOnlyIB:
    """Allow-list view of an IB object: only ``ALLOWED_READ_METHODS`` exist on it."""
    __slots__ = ("_t",)

    def __init__(self, ib):
        token = next(_TOKENS)
        _LIVE[token] = ib
        object.__setattr__(self, "_t", token)

    def close(self) -> None:
        _LIVE.pop(self._t, None)

    def accountSummary(self, account: str = ""):
        return _live(self._t).accountSummary(account)

    def accountValues(self, account: str = ""):
        return _live(self._t).accountValues(account)

    def portfolio(self, account: str = ""):
        return _live(self._t).portfolio(account)

    def managedAccounts(self):
        return _live(self._t).managedAccounts()

    def isConnected(self):
        return _live(self._t).isConnected()

    def __getattr__(self, name: str):          # only reached for names that are not defined above
        raise ReadOnlyViolation(f"{name!r} is not available: this IBKR connection is read-only")

    def __setattr__(self, name, value):
        raise ReadOnlyViolation("the read-only IBKR wrapper cannot be modified")

    def __dir__(self):
        return sorted(ALLOWED_READ_METHODS)


@dataclass(slots=True)
class RawPosition:
    symbol: str
    exchange: str
    primary_exchange: str
    currency: str
    sec_type: str
    position: float
    avg_cost: Optional[float]
    market_price: Optional[float]
    market_value: Optional[float]
    unrealized_pnl: Optional[float]


@dataclass(slots=True)
class RawAccount:
    account: str
    base_currency: str
    summary: dict[str, float]                       # SUMMARY_TAGS -> value in base currency
    cash_by_currency: dict[str, float] = field(default_factory=dict)
    fx_rates: dict[str, float] = field(default_factory=dict)   # currency -> units of base per 1 unit
    positions: list[RawPosition] = field(default_factory=list)


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _default_factory():
    from ib_async import IB   # the only ib_async import in the repo (enforced by tests)
    return IB()


def _read_account(ro: ReadOnlyIB, account: Optional[str]) -> RawAccount:
    acct = account
    if not acct:
        managed = list(ro.managedAccounts() or [])
        acct = managed[0] if managed else ""
    summary: dict[str, float] = {}
    base = ""
    for v in ro.accountSummary(acct) or []:
        if getattr(v, "tag", None) not in SUMMARY_TAGS:
            continue
        if acct and getattr(v, "account", acct) not in (acct, ""):
            continue
        val = _f(getattr(v, "value", None))
        if val is None:
            continue
        summary[v.tag] = val
        cur = (getattr(v, "currency", "") or "").upper()
        if v.tag == "NetLiquidation" and cur and cur != "BASE":
            base = cur
    if "NetLiquidation" not in summary or not base:
        raise IBKRUnavailable("IBKR returned no NetLiquidation for the account")

    cash: dict[str, float] = {}
    fx: dict[str, float] = {base: 1.0}
    for v in ro.accountValues(acct) or []:
        if acct and getattr(v, "account", acct) not in (acct, ""):
            continue
        cur = (getattr(v, "currency", "") or "").upper()
        if not cur or cur == "BASE":
            continue
        val = _f(getattr(v, "value", None))
        if val is None:
            continue
        if v.tag == "CashBalance":
            cash[cur] = val
        elif v.tag == "ExchangeRate" and val > 0:
            fx[cur] = val

    positions = []
    for it in ro.portfolio(acct) or []:
        c = it.contract
        qty = _f(getattr(it, "position", None))
        if not qty or (getattr(c, "secType", "") or "").upper() == "CASH":
            continue
        positions.append(RawPosition(
            symbol=getattr(c, "symbol", "") or "", exchange=getattr(c, "exchange", "") or "",
            primary_exchange=getattr(c, "primaryExchange", "") or "",
            currency=(getattr(c, "currency", "") or base).upper(), sec_type=getattr(c, "secType", "") or "",
            position=qty, avg_cost=_f(getattr(it, "averageCost", getattr(it, "avgCost", None))),
            market_price=_f(getattr(it, "marketPrice", None)), market_value=_f(getattr(it, "marketValue", None)),
            unrealized_pnl=_f(getattr(it, "unrealizedPNL", None))))
    return RawAccount(account=acct, base_currency=base, summary=summary, cash_by_currency=cash, fx_rates=fx,
                      positions=positions)


def _disconnect_quietly(ib) -> None:
    try:
        ib.disconnect()
    except Exception:
        pass


def _fetch(box: dict, ib_factory: Optional[Callable], host: str, port: int, client_id: int, account: str,
           timeout: float) -> RawAccount:
    try:
        ib = (ib_factory or _default_factory)()
    except Exception as e:
        raise IBKRUnavailable(f"cannot create the IBKR client: {type(e).__name__}: {e}") from e
    box["ib"] = ib
    try:
        try:
            ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True, account=account or "")
        except IBKRUnavailable:
            raise
        except Exception as e:     # TimeoutError, ConnectionRefusedError, OSError, ib_async connection errors
            raise IBKRUnavailable(f"cannot connect to IB Gateway at {host}:{port}: {type(e).__name__}: {e}") from e
        ib.RequestTimeout = timeout          # ib_async's default of 0 waits forever for an answer
        if not ib.isConnected():
            raise IBKRUnavailable(f"IB Gateway at {host}:{port} did not stay connected")
        view = ReadOnlyIB(ib)
        try:
            return _read_account(view, account)
        except IBKRUnavailable:
            raise
        except Exception as e:
            raise IBKRUnavailable(f"reading the IBKR account failed: {type(e).__name__}: {e}") from e
        finally:
            view.close()
    finally:
        _disconnect_quietly(ib)


def fetch_account(host: Optional[str] = None, port: Optional[int] = None, client_id: Optional[int] = None,
                  account: Optional[str] = None, timeout: Optional[float] = None,
                  ib_factory: Optional[Callable] = None, deadline: Optional[float] = None) -> RawAccount:
    """Connect read-only, read the account summary, positions, cash and FX rates, disconnect.

    ``ib_factory`` returns the IB object (tests pass a fake; default is ``ib_async.IB()``). ``timeout`` bounds the
    connect and each request; ``deadline`` (default 3 x timeout) bounds the whole fetch, which runs in a worker
    thread so a gateway that never answers cannot block the caller.
    Raises IBKRUnavailable on any connection problem, timeout or incomplete answer.
    """
    host = host or config.IBKR_HOST
    port = port if port is not None else config.IBKR_PORT
    client_id = client_id if client_id is not None else config.IBKR_CLIENT_ID
    account = account if account is not None else config.IBKR_ACCOUNT
    timeout = timeout if timeout is not None else config.IBKR_TIMEOUT_S
    deadline = deadline if deadline is not None else 3.0 * timeout
    box: dict = {}

    def work() -> None:
        loop = None
        try:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)                         # ib_async needs a loop in this thread
                box["loop"] = loop
            except Exception:
                loop = None
            box["raw"] = _fetch(box, ib_factory, host, port, client_id, account or "", timeout)
        except BaseException as e:      # handed to the caller below
            box["err"] = e
        finally:
            if loop is not None:
                try:
                    loop.close()
                except Exception:
                    pass

    t = threading.Thread(target=work, name="ibkr-fetch", daemon=True)
    t.start()
    t.join(deadline)
    if t.is_alive():
        # Never touch the IB object from this thread: ask the worker's own loop to disconnect it. If the worker is
        # blocked outside its loop, _fetch's finally disconnects as soon as it unblocks.
        ib, loop = box.get("ib"), box.get("loop")
        if ib is not None and loop is not None:
            try:
                loop.call_soon_threadsafe(_disconnect_quietly, ib)
            except RuntimeError:        # the loop is already closed: the worker is finishing and disconnects itself
                pass
        raise IBKRUnavailable(f"IB Gateway at {host}:{port} did not answer within {deadline:g}s")
    if "err" in box:
        raise box["err"]
    return box["raw"]
