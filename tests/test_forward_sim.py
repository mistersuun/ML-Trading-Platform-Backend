"""Forward paper test pieces (offline): external bar source, simulated broker, selection and safety."""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import execution
from brokers.alpaca import AlpacaBroker
from brokers.sim import SimAPIError, SimBroker
import external_bars
from data import adapters
from execution import OrderIntent, submit_intent
from risk_manager import RiskManager
from state import db

ROOT = Path(__file__).resolve().parent.parent


# ── helpers ──────────────────────────────────────────────────

def ibkr_json(rows, tz_hour=13, minute=30):
    """rows: [(YYYY-MM-DD, o, h, l, c, v)] -> raw IBKR columnar payload (session-open stamps, with metadata)."""
    return {"chart_step": 86400, "source": "Last", "expires": "x",
            "time": [f"{d}T{tz_hour}:{minute}:00Z" for d, *_ in rows],
            "open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
            "close": [r[4] for r in rows], "volume": [r[5] for r in rows]}


def frame(rows):
    df = pd.DataFrame([r[1:] for r in rows], columns=external_bars.OHLCV, index=pd.DatetimeIndex([r[0] for r in rows]))
    return df.astype(float)


def req(symbol="AAA", qty=10, side="buy", cid="c1", stop=None, tp=None, kind="market"):
    """An alpaca-py-shaped request object (class name drives the order type, like the real request classes)."""
    cls = {"market": "MarketOrderRequest", "limit": "LimitOrderRequest", "stop": "StopOrderRequest"}[kind]
    ns = type(cls, (), {})()
    ns.symbol, ns.qty, ns.side, ns.client_order_id = symbol, qty, SimpleNamespace(value=side), cid
    ns.time_in_force = SimpleNamespace(value="day")
    ns.limit_price = ns.stop_price = None
    ns.order_class = None
    if stop is not None and tp is not None:
        ns.order_class = SimpleNamespace(value="bracket")
        ns.take_profit = SimpleNamespace(limit_price=tp)
        ns.stop_loss = SimpleNamespace(stop_price=stop)
    return ns


def horizon_broker(path, df_by_symbol, **kw):
    """A SimBroker whose bar feed only shows data up to the day it was advanced to (what the real feed looks like on
    the evening of that session); `go(b, day)` moves the horizon and advances."""
    hz = {"d": None}

    def feed(sym):
        df = df_by_symbol.get(sym)
        if df is None:
            return pd.DataFrame()
        return df if hz["d"] is None else df[df.index <= pd.Timestamp(hz["d"])]
    b = SimBroker(path=path, bars=feed, **kw)
    b._hz = hz
    return b


def go(b, day):
    b._hz["d"] = day
    b._bar_cache.clear()
    return b.advance_to(day)


@pytest.fixture
def mk(tmp_path):
    n = [0]

    def make(rows, slippage=0.0, cash=100_000.0, symbol="AAA"):
        n[0] += 1
        return horizon_broker(tmp_path / f"sim{n[0]}.json", {symbol: frame(rows)}, start_cash=cash, slippage_bps=slippage)
    return make


# ── external source: parsing / merge / calendar ──────────────

def test_session_open_stamp_becomes_session_date_summer_and_winter():
    summer = external_bars.parse_payload(ibkr_json([("2024-06-03", 1, 2, 1, 2, 5)], 13, 30))
    winter = external_bars.parse_payload(ibkr_json([("2024-01-03", 1, 2, 1, 2, 5)], 14, 30))
    assert list(summer.index) == [pd.Timestamp("2024-06-03")] and list(winter.index) == [pd.Timestamp("2024-01-03")]
    assert summer.index.tz is None and list(summer.columns) == external_bars.OHLCV


def test_parse_rejects_bad_payloads():
    with pytest.raises(external_bars.ExternalBarsError):
        external_bars.parse_payload({"time": ["2024-06-03T13:30:00Z"], "open": [1]})
    bad = ibkr_json([("2024-06-03", 1, 2, 1, 2, 5)])
    bad["close"] = [1, 2]
    with pytest.raises(external_bars.ExternalBarsError):
        external_bars.parse_payload(bad)


def test_merge_topups_later_file_wins_and_dedups(tmp_path):
    (tmp_path / "XYZ.json").write_text(json.dumps(ibkr_json([("2024-06-03", 10, 11, 9, 10.5, 1), ("2024-06-04", 10, 11, 9, 10.6, 1)])))
    (tmp_path / "XYZ.20240606.json").write_text(json.dumps(ibkr_json([("2024-06-04", 10, 11, 9, 10.9, 1), ("2024-06-05", 10, 11, 9, 10.7, 1)])))
    (tmp_path / "XYZW.json").write_text("{}")   # a different symbol must not be picked up
    df = external_bars.load_bars("XYZ", root=tmp_path)
    assert [d.date().isoformat() for d in df.index] == ["2024-06-03", "2024-06-04", "2024-06-05"]
    assert df.loc["2024-06-04", "Close"] == 10.9


def test_repair_widens_high_low_but_check_rejects_large_inconsistency():
    df = external_bars.parse_payload(ibkr_json([("2024-06-03", 10, 10.5, 9.5, 10.52, 1)]))
    fixed, n = external_bars.repair_ohlc(df)
    assert n == 1 and fixed["High"].iloc[0] == 10.52 and external_bars.check_frame(df) == []
    big = external_bars.parse_payload(ibkr_json([("2024-06-03", 10, 10.5, 9.5, 20.0, 1)]))
    assert external_bars.check_frame(big)


def test_external_source_through_fetch_bars_keeps_calendar_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXTERNAL_BARS_DIR", str(tmp_path))
    sessions = pd.bdate_range("2024-05-01", "2024-06-07")
    sessions = [d for d in sessions if d.date().isoformat() not in ("2024-05-27",)]       # Memorial Day
    rows = [(d.date().isoformat(), 100 + i, 101 + i, 99 + i, 100.5 + i, 1000) for i, d in enumerate(sessions)]
    (tmp_path / "SPY.json").write_text(json.dumps(ibkr_json(rows)))
    now = datetime(2024, 6, 8, 12, 0, tzinfo=timezone.utc)
    b = adapters.fetch_bars("SPY", 400, "1d", None, ["external"], now, pin=False)
    assert b.source == "external" and b.df.index[-1] == pd.Timestamp("2024-06-07") and b.quality.ok
    assert pd.Timestamp("2024-05-27") not in b.df.index
    # a bar on a non-session day (Saturday) is dropped by the calendar check
    rows2 = rows + [("2024-06-08", 1, 2, 1, 2, 1)]
    (tmp_path / "SPY.json").write_text(json.dumps(ibkr_json(rows2)))
    b2 = adapters.fetch_bars("SPY", 400, "1d", None, ["external"], now, pin=False)
    assert pd.Timestamp("2024-06-08") not in b2.df.index
    with pytest.raises(Exception):                                   # missing symbol: no data, never a network fallback
        adapters.fetch_bars("NOPE", 400, "1d", None, ["external"], now, pin=False)


def test_external_is_supported_and_forward_mode_makes_it_the_only_source(monkeypatch):
    assert "external" in adapters.SUPPORTED_SOURCES
    for name, val in (("DATA_SOURCE_PRIORITY", ["alpaca", "yfinance"]), ("FORWARD_TEST", False), ("PAPER_BROKER", "alpaca"),
                      ("TRADING_MODE", "off"), ("PAPER_TRADE_ENABLED", False), ("IBKR_SYNC_ENABLED", True),
                      ("ALERT_METHOD", "telegram"), ("STATE_DB_PATH", "x")):
        monkeypatch.setattr(config, name, val)      # enable_forward_test mutates globals: monkeypatch restores them
    monkeypatch.delenv("STATE_DB_PATH", raising=False)
    config.enable_forward_test()
    assert config.DATA_SOURCE_PRIORITY == ["external"] and config.PAPER_BROKER == "sim"
    assert config.TRADING_MODE == "off" and not config.PAPER_TRADE_ENABLED and not config.IBKR_SYNC_ENABLED   # only step() turns it on
    assert config.STATE_DB_PATH.endswith("state/forward/trading.db")


def test_ingest_script_validates_and_topup(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest_ibkr_bars as ing
    good = json.dumps(ibkr_json([("2024-06-03", 10, 11, 9, 10.5, 1)]))
    p = ing.ingest("ABC", good, root=tmp_path)
    assert p.name == "ABC.json"
    t = ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-04", 10, 11, 9, 10.5, 1)])), topup=True, root=tmp_path)
    assert t.name == "ABC.20240604.json" and len(external_bars.load_bars("ABC", root=tmp_path)) == 2
    assert "expires" not in json.loads(p.read_text())
    with pytest.raises(external_bars.ExternalBarsError):
        ing.ingest("ABC", "not json", root=tmp_path)
    with pytest.raises(external_bars.ExternalBarsError):
        ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-03", -1, 11, 9, 10.5, 1)])), root=tmp_path)


# ── sim broker fills ─────────────────────────────────────────

ROWS = [("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 102, 103, 101, 102.5, 1),
        ("2024-06-05", 102, 104, 100, 103, 1), ("2024-06-06", 103, 104, 102, 103, 1)]


def test_market_order_fills_at_next_open_not_instantly(mk):
    b = mk(ROWS, slippage=5)
    go(b, "2024-06-03")
    o = b.submit_order(req(qty=10))
    assert o.status == "accepted" and b.get_all_positions() == []
    assert go(b, "2024-06-03")["fills"] == []                      # same session: nothing
    r = go(b, "2024-06-04")
    fill = r["fills"][0]
    assert fill["date"] == "2024-06-04" and fill["price"] == pytest.approx(102 * 1.0005, abs=1e-4)
    acct = b.get_account()
    assert float(acct.cash) == pytest.approx(100_000 - fill["price"] * 10, abs=0.01)
    pos = b.get_all_positions()[0]
    assert pos.symbol == "AAA" and float(pos.qty) == 10 and float(pos.current_price) == 102.5   # marked to the close


def test_gap_through_stop_fills_at_open(mk):
    rows = [("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100, 101, 99, 100, 1),
            ("2024-06-05", 90, 92, 88, 91, 1)]                                         # gaps below the 95 stop
    b = mk(rows)
    go(b, "2024-06-03")
    b.submit_order(req(qty=10, stop=95, tp=110))
    r = go(b, "2024-06-05")
    exit_fill = [f for f in r["fills"] if f["leg"]][0]
    assert exit_fill["type"] == "stop" and exit_fill["price"] == 90                  # open, not the 95 stop
    assert b.get_all_positions() == []
    assert not [o for o in b.get_orders()]                                            # the target leg was cancelled (OCO)


def test_stop_wins_when_both_touched_on_one_bar(mk):
    rows = [("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100, 101, 99, 100, 1),
            ("2024-06-05", 100, 112, 94, 100, 1)]                                      # touches 95 and 110
    b = mk(rows)
    go(b, "2024-06-03")
    b.submit_order(req(qty=5, stop=95, tp=110))
    r = go(b, "2024-06-05")
    leg = [f for f in r["fills"] if f["leg"]]
    assert len(leg) == 1 and leg[0]["type"] == "stop" and leg[0]["price"] == 95


def test_take_profit_fills_at_limit_and_entry_bar_can_stop_out(mk):
    b = mk([("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100, 111, 99.5, 110, 1)])
    go(b, "2024-06-03")
    b.submit_order(req(qty=5, stop=95, tp=110))
    r = go(b, "2024-06-04")
    assert [(f["type"], f["price"]) for f in r["fills"] if f["leg"]] == [("limit", 110)]
    c = mk([("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100, 101, 90, 92, 1)])
    go(c, "2024-06-03")
    c.submit_order(req(qty=5, stop=95, tp=110))
    assert [f["price"] for f in go(c, "2024-06-04")["fills"] if f["leg"]] == [95]       # stopped out on the entry bar


def test_persistence_round_trip_and_second_instance_sees_state(tmp_path):
    df = frame(ROWS)
    a = horizon_broker(tmp_path / "s.json", {"AAA": df}, start_cash=50_000, slippage_bps=0)
    go(a, "2024-06-03")
    a.submit_order(req(qty=3, stop=90, tp=120))
    go(a, "2024-06-04")
    before = (a.get_account().equity, [p.symbol for p in a.get_all_positions()], len(a.get_orders()))
    b = horizon_broker(tmp_path / "s.json", {"AAA": df}, start_cash=1)                 # restart: file wins over args
    assert (b.get_account().equity, [p.symbol for p in b.get_all_positions()], len(b.get_orders())) == before
    assert b.last_date == "2024-06-04" and b.s["start_cash"] == 50_000
    assert not list(tmp_path.glob("*.tmp*"))                                           # atomic write left no temp file
    with pytest.raises(SimAPIError) as e:
        b.submit_order(req(qty=3, stop=90, tp=120))                                    # duplicate client_order_id
    assert e.value.status_code == 422


def test_gap_above_target_fills_at_open_not_below_it(mk):
    b = mk([("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100.5, 101, 99.8, 100.6, 1), ("2024-06-05", 120, 125, 119, 124, 1)])
    go(b, "2024-06-03")
    b.submit_order(req(qty=5, stop=95, tp=110))
    assert [f["price"] for f in go(b, "2024-06-05")["fills"] if f["leg"]] == [120]


def test_cancel_and_lookup_semantics(mk):
    b = mk(ROWS)
    go(b, "2024-06-03")
    o = b.submit_order(req(qty=2, cid="k"))
    assert b.get_order_by_client_id("k").id == o.id
    b.cancel_order_by_id(o.id)
    assert b.get_orders() == []
    with pytest.raises(SimAPIError) as e:
        b.cancel_order_by_id("nope")
    assert e.value.status_code == 404
    with pytest.raises(SimAPIError) as e2:
        b.get_order_by_client_id("missing")
    assert e2.value.status_code == 404
    assert go(b, "2024-06-04")["fills"] == [] and b.get_all_positions() == []


def test_close_position_sells_at_next_open_and_voids_legs(mk):
    b = mk(ROWS)
    go(b, "2024-06-03")
    b.submit_order(req(qty=4, stop=90, tp=130))
    go(b, "2024-06-04")
    b.close_position("AAA")
    r = go(b, "2024-06-05")
    assert [(f["side"], f["price"]) for f in r["fills"]] == [("sell", 102)] and b.get_all_positions() == []


# ── end to end through execution.submit_intent ───────────────

def test_bracket_via_submit_intent_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    conn = db.connect()
    db.migrate(conn)
    rows = [("2024-06-03", 100, 101, 99, 100, 1), ("2024-06-04", 100, 101, 99.5, 100.5, 1),
            ("2024-06-05", 100, 101, 80, 85, 1)]
    df = frame(rows)
    sim = horizon_broker(tmp_path / "s.json", {"AAPL": df}, slippage_bps=0)
    broker = AlpacaBroker(sim)
    go(sim, "2024-06-03")
    rm = RiskManager(conn)
    rm.update_equity(config.SIGNAL_SLEEVE_EQUITY, datetime(2024, 6, 3, 21, tzinfo=timezone.utc))
    intent = OrderIntent(symbol="AAPL", direction=1, signal_bar_date="2024-06-03", strategy_key="t", confidence=1.0,
                         validation_status="deflated_validated", atr=2.0, price=100.0)
    d = submit_intent(intent, broker=broker, conn=conn, now=datetime(2024, 6, 3, 21, tzinfo=timezone.utc), risk_manager=rm)
    assert d.status == "submitted" and d.qty and d.stop_price and d.limit_price
    assert execution.reconcile(broker, conn).ok                                       # pending bracket: legs 'held'
    go(sim, "2024-06-04")                                                              # entry fills at the open
    assert [p.symbol for p in sim.get_all_positions()] == ["AAPL"]
    rec = execution.reconcile(broker, conn)
    assert rec.ok, rec.mismatches                                                      # filled parent, protective legs open
    go(sim, "2024-06-05")                                                              # gap down through the stop
    assert sim.get_all_positions() == []
    assert execution.reconcile(broker, conn).ok
    conn.close()


# ── selection and safety ─────────────────────────────────────

def test_paper_broker_selection(monkeypatch, tmp_path):
    from services import session
    monkeypatch.setattr(config, "SIM_BROKER_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(config, "PAPER_BROKER", "sim")
    b = session.get_broker()
    assert isinstance(b, AlpacaBroker) and isinstance(b.client, SimBroker)
    assert isinstance(execution.default_broker().client, SimBroker)
    monkeypatch.setattr(config, "PAPER_BROKER", "alpaca")
    built = {}
    monkeypatch.setattr(execution, "AlpacaBroker", lambda *a, **k: built.setdefault("x", SimpleNamespace(client=None)))
    assert session.get_broker() is built["x"]
    monkeypatch.setattr(config, "FORWARD_TEST", True)                                  # forward test never builds Alpaca
    with pytest.raises(RuntimeError):
        execution.default_broker()


def _local_import_closure(start: Path) -> set:
    seen, todo, mods = set(), [start], set()
    while todo:
        p = todo.pop()
        if p in seen or not p.exists():
            continue
        seen.add(p)
        for n in ast.walk(ast.parse(p.read_text())):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
                    ([n.module] + [f"{n.module}.{a.name}" for a in n.names]) if isinstance(n, ast.ImportFrom) and n.module else []
            for name in names:
                mods.add(name.split(".")[0])
                cand = ROOT / (name.replace(".", "/") + ".py")
                if cand.exists():
                    todo.append(cand)
                pkg = ROOT / name.replace(".", "/") / "__init__.py"
                if pkg.exists():
                    todo.append(pkg)
    return mods


def test_sim_broker_imports_no_network_library():
    banned = {"requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "socket", "ssl", "websocket", "websockets",
              "yfinance", "alpaca", "ib_insync", "ibapi", "smtplib", "ftplib", "telnetlib", "tenacity", "websocket_client"}
    mods = _local_import_closure(ROOT / "brokers" / "sim.py")
    assert not (mods & banned), mods & banned


def test_live_mode_still_impossible():
    src = (ROOT / "brokers" / "alpaca.py").read_text()
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "TradingClient"]
    assert calls and all(any(k.arg == "paper" and getattr(k.value, "value", None) is True for k in c.keywords) for c in calls)
    for path in (ROOT / "brokers" / "sim.py", ROOT / "services" / "forward.py", ROOT / "config.py"):
        assert "paper=False" not in path.read_text() and 'TRADING_MODE = "live"' not in path.read_text()
    assert config.TRADING_MODE in ("off", "paper")
    assert config.PAPER_BROKER in ("alpaca", "sim")


# ── forward runner: step / journal / shadow scoring / report ──

def _write_bars(d, sym, start, n, base):
    days = pd.bdate_range(start, periods=n)
    rows = [(x.date().isoformat(), base + i, base + i + 1, base + i - 1, base + i + 0.5, 1000) for i, x in enumerate(days)]
    (d / f"{sym}.json").write_text(json.dumps(ibkr_json(rows)))
    return [r[0] for r in rows]


def test_forward_step_end_to_end_journal_fill_shadow_and_report(monkeypatch, tmp_path):
    from services import forward, scan, session
    for name, val in (("DATA_SOURCE_PRIORITY", ["x"]), ("FORWARD_TEST", False), ("PAPER_BROKER", "alpaca"),
                      ("TRADING_MODE", "off"), ("PAPER_TRADE_ENABLED", False), ("IBKR_SYNC_ENABLED", False),
                      ("ALERT_METHOD", "console")):
        monkeypatch.setattr(config, name, val)
    bars = tmp_path / "bars"
    bars.mkdir()
    monkeypatch.setattr(config, "EXTERNAL_BARS_DIR", str(bars))
    monkeypatch.setattr(config, "SIM_BROKER_PATH", str(tmp_path / "sim.json"))
    monkeypatch.setattr(config, "FORWARD_JOURNAL_DIR", str(tmp_path / "j"))
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "fw.db"))
    monkeypatch.setenv("STATE_DB_PATH", "set")          # enable_forward_test keeps the monkeypatched path
    monkeypatch.setitem(config.WATCHLIST, "stocks", ["SPY", "AAPL", "MSFT"])      # the only symbols with bar files here
    dates = _write_bars(bars, "SPY", "2024-04-01", 40, 400)
    _write_bars(bars, "AAPL", "2024-04-01", 40, 100)
    _write_bars(bars, "MSFT", "2024-04-01", 40, 300)
    conn = db.connect()
    db.migrate(conn)
    conn.close()

    fake_results = {"technical": [{"symbol": "MSFT", "pattern": "p1", "direction": "🟢 BUY", "validation_status": "unvalidated",
                                   "signal_bar_date": dates[-1]}], "ml": [], "pairs": [], "technical_candidates": [],
                    "technical_funnel": {"tested": 3, "min_trades": 1, "oos_positive": 1, "psr": 0, "bh": 0, "dsr": 0, "orders": 0}}

    def fake_scan(modes=None, run_stress=False, paper_trade=False, **kw):
        assert paper_trade is True                      # the forward runner routes intents to the sim
        sess = session.open_session()
        assert sess is not None and isinstance(sess.broker.client, SimBroker)
        intent = OrderIntent(symbol="AAPL", direction=1, signal_bar_date=dates[-1], strategy_key="t", confidence=1.0,
                             validation_status="deflated_validated", atr=2.0, price=float(_last_close(bars, "AAPL")))
        res = {**fake_results, "decisions": session.dispatch_intents([intent], session=sess)}
        sess.conn.close()
        return res

    monkeypatch.setattr(scan, "run_full_scan", fake_scan)
    e1 = forward.step(modes=("technical",), run_stress=False, now=_eod(dates[-1]))
    assert e1["status"] == "ok" and e1["mode"]["paper_broker"] == "sim" and e1["mode"]["trading_mode"] == "paper"
    assert [d["status"] for d in e1["decisions"]] == ["submitted"] and e1["decisions"][0]["qty"]
    assert e1["reconciliation"]["ok"] and e1["risk"]["allowed"]
    assert e1["positions"] == [] and e1["open_orders"], "the order fills at the NEXT open, not instantly"
    assert [s["symbol"] for s in e1["shadow_new"]] == ["MSFT"]
    assert (tmp_path / "j" / "days" / f"{dates[-1]}.md").exists() and (tmp_path / "j" / "snapshot" / "sim_broker.json").exists()

    # next session arrives (top-up file): the order fills at its open, the shadow item gets a 1-day score
    nxt = "2024-05-28"                                   # dates[-1] is Fri 05-24; Mon 05-27 is Memorial Day
    (bars / f"AAPL.{nxt.replace('-', '')}.json").write_text(json.dumps(ibkr_json([(nxt, 139.6, 140, 139.0, 139.8, 1)])))
    (bars / f"SPY.{nxt.replace('-', '')}.json").write_text(json.dumps(ibkr_json([(nxt, 440, 441, 439, 440.5, 1)])))
    (bars / f"MSFT.{nxt.replace('-', '')}.json").write_text(json.dumps(ibkr_json([(nxt, 345, 346, 344, 345.5, 1)])))
    e2 = forward.step(modes=("technical",), run_stress=False, now=_eod(nxt))
    assert e2["date"] == nxt and [f["symbol"] for f in e2["sim_advance"]["fills"]] == ["AAPL"]
    assert e2["sim_advance"]["fills"][0]["price"] == pytest.approx(139.6 * 1.0005, abs=1e-3)
    assert [p["symbol"] for p in e2["positions"]] == ["AAPL"] and e2["reconciliation"]["ok"], e2["reconciliation"]
    assert any(s["type"] == "shadow_score" for s in forward.read_journal())
    txt = forward.report()
    assert "Days journaled: 2" in txt and "Shadow" in txt
    crash = forward.step(date="2030-01-01", modes=("technical",), run_stress=False, now=_eod(nxt))
    assert crash["status"] == "crash" and "latest session" in crash["errors"][0]


def _eod(day):
    return datetime.fromisoformat(day).replace(hour=23, tzinfo=timezone.utc)


def _last_close(d, sym):
    return external_bars.load_bars(sym, root=d)["Close"].iloc[-1]


# ── review fixes: closed bars, clock guards, idempotent reruns, state snapshot, ingest ──

@pytest.fixture
def fw(monkeypatch, tmp_path):
    """An isolated forward-test setup with SPY/AAPL/MSFT as the whole required watchlist and a fake scan that
    reports one non-eligible MSFT signal (-> one shadow item per night)."""
    from services import forward, scan
    for name, val in (("DATA_SOURCE_PRIORITY", ["x"]), ("FORWARD_TEST", False), ("PAPER_BROKER", "alpaca"),
                      ("TRADING_MODE", "off"), ("PAPER_TRADE_ENABLED", False), ("IBKR_SYNC_ENABLED", False),
                      ("ALERT_METHOD", "console")):
        monkeypatch.setattr(config, name, val)
    bars = tmp_path / "bars"
    bars.mkdir()
    monkeypatch.setattr(config, "EXTERNAL_BARS_DIR", str(bars))
    monkeypatch.setattr(config, "SIM_BROKER_PATH", str(tmp_path / "sim.json"))
    monkeypatch.setattr(config, "FORWARD_JOURNAL_DIR", str(tmp_path / "j"))
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "fw.db"))
    monkeypatch.setenv("STATE_DB_PATH", "set")
    monkeypatch.setitem(config.WATCHLIST, "stocks", ["SPY", "AAPL", "MSFT"])
    dates = [_write_bars(bars, s, "2024-04-01", 40, b) for s, b in (("SPY", 400), ("AAPL", 100), ("MSFT", 300))][0]
    conn = db.connect()
    db.migrate(conn)
    conn.close()

    def fake_scan(modes=None, run_stress=False, paper_trade=False, **kw):
        return {"technical": [{"symbol": "MSFT", "pattern": "p1", "direction": "🟢 BUY", "validation_status": "unvalidated",
                               "signal_bar_date": dates[-1]}], "ml": [], "pairs": [], "technical_candidates": [],
                "decisions": [], "technical_funnel": {}}
    monkeypatch.setattr(scan, "run_full_scan", fake_scan)

    def topup(day, rows_by_sym=None):
        for sym, px in (("SPY", 440), ("AAPL", 140), ("MSFT", 345)):
            (bars / f"{sym}.{day.replace('-', '')}.json").write_text(
                json.dumps(ibkr_json([(day, px, px + 1, px - 1, px + 0.5, 1)])))
    return SimpleNamespace(forward=forward, bars=bars, dates=dates, topup=topup, tmp=tmp_path)


def test_closed_bars_drop_unfinished_and_non_session_dates(tmp_path):
    rows = [("2024-06-03", 10, 11, 9, 10, 1), ("2024-06-04", 10, 11, 9, 10, 1), ("2024-06-05", 10, 11, 9, 10, 1),
            ("2024-06-08", 10, 11, 9, 10, 1)]                                           # a Saturday
    (tmp_path / "ZZ.json").write_text(json.dumps(ibkr_json(rows)))
    before_close = datetime(2024, 6, 5, 15, 0, tzinfo=timezone.utc)                    # 11:00 ET, session in progress
    after_close = datetime(2024, 6, 5, 22, 0, tzinfo=timezone.utc)
    days = lambda now: [d.date().isoformat() for d in external_bars.closed_bars("ZZ", now, root=tmp_path).index]
    assert days(before_close) == ["2024-06-03", "2024-06-04"]
    assert days(after_close) == ["2024-06-03", "2024-06-04", "2024-06-05"]             # Saturday never
    assert days(datetime(2024, 6, 9, 12, 0, tzinfo=timezone.utc)) == ["2024-06-03", "2024-06-04", "2024-06-05"]


def test_step_uses_previous_session_when_todays_bar_is_unfinished(fw):
    fw.topup("2024-05-28")                                    # the in-progress bar of "today"
    morning = datetime(2024, 5, 28, 15, 0, tzinfo=timezone.utc)     # open, not closed
    assert fw.forward.latest_session(morning).date().isoformat() == fw.dates[-1]
    e = fw.forward.step(modes=("technical",), run_stress=False, now=morning)
    assert e["status"] == "ok" and e["date"] == fw.dates[-1]
    assert e["spy"]["close"] == pytest.approx(external_bars.load_bars("SPY", root=fw.bars)["Close"].loc[fw.dates[-1]])
    assert all(r["last_bar"] == fw.dates[-1] for r in e["freshness"])
    assert [s["entry_price"] for s in e["shadow_new"] if s["symbol"] == "MSFT"] == [pytest.approx(339.5)]
    crash = fw.forward.step(date="2024-05-28", modes=("technical",), run_stress=False, now=morning, rerun=True)
    assert crash["status"] == "crash" and "latest session" in crash["errors"][0]


def test_sim_refuses_orders_on_a_fresh_or_stale_clock(tmp_path):
    df = frame(ROWS)
    fresh = SimBroker(path=tmp_path / "a.json", slippage_bps=0, bars=lambda s: df)
    with pytest.raises(SimAPIError) as e:
        fresh.submit_order(req(qty=1))                       # last_date None: would fill at the first bar of the file
    assert e.value.status_code == 409
    stale = SimBroker(path=tmp_path / "b.json", slippage_bps=0, bars=lambda s: df)
    stale.advance_to("2024-06-03")                           # the data already runs to 06-06
    with pytest.raises(SimAPIError) as e2:
        stale.submit_order(req(qty=1, cid="c2"))
    assert e2.value.status_code == 409 and "stale" in str(e2.value)
    stale.advance_to("2024-06-06")
    assert stale.submit_order(req(qty=1, cid="c3")).status == "accepted"


def test_forward_test_flag_alone_never_enables_trading(monkeypatch):
    for name, val in (("TRADING_MODE", "paper"), ("PAPER_TRADE_ENABLED", True)):
        monkeypatch.setattr(config, name, val)
    monkeypatch.setattr(config, "STATE_DB_PATH", "x")
    config.enable_forward_test()
    assert config.TRADING_MODE == "off" and config.PAPER_TRADE_ENABLED is False


def test_step_turns_paper_on_only_for_its_own_duration(fw, monkeypatch):
    seen = {}
    from services import scan
    orig = scan.run_full_scan

    def spy(**kw):
        seen["mode"] = (config.TRADING_MODE, config.PAPER_TRADE_ENABLED)
        return orig(**kw)
    monkeypatch.setattr(scan, "run_full_scan", spy)
    fw.forward.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    assert seen["mode"] == ("paper", True) and (config.TRADING_MODE, config.PAPER_TRADE_ENABLED) == ("off", False)


def test_sim_advance_refuses_to_skip_a_session_missing_for_a_held_symbol(tmp_path):
    full = frame(ROWS)
    gap = full.drop(pd.Timestamp("2024-06-05"))
    hz = {"df": full.iloc[:2]}
    b = SimBroker(path=tmp_path / "s.json", slippage_bps=0, bars=lambda s: hz["df"])
    b.advance_to("2024-06-03")
    b._bar_cache.clear()
    b.advance_to("2024-06-04")  # fresh clock is needed first: submit after catching up
    b.submit_order(req(qty=2))
    hz["df"] = gap.iloc[:3]        # 06-03, 06-04, 06-06 (06-05 late)
    b._bar_cache.clear()
    with pytest.raises(SimAPIError) as e:
        b.advance_to("2024-06-06")
    assert e.value.status_code == 409 and "2024-06-05" in str(e.value) and b.last_date == "2024-06-04"
    hz["df"] = full
    b._bar_cache.clear()
    assert b.advance_to("2024-06-06")["sessions"] == ["2024-06-05", "2024-06-06"]       # the late bar is not lost


def test_same_date_rerun_is_refused_then_replaces_the_day_and_scores_once(fw):
    f = fw.forward
    e1 = f.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    again = f.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    assert again["status"] == "crash" and "already journaled" in again["errors"][0]
    e1b = f.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]), rerun=True)
    assert e1b["rerun"] is True and e1b["status"] == "ok"
    fw.topup("2024-05-28")
    e2 = f.step(modes=("technical",), run_stress=False, now=_eod("2024-05-28"))
    j = f.read_journal()
    assert [e["date"] for e in j if e["type"] == "day"] == [fw.dates[-1], "2024-05-28"]
    scores = [e for e in j if e["type"] == "shadow_score"]
    assert len({(s["id"], s["horizon"]) for s in scores}) == len(scores) == 1     # only the 1d horizon exists yet
    s = scores[0]                                                                   # value check: exit is the close of D+1
    msft = external_bars.load_bars("MSFT", root=fw.bars)
    assert s["exit_price"] == msft["Close"].loc["2024-05-28"] and s["exit_date"] == "2024-05-28"
    assert s["entry_price"] == msft["Close"].loc[fw.dates[-1]] and s["entry_open"] == msft["Open"].loc["2024-05-28"]
    assert s["fwd_return"] == pytest.approx(s["exit_price"] / s["entry_price"] - 1, abs=1e-6)
    assert s["signed_return"] == pytest.approx(s["exit_price"] / s["entry_open"] - 1, abs=1e-6)   # executable basis
    txt = f.report()
    assert "Days journaled: 2" in txt and txt.count(f"| {fw.dates[-1]} |") == 1
    n_items = len({i["id"] for e in j if e["type"] == "day" for i in e["shadow_new"] if i["entry_price"]})
    assert f"Shadow score slots still pending: {n_items * 2 - 1}" in txt              # unique items x 2 horizons - 1 scored
    assert e2["status"] == "ok"


def test_score_shadow_dedups_ids_and_skips_stale_entries(fw):
    f = fw.forward
    item = {"id": "i1", "date": fw.dates[-2], "symbol": "MSFT", "kind": "technical", "direction": 1,
            "reason": "alert_only:x", "entry_price": 100.0}
    journal = [{"type": "day", "date": fw.dates[-2], "shadow_new": [item]},
               {"type": "day", "date": fw.dates[-2], "shadow_new": [item]}]        # the same item journaled twice
    rows = f.score_shadow(journal, "t", _eod(fw.dates[-1]))
    assert sorted(r["horizon"] for r in rows) == [1]                                # one row per (id, horizon)
    stale = {**item, "id": "i2", "entry_price": None}                               # symbol not current on its signal day
    assert f.score_shadow([{"type": "day", "date": "d", "shadow_new": [stale]}], "t", _eod(fw.dates[-1])) == []


def test_missing_required_symbol_degrades_the_step_and_is_not_called_optional(fw, monkeypatch):
    monkeypatch.setitem(config.WATCHLIST, "stocks", ["SPY", "AAPL", "MSFT", "ZZZ"])      # ZZZ is required, has no file
    e = fw.forward.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    assert e["status"] == "degraded" and any("ZZZ=missing" in x for x in e["errors"])
    cap = fw.forward._Capture()
    rec = type("R", (), {"getMessage": lambda s: "No usable data for ZZZ: x", "name": "n", "levelname": "WARNING"})()
    cap.emit(rec)
    assert not cap.missing and cap.items                                                 # kept as a warning, not collapsed
    rec2 = type("R", (), {"getMessage": lambda s: "No usable data for BTC-USD: x", "name": "n", "levelname": "WARNING"})()
    cap.emit(rec2)
    assert cap.missing == ["BTC-USD"]


def test_crash_is_journaled_and_reported(fw):
    c = fw.forward.step(date="2030-01-01", modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    assert c["status"] == "crash"
    assert [e["type"] for e in fw.forward.read_journal()] == ["crash"]
    assert "## Crashes" in fw.forward.report() and "latest session" in fw.forward.report()


def test_snapshot_restore_round_trip_includes_trials_and_holdout_and_is_all_or_nothing(fw):
    import sqlite3
    from services import trial_runs
    from state import holdout
    f = fw.forward
    f.step(modes=("technical",), run_stress=False, now=_eod(fw.dates[-1]))
    for path, val in ((trial_runs.trials_db_path(), "t1"), (holdout.default_path(), "h1")):
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE IF NOT EXISTS marker (v TEXT)")
        c.execute("INSERT INTO marker VALUES (?)", (val,))
        c.commit()
        c.close()
    f.snapshot()
    snap = f.journal_dir() / "snapshot"
    assert {p.name for p in snap.iterdir()} >= {"sim_broker.json", "trading.db", "trials.sqlite", "holdout_reads.sqlite"}
    for _, target, _ in f._state_files():
        target.unlink()
    assert len(f.restore()) == 4
    assert sqlite3.connect(trial_runs.trials_db_path()).execute("SELECT v FROM marker").fetchone() == ("t1",)
    assert sqlite3.connect(holdout.default_path()).execute("SELECT v FROM marker").fetchone() == ("h1",)
    Path(trial_runs.trials_db_path()).unlink()                                      # partially present: refuse, change nothing
    with pytest.raises(RuntimeError):
        f.restore()
    assert not Path(trial_runs.trials_db_path()).exists()
    assert len(f.restore(force=True)) == 4 and Path(trial_runs.trials_db_path()).exists()


def test_paper_trader_broker_follows_paper_broker_setting(monkeypatch, tmp_path):
    import paper_trader
    monkeypatch.setattr(config, "SIM_BROKER_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(config, "PAPER_BROKER", "sim")
    monkeypatch.setattr(paper_trader, "_client", None)
    assert isinstance(paper_trader._get_broker().client, SimBroker)
    monkeypatch.setattr(config, "PAPER_BROKER", "alpaca")
    monkeypatch.setattr(config, "FORWARD_TEST", True)
    with pytest.raises(RuntimeError):                       # a forward-test process never builds Alpaca here either
        paper_trader._get_broker()
    fake = object()
    monkeypatch.setattr(paper_trader, "_client", fake)
    assert paper_trader._get_broker().client is fake


def test_load_bars_rejects_corrupt_file_and_adapter_needs_a_real_history(tmp_path, monkeypatch):
    (tmp_path / "BAD.json").write_text(json.dumps(ibkr_json([("2024-06-03", 10, 10.5, 9.5, 20.0, 1)])))
    with pytest.raises(external_bars.ExternalBarsError):
        external_bars.load_bars("BAD", root=tmp_path)
    monkeypatch.setattr(config, "EXTERNAL_BARS_DIR", str(tmp_path))
    sessions = [d for d in pd.bdate_range("2024-05-28", "2024-06-07")]
    rows = [(d.date().isoformat(), 100, 101, 99, 100.5, 1000) for d in sessions]
    (tmp_path / "TOP.20240607.json").write_text(json.dumps(ibkr_json(rows)))      # a top-up file and no base file
    now = datetime(2024, 6, 8, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(Exception):
        adapters.fetch_bars("TOP", 400, "1d", None, ["external"], now, pin=False)
    (tmp_path / "TOP.json").write_text(json.dumps(ibkr_json(rows)))               # with a base file coverage is judged from it
    assert adapters.fetch_bars("TOP", 400, "1d", None, ["external"], now, pin=False).df.index[-1] == pd.Timestamp("2024-06-07")


def test_ingest_full_history_drops_old_topups_and_topup_must_agree_with_history(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest_ibkr_bars as ing
    ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-03", 10, 11, 9, 10.5, 1), ("2024-06-04", 10, 11, 9, 10.6, 1)])), root=tmp_path)
    with pytest.raises(external_bars.ExternalBarsError):                            # e.g. after a split: overlap disagrees
        ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-04", 5, 5.5, 4.5, 5.3, 1), ("2024-06-05", 5, 5.5, 4.5, 5.3, 1)])),
                   topup=True, root=tmp_path)
    assert not list(tmp_path.glob("ABC.2*.json"))
    ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-04", 10, 11, 9, 10.6, 1), ("2024-06-05", 10, 11, 9, 10.7, 1)])),
               topup=True, root=tmp_path)
    assert len(list(tmp_path.glob("ABC.2*.json"))) == 1
    ing.ingest("ABC", json.dumps(ibkr_json([("2024-06-03", 5, 5.5, 4.5, 5.2, 1), ("2024-06-05", 5, 5.5, 4.5, 5.3, 1)])), root=tmp_path)
    assert not list(tmp_path.glob("ABC.2*.json"))                                  # the old top-up cannot override the new history
    assert external_bars.load_bars("ABC", root=tmp_path)["Close"].iloc[-1] == 5.3
