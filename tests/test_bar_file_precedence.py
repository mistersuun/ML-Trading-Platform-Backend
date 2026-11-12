"""Bar-file merge order: dated top-ups must win over the older named seed files (external_bars, pilot_report)."""
import importlib.util
import json
from pathlib import Path

import external_bars as eb


def _write(p: Path, day: str, close: float) -> None:
    p.write_text(json.dumps({"time": [f"{day}T14:30:00Z"], "open": [close], "high": [close], "low": [close],
                             "close": [close], "volume": [1000]}))


def test_symbol_files_puts_named_seeds_before_dated_topups(tmp_path):
    for name in ("ABC.json", "ABC.gapgo.json", "ABC.20261111.json", "ABC.20261102.json", "ABC.investor.json"):
        (tmp_path / name).write_text("{}")
    names = [p.name for p in eb.symbol_files("ABC", root=tmp_path)]
    assert names == ["ABC.json", "ABC.gapgo.json", "ABC.investor.json", "ABC.20261102.json", "ABC.20261111.json"]


def test_pilot_report_daily_topups_override_the_base_file(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "pilot_report", Path(__file__).resolve().parent.parent / "scripts" / "pilot_report.py")
    pr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pr)
    _write(tmp_path / "XEF.D.json", "2026-11-06", 50.0)
    _write(tmp_path / "XEF.D.20261106.json", "2026-11-06", 51.0)
    monkeypatch.setattr(pr, "BARS", tmp_path)
    assert pr.load_daily("XEF").iloc[-1] == 51.0
