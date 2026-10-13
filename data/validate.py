"""OHLCV validation (WS2.1).

Hard failures raise ``DataQualityError`` and are never silently returned. Soft findings (large
one-day moves, small staleness) are recorded in the ``QualityReport`` flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

import config
from data.calendars import SessionCalendar, get_calendar

OHLCV = ["Open", "High", "Low", "Close", "Volume"]
STALE_MAX_SESSIONS = 5  # last bar more than this many expected sessions behind -> hard failure
_REL_TOL = 1e-6


class DataQualityError(ValueError):
    """Fetched data failed a hard validation check."""

    def __init__(self, message: str, report: "Optional[QualityReport]" = None, symbol: str = ""):
        super().__init__(message)
        self.report = report
        self.symbol = symbol


class DataUnavailableError(RuntimeError):
    """No source returned any data for the request."""


@dataclass
class QualityReport:
    symbol: str = ""
    n_bars: int = 0
    first: Optional[pd.Timestamp] = None
    last: Optional[pd.Timestamp] = None
    expected_sessions: Optional[int] = None
    coverage: Optional[float] = None
    stale_sessions: int = 0
    dropped_in_progress: int = 0
    dropped_non_session: int = 0
    flags: list = field(default_factory=list)  # soft findings
    failures: list = field(default_factory=list)  # hard findings

    @property
    def ok(self) -> bool:
        return not self.failures

    def raise_if_failed(self) -> "QualityReport":
        if self.failures:
            raise DataQualityError(f"{self.symbol or 'data'}: " + "; ".join(self.failures), self, self.symbol)
        return self


def validate_ohlcv(
    df: pd.DataFrame,
    calendar,
    *,
    symbol: str = "",
    start=None,
    end=None,
    now=None,
    daily: bool = True,
    raise_on_failure: bool = True,
    dropped_in_progress: int = 0,
    dropped_non_session: int = 0,
) -> QualityReport:
    """Validate a frame of bars.

    Coverage is the share of expected (closed) calendar sessions in [start, end] that are present;
    when start/end are omitted the span of the data itself is used (interior gaps only).
    """
    cal: SessionCalendar = get_calendar(calendar) if isinstance(calendar, str) else calendar
    rep = QualityReport(symbol=symbol, dropped_in_progress=dropped_in_progress, dropped_non_session=dropped_non_session)
    rep.n_bars = len(df)
    fails = rep.failures

    if df is None or df.empty:
        fails.append("no bars")
        return rep.raise_if_failed() if raise_on_failure else rep

    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        fails.append(f"missing columns {missing}")
        return rep.raise_if_failed() if raise_on_failure else rep

    rep.first, rep.last = df.index[0], df.index[-1]
    if not df.index.is_unique:
        fails.append(f"duplicate index entries ({int(df.index.duplicated().sum())})")
    if not df.index.is_monotonic_increasing:
        fails.append("index not monotonic increasing")

    vals = df[OHLCV].astype(float)
    if vals.isna().any().any():
        fails.append(f"NaN values in {[c for c in OHLCV if vals[c].isna().any()]}")
    if np.isinf(vals.to_numpy(dtype=float)).any():
        fails.append("non-finite values")
    o, h, lo, c, v = (vals[k] for k in OHLCV)
    ohlc_bad = int(((vals[["Open", "High", "Low", "Close"]] <= 0)).any(axis=1).sum())
    if ohlc_bad:
        fails.append(f"non-positive OHLC on {ohlc_bad} bars")
    tol = _REL_TOL * c.abs().clip(lower=1e-12)
    bad_hi = int((h < pd.concat([o, c, lo], axis=1).max(axis=1) - tol).sum())
    bad_lo = int((lo > pd.concat([o, c, h], axis=1).min(axis=1) + tol).sum())
    if bad_hi:
        fails.append(f"High < max(Open, Close, Low) on {bad_hi} bars")
    if bad_lo:
        fails.append(f"Low > min(Open, Close, High) on {bad_lo} bars")
    if int((v < 0).sum()):
        fails.append(f"negative Volume on {int((v < 0).sum())} bars")

    # large one-day moves are flagged, not dropped
    rets = c.pct_change(fill_method=None).abs()
    big = rets[rets > config.DATA_MAX_ABS_DAILY_RETURN]
    for ts, r in big.items():
        rep.flags.append(f"abs return {r:.2f} > {config.DATA_MAX_ABS_DAILY_RETURN} on {pd.Timestamp(ts).date()}")

    if daily and df.index.is_unique:
        now_utc = pd.Timestamp.now(tz="UTC") if now is None else now
        s_start = pd.Timestamp(start).normalize() if start is not None else df.index[0]
        s_end = pd.Timestamp(end).normalize() if end is not None else df.index[-1]
        expected = cal.closed_sessions(s_start, s_end, now_utc) if (start is not None or end is not None) else cal.sessions(s_start, s_end)
        rep.expected_sessions = len(expected)
        present = df.index.normalize().intersection(expected)
        rep.coverage = (len(present) / len(expected)) if len(expected) else 1.0
        if rep.coverage < config.DATA_COVERAGE_MIN:
            fails.append(
                f"coverage {rep.coverage:.3f} < {config.DATA_COVERAGE_MIN} ({len(present)}/{len(expected)} expected sessions)"
            )
        after = expected[expected > df.index[-1].normalize()]
        rep.stale_sessions = len(after)
        if rep.stale_sessions > STALE_MAX_SESSIONS:
            fails.append(f"stale: last bar {df.index[-1].date()} is {rep.stale_sessions} sessions behind")
        elif rep.stale_sessions:
            rep.flags.append(f"last bar {rep.stale_sessions} session(s) behind")

    return rep.raise_if_failed() if raise_on_failure else rep
