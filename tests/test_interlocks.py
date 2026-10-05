"""Order interlocks, ported to the Phase 1 chokepoint (execution.submit_intent).

Every test runs against a migrated tmp state DB with an equity reading and an order-eligible status, so
the checks under test actually execute (a missing DB would stop everything at `state_not_initialized`).
The property tests also assert that some examples really submit, so they can never pass vacuously.
"""
import math
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

import config
import execution
import main
import paper_trader
from brokers.alpaca import AlpacaBroker
from execution import OrderIntent, accept_reconciliation, submit_intent
from risk_manager import RiskManager
from state import db
from tests.fixtures.fake_broker import FakeBroker, init_state, install_fake_broker, pretend_validated

NOW = datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc)
BASE_DAY = datetime(2024, 1, 2, tzinfo=timezone.utc)


@contextmanager
def fresh_env(sleeve_equity: float = None):
    """Fresh tmp DB + FakeBroker + risk manager with an equity reading. Usable inside hypothesis examples."""
    with tempfile.TemporaryDirectory() as d, pytest.MonkeyPatch.context() as mp:
        init_state(mp, Path(d))
        pretend_validated(mp)
        mp.setattr(config, "TRADING_MODE", "paper")
        mp.setattr(config, "PAPER_TRADE_ENABLED", True)
        conn = db.connect()
        rm = RiskManager(conn)
        rm.update_equity(sleeve_equity or config.SIGNAL_SLEEVE_EQUITY, NOW)
        fake = FakeBroker()
        try:
            yield SimpleNamespace(fake=fake, broker=AlpacaBroker(fake), conn=conn, rm=rm, mp=mp)
        finally:
            conn.close()


def intent(i: int = 0, symbol="AAPL", direction=1, conf=1.0, price=100.0, atr=1.0, status="unvalidated"):
    bar = (BASE_DAY + timedelta(days=i)).date().isoformat()  # a new bar per example: the ledger allows one per bar
    return OrderIntent(symbol, direction, bar, "s1", conf, status, atr, price)


def go(env, it):
    return submit_intent(it, broker=env.broker, conn=env.conn, now=NOW, risk_manager=env.rm)


def _submits(env):
    return [c[1][0] for c in env.fake.calls_to("submit_order")]


def _cap():
    return min(config.MAX_ORDER_NOTIONAL, config.MAX_SYMBOL_PCT * config.SIGNAL_SLEEVE_EQUITY)


def test_fixture_env_really_reaches_the_broker():
    with fresh_env() as env:
        d = go(env, intent())
        assert d.status == "submitted" and len(_submits(env)) == 1  # guards every vacuous-pass regression


def test_notional_bounded_and_nonfinite_rejected():
    stats = {"submitted": 0, "rejected": 0}
    counter = iter(range(10 ** 6))

    @settings(max_examples=80, deadline=None)
    @given(conf=st.one_of(st.sampled_from([0.0, 0.5, 1.0, 1e10, -1.0, float("inf"), float("nan")]),
                          st.floats(0.01, 1.0)),
           price=st.one_of(st.floats(1e-9, 0.99), st.floats(1.0, 1e5)),
           atr_pct=st.floats(0.001, 0.2))
    def prop(conf, price, atr_pct):
        with fresh_env() as env:
            d = go(env, intent(next(counter) % 3000, conf=conf, price=price, atr=price * atr_pct))
            subs = _submits(env)
            if not (math.isfinite(conf) and 0.0 <= conf <= 1.0):
                assert d.status == "rejected" and d.reasons == ["invalid_confidence"] and subs == []
            elif price < execution.MIN_PRICE:
                assert d.status == "rejected" and d.reasons == ["invalid_price"] and subs == []
            elif subs:
                stats["submitted"] += 1
                o = subs[0]
                assert d.status == "submitted" and float(o.qty) >= 1 and float(o.qty) == int(o.qty)
                assert float(o.qty) * price <= _cap() + 1e-6
                assert o.stop_loss.stop_price < price < o.take_profit.limit_price
            else:
                stats["rejected"] += 1
                assert d.status == "rejected" and d.reasons[0] in ("qty_zero", "invalid_bracket_prices")

    prop()
    assert stats["submitted"] > 0, "no example ever submitted: the cap assertions ran over an empty list"


def test_trading_mode_off_means_zero_submits(monkeypatch, tmp_path):
    b = install_fake_broker(monkeypatch, FakeBroker())
    init_state(monkeypatch, tmp_path)
    pretend_validated(monkeypatch)
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    df = pd.DataFrame({"Open": 10.0, "High": 10.1, "Low": 9.9, "Close": 10.0, "Volume": 1e6,
                       "signal": 0}, index=pd.bdate_range("2024-01-01", periods=30))
    df.iloc[-1, df.columns.get_loc("signal")] = 1
    res = SimpleNamespace(is_valid=True, win_rate=0.6, profit_factor=2.0)
    res.summary = lambda: {"win_rate": 0.6, "profit_factor": 2.0}
    monkeypatch.setattr(main, "classic_backtest", lambda *a, **k: res)
    monkeypatch.setattr(main, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(main, "format_signal_alert", lambda *a, **k: "m")
    monkeypatch.setattr(main, "PATTERN_REGISTRY", {"p": lambda d: d.copy()})
    out = main.scan_technical({"AAPL": df}, paper_trade=True)
    assert out and b.calls == []  # not a single broker call, not just no submits


def test_mode_off_decision_is_dry_run_with_zero_broker_calls():
    with fresh_env() as env:
        env.mp.setattr(config, "TRADING_MODE", "off")
        d = go(env, intent())
        assert d.status == "dry_run" and env.fake.calls == []


def test_trading_mode_default_is_off():
    import importlib
    import os
    old = os.environ.pop("TRADING_MODE", None)
    try:
        assert importlib.reload(config).TRADING_MODE == "off"
        assert not hasattr(config, "ALLOW_SHORTS")
    finally:
        if old is not None:
            os.environ["TRADING_MODE"] = old
        importlib.reload(config)


def test_sell_qty_never_exceeds_held():
    stats = {"submitted": 0}

    @settings(max_examples=40, deadline=None)
    @given(held=st.integers(1, 500), conf=st.floats(0, 1))
    def prop(held, conf):
        with fresh_env() as env:
            env.fake.add_position("AAPL", held, 10.0)
            accept_reconciliation(env.broker, env.conn, NOW)  # the owner knows about this position
            d = go(env, intent(0, direction=-1, conf=conf, price=10.0, atr=None))
            assert d.status == "submitted", d.reasons
            stats["submitted"] += 1
            subs = _submits(env)
            assert len(subs) == 1 and float(subs[0].qty) == held  # a sell is capped at held, never scaled up

    prop()
    assert stats["submitted"] > 0


def test_sell_without_position_rejected():
    with fresh_env() as env:
        d = go(env, intent(direction=-1, atr=None))
        assert d.status == "rejected" and d.reasons == ["shorts_disabled", "no_long_position"]
        assert _submits(env) == []


def test_duplicate_buy_skipped_and_position_blocks_buy():
    with fresh_env() as env:
        assert go(env, intent(0)).status == "submitted"
        d = go(env, intent(0))
        assert d.status == "duplicate" and d.reasons == ["signal_already_processed"]
        d2 = go(env, intent(1))  # a new bar, but the first entry order is still open
        assert d2.status == "rejected" and d2.reasons == ["open_buy_order_exists"]
        assert len(_submits(env)) == 1
    with fresh_env() as env:
        env.fake.add_position("MSFT", 5, 10.0)
        accept_reconciliation(env.broker, env.conn, NOW)
        d = go(env, intent(0, symbol="MSFT", price=10.0, atr=0.2))
        assert d.status == "rejected" and d.reasons == ["position_exists"] and _submits(env) == []


@pytest.mark.parametrize("method", ["get_account", "get_all_positions", "get_orders"])
def test_lookup_error_fails_closed(monkeypatch, tmp_path, method):
    def run(fail: bool):
        b = install_fake_broker(monkeypatch, FakeBroker())
        init_state(monkeypatch, tmp_path / ("f" if fail else "ok"))
        pretend_validated(monkeypatch)
        if fail:
            b.raise_on(method)
        res = paper_trader.execute_signal("AAPL", 1, 1.0, 100.0, atr=1.0)
        return b, res

    b_ok, res_ok = run(False)
    assert res_ok is not None and len(b_ok.calls_to("submit_order")) == 1  # control: it does submit when healthy
    b, res = run(True)
    assert res is None and b.calls_to("submit_order") == []


@pytest.mark.parametrize("sym,reason", [("GC=F", "not_executable"), ("EURUSD=X", "not_executable"),
                                        ("^GSPC", "not_executable"), ("BTC-USD", "not_executable")])
def test_non_equity_symbols_rejected(sym, reason):
    with fresh_env() as env:
        d = go(env, intent(symbol=sym, price=10.0))
        assert d.status == "rejected" and d.reasons in ([reason], ["unknown_symbol"])
        assert env.fake.calls == []  # rejected before any broker call


def test_qty_zero_rejected():
    with fresh_env() as env:
        d = go(env, intent(price=5000.0, atr=50.0))
        assert d.status == "rejected" and d.reasons[0] == "qty_zero" and _submits(env) == []


def test_repeated_sell_never_exceeds_held():
    with fresh_env() as env:
        env.fake.add_position("AAPL", 10, 10.0)
        accept_reconciliation(env.broker, env.conn, NOW)
        ds = [go(env, intent(i, direction=-1, price=10.0, atr=None)) for i in range(3)]
        assert ds[0].status == "submitted" and ds[0].qty == 10
        assert [d.status for d in ds[1:]] == ["rejected", "rejected"]
        assert all(d.reasons == ["shorts_disabled", "open_sell_orders_cover_position"] for d in ds[1:])
        assert sum(float(o.qty) for o in _submits(env)) == 10


def test_tiny_price_rejected_and_qty_ceiling():
    with fresh_env() as env:
        d = go(env, intent(0, price=1e-9, atr=1e-10))
        assert d.status == "rejected" and d.reasons == ["invalid_price"] and _submits(env) == []
        d = go(env, intent(1, price=1.0, atr=0.01))  # cheapest allowed price: the notional cap bounds the qty
        assert d.status == "submitted" and 1 <= d.qty <= _cap() / 1.0
        assert float(_submits(env)[0].qty) * 1.0 <= _cap()


def test_mode_off_close_and_submit_never_touch_broker(monkeypatch):
    b = install_fake_broker(monkeypatch, FakeBroker())
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    assert paper_trader.close_position("AAPL") is None
    assert paper_trader.submit_order("AAPL", 1, "buy") is None
    assert b.calls_to("close_position") == [] and b.calls_to("submit_order") == []
