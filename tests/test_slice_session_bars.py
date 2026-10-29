"""scripts/slice_session_bars.py: one New York session cut out of a multi-day payload. Invented prices only."""
import pytest

from scripts.slice_session_bars import slice_session

PAYLOAD = {
    "chart_step": 300, "chart_start": "2026-01-05T14:30:00Z", "chart_end": "2026-01-06T21:00:00Z",
    "source": "Last",
    "time": ["2026-01-05T20:55:00Z", "2026-01-06T14:30:00Z", "2026-01-06T14:35:00Z"],
    "volume": [10, 20, 30], "open": [1.0, 2.0, 3.0], "close": [1.5, 2.5, 3.5],
    "high": [1.6, 2.6, 3.6], "low": [0.9, 1.9, 2.9],
}


def test_keeps_only_the_session_and_copies_values_exactly():
    out = slice_session(PAYLOAD, __import__("datetime").date(2026, 1, 6))
    assert out["time"] == ["2026-01-06T14:30:00Z", "2026-01-06T14:35:00Z"]
    assert out["close"] == [2.5, 3.5] and out["volume"] == [20, 30] and out["low"] == [1.9, 2.9]
    assert out["chart_start"] == "2026-01-06T14:30:00Z" and out["chart_end"] == "2026-01-06T14:40:00Z"
    assert out["source"] == "Last"


def test_missing_session_raises():
    with pytest.raises(ValueError):
        slice_session(PAYLOAD, __import__("datetime").date(2026, 1, 7))
