"""Keeps tests/bugs/README.md in sync with the bug tests (names + xfail markers)."""
import ast
import pathlib
import re

BUGS = pathlib.Path(__file__).parent / "bugs"
NAME_RE = re.compile(r"^test_([A-Z]+)_(\d+[a-z]?)_")
REASON_RE = re.compile(r"^(?:BUG-)?([A-Z]+-\d+)")
ROW_RE = re.compile(r"^\|\s*([A-Z]+-\d+[a-z]?)\s*\|\s*`([^`]+)`\s*\|\s*(xfail|fixed)\s*\|", re.M)


def _xfail_reason(fn):
    """Return (has_xfail, reason or None) for a test function node."""
    for d in fn.decorator_list:
        if "xfail" in ast.unparse(d):
            reason = None
            if isinstance(d, ast.Call):
                for kw in d.keywords:
                    if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                        reason = kw.value.value
            return True, reason
    return False, None


def _tests():
    found = {}
    for f in sorted(BUGS.glob("test_*.py")):
        for n in ast.parse(f.read_text()).body:
            if isinstance(n, ast.FunctionDef):
                m = NAME_RE.match(n.name)
                if m:
                    x, reason = _xfail_reason(n)
                    found[f"{f.name}::{n.name}"] = (f"{m[1]}-{m[2]}", "xfail" if x else "fixed", reason)
    return found


def _index():
    rows = {}
    for bug, test, status in ROW_RE.findall((BUGS / "README.md").read_text()):
        assert test not in rows, f"duplicate index row for {test}"
        rows[test] = (bug, status)
    return rows


def test_every_bug_test_is_indexed_and_vice_versa():
    tests, index = _tests(), _index()
    assert tests, "no bug tests discovered"
    assert set(tests) - set(index) == set(), "bug tests missing from tests/bugs/README.md"
    assert set(index) - set(tests) == set(), "README rows without a matching test"


def test_index_ids_match_test_names():
    tests, index = _tests(), _index()
    for test, (bug, _) in index.items():
        assert tests[test][0] == bug, f"{test}: README says {bug}, test name says {tests[test][0]}"


def test_index_status_matches_xfail_markers():
    tests, index = _tests(), _index()
    for test, (_, status) in index.items():
        assert tests[test][1] == status, f"{test}: README says {status}, marker says {tests[test][1]}"


def test_xfail_reason_bug_id_matches_test():
    for test, (bug, status, reason) in _tests().items():
        if status != "xfail":
            continue
        assert reason, f"{test}: xfail without reason"
        m = REASON_RE.match(reason)
        assert m, f"{test}: xfail reason must start with BUG-<ID>: or <ID>:"
        assert re.sub(r"[a-z]$", "", bug) == m[1], f"{test}: reason ID {m[1]} != test ID {bug}"
