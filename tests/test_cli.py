"""CLI subcommands and end-to-end scan routing (offline, FakeBroker, tmp state DB)."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import main
from services import pairs as pairs_svc, report as report_svc, scan as scan_svc, session as session_svc
import backtester, data_fetcher
from brokers.alpaca import AlpacaBroker
from brokers.fake import FakeBroker
from tests.fixtures.fake_broker import init_state


@pytest.fixture(autouse=True)
def _us_profile(monkeypatch):
    """These tests are written against the D4 US-ticker table; the platform default is now 'cad' (D14)."""
    import config
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")


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
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(broker))
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


def _cand(symbol, pattern="stub", status="unvalidated", **kw):
    """A validation.CandidateResult as evaluate_candidates would return it (OOS-positive, 40 OOS trades)."""
    import validation
    base = dict(symbol=symbol, pattern=pattern, params={}, n_trials=2, n_oos_trades=40,
                oos={"sharpe": 1.1, "win_rate": 0.55, "total_return": 0.08, "max_drawdown": -0.05,
                     "profit_factor": 1.6, "sortino": 1.4, "expectancy_equity": 0.001},
                oos_psr=0.96, holdout={"total_return": 0.01}, bh_adjusted_p=0.02, validation_status=status)
    base.update(kw)
    return validation.CandidateResult(**base)


@pytest.fixture
def scan_stubs(monkeypatch):
    """Offline scan: two symbols, one stub pattern, validation replaced by `scan_stubs.results` (a list)."""
    class Sent(list):
        results = None
        evaluate_calls = None

    sent = Sent()
    sent.results = None   # set by a test to override; default = both symbols OOS-positive but unvalidated
    stub_calls = []

    def fake_evaluate(frames, patterns, *a, **k):
        stub_calls.append((sorted(frames), sorted(patterns)))
        return sent.results if sent.results is not None else [_cand(s) for s in sorted(frames)]

    monkeypatch.setattr(scan_svc.validation, "evaluate_candidates", fake_evaluate)
    sent.evaluate_calls = stub_calls
    monkeypatch.setattr(session_svc, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
    monkeypatch.setattr(scan_svc, "send_daily_summary", lambda *a, **k: True)
    monkeypatch.setattr(scan_svc, "PATTERN_REGISTRY", {"stub": lambda d: d.copy()})
    monkeypatch.setattr(data_fetcher, "fetch_watchlist", lambda markets=None: {"AAPL": _frame(), "MSFT": _frame()})
    monkeypatch.setattr(data_fetcher, "fetch_ohlcv", lambda *a, **k: pd.DataFrame())  # research fetch falls back to short frame
    return sent


def test_scan_mode_off_makes_zero_broker_calls(monkeypatch, scan_stubs):
    broker = FakeBroker()
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    assert _run(["scan", "--mode", "technical", "--paper"]) == 0
    assert _run(["--mode", "technical"]) == 0  # legacy flat flags still mean scan
    assert broker.calls == []
    assert any(k == "signal" for k, _ in scan_stubs)  # alert-only


def test_scan_paper_mode_only_rejects_not_validated(monkeypatch, tmp_path, scan_stubs):
    init_state(monkeypatch, tmp_path)
    broker = FakeBroker()
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(broker))
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
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(broker))
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
    monkeypatch.setattr(data_fetcher, "fetch_ohlcv", lambda *a, **k: pd.DataFrame())
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
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(broker))
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    sent = []

    def real_send(msg, **k):  # the real dedup path (alerts_sent), console channel
        r = alerts.send_alert(msg, method="console", **k)
        sent.append((k.get("kind"), msg, r.sent))
        return r

    monkeypatch.setattr(session_svc, "send_alert", real_send)
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
    monkeypatch.setattr(session_svc, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
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
    assert session_svc._cancel_if_halted(broker, rm, conn) == []  # not halted: nothing happens
    assert fake.calls_to("cancel_order_by_id") == []
    rm.halt("test halt")
    assert session_svc._cancel_if_halted(broker, rm, conn) == [d.broker_id]
    assert [o.id for o in fake.orders if o.side == "sell"] == legs_before  # protective legs untouched
    assert any(k == "halt" and "CANCEL ON HALT" in m for k, m in sent)
    monkeypatch.setattr(config, "CANCEL_ON_HALT", False)
    assert session_svc._cancel_if_halted(broker, rm, conn) == []
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
    monkeypatch.setattr(session_svc, "send_alert", lambda msg, **k: sent.append((k.get("kind"), msg)) or True)
    session_svc._announce_events([{"type": "halt", "reason": "UNPROTECTED AAPL: no stop", "at": "t1"},
                           {"type": "baseline_seeded", "reason": "account baseline set to 1.00", "at": "t2"}], set())
    assert [k for k, _ in sent] == ["halt", "halt"]
    assert "UNPROTECTED AAPL" in sent[0][1] and "BASELINE" in sent[1][1]


def test_unreadable_broker_refuses_the_session_and_seeds_no_baseline(monkeypatch, tmp_path):
    import execution
    from state import db
    init_state(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    monkeypatch.setattr(session_svc, "send_alert", lambda *a, **k: True)
    fake = FakeBroker()
    fake.raise_on("get_all_positions")
    monkeypatch.setattr(session_svc, "get_broker", lambda: AlpacaBroker(fake))
    assert execution.reconcile(AlpacaBroker(fake), db.connect()).positions is None  # not a flat book
    assert session_svc.open_session() is None
    assert fake.calls_to("get_account") == []  # never fed to refresh_sleeve_equity
    conn = db.connect()
    assert conn.execute("SELECT account_baseline FROM risk_state").fetchone()[0] is None
    conn.close()


# ---------------------------------------------------------------- Phase 2: validation wiring
def test_technical_scan_uses_one_validation_run_and_reports_oos_status(monkeypatch, scan_stubs):
    scan_stubs.results = [_cand("AAPL", status="deflated_validated", n_trials=2), _cand("MSFT", n_oos_trades=5)]
    intents: list = []
    got = scan_svc.scan_technical({"AAPL": _frame(), "MSFT": _frame()}, intents=intents)
    # ONE evaluate_candidates call over every symbol (so BH spans the whole run)
    assert scan_stubs.evaluate_calls == [(["AAPL", "MSFT"], ["stub"])]
    # MSFT has too few OOS trades to be reported even informationally; AAPL is validated
    assert [g["symbol"] for g in got] == ["AAPL"]
    assert got[0]["validation_status"] == "deflated_validated"
    assert [(i.symbol, i.validation_status) for i in intents] == [("AAPL", "deflated_validated")]
    alert = next(m for k, m in scan_stubs if k == "signal")
    assert "OUT-OF-SAMPLE" in alert and "deflated_validated" in alert and "BH-adjusted" in alert


def test_unvalidated_but_oos_positive_signal_is_reported_as_unvalidated(scan_stubs):
    intents: list = []
    got = scan_svc.scan_technical({"AAPL": _frame()}, intents=intents)
    assert got[0]["validation_status"] == "unvalidated"
    assert intents[0].validation_status == "unvalidated"


def test_validation_failure_makes_nothing_eligible(monkeypatch, scan_stubs):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(scan_svc.validation, "evaluate_candidates", boom)
    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: SimpleNamespace(is_valid=False))
    intents: list = []
    assert scan_svc.scan_technical({"AAPL": _frame()}, intents=intents) == [] and intents == []


def test_legacy_in_sample_tier_is_retired(monkeypatch, scan_stubs):
    """D11 (Phase 3): no OOS evidence (too little history) is NOT rescued by the old in-sample heuristic any more."""
    scan_stubs.results = [_cand("AAPL", n_oos_trades=0, oos={}, oos_psr=None, holdout=None,
                                rejected_reasons=["insufficient_history"])]
    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: SimpleNamespace(is_valid=True))
    intents: list = []
    assert scan_svc.scan_technical({"AAPL": _frame()}, intents=intents) == [] and intents == []


def test_full_scan_fetches_research_length_history(monkeypatch, scan_stubs):
    calls = []
    monkeypatch.setattr(data_fetcher, "fetch_ohlcv", lambda s, period_days=None, **k: calls.append((s, period_days)) or pd.DataFrame())
    main.run_full_scan(modes=["technical"])
    assert {(s, d) for s, d in calls} == {("AAPL", config.RESEARCH_LOOKBACK_DAYS), ("MSFT", config.RESEARCH_LOOKBACK_DAYS)}


def _ml_cand(symbol, auc, base, validated, psr, direction=1):
    bt = SimpleNamespace(psr=psr, win_rate=0.55, profit_factor=1.5, profit_factor_undefined=False,
                         total_return_pct=0.05, max_drawdown_pct=-0.04, sharpe_ratio=0.9, total_trades=40)
    return {"symbol": symbol, "oos_auc": auc, "baseline_auc": base, "oos_validated": validated,
            "oos_start": "2024-02-01", "confidence": 0.7, "direction": direction, "oos_frame": _frame(),
            "backtest": bt, "oos_backtest_summary": {"win_rate": 0.55, "profit_factor": 1.5, "total_return": 0.05,
                                                     "max_drawdown": -0.04, "sharpe": 0.9, "total_trades": 40}}


def test_scan_ml_gates_on_auc_and_bh_status(monkeypatch, scan_stubs):
    frames = {s: _frame(n=300) for s in ("AAA", "BBB", "CCC")}
    cands = {"AAA": _ml_cand("AAA", 0.60, 0.53, True, 0.9999),    # beats baseline, validated, tiny p
             "BBB": _ml_cand("BBB", 0.51, 0.53, True, 0.9999),    # below baseline -> never reported
             "CCC": _ml_cand("CCC", 0.58, 0.53, False, 0.97)}     # beats baseline, own gates fail
    monkeypatch.setattr(scan_svc, "ml_scan_candidate", lambda df, sym: cands[sym])
    monkeypatch.setattr(scan_svc.MLPatternDetector, "train", lambda self, df: {"top_features": {"rsi": 1.0}})
    monkeypatch.setattr(backtester, "classic_backtest", lambda df, sym, name, **k: cands[sym]["backtest"])
    intents: list = []
    got = scan_svc.scan_ml(frames, intents=intents)
    status = {g["symbol"]: g["validation_status"] for g in got}
    # ML clears only its own gates: ml_oos_candidate, never an order-eligible status (review blocker)
    assert status == {"AAA": "ml_oos_candidate", "CCC": "unvalidated"}
    assert {i.symbol: i.validation_status for i in intents} == status


def test_scan_ml_own_gates_pass_but_bh_fails_is_unvalidated(monkeypatch, scan_stubs):
    frames = {s: _frame(n=300) for s in ("AAA", "BBB")}
    cands = {"AAA": _ml_cand("AAA", 0.60, 0.53, True, 0.9999),
             "BBB": _ml_cand("BBB", 0.60, 0.53, True, 0.80)}      # p = 0.2 > alpha: BH does not reject
    monkeypatch.setattr(scan_svc, "ml_scan_candidate", lambda df, sym: cands[sym])
    monkeypatch.setattr(scan_svc.MLPatternDetector, "train", lambda self, df: {"top_features": {}})
    monkeypatch.setattr(backtester, "classic_backtest", lambda df, sym, name, **k: cands[sym]["backtest"])
    got = scan_svc.scan_ml(frames, intents=[])
    assert {g["symbol"]: g["validation_status"] for g in got} == {"AAA": "ml_oos_candidate", "BBB": "unvalidated"}


def test_scan_ml_status_is_never_order_eligible(monkeypatch, scan_stubs):
    cands = {"AAA": _ml_cand("AAA", 0.60, 0.53, True, 0.9999)}
    monkeypatch.setattr(scan_svc, "ml_scan_candidate", lambda df, sym: cands[sym])
    monkeypatch.setattr(scan_svc.MLPatternDetector, "train", lambda self, df: {"top_features": {}})
    monkeypatch.setattr(backtester, "classic_backtest", lambda df, sym, name, **k: cands[sym]["backtest"])
    intents: list = []
    scan_svc.scan_ml({"AAA": _frame(n=300)}, intents=intents)
    assert intents and all(i.validation_status not in config.ORDER_ELIGIBLE_STATUSES for i in intents)


@pytest.mark.parametrize("psr, expected", [(0.94, False), (0.95, False), (0.951, True)])
def test_ml_scan_candidate_requires_psr_strictly_above_min(monkeypatch, psr, expected):
    """oos_validated needs PSR > OOS_PSR_MIN (the spec's strict inequality) on top of AUC and trade count."""
    import ml_patterns
    import backtester

    oos = _frame(n=60)
    oos["oos"] = True
    oos["ml_confidence"] = 0.7

    class Det:
        oos_start = oos.index[0]
        oos_metrics = {"auc": 0.6, "baseline_auc": 0.53, "auc_above_baseline": True}
        model_type = "stub"

        def walk_forward_predict(self, df):
            return oos

    bt = SimpleNamespace(psr=psr, total_trades=config.MIN_TRADES_OOS, numeric_summary=lambda: {})
    monkeypatch.setattr(ml_patterns, "MLPatternDetector", Det)
    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: bt)
    assert ml_patterns.ml_scan_candidate(_frame(n=60), "AAA")["oos_validated"] is expected


def test_pairs_alert_survives_none_fields_and_says_unvalidated(monkeypatch, scan_stubs):
    monkeypatch.setattr(pairs_svc, "scan_all_pairs", lambda data, pairs: [{
        "symbol_a": "KO", "symbol_b": "PEP", "has_signal": True, "signal_direction": "LONG_SPREAD",
        "current_zscore": 2.1, "half_life": 12.0, "correlation": 0.8, "win_rate": 0.6, "profit_factor": None,
        "total_return": 0.03, "sharpe_ratio": 0.7, "total_trades": 9, "adj_pvalue": 0.01, "n_tested": 5}])
    out = scan_svc.scan_pairs({})
    assert len(out) == 1
    msg = next(m for k, m in scan_stubs if k == "signal")
    assert "unvalidated" in msg and "alert-only" in msg


def test_report_shows_oos_and_validation_columns(tmp_path):
    out = tmp_path / "r.html"
    report_svc.generate_report({"technical": [{"direction": "🟢 BUY", "symbol": "A<B", "pattern": "p", "win_rate": "55.0%",
                                         "validation_status": "oos_validated", "sharpe": "1.00",
                                         "profit_factor": "n/a", "total_trades": 40, "holdout_return": 0.02,
                                         "bh_adjusted_p": 0.03}],
                          "pairs": [{"symbol_a": "K", "symbol_b": "P", "half_life": None, "profit_factor": None}],
                          "ml": []}, output=str(out))
    html = out.read_text()
    assert "oos_validated" in html and "out-of-sample" in html and "Hold-out" in html and "A&lt;B" in html


# ---------------------------------------------------------------- Phase 3 subcommands
def test_run_nightly_dispatches_and_maps_exit_codes(monkeypatch, capsys):
    import scheduler
    calls = []
    monkeypatch.setattr(scheduler, "run_nightly",
                        lambda **kw: calls.append(kw) or {"status": calls_status[0]})
    calls_status = ["ok"]
    assert _run(["run-nightly", "--mode", "pairs", "--no-stress"]) == 0
    assert calls[0] == {"modes": ("pairs",), "run_stress": False}
    calls_status[0] = "error"
    assert _run(["run-nightly"]) == 1
    assert calls[1]["modes"] == scheduler.MODES and calls[1]["run_stress"] is True
    calls_status[0] = "locked"
    assert _run(["run-nightly"]) == 0


def test_schedule_passes_time_and_zone(monkeypatch):
    import scheduler
    seen = {}
    monkeypatch.setattr(scheduler, "schedule_forever", lambda **kw: seen.update(kw))
    assert _run(["schedule", "--at", "18:00", "--tz", "UTC"]) == 0
    assert seen == {"at": "18:00", "tz": "UTC"}


def test_heartbeat_ok_is_silent_and_failure_alerts_and_exits_nonzero(monkeypatch, capsys):
    import scheduler
    sent = []
    monkeypatch.setattr(session_svc, "alert", lambda msg, kind="signal", dedup_key=None: sent.append((kind, msg)))
    monkeypatch.setattr(scheduler, "heartbeat_check", lambda max_age_hours=30: (True, "last run ok"))
    assert _run(["heartbeat"]) == 0 and sent == []
    monkeypatch.setattr(scheduler, "heartbeat_check", lambda max_age_hours=30: (False, "no nightly run recorded"))
    assert _run(["heartbeat", "--max-age-hours", "12"]) == 1
    assert sent and sent[0][0] == "heartbeat" and "no nightly run" in sent[0][1]
    assert "heartbeat" in __import__("alerts").ALERT_KINDS


def test_nightly_pairs_scan_gets_its_own_trial_registry(monkeypatch, tmp_path):
    """scan_pairs passes a fresh registry (own run id, own trials.sqlite, never trading.db) to the pairs scan."""
    seen = {}

    def fake_scan(data, pairs=None, trials=None):
        seen["trials"] = trials
        return []

    monkeypatch.setattr(scan_svc.pairs_service, "scan_pairs_data", fake_scan)
    scan_svc.scan_pairs({"AAPL": _frame(), "MSFT": _frame()})
    reg = seen["trials"]
    assert reg is not None and reg.run_id.startswith("pairs-") and reg.n_trials == 0
    assert (tmp_path / "state" / "trials.sqlite").is_file()
    assert not (tmp_path / "state" / "trading.db").exists()
