import dataclasses

import pytest

import config
import instruments
from instruments import UnknownSymbol

WATCH = [s for syms in config.WATCHLIST.values() for s in syms]
PAIR_SYMS = sorted({s for p in config.PAIRS for s in p})
ALL = sorted(set(WATCH) | set(PAIR_SYMS))


@pytest.mark.parametrize("sym", ALL)
def test_every_symbol_resolves(sym):
    i = instruments.get(sym)
    assert i.id == sym
    assert i.asset_class in {"equity", "etf", "crypto", "fx", "futures", "index"}
    assert i.shortable is False
    assert i.fractionable_default is False
    assert i.cluster
    assert (i.alpaca_trade_symbol is not None) == i.executable
    if i.executable:
        assert i.asset_class in ("equity", "etf")


@pytest.mark.parametrize("sym", ["GC=F", "EURUSD=X", "^GSPC", "BTC-USD", "ES=F"])
def test_not_executable(sym):
    i = instruments.get(sym)
    assert not i.executable
    assert i.alpaca_trade_symbol is None


def test_mappings():
    assert instruments.get("BTC-USD").alpaca_data_symbol == "BTC/USD"
    assert instruments.get("BRK-B").alpaca_trade_symbol == "BRK.B"
    assert instruments.alpaca_equity_symbol("BRK-B") == "BRK.B"
    assert instruments.alpaca_equity_symbol("AAPL") == "AAPL"


def test_bars_per_year_and_calendar():
    assert instruments.get("BTC-USD").bars_per_year == 365
    assert instruments.get("AAPL").bars_per_year == 252
    assert instruments.get("SPY").asset_class == "etf"
    assert instruments.get("EURUSD=X").bars_per_year == 260
    assert instruments.get("GC=F").calendar == "CMES"
    assert instruments.get("BTC-USD").calendar == "24/7"


def test_cluster():
    assert instruments.get("AAPL").cluster == "mega_tech"
    assert instruments.get("BTC-USD").cluster == "BTC-USD"


def test_etf_universe():
    for s in ("VTI", "SGOV", "GLD", "DBC", "QQQ"):
        i = instruments.get(s)
        assert i.asset_class == "etf" and i.executable


def test_unknown():
    with pytest.raises(UnknownSymbol):
        instruments.get("XYZ123")


def test_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        instruments.get("AAPL").executable = False


def test_default_pairs_and_research_only():
    dp = instruments.default_pairs()
    assert ("GC=F", "SI=F") not in dp and ("CL=F", "NG=F") not in dp
    assert ("AAPL", "MSFT") in dp
    assert len(dp) == len(config.PAIRS) - len(config.RESEARCH_ONLY_PAIRS)
    for s in ("GC=F", "SI=F", "CL=F", "NG=F"):
        assert instruments.get(s).research_only
    assert not instruments.get("AAPL").research_only


def test_all_instruments_covers_watchlist():
    ids = {i.id for i in instruments.all_instruments()}
    assert set(ALL) <= ids
