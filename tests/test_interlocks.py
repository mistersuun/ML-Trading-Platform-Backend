"""WS0.4 emergency order interlocks (temporary until the Phase 1 order chokepoint)."""
import math

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import config
import main
import paper_trader
from tests.fixtures.fake_broker import FakeBroker, install_fake_broker


def _submits(b):
    return [c[1][0] for c in b.calls_to("submit_order")]


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(conf=st.sampled_from([0, 0.5, 1, 1e10, float("inf"), float("nan")]),
       price=st.floats(1e-9, 1e5), equity=st.floats(1e3, 1e6))
def test_notional_bounded_and_nonfinite_rejected(monkeypatch, conf, price, equity):
    b = install_fake_broker(monkeypatch, FakeBroker(equity=equity, cash=equity))
    r = paper_trader.execute_signal("AAPL", 1, conf, price)
    orders = _submits(b)
    if not math.isfinite(conf) or price < 1.0:
        assert orders == [] and r is None
    for o in orders:
        assert float(o.qty) >= 1
        assert float(o.qty) * price <= min(1000, 0.05 * equity) + 1e-6


def test_trading_mode_off_means_zero_submits(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    df = pd.DataFrame({"Open": 10.0, "High": 10.1, "Low": 9.9, "Close": 10.0, "Volume": 1e6,
                       "signal": 0}, index=pd.bdate_range("2024-01-01", periods=30))
    df.iloc[-1, df.columns.get_loc("signal")] = 1
    from types import SimpleNamespace
    res = SimpleNamespace(is_valid=True, win_rate=0.6, profit_factor=2.0)
    res.summary = lambda: {"win_rate": 0.6, "profit_factor": 2.0}
    monkeypatch.setattr(main, "classic_backtest", lambda *a, **k: res)
    monkeypatch.setattr(main, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(main, "format_signal_alert", lambda *a, **k: "m")
    monkeypatch.setattr(main, "PATTERN_REGISTRY", {"p": lambda d: d.copy()})
    out = main.scan_technical({"AAPL": df}, paper_trade=True)
    assert out and b.calls_to("submit_order") == []


def test_trading_mode_default_is_off():
    import importlib, os
    old = os.environ.pop("TRADING_MODE", None)
    try:
        assert importlib.reload(config).TRADING_MODE == "off"
        assert not hasattr(config, "ALLOW_SHORTS")
    finally:
        if old is not None:
            os.environ["TRADING_MODE"] = old
        importlib.reload(config)


@given(held=st.integers(1, 500), conf=st.floats(0, 1))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_sell_qty_never_exceeds_held(monkeypatch, held, conf):
    b = install_fake_broker(monkeypatch, FakeBroker())
    b.add_position("AAPL", held, 10.0)
    paper_trader.execute_signal("AAPL", -1, conf, 10.0)
    for o in _submits(b):
        assert float(o.qty) <= held


def test_sell_without_position_rejected(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    assert paper_trader.execute_signal("AAPL", -1, 1.0, 10.0) is None
    assert _submits(b) == []


def test_duplicate_buy_skipped(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    assert paper_trader.execute_signal("AAPL", 1, 1.0, 10.0) is not None
    assert paper_trader.execute_signal("AAPL", 1, 1.0, 10.0) is None  # open buy order exists
    assert len(_submits(b)) == 1
    b2 = install_fake_broker(monkeypatch, FakeBroker())
    b2.add_position("MSFT", 5, 10.0)
    assert paper_trader.execute_signal("MSFT", 1, 1.0, 10.0) is None
    assert _submits(b2) == []


@pytest.mark.parametrize("method", ["get_account", "get_all_positions", "get_orders"])
def test_lookup_error_fails_closed(monkeypatch, method):
    b = install_fake_broker(monkeypatch, FakeBroker())
    b.raise_on(method)
    assert paper_trader.execute_signal("AAPL", 1, 1.0, 10.0) is None
    assert _submits(b) == []


@pytest.mark.parametrize("sym", ["GC=F", "EURUSD=X", "^GSPC", "BTC-USD"])
def test_non_equity_symbols_rejected(monkeypatch, sym):
    b = install_fake_broker(monkeypatch, FakeBroker())
    assert paper_trader.execute_signal(sym, 1, 1.0, 10.0) is None
    assert _submits(b) == []


def test_qty_zero_rejected(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    assert paper_trader.execute_signal("AAPL", 1, 1.0, 5000.0) is None
    assert _submits(b) == []


def test_repeated_sell_never_exceeds_held(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    b.add_position("AAPL", 10, 10.0)
    for _ in range(3):
        paper_trader.execute_signal("AAPL", -1, 1.0, 10.0)
    assert sum(float(o.qty) for o in _submits(b)) <= 10


def test_tiny_price_rejected_and_qty_ceiling(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    assert paper_trader.execute_signal("AAPL", 1, 1.0, 1e-9) is None
    monkeypatch.setattr(config, "PAPER_TRADE_MAX_ORDER_VALUE", 1e12)
    monkeypatch.setattr(config, "MAX_POSITION_SIZE_PCT", 1.0)
    paper_trader.execute_signal("AAPL", 1, 1.0, 1.0)
    assert all(float(o.qty) <= paper_trader.MAX_ORDER_QTY for o in _submits(b))


def test_mode_off_close_and_submit_never_touch_broker(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    assert paper_trader.close_position("AAPL") is None
    assert paper_trader.submit_order("AAPL", 1, "buy") is None
    assert b.calls_to("close_position") == [] and b.calls_to("submit_order") == []
