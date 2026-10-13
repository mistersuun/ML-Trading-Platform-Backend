"""Read-only IBKR access (D14): fake IB object only, no network. Account numbers here are invented."""
import ast
import asyncio
import dataclasses
import pathlib
import threading
import time
from types import SimpleNamespace as NS

import pytest

from brokers import ibkr_readonly as ro
from brokers.ibkr_readonly import IBKRUnavailable, ReadOnlyIB, ReadOnlyViolation, fetch_account

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "tests", "__pycache__", "node_modules", ".git"}
# Every ib_async IB / Client name that transacts, changes the account or builds an order. Anything new that moves
# money or changes the account belongs here.
BANNED_ORDER_NAMES = {"placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "whatIfOrderAsync",
                      "exerciseOptions", "reqAutoOpenOrders", "replaceFA", "requestFA", "sendMsg", "bracketOrder",
                      "oneCancelsAll", "modifyOrder", "placeOrderAsync", "cancelOrderAsync"}
READONLY_MODULE = ROOT / "brokers" / "ibkr_readonly.py"


def av(tag, value, currency="CAD", account="U0000001"):
    return NS(account=account, tag=tag, value=str(value), currency=currency, modelCode="")


def item(symbol, exchange, currency, qty, price, avg, primary="", sec="STK", pnl=0.0):
    c = NS(symbol=symbol, secType=sec, exchange=exchange, primaryExchange=primary, currency=currency)
    return NS(contract=c, position=qty, marketPrice=price, marketValue=qty * price, averageCost=avg,
              unrealizedPNL=pnl, realizedPNL=0.0, account="U0000001")


class FakeIB:
    """Mimics the ib_async.IB surface this module may touch, plus order methods that must stay unreachable."""

    def __init__(self, connect_exc=None, read_exc=None, connected=True):
        self.connect_exc, self.read_exc, self._connected = connect_exc, read_exc, connected
        self.connect_args = None
        self.disconnected = 0
        self.order_calls = []

    def connect(self, host, port, clientId=0, timeout=4, readonly=False, account=""):
        self.connect_args = dict(host=host, port=port, clientId=clientId, timeout=timeout, readonly=readonly,
                                 account=account)
        if self.connect_exc:
            raise self.connect_exc

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self.disconnected += 1

    def managedAccounts(self):
        return ["U0000001"]

    def accountSummary(self, account=""):
        if self.read_exc:
            raise self.read_exc
        return [av("NetLiquidation", 50_000), av("TotalCashValue", -25_000), av("GrossPositionValue", 75_000),
                av("BuyingPower", 12_000), av("ExcessLiquidity", 9_000), av("MaintMarginReq", 21_000),
                av("AccruedCash", 5), av("NetLiquidation", 1, account="OTHER")]

    def accountValues(self, account=""):
        return [av("CashBalance", -30_000, "CAD"), av("CashBalance", 5_000, "USD"),
                av("CashBalance", -25_000, "BASE"), av("ExchangeRate", 1.0, "CAD"),
                av("ExchangeRate", 1.35, "USD"), av("ExchangeRate", 1.0, "BASE")]

    def portfolio(self, account=""):
        return [item("VFV", "SMART", "CAD", 100, 120.0, 90.0, primary="TSE", pnl=3000.0),
                item("CGL.C", "TSE", "CAD", 50, 24.0, 20.0),
                item("ABCD", "VENTURE", "CAD", 10, 3.0, 2.0),
                item("MSFT", "SMART", "USD", 20, 400.0, 300.0, primary="NASDAQ"),
                item("USD", "IDEALPRO", "CAD", 5000, 1.0, 1.0, sec="CASH"),
                item("ZZZ", "TSE", "CAD", 0, 10.0, 9.0)]

    # order surface: must never be reached through this module
    def placeOrder(self, *a, **k):
        self.order_calls.append("placeOrder")

    def cancelOrder(self, *a, **k):
        self.order_calls.append("cancelOrder")


def test_wrapper_exposes_only_the_allowed_read_methods():
    ib = FakeIB()
    w = ReadOnlyIB(ib)
    assert set(dir(w)) == set(ro.ALLOWED_READ_METHODS)
    assert w.managedAccounts() == ["U0000001"] and w.isConnected() is True
    for name in ("placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "connect", "disconnect",
                 "modifyOrder", "reqExecutions", "__dict__"):
        with pytest.raises((ReadOnlyViolation, AttributeError)):
            getattr(w, name)
    assert not hasattr(w, "placeOrder")
    with pytest.raises(ReadOnlyViolation):
        w.placeOrder = lambda *a: None
    with pytest.raises(ReadOnlyViolation):
        w.newattr = 1
    assert ib.order_calls == []


def test_allowed_methods_are_reads_only():
    assert ro.ALLOWED_READ_METHODS == {"accountSummary", "accountValues", "portfolio", "managedAccounts",
                                       "isConnected"}
    assert not any(any(b.lower() in m.lower() for b in ("order", "cancel", "modify", "place"))
                   for m in ro.ALLOWED_READ_METHODS)


def test_fetch_connects_read_only_reads_everything_and_disconnects():
    ib = FakeIB()
    raw = fetch_account(host="127.0.0.1", port=4001, client_id=17, account=None, timeout=3, ib_factory=lambda: ib)
    assert ib.connect_args == dict(host="127.0.0.1", port=4001, clientId=17, timeout=3, readonly=True, account="")
    assert ib.disconnected == 1 and ib.order_calls == []
    assert raw.account == "U0000001" and raw.base_currency == "CAD"
    assert raw.summary == {"NetLiquidation": 50_000, "TotalCashValue": -25_000, "GrossPositionValue": 75_000,
                           "BuyingPower": 12_000, "ExcessLiquidity": 9_000, "MaintMarginReq": 21_000}
    assert raw.cash_by_currency == {"CAD": -30_000, "USD": 5_000}
    assert raw.fx_rates == {"CAD": 1.0, "USD": 1.35}
    syms = {p.symbol: p for p in raw.positions}
    assert set(syms) == {"VFV", "CGL.C", "ABCD", "MSFT"}            # zero-quantity and CASH contracts dropped
    p = syms["VFV"]
    assert (p.primary_exchange, p.exchange, p.currency, p.sec_type, p.position) == ("TSE", "SMART", "CAD", "STK", 100)
    assert (p.avg_cost, p.market_price, p.market_value, p.unrealized_pnl) == (90.0, 120.0, 12_000.0, 3000.0)


def test_connect_timeout_and_refusal_raise_ibkr_unavailable_and_disconnect():
    for exc in (TimeoutError("t"), asyncio.TimeoutError(), ConnectionRefusedError("no gateway"), OSError("x")):
        ib = FakeIB(connect_exc=exc)
        with pytest.raises(IBKRUnavailable):
            fetch_account(host="h", port=1, client_id=1, account=None, timeout=1, ib_factory=lambda: ib)
        assert ib.disconnected == 1


def test_read_timeout_incomplete_answer_and_dropped_connection_are_unavailable():
    ib = FakeIB(read_exc=asyncio.TimeoutError())
    with pytest.raises(IBKRUnavailable):
        fetch_account(host="h", port=1, client_id=1, account=None, timeout=1, ib_factory=lambda: ib)
    assert ib.disconnected == 1
    ib = FakeIB(connected=False)
    with pytest.raises(IBKRUnavailable):
        fetch_account(host="h", port=1, client_id=1, account=None, timeout=1, ib_factory=lambda: ib)
    assert ib.disconnected == 1

    class NoSummary(FakeIB):
        def accountSummary(self, account=""):
            return []
    with pytest.raises(IBKRUnavailable, match="NetLiquidation"):
        fetch_account(host="h", port=1, client_id=1, account=None, timeout=1, ib_factory=NoSummary)


def test_defaults_come_from_config(monkeypatch):
    import config
    assert (config.IBKR_HOST, config.IBKR_PORT, config.IBKR_CLIENT_ID) == ("127.0.0.1", 4001, 17)
    assert config.IBKR_ACCOUNT is None and config.BASE_CURRENCY == "CAD"
    assert config.ALLOCATION_PROFILE == "cad" and config.MAX_LEVERAGE_WARN == 1.0
    ib = FakeIB()
    fetch_account(ib_factory=lambda: ib)
    assert ib.connect_args["host"] == "127.0.0.1" and ib.connect_args["port"] == 4001
    assert ib.connect_args["clientId"] == 17 and ib.connect_args["readonly"] is True


# ---------------------------------------------------------------- AST bans
def _py_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if not (set(rel.parts) & SKIP_DIRS):
            yield rel


def order_api_hits(tree: ast.AST) -> list[str]:
    """Any use of an order / cancel API name: attribute, name, def, import alias or string (getattr tricks)."""
    hits = []
    for n in ast.walk(tree):
        names = []
        if isinstance(n, ast.Attribute):
            names.append(n.attr)
        elif isinstance(n, ast.Name):
            names.append(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(n.name)
        elif isinstance(n, ast.alias):
            names.append(n.name.split(".")[-1])
        elif isinstance(n, ast.keyword) and n.arg:
            names.append(n.arg)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            names.append(n.value)
        hits += [f"line {getattr(n, 'lineno', '?')}: {x}" for x in names if x in BANNED_ORDER_NAMES]
    return hits


def ib_async_imports(tree: ast.AST) -> list[str]:
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            hits += [a.name for a in n.names if a.name.split(".")[0] == "ib_async"]
        elif isinstance(n, ast.ImportFrom) and n.module and n.module.split(".")[0] == "ib_async":
            hits.append(n.module)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.split(".")[0] == "ib_async":
            hits.append(n.value)           # importlib.import_module("ib_async")
    return hits


def test_no_order_api_names_anywhere_outside_tests():
    offenders = {}
    for rel in _py_files():
        h = order_api_hits(ast.parse((ROOT / rel).read_text()))
        if h:
            offenders[str(rel)] = h
    assert offenders == {}, offenders


def test_ib_async_is_imported_only_by_the_readonly_module():
    seen_in = []
    for rel in _py_files():
        if ib_async_imports(ast.parse((ROOT / rel).read_text())):
            seen_in.append(rel.as_posix())
    assert seen_in == ["brokers/ibkr_readonly.py"]


# ---------------------------------------------------------------- raw IB object rules (brokers/ibkr_readonly.py)
def raw_ib_violations(tree: ast.AST) -> list[str]:
    """Inside the read-only module: the raw IB object (a variable called `ib`) may only be used for the attributes in
    ALLOWED_RAW_IB_ATTRS; the wrapper's target (`_live(...)`) only for the five read methods; and getattr /
    __getattribute__ / setattr / vars / eval / exec may not be called with anything but a constant name."""
    bad = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            v = n.value
            if isinstance(v, ast.Name) and v.id == "ib" and n.attr not in ro.ALLOWED_RAW_IB_ATTRS:
                bad.append(f"line {n.lineno}: ib.{n.attr}")
            if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "_live"
                    and n.attr not in ro.ALLOWED_READ_METHODS):
                bad.append(f"line {n.lineno}: _live(...).{n.attr}")
            if n.attr in ("__getattribute__", "__dict__", "__closure__", "__globals__"):
                bad.append(f"line {n.lineno}: .{n.attr}")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            fn = n.func.id
            if fn in ("eval", "exec", "vars", "__import__", "compile", "globals", "locals"):
                bad.append(f"line {n.lineno}: {fn}()")
            if fn in ("getattr", "setattr", "delattr", "hasattr"):
                name = n.args[1] if len(n.args) > 1 else None
                if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
                    bad.append(f"line {n.lineno}: {fn}() with a non-constant name")
                elif n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "ib" \
                        and name.value not in ro.ALLOWED_RAW_IB_ATTRS:
                    bad.append(f"line {n.lineno}: {fn}(ib, {name.value!r})")
    return bad


def test_the_readonly_module_only_touches_the_allowed_raw_ib_surface():
    assert raw_ib_violations(ast.parse(READONLY_MODULE.read_text())) == []


@pytest.mark.parametrize("snippet", [
    "ib.exerciseOptions(c, 1, 1, 'a')", "ib.whatIfOrderAsync(c, o)", "ib.placeOrder(c, o)", "ib.reqAutoOpenOrders(True)",
    "ib.client.sendMsg(1)", "getattr(ib, 'place' + 'Order')", "getattr(ib, name)", "getattr(ib, 'placeOrder')",
    "_live(t).placeOrder(c, o)", "_live(t).connect()", "ib.__getattribute__('x')", "ib.__dict__",
    "setattr(ib, 'x', 1)", "eval('1')", "vars(ib)",
])
def test_raw_ib_scanner_flags_these(snippet):
    assert raw_ib_violations(ast.parse(snippet)), snippet


def test_raw_ib_scanner_accepts_the_allowed_surface():
    ok = ast.parse("ib.connect(h, p)\nib.RequestTimeout = 3\nib.isConnected()\nib.disconnect()\n"
                   "_live(t).accountSummary(a)\ngetattr(v, 'tag', None)\n")
    assert raw_ib_violations(ok) == []


def test_banned_names_cover_the_transacting_ib_async_api():
    """If ib_async is installed, every transacting IB / Client method must be in the ban list (so a new ib_async
    release that adds one is noticed by the next reviewer who extends this set)."""
    must = {"placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "whatIfOrderAsync", "exerciseOptions",
            "reqAutoOpenOrders", "replaceFA", "sendMsg", "bracketOrder", "oneCancelsAll"}
    assert must <= BANNED_ORDER_NAMES
    try:
        from ib_async import IB, Client
    except ImportError:
        pytest.skip("ib_async not installed")
    for cls, names in ((IB, ("placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder", "whatIfOrderAsync",
                             "exerciseOptions", "reqAutoOpenOrders", "replaceFA", "bracketOrder", "oneCancelsAll")),
                       (Client, ("placeOrder", "cancelOrder", "reqGlobalCancel", "exerciseOptions", "sendMsg",
                                 "replaceFA"))):
        for name in names:
            assert hasattr(cls, name) and name in BANNED_ORDER_NAMES, (cls.__name__, name)


# ---------------------------------------------------------------- the IB object cannot escape
def _only_primitives(x, seen=None, path="raw"):
    seen = seen if seen is not None else set()
    if x is None or isinstance(x, (str, int, float, bool)):
        return
    assert id(x) not in seen, f"cycle at {path}"
    seen.add(id(x))
    if isinstance(x, dict):
        for k, v in x.items():
            _only_primitives(k, seen, f"{path}.key")
            _only_primitives(v, seen, f"{path}[{k!r}]")
    elif isinstance(x, (list, tuple)):
        for i, v in enumerate(x):
            _only_primitives(v, seen, f"{path}[{i}]")
    elif dataclasses.is_dataclass(x) and not isinstance(x, type):
        assert not hasattr(x, "__dict__"), f"{path}: a dataclass with __dict__ can grow a stray attribute"
        for f in dataclasses.fields(x):
            _only_primitives(getattr(x, f.name), seen, f"{path}.{f.name}")
    else:
        raise AssertionError(f"{path} is a {type(x).__name__}, not a primitive or a dataclass of primitives")


def test_the_result_holds_only_primitives_and_cannot_carry_the_ib_object():
    ib = FakeIB()
    raw = fetch_account(host="h", port=1, client_id=1, account=None, timeout=1, ib_factory=lambda: ib)
    _only_primitives(raw)
    with pytest.raises(AttributeError):
        raw.ib = ib                                  # slotted: no stray attribute can carry the IB object
    with pytest.raises(AttributeError):
        raw.positions[0].ib = ib


def test_primitive_check_rejects_an_object():
    class Leak:
        pass
    with pytest.raises(AssertionError):
        _only_primitives({"x": [Leak()]})
    box = ro.RawAccount("a", "CAD", {})
    box.summary["leak"] = FakeIB()
    with pytest.raises(AssertionError):
        _only_primitives(box)


def test_wrapper_does_not_hold_the_ib_object_in_a_slot_or_closure():
    ib = FakeIB()
    w = ReadOnlyIB(ib)
    held = [object.__getattribute__(w, s) for s in type(w).__slots__]   # test code may introspect
    assert all(not isinstance(h, FakeIB) for h in held)
    for name in ro.ALLOWED_READ_METHODS:
        fn = type(w).__dict__[name]
        assert all(not isinstance(c.cell_contents, FakeIB) for c in (fn.__closure__ or ()))
    w.close()
    with pytest.raises(ReadOnlyViolation):
        w.managedAccounts()


# ---------------------------------------------------------------- timeouts
def test_request_timeout_is_set_on_the_raw_ib_before_reading():
    seen = {}

    class Recording(FakeIB):
        def accountSummary(self, account=""):
            seen["rt"] = getattr(self, "RequestTimeout", None)
            return super().accountSummary(account)
    ib = Recording()
    fetch_account(host="h", port=1, client_id=1, account=None, timeout=7, ib_factory=lambda: ib)
    assert seen["rt"] == 7


def _join_fetch_threads():
    for th in threading.enumerate():
        if th.name == "ibkr-fetch":
            th.join(5)


def test_timeout_disconnect_runs_on_the_workers_loop_and_the_loop_is_closed():
    import asyncio
    release = threading.Event()
    seen = {}

    class Hung(FakeIB):
        def accountSummary(self, account=""):
            seen["loop"] = asyncio.get_event_loop()
            seen["thread"] = threading.current_thread()
            # blocked in a running loop (as ib_async is): the caller's call_soon_threadsafe is serviced here
            seen["loop"].call_later(0.05, release.set)
            seen["loop"].run_until_complete(asyncio.sleep(0.5))
            return []

        def disconnect(self):
            seen.setdefault("disc_threads", []).append(threading.current_thread())
            return super().disconnect()
    ib = Hung()
    with pytest.raises(IBKRUnavailable, match="did not answer"):
        fetch_account(host="h", port=1, client_id=1, account=None, timeout=0.1, deadline=0.2, ib_factory=lambda: ib)
    _join_fetch_threads()
    assert ib.disconnected >= 1
    assert all(t is seen["thread"] for t in seen["disc_threads"])       # never from the caller's thread
    assert seen["loop"].is_closed()


def test_a_gateway_that_never_answers_raises_within_the_deadline_and_disconnects():
    release = threading.Event()

    class Hung(FakeIB):
        def accountSummary(self, account=""):
            release.wait(30)                          # accepted the socket, never answers (ib_async RequestTimeout=0)
            return []
    ib = Hung()
    t0 = time.monotonic()
    try:
        with pytest.raises(IBKRUnavailable, match="did not answer"):
            fetch_account(host="h", port=1, client_id=1, account=None, timeout=0.2, ib_factory=lambda: ib)
        assert time.monotonic() - t0 < 5
    finally:
        release.set()
    _join_fetch_threads()
    assert ib.disconnected >= 1                        # the worker disconnects once it unblocks


def test_a_hung_connect_also_raises_within_the_deadline():
    release = threading.Event()

    class HungConnect(FakeIB):
        def connect(self, *a, **k):
            release.wait(30)
    t0 = time.monotonic()
    try:
        with pytest.raises(IBKRUnavailable):
            fetch_account(host="h", port=1, client_id=1, account=None, timeout=0.1, deadline=0.3,
                          ib_factory=HungConnect)
        assert time.monotonic() - t0 < 5
    finally:
        release.set()


def test_the_default_deadline_is_three_times_the_timeout():
    import inspect
    assert "3.0 * timeout" in inspect.getsource(fetch_account)


def test_the_module_docstring_does_not_claim_readonly_blocks_orders():
    doc = ro.__doc__
    assert "refuse to send orders" not in doc and "impossible by construction" not in doc
    assert "Read-Only API" in doc and "skips" in doc and "placeOrder" in doc


def test_the_ast_scanners_detect_violations():
    bad = ast.parse("ib.placeOrder(c, o)\nx = getattr(ib, 'cancelOrder')\ndef whatIfOrder(): pass\n"
                    "from ib_async import IB, reqGlobalCancel\nimport ib_async.util\n"
                    "importlib.import_module('ib_async')\n")
    got = " ".join(order_api_hits(bad))
    for name in ("placeOrder", "cancelOrder", "reqGlobalCancel", "whatIfOrder"):
        assert name in got
    assert len(ib_async_imports(bad)) == 3
    more = ast.parse("\n".join(f"ib.{n}()" for n in sorted(BANNED_ORDER_NAMES)))
    assert {h.split(": ")[1] for h in order_api_hits(more)} == BANNED_ORDER_NAMES
    clean = ast.parse("import ib\nx = 'orders'\n")
    assert order_api_hits(clean) == [] and ib_async_imports(clean) == []
