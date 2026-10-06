"""External bar files (forward paper test): raw IBKR ``get_price_history`` JSON on disk, no network.

Layout (``config.EXTERNAL_BARS_DIR``, default ``state/external_bars/``)::

    <SYMBOL>.json              the first / full history
    <SYMBOL>.<tag>.json        top-ups (e.g. AAPL.20261007.json); merged by timestamp, later files win

Each file is the columnar JSON IBKR returns: ``{"time": [ISO...], "open": [], "high": [], "low": [],
"close": [], "volume": []}`` (extra keys such as chart_step / expires are ignored). IBKR stamps a daily bar
with the session OPEN (13:30Z in summer, 14:30Z in winter for US stocks); this module converts that stamp to
the exchange session date (America/New_York for equities/ETFs/indices, UTC date otherwise) so the rest of the
stack sees ordinary naive, midnight-normalised session dates.

Pure file parsing: pandas + json only (the simulated broker imports this module, so it must stay offline).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)
OHLCV = ["Open", "High", "Low", "Close", "Volume"]
_COLS = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
_NY_CLASSES = ("equity", "etf", "index")


class ExternalBarsError(ValueError):
    """The JSON does not look like IBKR columnar bars."""


def bars_dir() -> Path:
    return Path(config.EXTERNAL_BARS_DIR)


def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-=^]", "_", symbol)


def symbol_files(symbol: str, root: Optional[Path] = None) -> list[Path]:
    """``<SYMBOL>.json`` first, then ``<SYMBOL>.<tag>.json`` sorted by name (later tags override earlier)."""
    d = Path(root) if root is not None else bars_dir()
    s = _safe(symbol)
    base = d / f"{s}.json"
    tops = sorted(p for p in d.glob(f"{s}.*.json") if re.fullmatch(re.escape(s) + r"\.[^.]+\.json", p.name))
    return ([base] if base.exists() else []) + tops


def parse_payload(payload: dict, asset_class: str = "equity") -> pd.DataFrame:
    """Raw IBKR columnar dict -> OHLCV frame indexed by naive session date. Raises ExternalBarsError."""
    if not isinstance(payload, dict):
        raise ExternalBarsError("payload is not a JSON object")
    missing = [k for k in ("time", *_COLS) if k not in payload]
    if missing:
        raise ExternalBarsError(f"missing keys {missing}")
    n = len(payload["time"])
    for k in _COLS:
        if len(payload[k]) != n:
            raise ExternalBarsError(f"column {k!r} has {len(payload[k])} values, time has {n}")
    if n == 0:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([]))
    try:
        ts = pd.DatetimeIndex(pd.to_datetime(payload["time"], utc=True))
    except Exception as e:
        raise ExternalBarsError(f"unparseable time values ({type(e).__name__})") from e
    df = pd.DataFrame({v: pd.to_numeric(pd.Series(payload[k]), errors="coerce").astype(float) for k, v in _COLS.items()})
    ts = ts.tz_convert("America/New_York") if asset_class in _NY_CLASSES else ts.tz_convert("UTC")
    df.index = pd.DatetimeIndex(ts.tz_localize(None)).normalize()
    return df.sort_index()


REPAIR_MAX_REL = 0.02     # an OHLC inconsistency larger than 2% of price is corrupt data, not rounding/last-trade noise


def repair_ohlc(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """IBKR daily bars occasionally carry a Close/Open a few bp outside the High-Low range (last-trade vs
    consolidated print). Widen High/Low to contain Open and Close (never move O/C). Returns (frame, n_repaired)."""
    if df.empty:
        return df, 0
    hi = df[["Open", "High", "Low", "Close"]].max(axis=1)
    lo = df[["Open", "High", "Low", "Close"]].min(axis=1)
    bad = (hi > df["High"]) | (lo < df["Low"])
    out = df.copy()
    out["High"], out["Low"] = hi, lo
    return out, int(bad.sum())


def check_frame(df: pd.DataFrame) -> list[str]:
    """Human-readable problems that must stop an ingest (empty list = fine)."""
    if df.empty:
        return ["no bars"]
    out = []
    if df[OHLCV].isna().any().any():
        out.append("NaN / non-numeric values")
    if (df[["Open", "High", "Low", "Close"]] <= 0).any().any():
        out.append("non-positive prices")
    if (df["High"] < df[["Open", "Close", "Low"]].max(axis=1) * (1 - REPAIR_MAX_REL)).any():
        out.append(f"High more than {REPAIR_MAX_REL:.0%} below Open/Close/Low")
    if (df["Low"] > df[["Open", "Close", "High"]].min(axis=1) * (1 + REPAIR_MAX_REL)).any():
        out.append(f"Low more than {REPAIR_MAX_REL:.0%} above Open/Close/High")
    if (df["Volume"] < 0).any():
        out.append("negative volume")
    return out


def load_bars(symbol: str, asset_class: str = "equity", root: Optional[Path] = None) -> pd.DataFrame:
    """All files of `symbol` merged by session date (later file wins a shared date). Empty frame if none."""
    frames = []
    for p in symbol_files(symbol, root):
        frames.append(parse_payload(json.loads(p.read_text()), asset_class))
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([]))
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    problems = check_frame(df)          # a file placed without the ingest script must not skip validation
    if problems:
        raise ExternalBarsError(f"{symbol}: " + "; ".join(problems))
    df, n = repair_ohlc(df)
    if n:
        logger.info("%s: widened High/Low on %d bar(s) whose Open/Close lay outside the range (<= %.0f%%)",
                    symbol, n, REPAIR_MAX_REL * 100)
    return df[OHLCV]


def closed_bars(symbol: str, now=None, calendar=None, asset_class: str = "equity",
                root: Optional[Path] = None) -> pd.DataFrame:
    """`load_bars` restricted to FINISHED sessions: only dates that are sessions of `calendar` (default XNYS) whose
    close is <= `now` (default: the current time). Drops an in-progress bar fetched intraday and bars on
    non-session dates (weekends, holidays); this is the same rule the research pipeline applies, so the forward
    runner and the simulated broker never act on a half-finished day."""
    df = load_bars(symbol, asset_class, root)
    if df.empty:
        return df
    if calendar is None:
        from data.calendars import get_calendar
        calendar = get_calendar("XNYS")
    now = datetime.now(timezone.utc) if now is None else now
    last_closed = calendar.last_closed_session(now)
    if last_closed is None:
        return df.iloc[0:0]
    df = df[df.index <= last_closed]
    if df.empty:
        return df
    sessions = calendar.sessions(df.index[0], df.index[-1])
    return df[df.index.isin(sessions)]
