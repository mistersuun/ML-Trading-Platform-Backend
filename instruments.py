"""Instrument registry and capability matrix (WS1.2).

Single source of truth for what a symbol is and what the platform may do with it.
Per D3: only US equities/ETFs are executable; nothing is shortable; crypto, forex,
futures and indices are research/alert only.
"""

from dataclasses import dataclass
from typing import Optional

import config

_ETF_UNIVERSE = (
    "VTI", "ITOT", "VEA", "IEFA", "VWO", "IEMG", "IEF", "VGIT", "TLT", "VGLT",
    "TIP", "VTIP", "SCHP", "SGOV", "BIL", "SHY", "GLD", "GLDM", "IAUM", "DBC",
    "PDBC", "VNQ", "SPY", "QQQ",
)
# Known-ETF tickers that appear in the watchlist (everything else in "stocks" is an equity)
_WATCHLIST_ETFS = {"SPY", "QQQ", "IWM", "DIA"}
_EXTRA_EQUITIES = ("BRK-B",)
_INDICES = ("^GSPC", "^IXIC", "^DJI", "^VIX")

_BARS_PER_YEAR = {"equity": 252, "etf": 252, "futures": 252, "index": 252, "fx": 260, "crypto": 365}
_CALENDAR = {"equity": "XNYS", "etf": "XNYS", "index": "XNYS", "crypto": "24/7", "fx": "FX", "futures": "CMES"}


class UnknownSymbol(KeyError):
    """Raised for any symbol that is not in the registry."""


@dataclass(frozen=True)
class Instrument:
    id: str
    asset_class: str  # equity | etf | crypto | fx | futures | index
    calendar: str  # XNYS | 24/7 | FX | CMES
    bars_per_year: int
    alpaca_data_symbol: Optional[str]
    alpaca_trade_symbol: Optional[str]  # None unless executable
    yfinance_symbol: str
    executable: bool
    shortable: bool = False
    fractionable_default: bool = False
    cluster: str = ""
    research_only: bool = False
    notes: str = ""


def alpaca_equity_symbol(symbol: str) -> str:
    """yfinance share-class dash -> Alpaca dot ('BRK-B' -> 'BRK.B')."""
    return symbol.replace("-", ".")


def _cluster_of(symbol: str) -> str:
    for name, members in config.CLUSTERS.items():
        if symbol in members:
            return name
    return symbol


def _research_only_symbols() -> set:
    return {s for pair in config.RESEARCH_ONLY_PAIRS for s in pair}


def _build(symbol: str, asset_class: str) -> Instrument:
    executable = asset_class in ("equity", "etf")
    if executable:
        data = trade = alpaca_equity_symbol(symbol)
    elif asset_class == "crypto":
        data, trade = symbol.replace("-", "/"), None  # data only; never executable
    else:
        data, trade = None, None
    return Instrument(
        id=symbol,
        asset_class=asset_class,
        calendar=_CALENDAR[asset_class],
        bars_per_year=_BARS_PER_YEAR[asset_class],
        alpaca_data_symbol=data,
        alpaca_trade_symbol=trade,
        yfinance_symbol=symbol,
        executable=executable,
        cluster=_cluster_of(symbol),
        research_only=symbol in _research_only_symbols(),
        notes="" if executable else "research/alert only (D3)",
    )


def _classify(symbol: str, group: Optional[str] = None) -> str:
    if symbol.startswith("^"):
        return "index"
    if symbol.endswith("=X"):
        return "fx"
    if symbol.endswith("=F"):
        return "futures"
    if group == "crypto" or symbol.endswith("-USD"):
        return "crypto"
    if symbol in _WATCHLIST_ETFS or symbol in _ETF_UNIVERSE:
        return "etf"
    return "equity"


def _registry() -> dict:
    reg = {}
    for group, symbols in config.WATCHLIST.items():
        for s in symbols:
            reg[s] = _build(s, _classify(s, group))
    for a, b in config.PAIRS:
        for s in (a, b):
            reg.setdefault(s, _build(s, _classify(s)))
    for s in _ETF_UNIVERSE:
        reg.setdefault(s, _build(s, "etf"))
    for s in _EXTRA_EQUITIES:
        reg.setdefault(s, _build(s, "equity"))
    for s in _INDICES:
        reg.setdefault(s, _build(s, "index"))
    return reg


def get(symbol: str) -> Instrument:
    inst = _registry().get(symbol)
    if inst is None:
        raise UnknownSymbol(symbol)
    return inst


def all_instruments() -> list:
    return list(_registry().values())


def default_pairs() -> list:
    """config.PAIRS minus research-only pairs (D3)."""
    excluded = {tuple(p) for p in config.RESEARCH_ONLY_PAIRS}
    return [tuple(p) for p in config.PAIRS if tuple(p) not in excluded]
