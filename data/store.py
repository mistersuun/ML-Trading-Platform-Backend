"""Persistent bar store (WS3.3): one parquet file per (asset class, symbol, interval, source, adjustment).

    <root>/{asset_class}/{symbol}__{interval}__{source}__{adj}.parquet      (root = data/bars/, gitignored)

* A read in the same session as the last fetch makes no network call (fresh until the next session close).
* The next session fetches only from ``last cached session - OVERLAP_SESSIONS`` and splices it on. If the
  overlap disagrees with the cache beyond tolerance (a new split/dividend adjustment), the full history
  is refetched and the event is logged.
* Files are written temp-then-``os.replace``: a crash mid-write leaves the old file intact.
* The pinned source is never switched silently: when it fails, cached data is served with ``meta.stale=True``.
* ``refresh=True`` forces a fetch. Requests with an explicit ``end_date`` or a non-daily interval bypass the store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import config
from data import adapters
from data.adapters import SUPPORTED_SOURCES, Bars, BarsMeta, _finalize, _spec
from data.calendars import get_calendar, to_utc
from data.validate import OHLCV, DataQualityError, DataUnavailableError

logger = logging.getLogger(__name__)

OVERLAP_SESSIONS = 5
OVERLAP_RTOL = 1e-4                       # relative OHLC disagreement that means "history was re-adjusted"
RETRY_BEHIND_SECONDS = 1800               # provider has not published the newest session yet: retry at most every 30 min
_META_KEY = b"bar_store"
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


# ------------------------------------------------------------------ paths

def bar_dir() -> Path:
    """BAR_STORE_DIR if set, else ``data/bars`` beside the state directory (so an isolated state path isolates bars)."""
    env = os.environ.get("BAR_STORE_DIR")
    if env:
        return Path(env)
    return Path(config.STATE_DB_PATH).resolve().parent.parent / "data" / "bars"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]", "_", s)


def bar_path(symbol: str, interval: str, source: str, adjusted: bool = True) -> Path:
    spec = _spec(symbol)
    return bar_dir() / _safe(spec.asset_class) / f"{_safe(symbol)}__{_safe(interval)}__{_safe(source)}__{'adj' if adjusted else 'raw'}.parquet"


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


# ------------------------------------------------------------------ file io

def write_atomic(path: Path, df: pd.DataFrame, meta: dict) -> None:
    """Write temp file in the same directory, fsync, then os.replace. The target is untouched on any failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        table = pa.Table.from_pandas(df, preserve_index=True)
        table = table.replace_schema_metadata({**(table.schema.metadata or {}), _META_KEY: json.dumps(meta).encode()})
        with open(tmp, "wb") as fh:
            pq.write_table(table, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def read_file(path: Path) -> Optional[tuple]:
    """(frame, meta dict) or None when the file is missing/corrupt (a corrupt file is treated as a cache miss)."""
    try:
        table = pq.read_table(path)
        meta = json.loads((table.schema.metadata or {}).get(_META_KEY, b"{}"))
        df = table.to_pandas()
        df.index = pd.DatetimeIndex(df.index)
        return df[OHLCV].astype(float), meta
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning("bar store file %s unreadable (%s); refetching", path.name, type(e).__name__)
        return None


def read_meta(path: Path) -> Optional[dict]:
    try:
        md = pq.read_schema(path).metadata or {}
        return json.loads(md.get(_META_KEY, b"{}"))
    except Exception:
        return None


# ------------------------------------------------------------------ helpers

def _iso(ts) -> Optional[str]:
    return None if ts is None else pd.Timestamp(ts).isoformat()


def _ts(s) -> Optional[pd.Timestamp]:
    return pd.Timestamp(s) if s else None


def _candidate_sources(symbol: str, period_days: int, sources: Optional[list]) -> list:
    if sources:
        return [s for s in sources if s in SUPPORTED_SOURCES]
    pinned = adapters.pinned_source(symbol, period_days)
    if pinned:
        return [pinned]
    return [s for s in config.DATA_SOURCE_PRIORITY if s in SUPPORTED_SOURCES]


def _build_meta(df, source, spec, requested_days, checked_through, now, interval, attempt=None) -> dict:
    return {"source": source, "adjusted": True, "calendar": spec.calendar, "interval": interval,
            "coverage_days": int(requested_days), "checked_through": _iso(checked_through),
            "feed": config.ALPACA_FEED if source == "alpaca" else None,
            "fetched_at": _iso(now), "last_attempt": _iso(attempt or now),
            "first_session": _iso(df.index[0]) if len(df) else None,
            "last_session": _iso(df.index[-1]) if len(df) else None}


def _bars_from(df, meta, spec, cal, now, interval, period_days, stale=False) -> Bars:
    """Window the cached frame and re-validate it (cheap, offline) so quality always reflects `now`."""
    end = now
    start = end - pd.Timedelta(days=period_days)
    window = df[df.index >= start.tz_localize(None).normalize()]
    window, rep = _finalize(window, spec, cal, start.tz_localize(None), end.tz_localize(None), now, interval)
    window.attrs["source"] = meta["source"]
    window.attrs["stale"] = bool(stale)
    lc = cal.last_closed_session(now)
    window.attrs["last_closed"] = None if lc is None else pd.Timestamp(lc).isoformat()
    fetched = _ts(meta.get("fetched_at")) or now
    bm = BarsMeta(source=meta["source"], adjusted=bool(meta.get("adjusted", True)), calendar=spec.calendar,
                  fetched_at=fetched.to_pydatetime(), quality=rep, stale=stale, from_cache=True)
    return Bars(window, bm)


def _overlap_differs(old: pd.DataFrame, new: pd.DataFrame) -> bool:
    idx = old.index.intersection(new.index)
    if not len(idx):
        return True  # no shared dates: re-adjustment cannot be ruled out, so refetch in full (fail closed)
    a, b = old.loc[idx, ["Open", "High", "Low", "Close"]], new.loc[idx, ["Open", "High", "Low", "Close"]]
    rel = ((a - b).abs() / a.abs().clip(lower=1e-12)).to_numpy()
    return bool((rel > OVERLAP_RTOL).any())


# ------------------------------------------------------------------ public API

def get_bars(symbol: str, period_days: int = config.LOOKBACK_DAYS, interval: str = "1d",
             end_date: Optional[datetime] = None, sources: Optional[list] = None,
             now: Optional[datetime] = None, refresh: bool = False) -> Bars:
    """Validated bars through the store. Same errors as ``adapters.fetch_bars`` (DataQualityError /
    DataUnavailableError) when there is no usable cache to fall back on."""
    now_utc = adapters._utc_now(now)
    if end_date is not None or interval not in ("1d", "1Day"):
        return adapters.fetch_bars(symbol, period_days, interval, end_date, sources, now_utc)

    spec = _spec(symbol)
    cal = get_calendar(spec.calendar)
    order = _candidate_sources(symbol, period_days, sources)
    cached_src = next((s for s in order if bar_path(symbol, interval, s).exists()), None)
    path = bar_path(symbol, interval, cached_src) if cached_src else None
    with _lock(path or bar_dir() / _safe(symbol)):
        cached = read_file(path) if path else None
        if cached is None:
            return _full_fetch(symbol, period_days, interval, sources, now_utc, spec, cal)
        df, meta = cached
        src = meta.get("source", cached_src)
        if src == "alpaca" and meta.get("feed") != config.ALPACA_FEED:
            logger.warning("%s: cached alpaca bars are from feed %r, now %r; refetching in full",
                           symbol, meta.get("feed"), config.ALPACA_FEED)
            return _full_fetch(symbol, period_days, interval, [src], now_utc, spec, cal)
        last_closed = cal.last_closed_session(now_utc)
        covers = int(meta.get("coverage_days", 0)) >= period_days
        through = _ts(meta.get("checked_through"))
        fresh = (last_closed is None or (through is not None and through >= last_closed))
        recently_tried = (_ts(meta.get("last_attempt")) is not None
                          and (now_utc.tz_localize(None) - to_utc(_ts(meta["last_attempt"])).tz_localize(None)).total_seconds()
                          < RETRY_BEHIND_SECONDS and covers)
        if covers and not refresh and (fresh or recently_tried):
            if adapters.pinned_source(symbol, period_days) != src:
                adapters._set_pin(symbol, period_days, src)
            return _bars_from(df, meta, spec, cal, now_utc, interval, period_days)
        try:
            if not covers:
                logger.info("%s: cached history (%sd) shorter than requested (%dd); refetching in full",
                            symbol, meta.get("coverage_days"), period_days)
                return _full_fetch(symbol, period_days, interval, [src], now_utc, spec, cal)
            return _incremental(symbol, df, meta, src, period_days, interval, now_utc, spec, cal, path)
        except Exception as e:  # any fetch failure on a cached series -> serve it, flagged stale
            logger.warning("%s: refresh from pinned source %s failed (%s: %s); serving cached bars flagged stale",
                           symbol, src, type(e).__name__, e)
            return _bars_from(df, meta, spec, cal, now_utc, interval, min(period_days, int(meta.get("coverage_days", period_days))),
                              stale=True)


def _full_fetch(symbol, period_days, interval, sources, now_utc, spec, cal, pin_days=None) -> Bars:
    bars = adapters.fetch_bars(symbol, period_days, interval, None, sources, now_utc, pin=False)
    src = bars.source
    adapters._set_pin(symbol, pin_days or period_days, src)
    last_closed = cal.last_closed_session(now_utc)
    through = last_closed if (len(bars.df) and last_closed is not None and bars.df.index[-1] >= last_closed) else \
        (bars.df.index[-1] if len(bars.df) else last_closed)
    meta = _build_meta(bars.df, src, spec, period_days, through, now_utc, interval)
    try:
        write_atomic(bar_path(symbol, interval, src), bars.df, meta)
    except Exception as e:
        logger.warning("%s: could not write bar store file (%s)", symbol, type(e).__name__)
    return bars


def _incremental(symbol, df, meta, src, period_days, interval, now_utc, spec, cal, path) -> Bars:
    last = df.index[-1]
    prior = cal.sessions(last - pd.Timedelta(days=30), last)
    start = prior[-(OVERLAP_SESSIONS + 1)] if len(prior) > OVERLAP_SESSIONS else prior[0] if len(prior) else last
    days = max((now_utc.tz_localize(None).normalize() - start).days, 1)
    new = adapters.fetch_bars(symbol, days, interval, None, [src], now_utc, pin=False)
    if _overlap_differs(df, new.df):
        logger.warning("%s: overlap with cache differs beyond %.0e (re-adjusted history, e.g. split/dividend); "
                       "refetching full history", symbol, OVERLAP_RTOL)
        return _full_fetch(symbol, int(meta.get("coverage_days", period_days)), interval, [src], now_utc, spec, cal,
                           pin_days=period_days)
    merged = pd.concat([df[df.index < new.df.index[0]], new.df]) if len(new.df) else df
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    last_closed = cal.last_closed_session(now_utc)
    caught_up = last_closed is None or merged.index[-1] >= last_closed
    through = last_closed if caught_up else _ts(meta.get("checked_through"))
    nmeta = _build_meta(merged, src, spec, int(meta.get("coverage_days", period_days)), through,
                        now_utc if caught_up else _ts(meta.get("fetched_at")) or now_utc, interval, attempt=now_utc)
    write_atomic(path, merged, nmeta)
    adapters._set_pin(symbol, period_days, src)
    return _bars_from(merged, nmeta, spec, cal, now_utc, interval, period_days)


def get_pair_bars(sym_a: str, sym_b: str, period_days: int = config.PAIRS_LOOKBACK,
                  now: Optional[datetime] = None, refresh: bool = False) -> tuple:
    """Both legs from the SAME source (through the store), aligned on common sessions."""
    now_utc = adapters._utc_now(now)
    pa_, pb_ = adapters.pinned_source(sym_a), adapters.pinned_source(sym_b)
    if pa_ and pb_ and pa_ == pb_:
        order = [pa_]
    else:
        if pa_ or pb_:
            logger.warning("pair %s/%s: legs pinned to different sources (%s, %s); re-pinning both", sym_a, sym_b, pa_, pb_)
            adapters.reset_pins(sym_a), adapters.reset_pins(sym_b)
        order = [s for s in config.DATA_SOURCE_PRIORITY if s in SUPPORTED_SOURCES]
    last_err: Optional[Exception] = None
    for src in order:
        try:
            ba = get_bars(sym_a, period_days, "1d", None, [src], now_utc, refresh)
            bb = get_bars(sym_b, period_days, "1d", None, [src], now_utc, refresh)
        except (DataQualityError, DataUnavailableError) as e:
            last_err = e
            adapters.reset_pins(sym_a), adapters.reset_pins(sym_b)
            continue
        da, db = adapters._align(ba.df, bb.df, ba.calendar, bb.calendar, sym_a, sym_b)
        return Bars(da, ba.meta), Bars(db, bb.meta)
    raise last_err or DataUnavailableError(f"no common source for {sym_a}/{sym_b}")


def cache_info(symbol: str, interval: str = "1d", now: Optional[datetime] = None) -> list:
    """Metadata of every stored file for `symbol` (no network): source, last session, coverage, cache age."""
    now_utc = adapters._utc_now(now)
    spec = _spec(symbol)
    out = []
    for src in SUPPORTED_SOURCES:
        p = bar_path(symbol, interval, src)
        m = read_meta(p) if p.exists() else None
        if m is None:
            continue
        f = _ts(m.get("fetched_at"))
        out.append({**m, "path": str(p),
                    "cache_age_seconds": (now_utc - to_utc(f)).total_seconds() if f is not None else None,
                    "asset_class": spec.asset_class})
    return out
