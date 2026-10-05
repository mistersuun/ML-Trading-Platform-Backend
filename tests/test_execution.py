"""Execution chokepoint (WS1.3/1.5/1.6, paper only). Offline, FakeBroker, tmp SQLite."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
import execution
from brokers import alpaca as alpaca_mod
from brokers.alpaca import AlpacaBroker
from brokers.base import OrderSpec, validate_spec
from brokers.fake import FakeAPIError, FakeBroker
from execution import Decision, OrderIntent, accept_reconciliation, reconcile, signal_key, submit_intent
from risk_manager import RiskManager
from state import db

NOW = datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "state" / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    conn = db.connect()
    db.migrate(conn)
    fake = FakeBroker()
    rm = RiskManager(conn)
    rm.update_equity(config.SIGNAL_SLEEVE_EQUITY, NOW)  # check() fails closed without an equity reading
    return SimpleNamespace(fake=fake, broker=AlpacaBroker(fake), conn=conn, rm=rm)


def mk(**kw) -> OrderIntent:
    d = dict(symbol="AAPL", direction=1, signal_bar_date="2024-06-03", strategy_key="s1", confidence=1.0,
             validation_status="oos_validated", atr=1.0, price=100.0)
    d.update(kw)
    return OrderIntent(**d)


def go(env, intent=None, **kw) -> Decision:
    kw.setdefault("risk_manager", env.rm)
    return submit_intent(intent or mk(), broker=env.broker, conn=env.conn, now=NOW, **kw)


def submits(env):
    return env.fake.calls_to("submit_order")


def ledger(env):
    return [dict(r) for r in env.conn.execute("SELECT * FROM signal_ledger")]


def n_orders(env):
    return env.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def accept(env):
    accept_reconciliation(env.broker, env.conn, NOW)


# ── paper only ──────────────────────────────────────────────

def test_trading_client_always_paper(monkeypatch):
    seen = {}

    class Spy:
        def __init__(self, *a, **k):
            seen["a"], seen["k"] = a, k

    import alpaca.trading.client as ac
    monkeypatch.setattr(ac, "TradingClient", Spy)
    alpaca_mod.build_trading_client("k", "s")
    assert seen["k"]["paper"] is True
    assert "paper" not in inspect.signature(alpaca_mod.build_trading_client).parameters


def test_no_live_mode_in_adapter_source():
    src = inspect.getsource(alpaca_mod)
    assert "paper=False" not in src and "LIVE_TRADING_CONFIRM" not in src


def test_invalid_trading_mode_rejected(env, monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    d = go(env)
    assert d.status == "rejected" and env.fake.calls == []


# ── dry run / halted: zero broker calls ─────────────────────

@pytest.mark.parametrize("mode,enabled", [("off", True), ("off", False), ("paper", False)])
def test_dry_run_makes_zero_broker_calls(env, monkeypatch, mode, enabled):
    monkeypatch.setattr(config, "TRADING_MODE", mode)
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", enabled)
    d = go(env)
    assert d.status == "dry_run"
    assert env.fake.calls == [] and ledger(env) == []


def test_halted_makes_zero_broker_calls(env):
    RiskManager(env.conn).halt("test")
    d = go(env)
    assert d.status == "halted" and any(r.startswith("halted") for r in d.reasons)
    assert env.fake.calls == []


def test_kill_switch_file_halts(env, tmp_path):
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "KILL").write_text("x")
    d = go(env)
    assert d.status == "halted" and "kill_switch" in d.reasons and env.fake.calls == []


def test_state_not_initialized_halts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    fake = FakeBroker()
    d = submit_intent(mk(), broker=AlpacaBroker(fake), now=NOW)
    assert d.status == "halted" and fake.calls == []
    assert not (tmp_path / "missing.db").exists()


def test_max_orders_per_day_rejects(env, monkeypatch):
    monkeypatch.setattr(config, "MAX_ORDERS_PER_DAY", 1)
    assert go(env).status == "submitted"
    d = go(env, mk(symbol="MSFT"))
    assert d.status == "rejected" and "max_orders_per_day" in d.reasons
    assert len(submits(env)) == 1


# ── eligibility ─────────────────────────────────────────────

def test_unvalidated_is_rejected_not_validated(env):
    d = go(env, mk(validation_status="unvalidated"))
    assert (d.status, d.reasons) == ("rejected", ["not_validated"])
    assert env.fake.calls == [] and ledger(env) == []  # no broker contact, key not consumed


@pytest.mark.parametrize("sym", ["BTC-USD", "GC=F", "EURUSD=X", "^GSPC", "NOPE"])
def test_non_executable_symbols_rejected(env, sym):
    d = go(env, mk(symbol=sym))
    assert d.status == "rejected" and d.reasons[0] in ("not_executable", "unknown_symbol")
    assert env.fake.calls == []


def test_asset_not_tradable_rejected(env):
    env.fake.assets["AAPL"] = SimpleNamespace(symbol="AAPL", tradable=False, fractionable=True, status="active")
    d = go(env)
    assert d.status == "rejected" and "asset_not_tradable" in d.reasons and not submits(env)


def test_asset_lookup_cached_per_broker(env):
    go(env)
    go(env)
    assert len(env.fake.calls_to("get_asset")) == 1


@pytest.mark.parametrize("kw,reason", [
    (dict(price=0.5), "invalid_price"), (dict(price=float("nan")), "invalid_price"),
    (dict(confidence=1.5), "invalid_confidence"), (dict(confidence=float("nan")), "invalid_confidence"),
    (dict(direction=0), "invalid_direction"),
])
def test_invalid_intent_fields(env, kw, reason):
    d = go(env, mk(**kw))
    assert d.status == "rejected" and d.reasons == [reason] and env.fake.calls == []


@pytest.mark.parametrize("atr", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_missing_or_bad_atr_rejected(env, atr):
    d = go(env, mk(atr=atr))
    assert d.status == "rejected" and "atr_invalid" in d.reasons and not submits(env)


# ── sizing and bracket ──────────────────────────────────────

def test_notional_cap_on_5000_dollar_stock_gives_qty_zero(env):
    d = go(env, mk(price=5000.0, atr=50.0))
    assert d.status == "rejected" and "qty_zero" in d.reasons
    assert not submits(env)


def test_bracket_at_100_atr_1(env):
    d = go(env)
    assert d.status == "submitted"
    assert (d.stop_price, d.limit_price, d.qty) == (98.00, 103.00, 10)  # 1000/100 notional cap binds
    req = submits(env)[0][1][0]
    assert str(req.order_class).lower().endswith("bracket")
    assert req.stop_loss.stop_price == 98.00 and req.take_profit.limit_price == 103.00
    assert str(req.time_in_force).lower().endswith("gtc") and req.qty == 10 and req.symbol == "AAPL"
    assert req.client_order_id == d.client_order_id == signal_key("AAPL", "buy", "2024-06-03")
    assert n_orders(env) == 1 and ledger(env)[0]["status"] == "submitted"


def test_confidence_scales_down(env):
    assert go(env, mk(confidence=0.5)).qty == 5


def test_risk_cap_binds_with_wide_stop(env):
    d = go(env, mk(atr=10.0))  # stop distance 20 -> 50/20 = 2.5 -> floor 2
    assert d.qty == 2


def test_stop_below_zero_rejected(env):
    d = go(env, mk(price=1.5, atr=1.0))
    assert d.status == "rejected" and any(r.startswith(("invalid_bracket", "qty_zero")) for r in d.reasons)


def test_gross_exposure_room_limits_qty(env):
    env.fake.add_position("SPY", 99, 100.0)  # $9900 of $10000 gross
    accept(env)
    d = go(env)
    assert d.status == "submitted" and d.qty == 1  # only $100 of gross room left


def test_max_open_positions(env, monkeypatch):
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 2)
    env.fake.add_position("XOM", 1, 100.0)
    env.fake.add_position("JPM", 1, 100.0)
    accept(env)
    d = go(env)
    assert d.status == "rejected" and "max_open_positions" in d.reasons


def test_max_positions_per_cluster(env):
    env.fake.add_position("SPY", 1, 100.0)
    env.fake.add_position("QQQ", 1, 100.0)
    accept(env)
    d = go(env, mk(symbol="IWM"))
    assert d.status == "rejected" and "max_positions_per_cluster" in d.reasons
    assert go(env, mk(symbol="XOM")).status == "submitted"


# ── explicit order types / whole shares ─────────────────────

def test_fractional_qty_rejected():
    spec = OrderSpec("AAPL", "buy", 1.5, "market", "day", "cid", bracket=True, stop_price=98.0, take_profit_price=103.0)
    with pytest.raises(ValueError):
        validate_spec(spec)
    fake = FakeBroker()
    with pytest.raises(ValueError):
        AlpacaBroker(fake).submit(spec)
    assert fake.calls == []


@pytest.mark.parametrize("spec", [
    OrderSpec("AAPL", "short", 1, "market", "day", "c"),
    OrderSpec("AAPL", "buy", 1, "stop_market", "day", "c"),
    OrderSpec("AAPL", "buy", 1, "limit", "day", "c"),  # limit without price
    OrderSpec("AAPL", "sell", 1, "market", "day", "c", bracket=True, stop_price=1.0, take_profit_price=2.0),
    OrderSpec("AAPL", "buy", 1, "market", "day", "c", bracket=True, stop_price=99.0, take_profit_price=98.0),
    OrderSpec("AAPL", "buy", 1, "market", "day", "", ),
])
def test_invalid_specs_raise(spec):
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_unknown_order_type_rejected_never_market(env, monkeypatch):
    monkeypatch.setattr(execution, "ENTRY_ORDER_TYPE", "stop_market")
    d = go(env)
    assert d.status == "rejected" and any("unknown order type" in r for r in d.reasons)
    assert not submits(env)


# ── reconciliation ──────────────────────────────────────────

def test_unknown_position_blocks_entries_and_emits_event(env):
    env.fake.add_position("TSLA", 5, 200.0)
    d = go(env)
    assert d.status == "rejected" and d.reasons[0] == "reconcile_mismatch" and "unknown_position:TSLA" in d.reasons
    assert d.events and d.events[0]["type"] == "reconcile_mismatch"
    assert not submits(env) and ledger(env) == []


def test_accept_reconciliation_unblocks(env):
    env.fake.add_position("TSLA", 5, 200.0)
    assert not reconcile(env.broker, env.conn).ok
    assert accept_reconciliation(env.broker, env.conn, NOW).ok
    assert go(env).status == "submitted"
    env.fake.add_position("NVDA", 1, 100.0)  # a NEW unknown position blocks again
    assert go(env, mk(symbol="MSFT")).reasons[0] == "reconcile_mismatch"


def test_unknown_open_order_blocks(env):
    env.fake.orders.append(SimpleNamespace(id="x1", client_order_id="manual-1", symbol="MSFT", qty="3", side="buy",
                                           status="new", legs=None))
    r = reconcile(env.broker, env.conn)
    assert not r.ok and "unknown_open_order:manual-1" in r.mismatches


def test_short_position_is_mismatch(env):
    env.fake.add_position("AAPL", 5, 100.0, side="short")
    assert "short_position:AAPL" in reconcile(env.broker, env.conn).mismatches


@pytest.mark.parametrize("method", ["get_all_positions", "get_orders"])
def test_broker_error_during_reconcile_blocks(env, method):
    env.fake.raise_on(method)
    r = reconcile(env.broker, env.conn)
    assert not r.ok and r.mismatches[0].startswith("broker_error") and r.events
    d = go(env)
    assert d.status == "rejected" and "reconcile_mismatch" in d.reasons and not submits(env)


def test_accept_refuses_unreadable_book(env):
    env.fake.raise_on("get_all_positions")
    with pytest.raises(ConnectionError):
        accept_reconciliation(env.broker, env.conn)


def test_own_order_not_flagged_by_reconcile(env):
    assert go(env).status == "submitted"
    assert reconcile(env.broker, env.conn).ok


# ── idempotency ─────────────────────────────────────────────

def test_ledger_dedupe_across_two_runs(env):
    assert go(env).status == "submitted"
    env.fake.orders.clear()
    env.fake.positions.clear()  # broker state clean again (filled and closed)
    d = go(env)
    assert d.status == "duplicate" and d.reasons == ["signal_already_processed"]
    assert len(submits(env)) == 1 and len(ledger(env)) == 1


def test_two_strategies_same_bar_one_order(env):
    a = go(env, mk(strategy_key="ema"))
    b = go(env, mk(strategy_key="rsi"))
    assert (a.status, b.status) == ("submitted", "duplicate")
    assert len(submits(env)) == 1 and n_orders(env) == 1
    assert ledger(env)[0]["strategy_key"] == "ema"


def test_next_bar_is_a_new_signal(env):
    go(env)
    env.fake.orders.clear()
    env.fake.positions.clear()
    assert go(env, mk(signal_bar_date="2024-06-04")).status == "submitted"


def test_signal_key_ignores_strategy_and_is_readable():
    k = signal_key("AAPL", "buy", "2024-06-03")
    assert k.startswith("AAPL-buy-2024-06-03-") and len(k.split("-")[-1]) == 12


def test_timeout_after_accept_gives_one_order(env):
    env.fake.timeout_after_accept("submit_order")
    d = go(env)
    assert d.status == "submitted" and d.broker_id and "recovered_by_client_order_id" in d.reasons
    assert len(submits(env)) == 1 and len(env.fake.orders) == 1 and n_orders(env) == 1
    env.fake.clear_failures()
    assert go(env).status == "duplicate"
    assert len(env.fake.orders) == 1


def test_duplicate_id_error_returns_duplicate(env):
    class Dup(FakeBroker):
        def submit_order(self, order_data=None, *a, **k):
            super().submit_order(order_data, *a, **k)
            raise FakeAPIError("client_order_id must be unique", 422)

    env.fake = Dup()
    env.broker = AlpacaBroker(env.fake)
    d = go(env)
    assert d.status == "duplicate" and len(env.fake.orders) == 1 and len(submits(env)) == 1


def test_failed_submit_unknown_blocks_next_entries(env):
    env.fake.raise_on("submit_order")
    d = go(env)
    assert d.status == "error" and "order_not_found" in d.reasons
    assert len(submits(env)) == 1  # no retry with a new id
    assert ledger(env)[0]["status"] == "unknown" and n_orders(env) == 0
    env.fake.clear_failures()
    d2 = go(env, mk(symbol="MSFT"))
    assert d2.status == "rejected" and any(r.startswith("orphan_ledger") for r in d2.reasons)
    accept(env)
    assert go(env, mk(symbol="MSFT")).status == "submitted"


def test_lookup_failure_after_submit_failure_is_unknown(env):
    env.fake.raise_on("submit_order")
    env.fake.raise_on("get_order_by_client_id")
    d = go(env)
    assert d.status == "error" and "lookup_failed" in d.reasons and ledger(env)[0]["status"] == "unknown"


def test_exception_fails_closed(env):
    class Boom:
        def get_asset(self, s):
            raise RuntimeError("boom")

    d = submit_intent(mk(), broker=Boom(), conn=env.conn, now=NOW, risk_manager=env.rm)
    assert d.status == "error" and not submits(env)


def test_record_order_increments_counter(env):
    go(env)
    assert RiskManager(env.conn).status()["orders_today"] == 1


def test_decision_statuses_are_declared(env):
    for d in (go(env), go(env), go(env, mk(validation_status="unvalidated"))):
        assert d.status in execution.DECISION_STATUSES


# ── no shorts / sells ───────────────────────────────────────

def test_sell_without_position_is_shorts_disabled(env):
    d = go(env, mk(direction=-1))
    assert d.status == "rejected" and "shorts_disabled" in d.reasons and not submits(env)


def test_sell_reduces_long_to_held_quantity(env):
    env.fake.add_position("AAPL", 7, 100.0)
    accept(env)
    d = go(env, mk(direction=-1))
    assert d.status == "submitted" and d.qty == 7 and d.stop_price is None
    req = submits(env)[0][1][0]
    assert str(req.side).lower().endswith("sell") and req.qty == 7 and getattr(req, "order_class", None) is None


def test_sell_never_exceeds_held_minus_pending(env):
    env.fake.add_position("AAPL", 10, 100.0)
    env.fake.orders.append(SimpleNamespace(id="p1", client_order_id="c-p1", symbol="AAPL", qty="4", side="sell",
                                           status="new", legs=None))
    accept(env)
    assert go(env, mk(direction=-1)).qty == 6
    env.fake.orders.clear()
    env.fake.orders.append(SimpleNamespace(id="p2", client_order_id="c-p2", symbol="AAPL", qty="10", side="sell",
                                           status="new", legs=None))
    accept(env)
    d = go(env, mk(direction=-1, signal_bar_date="2024-06-04"))
    assert d.status == "rejected" and "shorts_disabled" in d.reasons


def test_buy_with_existing_position_or_open_order_rejected(env):
    env.fake.add_position("AAPL", 3, 100.0)
    accept(env)
    assert "position_exists" in go(env).reasons
    env.fake.positions.clear()
    env.fake.orders.append(SimpleNamespace(id="o9", client_order_id="c9", symbol="AAPL", qty="3", side="buy",
                                           status="new", legs=None))
    accept(env)
    assert "open_buy_order_exists" in go(env, mk(signal_bar_date="2024-06-04")).reasons


def test_rejected_after_ledger_consumes_the_bar(env):
    env.fake.add_position("AAPL", 3, 100.0)
    accept(env)
    assert go(env).status == "rejected"
    assert ledger(env)[0]["status"] == "rejected"
    assert go(env).status == "duplicate"


# ── review fixes: protective legs, exits, cancel-on-halt, pending sizing, broker rejections ──

def _fill_own_bracket(env, nested=True):
    """Submit AAPL through the chokepoint, then make the broker look like the entry filled (legs open)."""
    d = go(env)
    assert d.status == "submitted"
    env.fake.orders.clear()
    env.fake.add_filled_bracket(d.client_order_id, "AAPL", d.qty, d.stop_price, d.limit_price, nested=nested,
                                broker_id=d.broker_id)
    return d


@pytest.mark.parametrize("nested", [True, False])
def test_filled_bracket_with_open_legs_reconciles_clean(env, nested):
    _fill_own_bracket(env, nested)
    r = reconcile(env.broker, env.conn)
    assert r.ok, r.mismatches


@pytest.mark.parametrize("nested", [True, False])
def test_open_orders_adapter_drops_filled_parent_keeps_legs(env, nested):
    _fill_own_bracket(env, nested)
    open_orders = env.broker.get_open_orders()
    assert {o.order_type for o in open_orders} == {"limit", "stop"} and all(o.side == "sell" for o in open_orders)


def test_unknown_unnested_leg_still_blocks(env):
    env.fake.orders.append(env.fake._leg("MSFT", 5, "stop", 90.0))  # a bracket leg we never placed
    r = reconcile(env.broker, env.conn)
    assert not r.ok and any(m.startswith("unknown_open_order") for m in r.mismatches)


@pytest.mark.parametrize("nested", [True, False])
def test_long_without_live_stop_is_flagged(env, nested):
    _fill_own_bracket(env, nested)
    for o in list(env.fake.orders):  # the GTC/day legs expired or were cancelled by hand
        o.legs = None
    env.fake.orders[:] = [o for o in env.fake.orders if o.side == "buy"]
    r = reconcile(env.broker, env.conn)
    assert not r.ok and "missing_protective_stop:AAPL" in r.mismatches
    assert r.events and r.events[0]["type"] == "reconcile_mismatch"
    assert go(env, mk(symbol="MSFT", signal_bar_date="2024-06-04")).reasons[0] == "reconcile_mismatch"
    assert accept_reconciliation(env.broker, env.conn, NOW).ok  # the owner can acknowledge it


def test_sell_exit_cancels_bracket_legs_then_sells_held(env):
    d = _fill_own_bracket(env, nested=True)
    ex = go(env, mk(direction=-1, signal_bar_date="2024-06-04"))
    assert ex.status == "submitted" and ex.qty == d.qty and "cancelled_protective_legs:2" in ex.reasons
    calls = [c[0] for c in env.fake.calls]
    assert calls.index("cancel_order_by_id") < len(calls) - 1 and calls[-1] == "submit_order"
    assert len(env.fake.calls_to("cancel_order_by_id")) == 2
    req = submits(env)[-1][1][0]
    assert str(req.side).lower().endswith("sell") and req.qty == d.qty and str(req.time_in_force).lower().endswith("day")


def test_sell_exit_with_unnested_legs(env):
    d = _fill_own_bracket(env, nested=False)
    ex = go(env, mk(direction=-1, signal_bar_date="2024-06-04"))
    assert ex.status == "submitted" and ex.qty == d.qty and len(env.fake.calls_to("cancel_order_by_id")) == 2


def test_exit_in_flight_counts_as_protection_and_blocks_second_exit(env):
    _fill_own_bracket(env)
    assert go(env, mk(direction=-1, signal_bar_date="2024-06-04")).status == "submitted"
    assert reconcile(env.broker, env.conn).ok  # legs gone, but a market exit is open
    d = go(env, mk(direction=-1, signal_bar_date="2024-06-05"))
    assert d.status == "rejected" and "open_sell_orders_cover_position" in d.reasons


def test_failed_leg_cancel_sends_no_sell_and_stays_retryable(env):
    _fill_own_bracket(env)
    env.fake.raise_on("cancel_order_by_id")
    ex = go(env, mk(direction=-1, signal_bar_date="2024-06-04"))
    assert ex.status == "error" and ex.reasons[0].startswith("cancel_protective_failed")
    assert len(submits(env)) == 1  # only the original entry
    env.fake.clear_failures()
    assert go(env, mk(direction=-1, signal_bar_date="2024-06-04")).status == "submitted"  # retryable


def test_cancel_open_entries_cancels_only_unfilled_parent_buys(env):
    d = go(env)  # open parent BUY with nested legs (not filled)
    env.fake.add_filled_bracket("other-filled", "MSFT", 3, 90.0, 110.0, nested=False)  # legs only; not ours
    cancelled = execution.cancel_open_entries(env.broker, env.conn)
    assert cancelled == [d.broker_id]
    assert [str(c[1][0]) for c in env.fake.calls_to("cancel_order_by_id")] == [d.broker_id]
    legs = [o for o in env.fake.orders if o.side == "sell"]
    assert len(legs) == 2  # the foreign legs are untouched
    assert env.conn.execute("SELECT status FROM orders WHERE client_order_id=?", (d.client_order_id,)).fetchone()[0] == "canceled"


def test_cancel_open_entries_never_cancels_protective_legs_of_a_filled_position(env):
    _fill_own_bracket(env, nested=False)
    assert execution.cancel_open_entries(env.broker, env.conn) == []
    assert env.fake.calls_to("cancel_order_by_id") == []


def test_pending_entries_count_against_gross_and_heat(env, monkeypatch):
    monkeypatch.setattr(config, "MAX_GROSS_EXPOSURE_PCT", 0.15)  # $1500 of gross room in total
    monkeypatch.setattr(config, "MAX_ORDER_NOTIONAL", 1000.0)
    first = go(env, mk(symbol="AAPL", price=100.0))
    assert first.status == "submitted" and first.qty == 10  # $1000 pending
    second = go(env, mk(symbol="XOM", price=100.0))  # only $500 of room remains once AAPL's pending is counted
    assert second.status == "submitted" and second.qty == 5
    third = go(env, mk(symbol="JPM", price=100.0))
    assert third.status == "rejected" and third.reasons[0] == "qty_zero" and "binding_cap:gross_room" in third.reasons


def test_pending_entries_count_against_heat(env, monkeypatch):
    monkeypatch.setattr(config, "MAX_PORTFOLIO_HEAT_PCT", 0.0075)  # $75 of heat in total
    monkeypatch.setattr(config, "MAX_GROSS_EXPOSURE_PCT", 1.0)
    monkeypatch.setattr(config, "MAX_POSITIONS_PER_CLUSTER", 8)
    a = go(env, mk(symbol="AAPL", price=100.0))  # risk 10 sh * $2 = $20 pending, i.e. ($75 - $20) room left
    b = go(env, mk(symbol="XOM", price=100.0))
    assert a.qty == 10 and b.status == "submitted" and b.qty == 10
    c = go(env, mk(symbol="JPM", price=100.0))  # $40 used, $35 left -> 17 sh of $2 stop but capped by notional 10
    assert c.status == "submitted"
    d = go(env, mk(symbol="MSFT", price=100.0, atr=5.0))  # $10 stop distance, only $15 left -> 1 share
    assert d.status == "submitted" and d.qty == 1


@pytest.mark.parametrize("code", [422, 403])
def test_definitive_broker_rejection_marks_rejected_and_does_not_block(env, code):
    env.fake.raise_on("submit_order", FakeAPIError("bracket prices invalid", code))
    d = go(env)
    assert d.status == "rejected" and d.reasons == [f"broker_rejected:{code}"]
    assert ledger(env)[0]["status"] == "rejected"
    env.fake.clear_failures()
    assert reconcile(env.broker, env.conn).ok  # no orphan_ledger
    assert go(env, mk(symbol="MSFT")).status == "submitted"


@pytest.mark.parametrize("code", [429, 500])
def test_transient_rejection_without_order_stays_unknown(env, code):
    env.fake.raise_on("submit_order", FakeAPIError("busy", code))
    d = go(env)
    assert d.status == "error" and ledger(env)[0]["status"] == "unknown"
    assert any(m.startswith("orphan_ledger") for m in reconcile(env.broker, env.conn).mismatches)


# ── review round 2: exit protocol, reduce-only exits, acceptance scope, attribution ──

def _exit(env, day="2024-06-04"):
    return go(env, mk(direction=-1, signal_bar_date=day))


def _refuse_sells(env, market_only: bool):
    """Make the broker refuse SELL submits (all of them, or only the market exit), like a 403 insufficient qty."""
    orig = env.fake.submit_order

    def refuse(order_data=None, *a, **k):
        is_sell = str(order_data.side).lower().endswith("sell")
        if is_sell and (not market_only or "Market" in type(order_data).__name__):
            raise FakeAPIError("insufficient qty available for order", 403)
        return orig(order_data, *a, **k)

    env.fake.submit_order = refuse
    return orig


def _never_settles(monkeypatch):
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    monkeypatch.setattr(execution, "_clock", clock)
    monkeypatch.setattr(execution, "_sleep", lambda s: None)


def test_exit_waits_for_async_cancel_then_sells(env, monkeypatch):
    d = _fill_own_bracket(env)
    env.fake.async_cancel = env.fake.enforce_held_qty = True
    sleeps = []
    monkeypatch.setattr(execution, "_sleep", lambda s: (sleeps.append(s), env.fake.tick()))
    ex = _exit(env)
    assert ex.status == "submitted" and ex.qty == d.qty and "cancelled_protective_legs:2" in ex.reasons
    assert sleeps  # it polled until the legs were terminal instead of selling into pending_cancel
    assert ledger(env)[-1]["status"] == "submitted"


def test_fake_broker_refuses_sell_while_legs_pending_cancel(env):
    _fill_own_bracket(env)
    env.fake.async_cancel = env.fake.enforce_held_qty = True
    for o in env.broker.get_open_orders():
        env.broker.cancel_order(o.id)
    with pytest.raises(FakeAPIError) as ei:
        env.fake.submit_order(SimpleNamespace(client_order_id="x", symbol="AAPL", qty=10, side="sell"))
    assert ei.value.status_code == 422
    with pytest.raises(FakeAPIError) as ei2:  # cancelling a pending_cancel order again is a 422 too
        env.broker.cancel_order(env.broker.get_open_orders()[0].id)
    assert ei2.value.status_code == 422
    env.fake.tick()
    assert env.fake.reserved_qty("AAPL") == 0


def test_sell_refused_after_cancel_replaces_standalone_stop_and_stays_retryable(env):
    d = _fill_own_bracket(env)
    orig = _refuse_sells(env, market_only=True)
    ex = _exit(env)
    assert ex.status == "rejected" and "standalone_stop_replaced" in ex.reasons and "UNPROTECTED" not in ex.reasons
    assert ledger(env)[-1]["status"] == "rejected_retryable"  # never 'rejected'
    stops = [o for o in env.broker.get_open_orders() if o.order_type == "stop" and o.parent_id is None]
    assert len(stops) == 1 and stops[0].qty == d.qty and stops[0].stop_price == d.stop_price
    req = [c for c in submits(env) if "Stop" in type(c[1][0]).__name__][0][1][0]
    assert str(req.time_in_force).lower().endswith("gtc") and str(req.side).lower().endswith("sell")
    assert reconcile(env.broker, env.conn).ok  # the re-placed stop is ours and covers the position
    env.fake.submit_order = orig  # next run: the retryable row is revived (not 'duplicate'), the stop swapped for the exit
    again = _exit(env)
    assert again.status == "submitted" and again.qty == d.qty
    assert ledger(env)[-1]["status"] == "submitted"


def test_stop_replace_also_fails_emits_unprotected_and_halts(env):
    d = _fill_own_bracket(env)
    orig = _refuse_sells(env, market_only=False)
    ex = _exit(env)
    assert ex.status == "rejected" and "UNPROTECTED" in ex.reasons
    ev = [e for e in ex.events if e["type"] == "halt" and e["reason"].startswith("UNPROTECTED AAPL")]
    assert ev
    assert env.rm.status()["halted"] and "UNPROTECTED AAPL" in env.rm.status()["halt_reason"]
    assert ledger(env)[-1]["status"] == "rejected_retryable"
    assert go(env, mk(symbol="MSFT", signal_bar_date="2024-06-05")).status == "halted"
    env.rm.resume(confirm=True)
    env.fake.submit_order = orig
    assert _exit(env).status == "submitted"  # retry after the operator resumed


def test_legs_that_never_settle_end_unprotected_not_silently_rejected(env, monkeypatch):
    _fill_own_bracket(env)
    env.fake.async_cancel = env.fake.enforce_held_qty = True
    _never_settles(monkeypatch)
    ex = _exit(env)
    assert "legs_not_confirmed_cancelled" in ex.reasons and "UNPROTECTED" in ex.reasons
    assert ledger(env)[-1]["status"] == "rejected_retryable"


def test_sibling_auto_cancel_404_is_treated_as_gone(env):
    d = _fill_own_bracket(env)
    env.fake.oco_cascade = True  # cancelling the stop leg also cancels the take-profit leg
    ex = _exit(env)
    assert ex.status == "submitted" and ex.qty == d.qty and not any("UNPROTECTED" in r for r in ex.reasons)
    assert len(env.fake.calls_to("cancel_order_by_id")) == 2  # the second one 404'd and was ignored


def test_exit_proceeds_when_the_protective_stop_is_missing(env):
    d = _fill_own_bracket(env)
    for o in env.fake.orders:
        o.legs = None  # GTC legs expired / cancelled by hand
    assert not reconcile(env.broker, env.conn).ok
    ex = _exit(env)
    assert ex.status == "submitted" and ex.qty == d.qty
    assert any(e["type"] == "reconcile_mismatch" for e in ex.events)  # still alerted
    assert env.fake.calls_to("cancel_order_by_id") == []


@pytest.mark.parametrize("method", ["get_all_positions", "get_orders"])
def test_exit_blocked_when_the_book_is_unreadable(env, method):
    env.fake.add_position("AAPL", 5, 100.0)
    accept(env)
    env.fake.raise_on(method)
    d = _exit(env)
    assert d.status == "rejected" and "reconcile_mismatch" in d.reasons and not submits(env)


def test_exit_blocked_by_a_short_position(env):
    env.fake.add_position("MSFT", 5, 100.0, side="short")
    env.fake.add_position("AAPL", 5, 100.0)
    d = _exit(env)
    assert d.status == "rejected" and "short_position:MSFT" in d.reasons and not submits(env)


def test_acceptance_is_scoped_to_the_latest_snapshot(env):
    env.fake.add_position("TSLA", 5, 200.0)
    accept(env)
    assert reconcile(env.broker, env.conn).ok
    env.fake.positions.clear()
    accept(env)  # second snapshot: TSLA gone
    env.fake.add_position("TSLA", 5, 200.0)  # a NEW manual position in the same symbol
    assert "unknown_position:TSLA" in reconcile(env.broker, env.conn).mismatches


def test_acceptance_expires_when_a_new_bracket_is_placed_on_the_symbol(env):
    _fill_own_bracket(env)
    env.fake.orders.clear()  # no live legs
    accept(env)
    assert reconcile(env.broker, env.conn).ok  # accepted: exempt from the missing-stop check
    env.fake.positions.clear()
    assert go(env, mk(signal_bar_date="2024-06-04")).status == "submitted"  # a new platform bracket on AAPL
    env.fake.orders.clear()
    env.fake.add_position("AAPL", 10, 100.0)  # filled, but its legs are gone again
    assert "missing_protective_stop:AAPL" in reconcile(env.broker, env.conn).mismatches


def test_stop_smaller_than_position_is_insufficient(env):
    d = _fill_own_bracket(env)
    stop = [leg for o in env.fake.orders for leg in (o.legs or []) if leg.order_type == "stop"][0]
    stop.qty = 1  # a 1-share stop on a 10-share position
    r = reconcile(env.broker, env.conn)
    assert not r.ok and "insufficient_protective_stop:AAPL" in r.mismatches and d.qty == 10
    stop.qty = d.qty
    assert reconcile(env.broker, env.conn).ok


def test_unattributable_sell_leg_is_unknown_and_never_cancelled_by_an_exit(env):
    d = _fill_own_bracket(env, nested=False)
    foreign = env.fake._leg("AAPL", 3, "stop", 50.0)  # an OCO leg on a symbol we bracketed, but not ours
    env.fake.orders.append(foreign)
    r = reconcile(env.broker, env.conn)
    assert not r.ok and f"unknown_open_order:{foreign.client_order_id}" in r.mismatches
    ex = _exit(env)
    assert ex.status == "submitted" and ex.qty == d.qty - 3  # the foreign leg keeps reserving its shares
    cancelled = {str(c[1][0]) for c in env.fake.calls_to("cancel_order_by_id")}
    assert str(foreign.id) not in cancelled and len(cancelled) == 2
    assert foreign in env.fake.orders


def test_nested_leg_under_an_unknown_parent_is_unknown(env):
    _fill_own_bracket(env)
    env.fake.add_filled_bracket("someone-elses", "AAPL", 10, 98.0, 103.0, nested=True)  # same prices, not our parent
    assert any(m.startswith("unknown_open_order") for m in reconcile(env.broker, env.conn).mismatches)


# ── review round 2: cap and ownership mutation targets ──

def test_notional_cap_alone_binds(env, monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_SLEEVE_EQUITY", 100_000.0)  # MAX_SYMBOL_PCT * sleeve = $10k > MAX_ORDER_NOTIONAL
    monkeypatch.setattr(config, "MAX_ORDER_NOTIONAL", 500.0)
    _qty, _dist, caps = execution._size_buy(mk(), 1.0, [], env.conn)
    assert min(caps, key=caps.get) == "order_notional"
    d = go(env)
    assert d.status == "submitted" and d.qty == 5  # 500 / 100; every other cap allows far more


def test_cancel_open_entries_leaves_a_foreign_buy_alone(env):
    env.fake.orders.append(SimpleNamespace(id="f1", client_order_id="manual-buy", symbol="MSFT", qty="3", side="buy",
                                           status="new", legs=None))
    assert execution.cancel_open_entries(env.broker, env.conn) == []
    assert env.fake.calls_to("cancel_order_by_id") == []
    assert [o.id for o in env.fake.orders] == ["f1"]
