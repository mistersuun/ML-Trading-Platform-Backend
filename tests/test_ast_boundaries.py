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


def test_only_execution_and_brokers_import_brokers():
    offenders = [str(r) for r in _sources()
                 if r.parts[0] != "brokers" and r.name != "execution.py" and _hits(_imports(ROOT / r), "brokers")]
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
