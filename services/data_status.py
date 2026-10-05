"""Data status: per watchlist + allocation symbol, what the bar store / data layer reports (WS3.3).

Source, adjusted flag, calendar, last session, coverage, quality flags, cache age and whether the series was served
stale (pinned source failing, cached bars returned). Reads go through the provider, so a fresh cache costs no network."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import allocation
import config
from services.models import SymbolStatus
from services.providers import DataProvider, default_provider

logger = logging.getLogger(__name__)


class StoreSymbolStatus(SymbolStatus):
    """SymbolStatus plus the bar store's stale flag."""
    stale: bool = False


def _iso(ts) -> Optional[str]:
    try:
        return ts.isoformat() if ts is not None else None
    except Exception:
        return str(ts)


def _age(fetched_at) -> Optional[float]:
    try:
        t = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - t).total_seconds())
    except Exception:
        return None


def configured_symbols() -> list:
    """Every watchlist symbol plus every allocation symbol (core, trend universe, cash), de-duplicated in order."""
    syms = [s for group in config.WATCHLIST.values() for s in group]
    syms += list(allocation.CORE_TARGETS) + list(allocation.TREND_UNIVERSE) + [allocation.CASH_SYMBOL]
    return list(dict.fromkeys(syms))


def symbol_status(provider: DataProvider, symbol: str) -> StoreSymbolStatus:
    try:
        bars = provider.bars(symbol)
    except Exception as e:
        name = type(e).__name__
        status = "no_data" if name == "DataUnavailableError" else "quality_failed" if name == "DataQualityError" else "error"
        return StoreSymbolStatus(symbol=symbol, status=status, error=f"{name}: {e}")
    q = bars.quality
    df = bars.df
    meta = getattr(bars, "meta", None)
    return StoreSymbolStatus(
        symbol=symbol, source=bars.source, adjusted=bool(bars.adjusted), calendar=bars.calendar,
        last_session=_iso(df.index[-1]) if len(df) else None, n_bars=int(len(df)),
        coverage=q.coverage, stale_sessions=q.stale_sessions, fetched_at=_iso(bars.fetched_at),
        quality_flags=[str(f) for f in q.flags],
        cache_age_seconds=_age(bars.fetched_at) if getattr(meta, "from_cache", False) else None,
        stale=bool(getattr(meta, "stale", False)),
    )


def data_status(provider: Optional[DataProvider] = None, symbols: Optional[list] = None) -> list[SymbolStatus]:
    """One SymbolStatus per symbol (default: every configured watchlist symbol)."""
    provider = provider or default_provider()
    if symbols is None:
        symbols = configured_symbols()
    return [symbol_status(provider, s) for s in symbols]
