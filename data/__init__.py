"""Data layer: adapters (WS2.1), calendars, validation, persistent bar store (WS3.3)."""

from data.adapters import Bars, BarsMeta, fetch_bars, fetch_pair_bars, pinned_source, reset_pins
from data.store import cache_info, get_bars, get_pair_bars
from data.calendars import SessionCalendar, get_calendar
from data.validate import DataQualityError, DataUnavailableError, QualityReport, validate_ohlcv

__all__ = [
    "Bars", "BarsMeta", "fetch_bars", "fetch_pair_bars", "pinned_source", "reset_pins",
    "get_bars", "get_pair_bars", "cache_info",
    "SessionCalendar", "get_calendar",
    "DataQualityError", "DataUnavailableError", "QualityReport", "validate_ohlcv",
]
