"""The forward-test PLUMBING TEST trade: one labelled SPY long on the sim broker that exercises the order path.
Offline. It is not a strategy; these tests pin that it is sim-only, goes through every normal gate, is idempotent
and never leaks into strategy / shadow statistics."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import execution
from brokers.alpaca import AlpacaBroker
from brokers.fake import FakeBroker
from execution import OrderIntent, PlumbingTestRefused, build_plumbing_intent, submit_intent
from risk_manager import RiskManager
from state import db
from tests.test_forward_sim import _eod, fw, frame, go, horizon_broker, ibkr_json  # noqa: F401  (fw is a fixture)

NIGHT1 = "2024-05-24"      # last bar of the fixture's bar files (SPY close 439.5)
D2, D3, D4 = "2024-05-28", "2024-05-29", "2024-05-30"


def step(fw, day, **kw):
    return fw.forward.step(modes=("technical",), run_stress=False, now=_eod(day), **kw)


def spy_bar(fw, day, o, h, l, c):
    fw.topup(day)
    (fw.bars / f"SPY.{day.replace('-', '')}.json").write_text(json.dumps(ibkr_json([(day, o, h, l, c, 1)])))


def pt_lines(fw, action=None):
    return [e for e in fw.forward.read_journal() if e["type"] == "plumbing_test" and (action is None or e["action"] == action)]


def buy_rows():
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM orders WHERE side='buy'")]
    finally:
        conn.close()


# ── entry: next-open fill, bracket legs, sizing, reconciliation, marking ───────────────

def test_entry_fills_at_next_open_with_bracket_and_reconciles_while_held(fw):
    e1 = step(fw, NIGHT1, plumbing_test=True)
    assert e1["status"] == "ok" and e1["reconciliation"]["ok"], e1["reconciliation"]
    (ln,) = pt_lines(fw, "entry")
    assert ln["outcome"] == "submitted" and ln["label"] == "PLUMBING TEST — not a strategy"
    d = ln["decision"]
    assert 1 <= d["qty"] <= 3                                                    # tiny: capped by the symbol / order notional
    assert d["stop_price"] == pytest.approx(439.5 * 0.97, abs=0.01)             # 3% below the entry reference
    assert d["limit_price"] > 439.5 and d["status"] == "submitted"             # platform take-profit leg
    assert e1["positions"] == [] and [o["symbol"] for o in e1["open_orders"]] == ["SPY"]    # nothing fills tonight
    assert {l["type"] for l in e1["open_orders"][0]["legs"]} == {"stop", "limit"}
    assert e1["decisions"] == [] and e1["counts"]["intents_decided"] == 0       # not a strategy decision
    assert e1["plumbing_test"]["state"] == "entry_pending"
    assert all(s["symbol"] != "SPY" for s in e1["shadow_new"])
    assert (fw.tmp / "j" / "days" / f"{NIGHT1}.md").read_text().count("PLUMBING TEST — not a strategy") >= 1

    fw.topup(D2)
    e2 = step(fw, D2)                                                            # entry fills at the D2 open (440) + 5 bp
    (fill,) = e2["sim_advance"]["fills"]
    assert fill["symbol"] == "SPY" and fill["price"] == pytest.approx(440 * 1.0005, abs=1e-3) and fill["qty"] == d["qty"]
    assert e2["reconciliation"]["ok"], e2["reconciliation"]                     # open position + live protective legs
    pt = e2["plumbing_test"]
    assert pt["state"] == "open" and pt["position"]["qty"] == d["qty"] and pt["stop"] == d["stop_price"]
    assert e2["positions"][0]["plumbing_test"] is True                           # marked to market, flagged
    assert pt["unrealized_pl"] == pytest.approx(d["qty"] * (440.5 - 440.22), abs=0.02)
    a = e2["account"]
    assert a["plumbing_pl"] == pt["pl"] and a["equity_ex_plumbing"] == pytest.approx(a["equity"] - pt["pl"], abs=0.01)
    day_md = (fw.tmp / "j" / "days" / f"{D2}.md").read_text()
    assert "PLUMBING TEST — not a strategy" in day_md and "[PLUMBING TEST — not a strategy]" in day_md
    assert [l["action"] for l in pt_lines(fw)] == ["entry", "status"]


def test_stop_leg_lifecycle_realises_loss_and_flattens(fw):
    step(fw, NIGHT1, plumbing_test=True)
    fw.topup(D2)
    step(fw, D2)
    spy_bar(fw, D3, 435, 436, 420, 425)                                          # low pierces the ~426.3 stop
    e3 = step(fw, D3)
    (fill,) = [f for f in e3["sim_advance"]["fills"]]
    assert fill["leg"] and fill["type"] == "stop" and fill["price"] == pytest.approx(426.3 * (1 - 0.0005), abs=0.05)
    pt = e3["plumbing_test"]
    assert pt["state"] == "closed" and pt["exit_reason"] == "stop_loss" and pt["realized_pl"] < 0
    assert e3["positions"] == [] and e3["reconciliation"]["ok"] and pt["unrealized_pl"] == 0
    assert e3["open_orders"] == []                                               # the take-profit leg was cancelled (OCO)
    assert pt_lines(fw)[-1]["state"] == "closed"


def test_take_profit_leg_lifecycle(fw):
    step(fw, NIGHT1, plumbing_test=True)
    fw.topup(D2)
    step(fw, D2)
    spy_bar(fw, D3, 445, 470, 444, 468)                                          # high clears the ~459.3 target
    e3 = step(fw, D3)
    pt = e3["plumbing_test"]
    assert pt["state"] == "closed" and pt["exit_reason"] == "take_profit" and pt["realized_pl"] > 0
    assert [f["type"] for f in pt["fills"]] == ["market", "limit"] and e3["reconciliation"]["ok"]


def test_close_at_next_open_on_a_later_night(fw):
    step(fw, NIGHT1, plumbing_test=True)
    fw.topup(D2)
    step(fw, D2)
    fw.topup(D3)
    e3 = step(fw, D3, plumbing_close=True)
    (ln,) = pt_lines(fw, "close")
    assert ln["outcome"] == "submitted" and ln["decision"]["reasons"] == ["cancelled_protective_legs:2"]
    assert e3["plumbing_test"]["state"] == "open" and e3["reconciliation"]["ok"]   # sells at the NEXT open, not tonight
    fw.topup(D4)
    e4 = step(fw, D4)
    pt = e4["plumbing_test"]
    assert pt["state"] == "closed" and pt["exit_reason"] == "closed_at_open" and e4["positions"] == []
    assert pt["fills"][-1]["price"] == pytest.approx(440 * (1 - 0.0005), abs=1e-3)
    assert e4["reconciliation"]["ok"]
    again = step(fw, D4, rerun=True, plumbing_close=True)                         # a second close is refused, idempotently
    assert pt_lines(fw, "close")[-1]["outcome"] == "skipped" and again["plumbing_test"]["state"] == "closed"


def test_close_is_skipped_without_an_open_position_or_on_the_entry_night(fw):
    e1 = step(fw, NIGHT1, plumbing_close=True)
    assert pt_lines(fw, "close")[-1]["reason"] == "no_plumbing_entry_was_ever_placed" and e1["plumbing_test"]["state"] == "none"
    step(fw, NIGHT1, rerun=True, plumbing_test=True)
    step(fw, NIGHT1, rerun=True, plumbing_close=True)
    assert "no_open_plumbing_position" in pt_lines(fw, "close")[-1]["reason"]     # entry still pending: nothing to close


# ── idempotency ────────────────────────────────────────────────────────────────

def test_idempotent_across_reruns_and_later_nights(fw):
    step(fw, NIGHT1, plumbing_test=True)
    step(fw, NIGHT1, rerun=True, plumbing_test=True)
    fw.topup(D2)
    step(fw, D2, plumbing_test=True)
    outcomes = [(l["date"], l["outcome"]) for l in pt_lines(fw, "entry")]
    assert outcomes == [(NIGHT1, "submitted"), (NIGHT1, "skipped"), (D2, "skipped")]
    assert pt_lines(fw, "entry")[1]["reason"].startswith("already_placed:")
    assert len(buy_rows()) == 1                                                   # one order in the state DB ...
    sim = json.loads((fw.tmp / "sim.json").read_text())
    assert len([o for o in sim["orders"] if o["side"] == "buy" and not o["parent_id"]]) == 1   # ... and one at the sim


def test_after_the_trade_is_done_it_is_never_placed_again(fw):
    step(fw, NIGHT1, plumbing_test=True)
    fw.topup(D2)
    step(fw, D2)
    spy_bar(fw, D3, 435, 436, 420, 425)
    step(fw, D3)
    spy_bar(fw, D4, 440, 441, 439, 440)
    e4 = step(fw, D4, plumbing_test=True)
    assert pt_lines(fw, "entry")[-1]["outcome"] == "skipped" and len(buy_rows()) == 1 and e4["positions"] == []


def test_both_flags_are_rejected(fw):
    with pytest.raises(ValueError):
        fw.forward.step(plumbing_test=True, plumbing_close=True)
    import main
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["forward", "step", "--plumbing-test", "--plumbing-test-close"])
    ns = main.build_parser().parse_args(["forward", "step", "--plumbing-test-close"])
    assert ns.plumbing_close and not ns.plumbing_test


# ── a broken night never runs the wiring check; reporting problems never stop the step ──

def test_entry_is_skipped_on_a_failed_or_degraded_night_and_can_be_reused(fw, monkeypatch):
    import scheduler
    real, broken = scheduler.run_nightly, {"on": True}
    monkeypatch.setattr(scheduler, "run_nightly", lambda **kw: {**real(**kw), "status": "error"} if broken["on"] else real(**kw))
    e1 = step(fw, NIGHT1, plumbing_test=True)
    (ln,) = pt_lines(fw, "entry")
    assert ln["outcome"] == "skipped" and ln["reason"] == "nightly_not_ok:error" and not buy_rows()
    assert e1["plumbing_test"]["state"] == "none"
    broken["on"] = False
    step(fw, NIGHT1, rerun=True, plumbing_test=True)                              # reusable after a fixed rerun
    assert pt_lines(fw, "entry")[-1]["outcome"] == "submitted" and len(buy_rows()) == 1


def test_entry_is_skipped_when_a_required_symbol_is_stale(fw):
    (fw.bars / "SPY.20240528.json").write_text(json.dumps(ibkr_json([(D2, 440, 441, 439, 440.5, 1)])))   # AAPL/MSFT stay a session behind
    e = step(fw, D2, plumbing_test=True)
    assert e["status"] == "degraded"
    assert pt_lines(fw, "entry")[-1]["reason"] == "nightly_not_ok:degraded" and not buy_rows()


def test_status_failure_is_reported_not_raised(fw, monkeypatch):
    monkeypatch.setattr(fw.forward, "_plumbing_ids", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt")))
    e = step(fw, NIGHT1)
    assert e["plumbing_test"]["state"] == "unknown" and e["plumbing_test"]["pl"] == 0.0
    assert any(x.startswith("plumbing test status: RuntimeError") for x in e["errors"])


def test_plumbing_position_is_taken_from_its_own_fills_not_any_spy_position(fw):
    class Sim:
        def __init__(self, fills, pos):
            self.s = {"orders": [{"client_order_id": "E", "status": "filled", "leg_ids": []}], "fills": fills,
                      "positions": {"SPY": pos} if pos else {}}
        def _sync(self): pass
        def _find(self, _): return None
    f = lambda cid, side, qty, px, typ, pl=0.0: {"client_order_id": cid, "side": side, "qty": qty, "price": px, "type": typ, "realized_pl": pl}
    ids = {"entry_cid": "E", "entry_date": "d", "close_cid": None, "close_date": None}
    # plumbing bought 2, stopped out; a strategy then bought 5 SPY: the position is NOT the plumbing one
    done = [f("E", "buy", 2, 440, "market"), f("E-sl", "sell", 2, 426, "stop", -28.0), f("strat", "buy", 5, 450, "market")]
    st = fw.forward.plumbing_status(Sim(done, {"qty": 5, "avg_entry": 450, "last_price": 460}), ids)
    assert st["state"] == "closed" and st["position"] is None and st["unrealized_pl"] == 0 and st["pl"] == -28.0
    # still open with a strategy position merged into the same sim position: only its own 2 shares count
    held = [f("E", "buy", 2, 440, "market"), f("strat", "buy", 5, 450, "market")]
    st = fw.forward.plumbing_status(Sim(held, {"qty": 7, "avg_entry": 447, "last_price": 460}), ids)
    assert st["state"] == "open" and st["position"]["qty"] == 2 and st["unrealized_pl"] == pytest.approx(2 * (460 - 440))


# ── journal / report separation ────────────────────────────────────────────────

def test_report_and_shadow_exclude_the_plumbing_test(fw):
    step(fw, NIGHT1, plumbing_test=True)
    fw.topup(D2)
    e2 = step(fw, D2)
    j = fw.forward.read_journal()
    assert {e["type"] for e in j} >= {"day", "plumbing_test"}
    assert all(i["symbol"] != "SPY" for e in j if e["type"] == "day" for i in e["shadow_new"])
    assert all(s["symbol"] != "SPY" for s in j if s["type"] == "shadow_score")
    txt = fw.forward.report()
    assert "## PLUMBING TEST — not a strategy" in txt and "EXCLUDE the PLUMBING TEST — not a strategy" in txt
    row = [ln for ln in txt.splitlines() if ln.startswith(f"| {D2} |")][0].split("|")
    assert row[4].strip() == str(e2["account"]["return_pct_ex_plumbing"])         # strategy table uses the ex-plumbing return
    assert row[10].strip() == "0" and row[11].strip() == "0"                      # no strategy fill / position counted
    assert (fw.tmp / "j" / "REPORT.md").read_text() == txt


# ── gates: sim only, normal checks still apply, normal intents unchanged ───────

@pytest.fixture
def simenv(monkeypatch, tmp_path):
    """submit_intent against a SimBroker in forward mode (the only place the plumbing test may exist)."""
    monkeypatch.setattr(config, "FORWARD_TEST", True)
    monkeypatch.setattr(config, "PAPER_BROKER", "sim")
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    conn = db.connect()
    db.migrate(conn)
    days = pd.bdate_range("2024-06-03", periods=6)
    rows = [(d.date().isoformat(), 500 + i, 501 + i, 499 + i, 500 + i, 1) for i, d in enumerate(days)]
    sim = horizon_broker(tmp_path / "s.json", {"SPY": frame(rows), "AAPL": frame(rows)}, slippage_bps=0)
    go(sim, rows[0][0])
    now = datetime(2024, 6, 3, 21, tzinfo=timezone.utc)
    rm = RiskManager(conn)
    rm.update_equity(config.SIGNAL_SLEEVE_EQUITY, now)
    return SimpleNamespace(sim=sim, broker=AlpacaBroker(sim), conn=conn, rm=rm, now=now, rows=rows, tmp=tmp_path)


def go_intent(env, intent, broker=None):
    return submit_intent(intent, broker=broker or env.broker, conn=env.conn, now=env.now, risk_manager=env.rm)


def test_intent_creation_refused_for_any_non_sim_broker(simenv, monkeypatch):
    assert build_plumbing_intent(simenv.broker, 1, "2024-06-03", 500.0).source == "plumbing_test"
    for bad in (AlpacaBroker(FakeBroker()), FakeBroker(), AlpacaBroker(), object()):
        with pytest.raises(PlumbingTestRefused):
            build_plumbing_intent(bad, 1, "2024-06-03", 500.0)
    monkeypatch.setattr(config, "PAPER_BROKER", "alpaca")                         # sim object but not sim mode
    with pytest.raises(PlumbingTestRefused):
        build_plumbing_intent(simenv.broker, 1, "2024-06-03", 500.0)
    monkeypatch.setattr(config, "PAPER_BROKER", "sim")
    monkeypatch.setattr(config, "FORWARD_TEST", False)                            # outside forward mode
    with pytest.raises(PlumbingTestRefused):
        build_plumbing_intent(simenv.broker, 1, "2024-06-03", 500.0)


def test_hand_built_plumbing_intent_cannot_bypass_the_gate(simenv, monkeypatch):
    hand = OrderIntent(symbol="SPY", direction=1, signal_bar_date="2024-06-03", strategy_key="plumbing_test", confidence=1.0,
                       validation_status="plumbing_test", atr=7.5, price=500.0, source="plumbing_test")
    fake = FakeBroker()
    d = go_intent(simenv, hand, broker=AlpacaBroker(fake))
    assert d.status == "rejected" and d.reasons == ["plumbing_test_not_allowed"] and not fake.calls_to("submit_order")
    monkeypatch.setattr(config, "PAPER_BROKER", "alpaca")                         # right broker object, wrong mode
    assert go_intent(simenv, hand).reasons == ["plumbing_test_not_allowed"]
    monkeypatch.setattr(config, "PAPER_BROKER", "sim")
    monkeypatch.setattr(config, "FORWARD_TEST", False)
    assert go_intent(simenv, hand).reasons == ["plumbing_test_not_allowed"]
    assert simenv.sim.get_orders() == []
    # an unbuilt Alpaca broker is refused without ever building a client (no credentials, no network)
    monkeypatch.setattr("brokers.alpaca.build_trading_client", lambda *a, **k: pytest.fail("built an Alpaca client"))
    monkeypatch.setattr(config, "FORWARD_TEST", True)
    assert go_intent(simenv, hand, broker=AlpacaBroker()).reasons == ["plumbing_test_not_allowed"]


def test_plumbing_source_is_narrow(simenv):
    mk = lambda **kw: OrderIntent(**{**dict(symbol="SPY", direction=1, signal_bar_date="2024-06-03", strategy_key="plumbing_test",
                                            confidence=1.0, validation_status="plumbing_test", atr=7.5, price=500.0,
                                            source="plumbing_test"), **kw})
    assert go_intent(simenv, mk(symbol="AAPL")).reasons == ["plumbing_test_malformed"]          # SPY only
    assert go_intent(simenv, mk(strategy_key="x")).reasons == ["plumbing_test_malformed"]
    assert go_intent(simenv, mk(validation_status="deflated_validated")).reasons == ["plumbing_test_malformed"]
    assert go_intent(simenv, mk(source="whatever")).reasons == ["invalid_source"]


def test_normal_intents_still_need_an_eligible_status(simenv):
    assert config.ORDER_ELIGIBLE_STATUSES == ("deflated_validated",)
    base = dict(symbol="SPY", direction=1, signal_bar_date="2024-06-03", strategy_key="s", confidence=1.0, atr=7.5, price=500.0)
    for status, reason in (("unvalidated", "not_validated"), ("oos_validated", "not_deflated"),
                           ("plumbing_test", "not_validated")):                  # the plumbing status alone opens nothing
        d = go_intent(simenv, OrderIntent(**base, validation_status=status))
        assert d.status == "rejected" and d.reasons == [reason]
    assert simenv.sim.get_orders() == []
    ok = go_intent(simenv, OrderIntent(**base, validation_status="deflated_validated"))
    assert ok.status == "submitted"                                               # eligible intents are unchanged


def test_plumbing_intent_goes_through_every_normal_gate(simenv):
    intent = build_plumbing_intent(simenv.broker, 1, "2024-06-03", 500.0)
    (simenv.tmp / "KILL").write_text("x")                                         # kill switch
    d = go_intent(simenv, intent)
    assert d.status == "halted" and "kill_switch" in d.reasons and simenv.sim.get_orders() == []
    (simenv.tmp / "KILL").unlink()
    simenv.rm.halt("test halt", simenv.now)                                       # halts
    assert go_intent(simenv, intent).status == "halted"
    simenv.conn.execute("DELETE FROM runs WHERE kind != 'reconcile_accept' AND status = 'halted'")
    # a fresh risk manager state for the remaining gates
    conn2 = db.connect(str(simenv.tmp / "state" / "t2.db"))
    db.migrate(conn2)
    rm2 = RiskManager(conn2)
    rm2.update_equity(config.SIGNAL_SLEEVE_EQUITY, simenv.now)
    d = submit_intent(intent, broker=simenv.broker, conn=conn2, now=simenv.now, risk_manager=rm2)
    assert d.status == "submitted" and 1 <= d.qty <= 3
    assert d.stop_price == pytest.approx(500 * 0.97, abs=0.01) and d.limit_price == pytest.approx(500 + 3 * 500 * 0.015, abs=0.01)
    again = submit_intent(intent, broker=simenv.broker, conn=conn2, now=simenv.now, risk_manager=rm2)
    assert again.status == "duplicate"                                            # client_order_id / ledger
    nxt = build_plumbing_intent(simenv.broker, 1, "2024-06-04", 501.0)
    assert submit_intent(nxt, broker=simenv.broker, conn=conn2, now=simenv.now, risk_manager=rm2).reasons[:1] == ["open_buy_order_exists"]
    row = conn2.execute("SELECT strategy_key, status FROM signal_ledger").fetchone()
    assert tuple(row) == ("plumbing_test", "submitted")
    conn2.close()


def test_exposure_limits_apply_to_the_plumbing_trade(simenv, monkeypatch):
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 0)
    d = go_intent(simenv, build_plumbing_intent(simenv.broker, 1, "2024-06-03", 500.0))
    assert d.status == "rejected" and d.reasons == ["max_open_positions"]
    monkeypatch.setattr(config, "MAX_OPEN_POSITIONS", 5)
    monkeypatch.setattr(config, "MAX_ORDER_NOTIONAL", 100.0)                      # 1% of the sleeve cannot buy one SPY share
    d = go_intent(simenv, build_plumbing_intent(simenv.broker, 1, "2024-06-04", 500.0))
    assert d.status == "rejected" and d.reasons[0] == "qty_zero"
