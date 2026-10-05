"""Bug-pinning tests for order execution / signal selection (WS0.3: ORD-*, SIG-1).

xfail(strict) tests assert the CORRECT behaviour and currently fail with AssertionError.
ORD-7 is a normal passing guard test.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import config
import main
import paper_trader
from brokers.alpaca import AlpacaBroker
from risk_manager import RiskManager
from tests.fixtures.fake_broker import init_state, pretend_validated

BUG = dict(strict=True, raises=AssertionError)


# --------------------------------------------------------------------------- helpers
def _frame(n=60, price=10.0, last_signal=1):
    idx = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({"Open": price, "High": price * 1.01, "Low": price * 0.99,
                       "Close": price, "Volume": 1_000_000}, index=idx)
    df["signal"] = 0
    df.iloc[-1, df.columns.get_loc("signal")] = last_signal
    return df


def _fake_result(win_rate=0.6, profit_factor=1e10, trades=20):
    r = SimpleNamespace(is_valid=True, win_rate=win_rate, profit_factor=profit_factor,
                        total_trades=trades, total_return_pct=0.1)
    r.summary = lambda: {"symbol": "X", "pattern": "stub", "win_rate": win_rate,
                         "profit_factor": profit_factor}
    return r


@pytest.fixture
def paper_env(monkeypatch, fake_broker, tmp_path):
    """FakeBroker + tmp state DB; 'unvalidated' is treated as eligible so the order path can be exercised."""
    init_state(monkeypatch, tmp_path)
    pretend_validated(monkeypatch)
    return fake_broker


@pytest.fixture
def scan_env(monkeypatch, paper_env):
    """Stub out alerts and the backtest so scan_technical only exercises order routing."""
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(paper_env))
    monkeypatch.setattr(main, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(main, "format_signal_alert", lambda *a, **k: "msg")
    monkeypatch.setattr(main, "classic_backtest", lambda df, sym, pat: _fake_result())

    def stub_pattern(df):
        return df.copy()

    monkeypatch.setattr(main, "PATTERN_REGISTRY", {"stub_a": stub_pattern, "stub_b": stub_pattern})
    monkeypatch.setattr(main.signals, "latest_atr", lambda df, *a, **k: 0.2)  # flat synthetic frame has ATR ~0.2
    return paper_env


def _submits(broker):
    return [c[1][0] for c in broker.calls_to("submit_order")]


# --------------------------------------------------------------------------- ORD-1
def test_ORD_1_qty_bounded_when_profit_factor_huge(paper_env):
    price = 10.0
    # an out-of-range "confidence" (the old win_rate * profit_factor) is rejected outright
    paper_trader.execute_signal("AAPL", 1, confidence=0.6 * 1e10, current_price=price, atr=0.2)
    assert _submits(paper_env) == []
    paper_trader.execute_signal("MSFT", 1, confidence=1.0, current_price=price, atr=0.2)
    orders = _submits(paper_env)
    assert len(orders) == 1
    notional = float(orders[0].qty) * price
    assert notional <= config.MAX_ORDER_NOTIONAL


# --------------------------------------------------------------------------- ORD-2
def test_ORD_2_one_order_per_symbol_bar(scan_env):
    data = {"AAPL": _frame(last_signal=1)}
    main.scan_technical(data, paper_trade=True)  # two patterns both fire
    main.scan_technical(data, paper_trade=True)  # repeated run, same bar
    keys = [(o.symbol, str(o.side)) for o in _submits(scan_env)]
    assert len(keys) == len(set(keys)) and len(keys) <= 1


def test_ORD_2b_no_rebuy_on_same_bar_after_position_closed(scan_env):
    data = {"AAPL": _frame(last_signal=1)}
    main.scan_technical(data, paper_trade=True)
    assert len(_submits(scan_env)) == 1
    # the order filled and the position was closed between runs: broker state is clean again
    scan_env.orders.clear()
    scan_env.positions.clear()
    main.scan_technical(data, paper_trade=True)  # same bar, second run
    assert len(_submits(scan_env)) <= 1, "same (symbol, bar) bought twice"


# --------------------------------------------------------------------------- ORD-3
def test_ORD_3_risk_manager_blocks_orders_when_halted(scan_env, monkeypatch):
    rm = RiskManager()
    rm.halt("test")
    assert rm.check().allowed is False
    main.scan_technical({"AAPL": _frame(last_signal=1)}, paper_trade=True)
    assert _submits(scan_env) == []


# --------------------------------------------------------------------------- ORD-4
def test_ORD_4_no_non_equity_symbol_reaches_broker(scan_env):
    data = {s: _frame(last_signal=1) for s in ("GC=F", "EURUSD=X", "^GSPC")}
    main.scan_technical(data, paper_trade=True)
    bad = [o.symbol for o in _submits(scan_env) if any(t in o.symbol for t in ("=F", "=X", "^"))]
    assert bad == []


# --------------------------------------------------------------------------- ORD-5
def test_ORD_5_sell_without_position_rejected(paper_env):
    assert paper_env.positions == []
    paper_trader.execute_signal("AAPL", -1, confidence=1.0, current_price=100.0)
    sells = [o for o in _submits(paper_env) if "sell" in str(o.side).lower()]
    assert sells == []


# --------------------------------------------------------------------------- ORD-6
def test_ORD_6_ml_sell_confidence_at_least_half(monkeypatch, fake_broker):
    class StubDetector:
        def train(self, df):
            return {"mean_cv_accuracy": 0.6, "top_features": {}}

        def predict(self, df):
            out = df.copy()
            out["signal"] = 0
            out["ml_confidence"] = 0.5
            out.iloc[-1, out.columns.get_loc("signal")] = -1
            out.iloc[-1, out.columns.get_loc("ml_confidence")] = 0.15  # prob_up -> strong SELL
            return out

    intents = []
    monkeypatch.setattr(main, "MLPatternDetector", StubDetector)
    monkeypatch.setattr(main, "classic_backtest", lambda df, s, p: _fake_result(profit_factor=1.5))
    main.scan_ml({"AAPL": _frame(n=250, last_signal=0)}, paper_trade=True, intents=intents)
    assert len(intents) == 1 and intents[0].direction == -1
    assert intents[0].confidence >= 0.5


# --------------------------------------------------------------------------- SIG-1
def test_SIG_1_latest_nonzero_signal_wins():
    df = _frame(n=10, last_signal=0)
    col = df.columns.get_loc("signal")
    df.iloc[-3, col] = 1
    df.iloc[-1, col] = -1  # latest non-zero within the window is a SELL
    assert main.check_recent_signal(df) == -1
    df2 = _frame(n=10, last_signal=0)
    df2.iloc[-3, col] = -1
    df2.iloc[-1, col] = 1
    assert main.check_recent_signal(df2) == 1


# --------------------------------------------------------------------------- ORD-7 (passing)
def test_ORD_7_trading_client_is_always_paper(monkeypatch):
    seen = {}

    class SpyClient:
        def __init__(self, *args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs

    import alpaca.trading.client as alpaca_client
    monkeypatch.setattr(alpaca_client, "TradingClient", SpyClient)
    monkeypatch.setattr(paper_trader, "_client", None)
    monkeypatch.setattr(config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    client = paper_trader._get_broker().client
    assert isinstance(client, SpyClient)
    assert seen["kwargs"].get("paper") is True
    monkeypatch.setattr(paper_trader, "_client", None)  # don't leak the spy
