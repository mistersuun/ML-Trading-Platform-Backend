"""Import-graph rules: only execution.py (and brokers/*, tests) may import brokers.*;
advisory modules must never reach the order path."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "tests", "__pycache__", "node_modules", ".git"}


def _imports(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            names.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
            if n.level == 0:
                names.update(f"{n.module}.{a.name}" for a in n.names)
    return names


def _sources():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if not (set(rel.parts) & SKIP_DIRS):
            yield rel


def _hits(names: set[str], mod: str) -> bool:
    return any(n == mod or n.startswith(mod + ".") for n in names)


# D14: the one extra importer is the read-only IBKR sync, and only of the read-only module.
_READONLY_IMPORTERS = {pathlib.Path("account/sync.py")}


def _only_readonly_module(broker_names: set[str]) -> bool:
    """True only when there is at least one broker import and every one is brokers.ibkr_readonly (or a member of it).
    A bare `import brokers` leaves the set empty and must NOT pass (all() of nothing is True)."""
    return bool(broker_names) and all(_hits({n}, "brokers.ibkr_readonly") for n in broker_names)


def test_readonly_importer_exception_is_not_vacuous():
    assert _only_readonly_module({"brokers.ibkr_readonly", "brokers.ibkr_readonly.fetch_account"})
    assert not _only_readonly_module(set())                       # bare `import brokers`
    assert not _only_readonly_module({"brokers.ibkr_readonly", "brokers.alpaca_broker"})
    assert not _only_readonly_module({"brokers.ibkr_readonly_extra"})


def test_only_execution_and_brokers_import_brokers():
    offenders = []
    for r in _sources():
        if r.parts[0] == "brokers" or r.name == "execution.py":
            continue
        names = _imports(ROOT / r)
        if not _hits(names, "brokers"):
            continue
        broker_names = {n for n in names if _hits({n}, "brokers")} - {"brokers"}
        if r in _READONLY_IMPORTERS and _only_readonly_module(broker_names):
            continue
        offenders.append(str(r))
    assert offenders == [], f"brokers.* may only be imported by execution.py: {offenders}"


def test_nothing_imports_the_alpaca_trading_sdk_outside_brokers():
    offenders = [str(r) for r in _sources()
                 if r.parts[0] != "brokers" and _hits(_imports(ROOT / r), "alpaca.trading")]
    assert offenders == [], offenders


def test_advisory_modules_never_import_the_order_path():
    for name in ("claude_integration.py", "allocation.py"):
        names = _imports(ROOT / name)
        for banned in ("execution", "brokers", "paper_trader", "signals"):
            assert not _hits(names, banned), f"{name} imports {banned}"


def test_briefing_modules_never_import_the_order_path_or_risk_writes():
    for name in ("claude_integration.py", "services/briefing.py", "routes/briefing_routes.py"):
        names = _imports(ROOT / name)
        for banned in ("execution", "brokers", "paper_trader", "signals", "risk_manager", "alerts"):
            assert not _hits(names, banned), f"{name} imports {banned}"


def test_paper_trader_shim_routes_through_execution():
    names = _imports(ROOT / "paper_trader.py")
    assert _hits(names, "execution") and not _hits(names, "brokers")


def test_execution_never_imports_the_scan_or_alert_layers():
    names = _imports(ROOT / "execution.py")
    for banned in ("main", "server", "alerts", "claude_integration", "allocation", "paper_trader"):
        assert not _hits(names, banned), banned


def test_walker_sees_real_imports():  # guard against the scan silently matching nothing
    assert _hits(_imports(ROOT / "execution.py"), "brokers")
    assert pathlib.Path("brokers/alpaca.py") in set(_sources())


# ── no raw order calls outside the chokepoint ───────────────────────────────

_RAW_ORDER_METHODS = {"submit_order", "close_position", "close_all_positions", "cancel_order_by_id",
                      "cancel_orders", "cancel_order", "replace_order_by_id", "submit"}  # submit = AlpacaBroker.submit
_CANCEL_METHODS = {"cancel_order_by_id", "cancel_orders", "cancel_order"}  # execution.py may cancel (halt, exits)
_EXECUTION_METHODS = _CANCEL_METHODS | {"submit"}  # ...and is the one module that submits


def _raw_calls(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text())
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _RAW_ORDER_METHODS:
            hits.append(f"{path.name}:{n.lineno} .{n.func.attr}(")
        if isinstance(n, (ast.Name, ast.Attribute)) and getattr(n, "id", getattr(n, "attr", "")) == "build_trading_client":
            hits.append(f"{path.name}:{n.lineno} build_trading_client")
        if isinstance(n, ast.ImportFrom) and any(a.name == "build_trading_client" for a in n.names):
            hits.append(f"{path.name}:{n.lineno} import build_trading_client")
    return hits


def test_no_raw_order_calls_or_raw_client_outside_brokers_and_execution():
    offenders = []
    for r in _sources():
        if r.parts[0] == "brokers":
            continue
        for h in _raw_calls(ROOT / r):
            method = h.split(".")[-1].rstrip("(")
            if r.name == "execution.py" and method in _EXECUTION_METHODS:
                continue  # the chokepoint: submit, cancel_open_entries, protective-leg cancel before an exit
            offenders.append(f"{r}: {h}")
    assert offenders == [], offenders


def test_raw_call_scanner_detects_violations(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import x\nc = x.build_trading_client()\nc.submit_order(1)\nc.close_position('A')\n"
                   "broker.submit(spec)\nfrom brokers.alpaca import build_trading_client\n")
    assert len(_raw_calls(bad)) >= 5
    assert any(h.endswith(".submit(") for h in _raw_calls(bad))
    good = tmp_path / "good.py"
    good.write_text("def submit_order(*a):\n    return None\n")  # defining a stub is not calling one
    assert _raw_calls(good) == []


def test_execution_has_no_raw_client_reexport():
    assert not hasattr(__import__("execution"), "build_trading_client")


# ── defence in depth: no reach-around of the read-only IBKR wrapper (D14) ─────
_READONLY_MODULE = pathlib.Path("brokers/ibkr_readonly.py")
_NO_DYNAMIC_DIRS = {"account", "services"}


def _is_const_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _live_refs(tree: ast.AST) -> list[str]:
    hits = []
    for n in ast.walk(tree):
        ident = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.name if isinstance(n, ast.alias) else None
        if ident in ("_LIVE", "_live"):
            hits.append(f"line {getattr(n, 'lineno', '?')}: {ident}")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in ("_LIVE", "_live"):
            hits.append(f"line {n.lineno}: '{n.value}'")
    return hits


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""


def _dynamic_getattr(tree: ast.AST) -> list[str]:
    """getattr/setattr/delattr/vars-style access whose attribute name is not a string constant."""
    return [f"line {n.lineno}: {_call_name(n)}(...)" for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _call_name(n) in ("getattr", "setattr", "delattr")
            and not (len(n.args) >= 2 and _is_const_str(n.args[1]))]


def _computed_imports(tree: ast.AST) -> list[str]:
    return [f"line {n.lineno}: {_call_name(n)}(...)" for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _call_name(n) in ("import_module", "__import__")
            and not (n.args and _is_const_str(n.args[0]) and not n.keywords)]


def test_live_registry_is_referenced_only_inside_the_readonly_module():
    offenders = {}
    for r in _sources():
        if r == _READONLY_MODULE:
            continue
        h = _live_refs(ast.parse((ROOT / r).read_text()))
        if h:
            offenders[str(r)] = h
    assert offenders == {}, offenders


def test_no_dynamic_getattr_in_account_and_services():
    offenders = {}
    for r in _sources():
        if r.parts[0] in _NO_DYNAMIC_DIRS:
            h = _dynamic_getattr(ast.parse((ROOT / r).read_text()))
            if h:
                offenders[str(r)] = h
    assert offenders == {}, offenders


def test_no_computed_imports_anywhere_outside_the_readonly_module():
    offenders = {}
    for r in _sources():
        if r == _READONLY_MODULE:
            continue
        h = _computed_imports(ast.parse((ROOT / r).read_text()))
        if h:
            offenders[str(r)] = h
    assert offenders == {}, offenders


def test_the_new_scanners_detect_violations():
    bad = ast.parse("import importlib\nx = r._LIVE\nfrom m import _live\n"
                    "getattr(x, 'place' + 'Order')\ngetattr(x, name, None)\nsetattr(x, n, 1)\n"
                    "importlib.import_module('ib_' + 'async')\n__import__(name)\nimportlib.import_module(n)\n"
                    "importlib.import_module('ib_async')\n")
    assert len(_live_refs(bad)) == 2
    assert len(_dynamic_getattr(bad)) == 3
    assert len(_computed_imports(bad)) == 3           # the plain constant import_module('ib_async') is not "computed"
    ok = ast.parse("getattr(x, 'tag', None)\nimportlib.import_module('json')\n")
    assert _dynamic_getattr(ok) == [] and _computed_imports(ok) == []
