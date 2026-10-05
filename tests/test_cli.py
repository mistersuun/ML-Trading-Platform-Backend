"""CLI subcommands and end-to-end scan routing (offline, FakeBroker, tmp state DB)."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import main
from brokers.alpaca import AlpacaBroker
from brokers.fake import FakeBroker
from tests.fixtures.fake_broker import init_state


def _run(argv):
    try:
        return main.main(argv)
    except SystemExit as e:  # argparse errors
        return e.code or 0


# ---------------------------------------------------------------- risk
def test_risk_resume_without_confirm_exits_nonzero(capsys):
    assert _run(["risk", "init"]) == 0
    assert _run(["risk", "halt", "--reason", "test"]) == 0
    assert _run(["risk", "resume"]) != 0
    assert "--confirm" in capsys.readouterr().err
    assert json.loads(_status(capsys))["halted"] is True


def _status(capsys):
    capsys.readouterr()
    assert _run(["risk", "status"]) == 0
    return capsys.readouterr().out


def test_risk_init_then_status_and_resume(capsys):
    assert _run(["risk", "status"]) != 0  # refuses without init
    assert "risk init" in capsys.readouterr().err
    assert _run(["risk", "init"]) == 0
    assert _run(["risk", "init"]) == 0  # idempotent
    st = json.loads(_status(capsys))
    assert st["initialized"] is True and st["halted"] is False
    assert _run(["risk", "halt", "--reason", "manual"]) == 0
    assert json.loads(_status(capsys))["halt_reason"] == "manual"
    assert _run(["risk", "resume", "--confirm"]) == 0
    assert json.loads(_status(capsys))["halted"] is False


def test_backup_and_reconcile(monkeypatch, tmp_path, capsys):
    broker = FakeBroker()
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(broker))
    assert _run(["risk", "reconcile"]) != 0  # no state DB yet
    _run(["risk", "init"])
    assert _run(["risk", "reconcile"]) == 0
    dest = tmp_path / "bk" / "copy.db"
    assert _run(["backup", "--dest", str(dest)]) == 0 and dest.exists()
    broker.add_position("AAPL", 5, 10.0)
    assert _run(["risk", "reconcile"]) != 0
    assert _run(["risk", "reconcile", "--accept"]) == 0
    assert _run(["risk", "reconcile"]) == 0
    capsys.readouterr()


# ---------------------------------------------------------------- scan
def _frame(n=60, price=10.0):
    idx = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({"Open": price, "High": price * 1.01, "Low": price * 0.99,
                       "Close": price, "Volume": 1_000_000}, index=idx)
    df["signal"] = 0
    df.iloc[-1, df.columns.get_loc("signal")] = 1
    return df


@pytest.fixture
def scan_stubs(monkeypatch):
    res = SimpleNamespace(is_valid=True, win_rate=0.6, profit_factor=2.0, total_trades=20, total_return_pct=0.1)
    res.summary = lambda: {"symbol": "AAPL", "pattern": "stub", "win_rate": 0.6, "profit_factor": 2.0}
    sent = []
    monkeypatch.setattr(main, "classic_backtest", lambda *a, **k: res)
    monkeypatch.setattr(main, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
    monkeypatch.setattr(main, "send_daily_summary", lambda *a, **k: True)
    monkeypatch.setattr(main, "PATTERN_REGISTRY", {"stub": lambda d: d.copy()})
    monkeypatch.setattr(main, "fetch_watchlist", lambda markets=None: {"AAPL": _frame(), "MSFT": _frame()})
    return sent


def test_scan_mode_off_makes_zero_broker_calls(monkeypatch, scan_stubs):
    broker = FakeBroker()
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    assert _run(["scan", "--mode", "technical", "--paper"]) == 0
    assert _run(["--mode", "technical"]) == 0  # legacy flat flags still mean scan
    assert broker.calls == []
    assert any(k == "signal" for k, _ in scan_stubs)  # alert-only


def test_scan_paper_mode_only_rejects_not_validated(monkeypatch, tmp_path, scan_stubs):
    init_state(monkeypatch, tmp_path)
    broker = FakeBroker()
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    res = main.run_full_scan(modes=["technical"], paper_trade=True)
    assert len(res["decisions"]) == 2
    assert {(d["status"], tuple(d["reasons"])) for d in res["decisions"]} == {("rejected", ("not_validated",))}
    assert broker.calls_to("submit_order") == []
    assert any(k == "order_decision" and "not_validated" in m and "paper" in m for k, m in scan_stubs)
    from state import db
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind='scan' AND status='ok'").fetchone()[0] == 1
    conn.close()


def test_scan_paper_mode_refuses_without_state_db(monkeypatch, scan_stubs):
    broker = FakeBroker()
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    res = main.run_full_scan(modes=["technical"], paper_trade=True)
    assert res["decisions"] == [] and broker.calls == []


# ---------------------------------------------------------------- rebalance
def test_rebalance_prints_proposal(monkeypatch, tmp_path, capsys, patch_fetch):
    import allocation
    csv = tmp_path / "holdings.csv"
    csv.write_text("symbol,quantity\nVTI,10\nIEF,5\nCASH,2000\n")
    assert _run(["rebalance", "--holdings", str(csv), "--contributions", "500"]) == 0
    out = capsys.readouterr().out
    assert "VTI" in out and "nan" not in out.lower() and "Advisory only" in out
    assert {s for s, _ in patch_fetch.calls} >= set(allocation.CORE_TARGETS)


def test_rebalance_network_failure_is_a_clear_error(monkeypatch, tmp_path, capsys):
    csv = tmp_path / "h.csv"
    csv.write_text("symbol,quantity\nVTI,10\n")
    monkeypatch.setattr(main, "fetch_ohlcv", lambda *a, **k: pd.DataFrame())
    assert _run(["rebalance", "--holdings", str(csv)]) != 0
    assert "could not fetch prices" in capsys.readouterr().err
    assert _run(["rebalance", "--holdings", str(tmp_path / "missing.csv")]) != 0


def test_check_llm_without_key_fails(capsys):
    assert _run(["check-llm"]) != 0
    assert "FAILED" in capsys.readouterr().out


# ---------------------------------------------------------------- review fixes
def test_order_decision_alerts_are_not_collapsed_by_dedup(monkeypatch, tmp_path, scan_stubs):
    """Decisions made before the ledger key exists have no client_order_id; each symbol still alerts once."""
    import alerts
    init_state(monkeypatch, tmp_path)
    broker = FakeBroker()
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    sent = []

    def real_send(msg, **k):  # the real dedup path (alerts_sent), console channel
        r = alerts.send_alert(msg, method="console", **k)
        sent.append((k.get("kind"), msg, r.sent))
        return r

    monkeypatch.setattr(main, "send_alert", real_send)
    res = main.run_full_scan(modes=["technical"], paper_trade=True)
    decisions = [(m, ok) for k, m, ok in sent if k == "order_decision"]
    assert len(res["decisions"]) == 2 and len(decisions) == 2 and all(ok for _, ok in decisions)
    assert {"AAPL", "MSFT"} == {s for s in ("AAPL", "MSFT") for m, _ in decisions if s in m}
    sent.clear()
    main.run_full_scan(modes=["technical"], paper_trade=True)  # same bars again: now it IS a duplicate
    assert [ok for k, _, ok in sent if k == "order_decision"] == [False, False]


def test_cancel_on_halt_cancels_unfilled_entries_only_and_alerts(monkeypatch, tmp_path):
    import execution
    from risk_manager import RiskManager
    from state import db
    from tests.fixtures.fake_broker import pretend_validated
    init_state(monkeypatch, tmp_path)
    pretend_validated(monkeypatch)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    sent = []
    monkeypatch.setattr(main, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
    fake = FakeBroker()
    broker = AlpacaBroker(fake)
    conn = db.connect()
    rm = RiskManager(conn)
    execution.refresh_sleeve_equity(rm, broker)
    it = execution.OrderIntent("AAPL", 1, "2024-06-03", "s", 1.0, "unvalidated", 1.0, 100.0)
    d = execution.submit_intent(it, broker=broker, conn=conn, risk_manager=rm)
    assert d.status == "submitted"
    fake.add_filled_bracket("filled-1", "XOM", 3, 90.0, 110.0, nested=False)
    legs_before = [o.id for o in fake.orders if o.side == "sell"]
    assert main._cancel_if_halted(broker, rm, conn) == []  # not halted: nothing happens
    assert fake.calls_to("cancel_order_by_id") == []
    rm.halt("test halt")
    assert main._cancel_if_halted(broker, rm, conn) == [d.broker_id]
    assert [o.id for o in fake.orders if o.side == "sell"] == legs_before  # protective legs untouched
    assert any(k == "halt" and "CANCEL ON HALT" in m for k, m in sent)
    monkeypatch.setattr(config, "CANCEL_ON_HALT", False)
    assert main._cancel_if_halted(broker, rm, conn) == []
    conn.close()


# ---------------------------------------------------------------- review round 2
def test_risk_init_prints_the_dedicated_account_assumption(capsys):
    assert _run(["risk", "init"]) == 0
    out = capsys.readouterr().out
    assert "D10" in out and "dedicated" in out and "rebaseline" in out


def test_risk_rebaseline_needs_confirm(capsys):
    _run(["risk", "init"])
    capsys.readouterr()
    assert _run(["risk", "rebaseline"]) != 0 and "--confirm" in capsys.readouterr().err
    assert _run(["risk", "rebaseline", "--confirm"]) == 0


def test_unprotected_and_baseline_events_alert_as_halt(monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
    main._announce_events([{"type": "halt", "reason": "UNPROTECTED AAPL: no stop", "at": "t1"},
                           {"type": "baseline_seeded", "reason": "account baseline set to 1.00", "at": "t2"}], set())
    assert [k for k, _ in sent] == ["halt", "halt"]
    assert "UNPROTECTED AAPL" in sent[0][1] and "BASELINE" in sent[1][1]


def test_unreadable_broker_refuses_the_session_and_seeds_no_baseline(monkeypatch, tmp_path):
    import execution
    from state import db
    init_state(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    monkeypatch.setattr(main, "send_alert", lambda *a, **k: True)
    fake = FakeBroker()
    fake.raise_on("get_all_positions")
    monkeypatch.setattr(main, "get_broker", lambda: AlpacaBroker(fake))
    assert execution.reconcile(AlpacaBroker(fake), db.connect()).positions is None  # not a flat book
    assert main.open_session() is None
    assert fake.calls_to("get_account") == []  # never fed to refresh_sleeve_equity
    conn = db.connect()
    assert conn.execute("SELECT account_baseline FROM risk_state").fetchone()[0] is None
    conn.close()
