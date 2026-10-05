"""Market-data adapters and the validated fetch pipeline (WS2.1).

Sources: Alpaca (US equities/ETFs + crypto) and yfinance (everything). Alpha Vantage was removed:
it mixed adjusted Close with unadjusted O/H/L (DATA-2) and was sent futures as equities (DATA-1).

Daily bars are normalised to exchange session dates, bars whose session has not closed are
dropped (DATA-3), and the result is validated; hard failures raise ``DataQualityError``.
One source is pinned per symbol per process so a series never silently mixes vendors.
"""

from __future__ import annotations

import logging
import threading
import time
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests
import yfinance as yf
from tenacity import RetryCallState, Retrying, retry_if_exception_type, retry_if_result, stop_after_attempt, wait_exponential_jitter

import config
import instruments
from data.calendars import SessionCalendar, get_calendar, to_utc
from data.validate import (
    OHLCV,
    DataQualityError,
    DataUnavailableError,
    QualityReport,
    validate_ohlcv,
)

logger = logging.getLogger(__name__)

SUPPORTED_SOURCES = ("alpaca", "yfinance")
ALPACA_DELAY = timedelta(minutes=16)  # free-tier SIP data is delayed 15 min; clamp the request end
_TIMEFRAMES = {"1d": "1Day", "1Day": "1Day", "1h": "1Hour", "1Hour": "1Hour", "15m": "15Min",
               "15Min": "15Min", "5m": "5Min", "5Min": "5Min", "1m": "1Min", "1Min": "1Min"}


# ------------------------------------------------------------------ result types

@dataclass(frozen=True)
class BarsMeta:
    source: str
    adjusted: bool
    calendar: str
    fetched_at: datetime
    quality: QualityReport
    stale: bool = False        # served from the bar store because the (pinned) source failed; never a silent switch
    from_cache: bool = False


@dataclass
class Bars:
    df: pd.DataFrame
    meta: BarsMeta

    source = property(lambda self: self.meta.source)
    adjusted = property(lambda self: self.meta.adjusted)
    calendar = property(lambda self: self.meta.calendar)
    fetched_at = property(lambda self: self.meta.fetched_at)
    quality = property(lambda self: self.meta.quality)
    stale = property(lambda self: self.meta.stale)


@dataclass(frozen=True)
class _Spec:
    symbol: str
    asset_class: str
    calendar: str
    yf_symbol: str
    alpaca_symbol: Optional[str]


def _spec(symbol: str) -> _Spec:
    """Route by the instrument registry; unknown tickers are inferred from their suffix."""
    try:
        i = instruments.get(symbol)
        return _Spec(symbol, i.asset_class, i.calendar, i.yfinance_symbol, i.alpaca_data_symbol)
    except instruments.UnknownSymbol:
        logger.warning("%s is not in the instrument registry; inferring asset class from the ticker", symbol)
        if symbol.startswith("^"):
            ac, cal = "index", "XNYS"
        elif symbol.endswith("=X"):
            ac, cal = "fx", "FX"
        elif symbol.endswith("=F"):
            ac, cal = "futures", "CMES"
        elif symbol.endswith("-USD"):
            ac, cal = "crypto", "24/7"
        else:
            ac, cal = "equity", "XNYS"
        alp = symbol.replace("-", ".") if ac == "equity" else (symbol.replace("-", "/") if ac == "crypto" else None)
        return _Spec(symbol, ac, cal, symbol, alp)


# ------------------------------------------------------------------ source pins

_PINNED: dict = {}          # in-memory fallback / cache; the bar_sources table is authoritative when the state DB exists
_PIN_LOCK = threading.Lock()


def _window_class(period_days: Optional[int] = None) -> str:
    """Pins are per (symbol, window class): a vendor that covers 730 days (e.g. a short IEX feed) may not cover
    the 8-year research window, so the research window decides its own pin instead of inheriting a short one."""
    return "research" if period_days is not None and period_days >= config.RESEARCH_LOOKBACK_DAYS else "default"


def _pin_key(symbol: str, period_days: Optional[int] = None) -> str:
    return f"{symbol}#research" if _window_class(period_days) == "research" else symbol


def _state_conn():
    """Connection to the migrated state DB, or None when it is not initialised (research scripts, tests)."""
    try:
        from state import db as _db
        return _db.require_initialized()
    except Exception:
        return None


def _db_run(fn):
    conn = _state_conn()
    if conn is None:
        return None, False
    try:
        return fn(conn), True
    except Exception as e:  # a broken pin table must not break data fetching
        logger.warning("bar_sources unavailable (%s); using in-memory pins", type(e).__name__)
        return None, False
    finally:
        conn.close()


def _get_pin(symbol: str, period_days: Optional[int] = None) -> Optional[str]:
    wc = _window_class(period_days)
    row, used = _db_run(lambda c: c.execute(
        "SELECT source FROM bar_sources WHERE symbol=? AND window_class=?", (symbol, wc)).fetchone())
    if used:
        return row["source"] if row else None
    return _PINNED.get(_pin_key(symbol, period_days))


def _set_pin(symbol: str, period_days: Optional[int], source: str) -> None:
    wc = _window_class(period_days)
    with _PIN_LOCK:
        _PINNED[_pin_key(symbol, period_days)] = source
    _db_run(lambda c: c.execute(
        "INSERT INTO bar_sources(symbol, window_class, source, pinned_at) VALUES (?,?,?,?) "
        "ON CONFLICT(symbol, window_class) DO UPDATE SET source=excluded.source, pinned_at=excluded.pinned_at",
        (symbol, wc, source, datetime.now(timezone.utc).isoformat())))


def pinned_source(symbol: str, period_days: Optional[int] = None) -> Optional[str]:
    return _get_pin(symbol, period_days)


def reset_pins(symbol: Optional[str] = None) -> None:
    with _PIN_LOCK:
        if symbol is None:
            _PINNED.clear()
        else:
            _PINNED.pop(symbol, None)
            _PINNED.pop(f"{symbol}#research", None)
    if symbol is None:
        _db_run(lambda c: c.execute("DELETE FROM bar_sources"))
    else:
        _db_run(lambda c: c.execute("DELETE FROM bar_sources WHERE symbol=?", (symbol,)))


# ------------------------------------------------------------------ HTTP retries (tenacity)

RETRY_ATTEMPTS = 4
RETRY_MAX_WAIT = 60.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})   # never 401/403/404: those will not heal by waiting
_sleep = time.sleep                                     # tests replace this so no test waits


def _retry_after(resp) -> Optional[float]:
    """Seconds from a Retry-After header (delta-seconds or HTTP date), else None."""
    try:
        raw = (getattr(resp, "headers", None) or {}).get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            return max(0.0, (parsedate_to_datetime(str(raw)) - datetime.now(timezone.utc)).total_seconds())
        except Exception:
            return None


_backoff = wait_exponential_jitter(initial=1.0, max=RETRY_MAX_WAIT, jitter=1.0)


def _wait(state: RetryCallState) -> float:
    out = state.outcome
    if out is not None and not out.failed:
        ra = _retry_after(out.result())
        if ra is not None:
            return min(ra, RETRY_MAX_WAIT)
    return _backoff(state)


def _retryable_response(resp) -> bool:
    return getattr(resp, "status_code", 200) in _RETRY_STATUS


def _retrying() -> Retrying:
    return Retrying(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=_wait,
        retry=(retry_if_exception_type((requests.Timeout, requests.ConnectionError))
               | retry_if_result(_retryable_response)),
        sleep=lambda s: _sleep(s),
        retry_error_callback=lambda st: st.outcome.result(),   # exhausted on a status: hand the response back
        reraise=True,
    )


def http_get(url: str, **kw):
    """requests.get with retries for 429/5xx/timeouts (exponential backoff + jitter, honours Retry-After)."""
    return _retrying()(lambda: requests.get(url, **kw))


# ------------------------------------------------------------------ helpers

def _utc_now(now=None) -> pd.Timestamp:
    return to_utc(datetime.now(timezone.utc) if now is None else now)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV)


def _session_index(idx: pd.DatetimeIndex, spec: _Spec, tz_aware_utc: bool) -> pd.DatetimeIndex:
    """Daily timestamps -> naive session dates in the instrument's own trading-day convention."""
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        if tz_aware_utc and spec.asset_class in ("equity", "etf", "index"):
            idx = idx.tz_convert("America/New_York")  # Alpaca stock daily bars stamp midnight ET in UTC
        else:
            idx = idx.tz_convert("UTC")
        idx = idx.tz_localize(None)
    return idx.normalize()


# ------------------------------------------------------------------ adapters

def fetch_alpaca(spec: _Spec, start: pd.Timestamp, end: pd.Timestamp, interval: str, now: pd.Timestamp) -> pd.DataFrame:
    """Alpaca Data API v2: adjustment=all, explicit feed, end clamped to now-16min."""
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        return _empty()
    if spec.alpaca_symbol is None or spec.asset_class not in ("equity", "etf", "crypto"):
        logger.debug("Alpaca does not serve %s (%s)", spec.symbol, spec.asset_class)
        return _empty()

    is_crypto = spec.asset_class == "crypto"
    end = min(end, (now - ALPACA_DELAY).tz_localize(None))
    if end <= start:
        return _empty()
    params = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timeframe": _TIMEFRAMES.get(interval, "1Day"),
        "limit": 10000,
        "adjustment": "all",
    }
    if is_crypto:
        url = f"{config.ALPACA_DATA_URL}/v1beta3/crypto/us/bars"
        params["symbols"] = spec.alpaca_symbol
    else:
        url = f"{config.ALPACA_DATA_URL}/v2/stocks/{spec.alpaca_symbol}/bars"
        params["feed"] = config.ALPACA_FEED
    headers = {"APCA-API-KEY-ID": config.ALPACA_API_KEY, "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY}

    rows: list = []
    try:
        while True:
            resp = http_get(url, headers=headers, params=params, timeout=15)
            if getattr(resp, "status_code", 200) >= 400:
                logger.warning("Alpaca rejected %s: HTTP %s %s", spec.symbol, resp.status_code,
                               str(getattr(resp, "text", ""))[:200])
                return _empty()
            data = resp.json()
            bars = data.get("bars", {}).get(spec.alpaca_symbol, []) if is_crypto else data.get("bars")
            rows.extend(bars or [])
            token = data.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
    except Exception as e:  # network/JSON errors: report, let the next source try
        logger.warning("Alpaca failed for %s: %s", spec.symbol, type(e).__name__)
        return _empty()

    if not rows:
        return _empty()
    df = pd.DataFrame(rows)
    df = df.rename(columns={"t": "ts", "o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"], utc=True))
    df = df[OHLCV].astype(float)
    df.index = _session_index(idx, spec, True) if interval in ("1d", "1Day") else idx.tz_localize(None)
    return df.sort_index()


def fetch_yfinance(spec: _Spec, start: pd.Timestamp, end: pd.Timestamp, interval: str, now: pd.Timestamp) -> pd.DataFrame:
    """yfinance with auto_adjust=True stated explicitly (split+dividend adjusted O/H/L/C)."""
    try:
        hist = _retrying()(lambda: yf.Ticker(spec.yf_symbol).history(
            start=start.strftime("%Y-%m-%d"),
            end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
            interval=interval,
            auto_adjust=True,
            actions=False,
        ))
    except Exception as e:
        logger.warning("yfinance failed for %s: %s", spec.symbol, type(e).__name__)
        return _empty()
    if hist is None or hist.empty:
        return _empty()
    df = hist[OHLCV].copy()
    idx = pd.DatetimeIndex(df.index)
    if interval in ("1d", "1Day"):
        # yfinance stamps local exchange midnight: keep the wall-clock date
        df.index = pd.DatetimeIndex(idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    else:
        df.index = idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx
    return df.sort_index()


_ADAPTERS = {"alpaca": fetch_alpaca, "yfinance": fetch_yfinance}


# ------------------------------------------------------------------ pipeline

def _finalize(df: pd.DataFrame, spec: _Spec, cal: SessionCalendar, start, end, now, interval: str) -> tuple:
    """Clip to window, keep only closed session dates, validate. Returns (df, QualityReport)."""
    df = df.dropna(how="all").sort_index()
    daily = interval in ("1d", "1Day")
    dropped_np = dropped_ip = 0
    if daily:
        df = df[(df.index >= start.normalize()) & (df.index <= end.normalize())]
        sessions = cal.sessions(start, end)
        keep = df.index.isin(sessions)
        dropped_np = int((~keep).sum())
        if dropped_np:
            logger.warning("%s: dropped %d bars on non-session dates (%s calendar)", spec.symbol, dropped_np, cal.name)
        df = df[keep]
        closed = [cal.session_close(s) <= now for s in df.index]
        dropped_ip = int(len(closed) - sum(closed))
        if dropped_ip:
            logger.info("%s: dropped %d in-progress session bar(s)", spec.symbol, dropped_ip)
        df = df[closed]
    rep = validate_ohlcv(df, cal, symbol=spec.symbol, start=start if daily else None, end=end if daily else None,
                         now=now, daily=daily, dropped_in_progress=dropped_ip, dropped_non_session=dropped_np)
    return df, rep


def fetch_bars(
    symbol: str,
    period_days: int = config.LOOKBACK_DAYS,
    interval: str = "1d",
    end_date: Optional[datetime] = None,
    sources: Optional[list] = None,
    now: Optional[datetime] = None,
    pin: bool = True,
) -> Bars:
    """Fetch validated bars. Raises DataQualityError (bad data) or DataUnavailableError (no data).

    A symbol is pinned to the first source that returns valid data and keeps using it for the rest
    of the process. An explicit ``sources`` list overrides (and re-pins) - used by fetch_pair.
    ``pin=False`` leaves the pin untouched (the bar store pins explicitly with the caller's window).
    """
    now_utc = _utc_now(now)
    end = min(to_utc(end_date) if end_date is not None else now_utc, now_utc)
    start = end - pd.Timedelta(days=period_days)
    start_n, end_n = start.tz_localize(None), end.tz_localize(None)
    spec = _spec(symbol)
    cal = get_calendar(spec.calendar)
    pinned = _get_pin(symbol, period_days)

    if sources:
        order = [s for s in sources if s in SUPPORTED_SOURCES]
        for s in sources:
            if s not in SUPPORTED_SOURCES:
                logger.warning("data source %r is not supported (ignored)", s)
    elif pinned:
        order = [pinned]
    else:
        order = [s for s in config.DATA_SOURCE_PRIORITY if s in SUPPORTED_SOURCES]
        if len(order) < len(config.DATA_SOURCE_PRIORITY):
            logger.debug("unsupported sources removed from DATA_SOURCE_PRIORITY")

    last_err: Optional[Exception] = None
    for src in order:
        raw = _ADAPTERS[src](spec, start_n, end_n, interval, now_utc)
        if raw is None or raw.empty:
            continue
        try:
            df, rep = _finalize(raw, spec, cal, start_n, end_n, now_utc, interval)
        except DataQualityError as e:
            logger.warning("%s from %s failed validation: %s", symbol, src, e)
            last_err = e
            continue
        if pin:
            _set_pin(symbol, period_days, src)
        df.attrs["source"] = src
        df.attrs["stale"] = False
        lc = cal.last_closed_session(now_utc)
        df.attrs["last_closed"] = None if lc is None else pd.Timestamp(lc).isoformat()   # JSON-safe (parquet attrs)
        meta = BarsMeta(source=src, adjusted=True, calendar=spec.calendar,
                        fetched_at=datetime.now(timezone.utc), quality=rep)
        logger.info("  %s: %d bars for %s", src, len(df), symbol)
        return Bars(df, meta)

    if last_err is not None:
        raise last_err
    raise DataUnavailableError(f"no data for {symbol} from sources {order}")


def _align(a: pd.DataFrame, b: pd.DataFrame, cal_a: str, cal_b: str, sym_a: str, sym_b: str) -> tuple:
    idx = a.index.intersection(b.index)
    if cal_a != cal_b:
        # mixed calendars: the intersection is the stricter calendar's sessions
        logger.warning("pair %s/%s mixes calendars %s and %s; aligned on the stricter (%d common bars)",
                       sym_a, sym_b, cal_a, cal_b, len(idx))
    return a.loc[idx], b.loc[idx]


def fetch_pair_bars(sym_a: str, sym_b: str, period_days: int = config.PAIRS_LOOKBACK,
                    end_date: Optional[datetime] = None, now: Optional[datetime] = None) -> tuple:
    """Fetch both legs from the SAME source and align them per calendar. Returns (Bars_a, Bars_b)."""
    pa, pb = _get_pin(sym_a), _get_pin(sym_b)
    if pa and pb and pa == pb:
        order = [pa]
    else:
        if pa or pb:
            logger.warning("pair %s/%s: legs pinned to different sources (%s, %s); re-pinning both", sym_a, sym_b, pa, pb)
            reset_pins(sym_a), reset_pins(sym_b)
        order = [s for s in config.DATA_SOURCE_PRIORITY if s in SUPPORTED_SOURCES]
    last_err: Optional[Exception] = None
    for src in order:
        try:
            ba = fetch_bars(sym_a, period_days, "1d", end_date, [src], now)
            bb = fetch_bars(sym_b, period_days, "1d", end_date, [src], now)
        except (DataQualityError, DataUnavailableError) as e:
            last_err = e
            reset_pins(sym_a), reset_pins(sym_b)
            continue
        da, db = _align(ba.df, bb.df, ba.calendar, bb.calendar, sym_a, sym_b)
        return Bars(da, ba.meta), Bars(db, bb.meta)
    raise last_err or DataUnavailableError(f"no common source for {sym_a}/{sym_b}")
