"""Session calendars (WS2.1).

One tiny interface over four calendars used by the instrument registry:

* ``XNYS``  - NYSE sessions (exchange_calendars, ships its holiday data offline)
* ``CMES``  - CME Globex equity/metal/energy futures sessions (exchange_calendars)
* ``24/7``  - crypto: every UTC day is a session, closing at the next 00:00 UTC
* ``FX``    - spot FX: the trading week runs Sun evening to Fri 17:00 New York; a daily bar is
              labelled by the date it closes, so sessions are Mon-Fri, closing 17:00 New York

Session labels are tz-naive, midnight-normalised ``pd.Timestamp`` dates; closes are tz-aware UTC.
Naive datetimes passed as "now" are interpreted as UTC.
"""

from __future__ import annotations

import warnings
from functools import lru_cache
from typing import Optional, Union

import pandas as pd

TimeLike = Union[str, pd.Timestamp, "datetime.datetime"]  # noqa: F821


def to_utc(ts) -> pd.Timestamp:
    """Timestamp -> tz-aware UTC (naive input is taken to be UTC)."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _day(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    if t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t.normalize()


class SessionCalendar:
    name = ""

    def sessions(self, start, end) -> pd.DatetimeIndex:  # inclusive, naive normalised dates
        raise NotImplementedError

    def session_close(self, session) -> pd.Timestamp:  # tz-aware UTC
        raise NotImplementedError

    def is_session(self, day) -> bool:
        d = _day(day)
        return len(self.sessions(d, d)) == 1

    def last_closed_session(self, now) -> Optional[pd.Timestamp]:
        """Latest session whose close is <= now (None if there is none in the last 15 days)."""
        n = to_utc(now)
        today = _day(n)
        for s in reversed(self.sessions(today - pd.Timedelta(days=15), today)):
            if self.session_close(s) <= n:
                return s
        return None

    def closed_sessions(self, start, end, now) -> pd.DatetimeIndex:
        """Sessions in [start, end] whose close is <= now."""
        s = self.sessions(start, end)
        n = to_utc(now)
        return s[[self.session_close(x) <= n for x in s]] if len(s) else s


class _ExchangeCalendar(SessionCalendar):
    def __init__(self, name: str):
        import exchange_calendars as xcals

        self.name = name
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._cal = xcals.get_calendar(name)

    def sessions(self, start, end) -> pd.DatetimeIndex:
        lo = max(_day(start), self._cal.first_session)
        hi = min(_day(end), self._cal.last_session)
        if lo > hi:
            return pd.DatetimeIndex([])
        return self._cal.sessions_in_range(lo, hi)

    def session_close(self, session) -> pd.Timestamp:
        return to_utc(self._cal.session_close(_day(session)))


class _Crypto(SessionCalendar):
    name = "24/7"

    def sessions(self, start, end) -> pd.DatetimeIndex:
        return pd.date_range(_day(start), _day(end), freq="D")

    def session_close(self, session) -> pd.Timestamp:
        return to_utc(_day(session) + pd.Timedelta(days=1))


class _FX(SessionCalendar):
    name = "FX"

    def sessions(self, start, end) -> pd.DatetimeIndex:
        return pd.bdate_range(_day(start), _day(end))

    def session_close(self, session) -> pd.Timestamp:
        local = (_day(session) + pd.Timedelta(hours=17)).tz_localize("America/New_York")
        return local.tz_convert("UTC")


@lru_cache(maxsize=None)
def get_calendar(name: str) -> SessionCalendar:
    key = name.upper()
    if key in ("XNYS", "CMES"):
        return _ExchangeCalendar(key)
    if key in ("24/7", "CRYPTO"):
        return _Crypto()
    if key == "FX":
        return _FX()
    raise KeyError(f"unknown calendar {name!r}")
