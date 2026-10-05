"""Golden snapshot of every PATTERN_REGISTRY entry on gbm_ohlc(500, seed=42).

Regenerate deliberately with:  REGEN_GOLDEN=1 uv run pytest tests/test_golden_patterns.py
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from patterns import PATTERN_REGISTRY
from tests.fixtures.synthetic import gbm_ohlc

GOLDEN = Path(__file__).parent / "golden" / "pattern_signal_counts.json"


def _snapshot(name: str) -> dict:
    df = gbm_ohlc(n=500, seed=42)
    sig = PATTERN_REGISTRY[name](df)["signal"].fillna(0).to_numpy(dtype="int64")
    return {"buy": int((sig == 1).sum()), "sell": int((sig == -1).sum()),
            "sha256": hashlib.sha256(sig.tobytes()).hexdigest()}


def _regen() -> dict:
    data = {name: _snapshot(name) for name in sorted(PATTERN_REGISTRY)}
    GOLDEN.parent.mkdir(exist_ok=True)
    GOLDEN.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data


@pytest.fixture(scope="module")
def golden():
    if os.environ.get("REGEN_GOLDEN") == "1":
        return _regen()
    return json.loads(GOLDEN.read_text())


def test_golden_covers_registry(golden):
    assert set(golden) == set(PATTERN_REGISTRY), (
        "Golden file out of sync with PATTERN_REGISTRY. If deliberate, regenerate with "
        "REGEN_GOLDEN=1 uv run pytest tests/test_golden_patterns.py")


@pytest.mark.parametrize("name", sorted(PATTERN_REGISTRY))
def test_pattern_signals_match_golden(name, golden):
    assert name in golden, f"{name} missing from golden; regenerate with REGEN_GOLDEN=1"
    got = _snapshot(name)
    assert got == golden[name], (
        f"Signals for pattern '{name}' changed: expected {golden[name]}, got {got}. "
        "If this change is intentional, regenerate the snapshot with: "
        "REGEN_GOLDEN=1 uv run pytest tests/test_golden_patterns.py  (then review the git diff).")
