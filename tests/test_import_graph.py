"""WS3.1 import graph: the CLI and the routes reach the domain only through services/."""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

ALLOWED_FIRST_PARTY = {"services", "execution", "alerts", "risk_manager", "state", "config", "api",
                       "logging_setup", "allocation", "claude_integration", "routes", "results", "scheduler"}
BANNED = {"engine", "patterns", "pairs_trading", "ml_patterns", "backtester", "validation", "data_fetcher",
          "signals", "stress_test", "instruments", "features", "metrics", "portfolio_backtester"}


def _first_party_names() -> set[str]:
    return {p.stem for p in ROOT.glob("*.py")} | {p.name for p in ROOT.iterdir()
                                                  if p.is_dir() and (p / "__init__.py").exists()}


def _imported_roots(path: pathlib.Path) -> set[str]:
    roots = set()
    for n in ast.walk(ast.parse(path.read_text())):          # includes imports inside functions
        if isinstance(n, ast.Import):
            roots.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            roots.add(n.module.split(".")[0])
    return roots


def _adapters():
    return [ROOT / "main.py"] + sorted((ROOT / "routes").glob("*.py"))


def test_routes_and_main_import_only_the_allowed_modules():
    first_party = _first_party_names()
    bad = {}
    for p in _adapters():
        roots = _imported_roots(p)
        offenders = sorted((roots & first_party) - ALLOWED_FIRST_PARTY)
        if offenders:
            bad[p.name] = offenders
    assert bad == {}, f"import only services/execution/alerts/risk_manager/state/config/api/...: {bad}"


def test_routes_and_main_never_import_the_domain_modules_directly():
    for p in _adapters():
        hit = _imported_roots(p) & BANNED
        assert not hit, f"{p.name} imports {sorted(hit)} directly"


def test_the_graph_test_sees_the_adapters():
    names = {p.name for p in _adapters()}
    assert {"main.py", "pattern_routes.py", "backtest_routes.py", "pairs_routes.py", "ml_routes.py",
            "stress_routes.py", "data_routes.py"} <= names


def test_services_do_not_import_the_adapters():
    for p in (ROOT / "services").glob("*.py"):
        assert not (_imported_roots(p) & {"main", "routes", "server"}), p.name
