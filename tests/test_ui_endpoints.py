"""Endpoints behind the new UI: portfolio overview, allocation proposal, risk status, scanner candidate, funnel.

Fixture provider + tmp state only (no network, no real holdings). Every response is parsed strictly (no NaN)."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import allocation
import config
import scheduler
from api.concurrency import HEAVY
from results import store
from risk_manager import RiskManager
from services import candidate as candidate_service
from services import scan
from services.providers import get_provider
from state import db as state_db
from tests.fixtures import synthetic
from tests.fixtures.fake_broker import init_state

SYMBOLS = sorted(set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE) | {"SPY", "IEF"})
HOLDINGS = {"VTI": 300, "VEA": 250, "VWO": 80, "IEF": 100, "TLT": 40, "TIP": 60, "SGOV": 80, "GLDM": 120,
            "PDBC": 40, "SPY": 15, "GLD": 10, "DBC": 100, "VNQ": 30, "CASH": 4560}


@pytest.fixture(autouse=True)
def _us_profile(monkeypatch):
    """These tests are written against the D4 US-ticker table; the platform default is now 'cad' (D14)."""
    import config
    monkeypatch.setattr(config, "ALLOCATION_PROFILE", "us")


def _no_constants(name):
    raise AssertionError(f"non-finite JSON constant {name}")


def strict(resp):
    return json.loads(resp.text, parse_constant=_no_constants)


class FakeProvider:
    """Deterministic frames per symbol; `missing` symbols return an empty frame."""

    def __init__(self, n=800, start="2023-09-01", missing=()):
        self.n, self.start, self.missing, self.calls = n, start, set(missing), []

    def frame(self, symbol, n=None):
        seed = sum(ord(c) * (i + 1) for i, c in enumerate(symbol))
        return synthetic.gbm_ohlc(n=n or self.n, seed=seed, start=self.start, start_price=50 + seed % 150)

    def ohlcv(self, symbol, period_days=None):
        self.calls.append((symbol, period_days))
        if symbol in self.missing:
            return pd.DataFrame()
        return self.frame(symbol)

    def watchlist(self, markets=None):
        return {}

    def pair(self, *a, **k):
        return None

    def bars(self, *a, **k):
        raise NotImplementedError


@pytest.fixture
def app_client():
    from server import app
    holder = {}

    def make(provider):
        app.dependency_overrides[get_provider] = lambda: provider
        holder["app"] = app
        return TestClient(app, raise_server_exceptions=False)

    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def holdings_csv(tmp_path, monkeypatch):
    p = tmp_path / "holdings.csv"
    p.write_text("symbol,quantity\n" + "\n".join(f"{s},{q}" for s, q in HOLDINGS.items()) + "\n")
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(p))
    monkeypatch.setattr(config, "BASE_CURRENCY", "USD")        # a US-ticker portfolio held in US dollars
    return p


@pytest.fixture
def no_holdings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(tmp_path / "nope.csv"))


# ================================================================ portfolio overview
def test_overview_totals_series_and_allocation(app_client, holdings_csv):
    prov = FakeProvider()
    r = app_client(prov).get("/api/portfolio/overview?range=1Y")
    assert r.status_code == 200, r.text
    b = strict(r)
    last = {s: float(prov.frame(s)["Close"].iloc[-1]) for s in HOLDINGS if s != "CASH"}
    prev = {s: float(prov.frame(s)["Close"].iloc[-2]) for s in HOLDINGS if s != "CASH"}
    total = sum(HOLDINGS[s] * last[s] for s in last) + HOLDINGS["CASH"]
    day = sum(HOLDINGS[s] * (last[s] - prev[s]) for s in last)
    assert b["total_value"] == pytest.approx(total)
    assert b["day_change"] == pytest.approx(day)
    assert b["cash"] == 4560
    assert "constant" in b["method"].lower() and "current holdings" in b["method"].lower()
    accts = {a["key"]: a["value"] for a in b["accounts"]}
    assert set(accts) == {"core", "trend", "cash"}
    assert sum(accts.values()) == pytest.approx(total)
    assert accts["core"] == pytest.approx(sum(HOLDINGS[s] * last[s] for s in allocation.CORE_TARGETS))
    sl = b["paper_sleeve"]
    assert sl["label"] == "paper" and sl["value"] is None            # state not initialised: no number invented
    s = b["series"]
    assert s[0]["portfolio"] == pytest.approx(100.0) and s[0]["benchmark"] == pytest.approx(100.0)
    assert s[0]["drawdown"] == 0
    assert all(p["drawdown"] <= 0 for p in s)
    assert b["max_drawdown"] == pytest.approx(min(p["drawdown"] for p in s))
    assert b["period_return"] == pytest.approx(s[-1]["portfolio"] / 100 - 1)
    assert b["benchmark_return"] == pytest.approx(s[-1]["benchmark"] / 100 - 1)
    assert b["benchmark_label"] == "SPY 60%/IEF 40%"
    keys = [g["key"] for g in b["allocation"]]
    assert keys == ["us_equity", "intl_equity", "treasuries", "tips_bills", "gold_commodities"]
    assert sum(g["now"] for g in b["allocation"]) == pytest.approx(1.0)
    assert sum(g["target"] for g in b["allocation"]) == pytest.approx(1.0)
    for g in b["allocation"]:
        assert g["drift"] == pytest.approx(g["now"] - g["target"])


def test_overview_ranges_and_benchmark_is_monthly_rebalanced(app_client, holdings_csv):
    c = app_client(FakeProvider())
    lens = {}
    for rng in ("1M", "3M", "YTD", "1Y", "ALL"):
        r = c.get(f"/api/portfolio/overview?range={rng}")
        assert r.status_code == 200, (rng, r.text)
        lens[rng] = len(strict(r)["series"])
    assert lens["1M"] < lens["3M"] < lens["1Y"] <= lens["ALL"]
    # the benchmark is the 60/40 mix rebalanced at month starts: replicate it by hand over 3M
    prov = FakeProvider()
    b = strict(app_client(prov).get("/api/portfolio/overview?range=3M"))
    dates = [pd.Timestamp(p["date"]) for p in b["series"]]
    px = pd.concat({s: prov.frame(s)["Close"] for s in ("SPY", "IEF")}, axis=1).loc[dates]
    val, anchor_val, anchor = 100.0, 100.0, px.iloc[0].to_numpy()
    for i in range(1, len(px)):
        if px.index[i].month != px.index[i - 1].month:
            anchor_val, anchor = val, px.iloc[i - 1].to_numpy()
        val = anchor_val * float(np.dot([0.6, 0.4], px.iloc[i].to_numpy() / anchor))
    assert b["series"][-1]["benchmark"] == pytest.approx(val)


def test_overview_unpriced_symbol_is_reported_not_invented(app_client, holdings_csv):
    r = app_client(FakeProvider(missing={"VNQ"})).get("/api/portfolio/overview")
    b = strict(r)
    assert r.status_code == 200 and b["unpriced"] == ["VNQ"]
    assert any("VNQ" in w for w in b["warnings"])


def test_overview_no_holdings_404(app_client, no_holdings):
    r = app_client(FakeProvider()).get("/api/portfolio/overview")
    assert r.status_code == 404
    e = strict(r)["error"]
    assert e["code"] == "no_holdings" and "symbol,quantity" in e["message"] and "CASH" in e["message"]


def test_overview_bad_range_422_and_bad_csv(app_client, holdings_csv, tmp_path, monkeypatch):
    c = app_client(FakeProvider())
    assert c.get("/api/portfolio/overview?range=5Y").status_code == 422
    bad = tmp_path / "bad.csv"
    bad.write_text("symbol,quantity\nVTI,abc\n")
    monkeypatch.setattr(config, "HOLDINGS_CSV", str(bad))
    r = c.get("/api/portfolio/overview")
    assert r.status_code == 422 and strict(r)["error"]["code"] == "bad_holdings"


def test_overview_paper_sleeve_from_state(app_client, holdings_csv, monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    rm = RiskManager()
    rm.update_equity(10_000, datetime(2026, 9, 1, tzinfo=timezone.utc))
    rm.update_equity(9_800, datetime(2026, 9, 2, tzinfo=timezone.utc))
    sl = strict(app_client(FakeProvider()).get("/api/portfolio/overview"))["paper_sleeve"]
    assert sl["value"] == 9_800 and sl["peak"] == 10_000 and sl["drawdown"] == pytest.approx(-0.02)
    assert sl["label"] == "paper" and "not real money" in sl["note"]


def test_heavy_endpoints_share_the_cap(app_client, holdings_csv):
    c = app_client(FakeProvider())
    assert HEAVY.acquire(blocking=False)
    try:
        for url in ("/api/portfolio/overview", "/api/allocation/proposal",
                    "/api/scanner/candidate?symbol=SPY&pattern=ma_crossover"):
            r = c.get(url)
            assert r.status_code == 429 and strict(r)["error"]["code"] == "busy", url
    finally:
        HEAVY.release()


# ================================================================ allocation proposal
def test_proposal_matches_propose_rebalance(app_client, holdings_csv):
    prov = FakeProvider()
    r = app_client(prov).get("/api/allocation/proposal?contribution=2000")
    assert r.status_code == 200, r.text
    b = strict(r)
    prices = {s: float(prov.frame(s)["Close"].iloc[-1]) for s in SYMBOLS}
    monthly = pd.DataFrame({s: prov.frame(s)["Close"].resample("ME").last() for s in allocation.TREND_UNIVERSE})
    ref = allocation.propose_rebalance(dict(HOLDINGS), prices, monthly, contributions=2000.0)
    assert b["cash_after"] == pytest.approx(ref.leftover_cash) and b["contribution"] == 2000
    assert b["signal_month"] == ref.signal_month
    rows = {x["symbol"]: x for x in b["rows"]}
    assert set(rows) == set(allocation.CORE_TARGETS) | set(allocation.TREND_UNIVERSE)
    for sr in ref.rows:
        x = rows[sr.symbol]
        assert x["target"] == pytest.approx(sr.target) and x["current"] == pytest.approx(sr.weight)
        assert x["drift"] == pytest.approx(sr.drift) and x["outside"] == sr.out_of_band
        assert x["trade_shares"] == sr.trade_shares
        assert x["band"] == pytest.approx(max(0.05, 0.25 * sr.target))
    assert [(t["symbol"], t["shares"]) for t in b["trades"]] == [(t.symbol, abs(t.trade_shares)) for t in ref.trades]
    for t in b["trades"]:
        assert isinstance(t["shares"], int) and t["action"] in ("buy", "sell") and t["amount"] > 0
    assert "never" not in b["advisory"] and "Advisory" in b["advisory"]


def test_proposal_groups_and_trend_detail(app_client, holdings_csv):
    b = strict(app_client(FakeProvider()).get("/api/allocation/proposal"))
    assert b["contribution"] == 0
    g = b["groups"]
    assert [x["key"] for x in g][0] == "us_equity" and len(g) == 5
    for col in ("target", "now", "after"):
        assert sum(x[col] for x in g) == pytest.approx(1.0)
    assert [t["symbol"] for t in b["trend"]] == allocation.TREND_UNIVERSE
    ref_scores, _ = allocation.trend_signals(pd.DataFrame(
        {s: FakeProvider().frame(s)["Close"].resample("ME").last() for s in allocation.TREND_UNIVERSE}))
    for t in b["trend"]:
        assert len(t["months"]) == 13 and [v["lookback"] for v in t["votes"]] == [8, 10, 12]
        assert t["score"] == pytest.approx(ref_scores[t["symbol"]])
        assert t["score"] == pytest.approx(sum(v["above"] for v in t["votes"]) / 3)
        assert t["weight"] == pytest.approx(0.10 / 6 * t["score"])
        assert t["state"] == {1.0: "held", 0.0: "tbills"}.get(t["score"], "partial")
        assert all(m["average"] is not None for m in t["months"])        # 13 closes after >= 10 months of warm-up
        m = t["months"][-1]
        s = FakeProvider().frame(t["symbol"])["Close"].resample("ME").last()
        s = s[s.index.to_period("M") < pd.Period(datetime.now(), freq="M")]
        assert m["average"] == pytest.approx(float(s.iloc[-10:].mean()))


def test_proposal_missing_prices_502_and_no_holdings_404(app_client, holdings_csv, no_holdings):
    # no_holdings fixture is applied last, so the CSV is missing
    assert app_client(FakeProvider()).get("/api/allocation/proposal").status_code == 404


def test_proposal_missing_price_is_502(app_client, holdings_csv):
    r = app_client(FakeProvider(missing={"TLT"})).get("/api/allocation/proposal")
    assert r.status_code == 502 and strict(r)["error"]["code"] == "missing_prices"
    assert strict(r)["error"]["details"]["symbols"] == ["TLT"]


def test_proposal_rejects_negative_contribution(app_client, holdings_csv):
    assert app_client(FakeProvider()).get("/api/allocation/proposal?contribution=-1").status_code == 422


# ================================================================ risk status
def test_risk_status_503_when_state_missing(app_client):
    r = app_client(FakeProvider()).get("/api/risk/status")
    assert r.status_code == 503
    e = strict(r)["error"]
    assert e["code"] == "state_not_initialized" and "risk init" in e["message"]


def _seed_state(monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    rm = RiskManager()
    rm.update_equity(10_000, now - timedelta(days=3))
    rm.update_equity(10_100, now - timedelta(days=2))
    rm.update_equity(9_860, now - timedelta(days=1))
    conn = state_db.connect()

    def ledger(i, sym, side, status, reasons, age_days):
        conn.execute("INSERT INTO signal_ledger (signal_key, strategy_key, symbol, side, bar_date, created_at, status, "
                     "decision_json) VALUES (?,?,?,?,?,?,?,?)",
                     (f"k{i}", "technical:x", sym, side, "2026-10-01", (now - timedelta(days=age_days)).isoformat(),
                      status, json.dumps({"status": status, "reasons": reasons})))

    ledger(1, "QQQ", "buy", "rejected", ["not_validated"], 0)
    ledger(2, "SPY", "buy", "rejected", ["not_validated"], 2)
    ledger(3, "MSFT", "sell", "rejected", ["shorts_disabled"], 3)
    ledger(4, "XOM", "buy", "halted", ["halted: drawdown 10.0% >= halt 10%"], 4)
    ledger(5, "OLD", "buy", "rejected", ["not_validated"], 45)           # outside the 30-day window
    summary = {"decisions": [
        {"symbol": "QQQ", "direction": 1, "status": "rejected", "reasons": ["not_validated"], "mode": "paper"},   # dup of ledger
        {"symbol": "BTC-USD", "direction": 1, "status": "rejected", "reasons": ["not_executable"], "mode": "paper"}]}
    conn.execute("INSERT INTO runs (kind, started_at, finished_at, status, summary_json) VALUES ('scan',?,?,?,?)",
                 (now.isoformat(), now.isoformat(), "ok", json.dumps(summary)))
    conn.close()
    return now


def test_risk_status_happy_path(app_client, monkeypatch, tmp_path):
    _seed_state(monkeypatch, tmp_path)
    r = app_client(FakeProvider()).get("/api/risk/status")
    assert r.status_code == 200, r.text
    b = strict(r)
    assert b["sleeve_value"] == 9_860 and b["peak"] == 10_100
    assert b["drawdown"] == pytest.approx(-(10_100 - 9_860) / 10_100)
    assert b["halted"] is False and b["kill_switch"] is False and b["mode"] == "off"
    lad = {l["kind"] + str(l["drawdown"]): l for l in b["ladder"]}
    assert lad["cut0.05"]["threshold_equity"] == pytest.approx(10_100 * 0.95)
    assert lad["cut0.05"]["room"] == pytest.approx(9_860 - 10_100 * 0.95)
    assert lad["halt0.1"]["room"] == pytest.approx(9_860 - 10_100 * 0.90)
    assert lad["cut0.05"]["risk_multiplier"] == 0.5 and lad["halt0.1"]["risk_multiplier"] == 0
    lim = {x["key"]: x for x in b["limits"]}
    assert set(lim) == {"risk_per_trade", "heat", "gross", "positions", "orders_today", "daily_loss", "weekly_loss"}
    assert lim["positions"]["used"] == 0 and lim["positions"]["maximum"] == 8
    assert lim["heat"]["used"] == 0 and lim["orders_today"]["used"] == 0
    assert lim["daily_loss"]["maximum"] == config.DAILY_LOSS_STOP_PCT
    assert b["reconcile"]["broker_checked"] is False and b["reconcile"]["status"] == "clean"
    h = b["equity_history"]
    assert [p["sleeve_equity"] for p in h] == [10_000, 10_100, 9_860] and h[-1]["peak"] == 10_100
    # 4 ledger rows in the window + BTC-USD from the scan run (the QQQ scan decision duplicates a ledger row)
    assert b["decision_window_days"] == 30 and b["decision_total"] == 5
    reasons = {x["reason"]: x["count"] for x in b["decision_reasons"]}
    assert reasons == {"not_validated": 2, "shorts_disabled": 1, "halted": 1, "not_executable": 1}
    assert b["decision_reasons"][0]["reason"] == "not_validated"
    assert len(b["decisions"]) == 5
    assert b["decisions"][0]["ts"] >= b["decisions"][-1]["ts"]
    assert {d["source"] for d in b["decisions"]} == {"ledger", "scan_run"}


def test_risk_status_latest_decisions_capped_at_20(app_client, monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    RiskManager().update_equity(10_000)
    conn = state_db.connect()
    now = datetime.now(timezone.utc)
    for i in range(30):
        conn.execute("INSERT INTO signal_ledger (signal_key, symbol, side, created_at, status) VALUES (?,?,?,?,?)",
                     (f"k{i}", f"S{i}", "buy", (now - timedelta(hours=i)).isoformat(), "rejected"))
    conn.close()
    b = strict(app_client(FakeProvider()).get("/api/risk/status"))
    assert len(b["decisions"]) == 20 and b["decision_total"] == 30
    assert b["decisions"][0]["symbol"] == "S0"


def test_risk_status_halted_and_unmeasurable_limits(app_client, monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    rm = RiskManager()
    rm.update_equity(10_000)
    rm.halt("test halt")
    conn = state_db.connect()
    conn.execute("INSERT INTO orders (client_order_id, symbol, side, qty, status, created_at) "
                 "VALUES ('c1','SPY','buy',3,'filled',?)", (datetime.now(timezone.utc).isoformat(),))
    conn.close()
    b = strict(app_client(FakeProvider()).get("/api/risk/status"))
    assert b["halted"] is True and b["halt_reason"] == "test halt"
    lim = {x["key"]: x for x in b["limits"]}
    assert b["open_positions"] == 1 and lim["positions"]["used"] == 1
    assert lim["heat"]["used"] is None and lim["gross"]["used"] is None      # unknown, never invented


def test_risk_status_empty_history_for_fresh_state(app_client, monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    b = strict(app_client(FakeProvider()).get("/api/risk/status"))
    assert b["equity_history"] == [] and b["sleeve_value"] is None and b["drawdown"] is None
    assert all(l["room"] is None for l in b["ladder"])
    assert b["decisions"] == [] and b["decision_total"] == 0


def test_risk_status_kill_switch_file(app_client, monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    (tmp_path / "state" / "KILL").write_text("")
    assert strict(app_client(FakeProvider()).get("/api/risk/status"))["kill_switch"] is True


# ================================================================ equity_history persistence
def test_update_equity_persists_every_accepted_reading(monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    rm = RiskManager()
    t = datetime(2026, 3, 4, 15, tzinfo=timezone.utc)
    rm.update_equity(10_000, t)
    rm.update_equity(10_400, t + timedelta(hours=1))
    rm.update_equity(10_100, t + timedelta(hours=2))
    for bad in (float("nan"), 0, -5, float("inf")):
        with pytest.raises(ValueError):
            rm.update_equity(bad, t + timedelta(hours=3))
    conn = state_db.connect()
    rows = conn.execute("SELECT ts, sleeve_equity, peak FROM equity_history ORDER BY id").fetchall()
    assert [(r["sleeve_equity"], r["peak"]) for r in rows] == [(10_000, 10_000), (10_400, 10_400), (10_100, 10_400)]
    assert rows[0]["ts"] == t.isoformat()
    assert state_db.current_version(conn) == state_db.latest_version() >= 5


def test_update_equity_rollback_leaves_no_history(monkeypatch, tmp_path):
    init_state(monkeypatch, tmp_path)
    conn = state_db.connect()
    conn.execute("DROP TABLE equity_history")        # an insert failure must roll the whole update back
    rm = RiskManager(conn)
    with pytest.raises(sqlite3.OperationalError):
        rm.update_equity(10_000)
    assert conn.execute("SELECT peak_equity FROM risk_state").fetchone()[0] is None


# ================================================================ scanner candidate
LONG_START, LONG_N = "2017-01-02", 2400          # ~9 years, spans HOLDOUT_START


@pytest.fixture
def fast_validation(monkeypatch):
    import validation
    orig = validation.evaluate_candidates
    monkeypatch.setattr(validation, "evaluate_candidates", lambda *a, **k: orig(*a, **{**k, "draws": 60}))


@pytest.fixture
def tmp_results(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")


PATTERN = "ma_crossover"


def _pattern():
    from patterns import PATTERN_REGISTRY
    return PATTERN if PATTERN in PATTERN_REGISTRY else sorted(PATTERN_REGISTRY)[0]


def test_candidate_happy_path(app_client, fast_validation, tmp_results):
    pat = _pattern()
    prov = FakeProvider(n=LONG_N, start=LONG_START)
    r = app_client(prov).get(f"/api/scanner/candidate?symbol=SPY&pattern={pat}")
    assert r.status_code == 200, r.text
    b = strict(r)
    assert b["symbol"] == "SPY" and b["pattern"] == pat and b["holdout_start"] == config.HOLDOUT_START
    assert b["oos_curve"] and all(p["date"] < config.HOLDOUT_START for p in b["oos_curve"])
    assert b["oos_return"] == pytest.approx(b["oos_curve"][-1]["value"], abs=1e-6)
    assert b["n_oos_trades"] == len(b["oos_trade_returns"])
    keys = [g["key"] for g in b["gates"]]
    assert keys == ["oos_trades", "psr", "bh", "dsr", "pbo", "holdout", "cost", "delay"]
    g = {x["key"]: x for x in b["gates"]}
    assert g["oos_trades"]["threshold"] == config.MIN_TRADES_OOS and g["oos_trades"]["value"] == b["n_oos_trades"]
    assert g["oos_trades"]["status"] == ("pass" if b["n_oos_trades"] >= config.MIN_TRADES_OOS else "fail")
    assert g["psr"]["threshold"] == config.OOS_PSR_MIN
    assert g["delay"]["status"] == "recorded" and g["delay"]["gating"] is False
    assert g["dsr"]["gating"] is True and g["dsr"]["threshold"] == config.DSR_P_MAX and g["dsr"]["comparator"] == "<"
    assert g["dsr"]["status"] == "unavailable" and g["dsr"]["value"] is None       # no nightly run to take N from
    assert g["pbo"]["gating"] is False and "advisory" in g["pbo"]["name"] and "advisory" in (g["pbo"]["note"] or "")
    assert g["bh"]["status"] == "unavailable" and "not in last scan" in g["bh"]["note"]   # no nightly file
    assert b["nightly"]["in_last_scan"] is False and b["nightly"]["label"].startswith("not in last scan")
    assert b["verdict"] == "alert_only" and b["gates_failed"] >= 1      # BH gate unavailable can never validate
    assert 0 < len(b["bars"]) <= 120 and b["bars"][-1]["date"].startswith(prov.frame("SPY").index[-1].date().isoformat())
    for t in b["trades"]:
        assert t["entry_price"] > 0 and (t["exit_price"] is None or t["exit_price"] > 0)
    # no frozen hold-out read -> no hold-out numbers anywhere (D11: one read per strategy version)
    assert b["holdout_frozen"] is False and b["holdout_return"] is None
    assert g["holdout"]["status"] == "unavailable" and g["holdout"]["value"] is None
    assert "not read yet" in g["holdout"]["note"]
    assert all(t["pnl_pct"] is None and t["exit_price"] is None and t["exit_reason"] is None and t["exit_date"] is None
               for t in b["trades"] if t["in_holdout"])
    assert "holdout_negative" not in b["rejected_reasons"] and "holdout_not_read" in b["rejected_reasons"]
    if b["in_sample_curve"] and b["oos_curve"]:
        assert b["in_sample_curve"][-1]["date"] <= b["oos_curve"][-1]["date"]
    if b["win_rate"] is not None and b["n_oos_trades"]:
        assert b["win_rate_ci_low"] <= b["win_rate"] <= b["win_rate_ci_high"]
    assert "long-only" in b["variant"] and "ATR" in b["variant"]
    assert b["last_price"] == pytest.approx(float(prov.frame("SPY")["Close"].iloc[-1]))


def test_candidate_uses_nightly_bh_q_when_present(app_client, fast_validation, tmp_results):
    pat = _pattern()
    store.write_result("technical", [{"symbol": "SPY", "pattern": pat, "bh_adjusted_p": 0.31,
                                      "validation_status": "unvalidated", "n_trials": 780, "dsr_p": 0.42}])
    store.write_result("funnel", {"tested": 780, "min_trades": 120, "oos_positive": 60, "psr": 20, "bh": 3, "dsr": 0,
                                  "orders": 0, "n_trials": 780, "pbo": 0.55, "sharpe_var": 0.0004})
    b = strict(app_client(FakeProvider(n=LONG_N, start=LONG_START)).get(f"/api/scanner/candidate?symbol=SPY&pattern={pat}"))
    g = {x["key"]: x for x in b["gates"]}
    assert g["bh"]["value"] == 0.31 and g["bh"]["status"] == "fail" and g["bh"]["threshold"] == config.FDR_ALPHA
    assert b["nightly"]["in_last_scan"] is True and b["nightly"]["tested"] == 780
    assert g["dsr"]["value"] == 0.42 and g["dsr"]["status"] == "fail" and "N = 780" in g["dsr"]["note"]
    assert g["pbo"]["value"] == 0.55 and g["pbo"]["status"] == "recorded" and g["pbo"]["gating"] is False
    assert b["nightly"]["dsr_p"] == 0.42 and b["nightly"]["n_trials"] == 780 and b["nightly"]["pbo"] == 0.55
    assert b["verdict"] == "alert_only"
    # a different symbol is not in that scan
    b2 = strict(app_client(FakeProvider(n=LONG_N, start=LONG_START)).get(f"/api/scanner/candidate?symbol=QQQ&pattern={pat}"))
    assert b2["nightly"]["in_last_scan"] is False and b2["nightly"]["label"].startswith("no stored q")
    # not in the scan: deflated against the latest run's N and cross-trial Sharpe variance
    g2 = {x["key"]: x for x in b2["gates"]}
    assert g2["dsr"]["status"] in ("pass", "fail") and g2["dsr"]["value"] is not None
    assert "latest nightly run" in g2["dsr"]["note"] and "N = 780" in g2["dsr"]["note"]


def test_candidate_errors(app_client, tmp_results):
    c = app_client(FakeProvider(missing={"ZZZZ"}))
    assert c.get("/api/scanner/candidate?symbol=ZZZZ&pattern=" + _pattern()).status_code == 404
    r = c.get("/api/scanner/candidate?symbol=SPY&pattern=nope")
    assert r.status_code == 422 and strict(r)["error"]["code"] == "validation_error"
    assert c.get("/api/scanner/candidate?symbol=SPY").status_code == 422
    short = app_client(FakeProvider(n=300, start="2025-01-01")).get(
        "/api/scanner/candidate?symbol=SPY&pattern=" + _pattern())
    assert short.status_code == 422 and strict(short)["error"]["code"] == "insufficient_data"


def test_candidate_does_not_write_a_holdout_read(app_client, fast_validation, tmp_results, tmp_path):
    app_client(FakeProvider(n=LONG_N, start=LONG_START)).get(f"/api/scanner/candidate?symbol=SPY&pattern={_pattern()}")
    assert not (tmp_path / "state" / "holdout_reads.sqlite").exists()       # a GET never freezes a hold-out read


def test_candidate_service_reuses_a_frozen_holdout_read(monkeypatch, tmp_path):
    from state.holdout import HoldoutStore
    path = tmp_path / "state" / "holdout_reads.sqlite"
    hs = HoldoutStore(str(path))
    hs.put("v", "SPY|k", {"result": {"total_return": 0.1}, "asof": "x"})
    ro = candidate_service._holdout_store()
    assert ro.get("v", "SPY|k")["result"]["total_return"] == 0.1
    assert ro.put("v", "other", {"result": {}, "asof": "y"}) is False
    assert hs.get("v", "other") is None
    ro.close()


# ================================================================ funnel
class _R:
    def __init__(self, n, tot, psr, bh, status, dsr_p=None, n_trials=6, pbo=None):
        self.n_oos_trades, self.oos, self.oos_psr = n, {"total_return": tot}, psr
        self.bh_significant, self.validation_status = bh, status
        self.dsr_p, self.n_trials, self.pbo = dsr_p, n_trials, pbo


_FUNNEL_STAGES = ("tested", "min_trades", "oos_positive", "psr", "bh", "dsr", "orders")


def test_funnel_counts_are_sequential():
    rs = [_R(5, 0.1, 0.99, True, "unvalidated", 0.01),        # too few trades
          _R(40, -0.1, 0.99, True, "unvalidated", 0.01),      # negative OOS
          _R(40, 0.1, 0.90, True, "unvalidated", 0.01),       # PSR too low
          _R(40, 0.1, 0.99, False, "unvalidated", 0.01),      # BH fails
          _R(40, 0.1, 0.99, True, "oos_validated", 0.30),     # BH passes, does not survive the deflated Sharpe
          _R(40, 0.1, 0.99, True, "unvalidated", 0.01),       # survives DSR, fails hold-out / cost
          _R(40, 0.1, 0.99, True, "deflated_validated", 0.01, pbo=0.4)]
    f = scan.funnel_counts(rs)
    assert {k: f[k] for k in _FUNNEL_STAGES} == {"tested": 7, "min_trades": 6, "oos_positive": 5, "psr": 4, "bh": 3,
                                                  "dsr": 2, "orders": 1}
    assert f["n_trials"] == 6 and f["pbo"] == 0.4
    empty = scan.funnel_counts([])
    assert {k: empty[k] for k in _FUNNEL_STAGES} == {k: 0 for k in _FUNNEL_STAGES}
    assert empty["n_trials"] == 0 and empty["pbo"] is None


def test_oos_validated_without_deflation_is_not_an_order():
    """A candidate that clears every OOS gate but not the DSR must not count as an order (D15)."""
    f = scan.funnel_counts([_R(40, 0.1, 0.99, True, "oos_validated", 0.30)])
    assert f["bh"] == 1 and f["dsr"] == 0 and f["orders"] == 0


def test_technical_scan_reports_funnel(monkeypatch, tmp_path):
    prov = FakeProvider(n=LONG_N, start=LONG_START)
    import validation
    orig = validation.evaluate_candidates
    monkeypatch.setattr(validation, "evaluate_candidates", lambda *a, **k: orig(*a, **{**k, "draws": 30}))
    monkeypatch.setattr(scan.sess_mod, "_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan, "_holdout_store", lambda: None)
    pats = [_pattern()]
    funnel: dict = {}
    scan.scan_technical({"SPY": prov.frame("SPY")}, pats, funnel_out=funnel)
    assert set(funnel) == set(_FUNNEL_STAGES) | {"n_trials", "pbo", "run_id", "sharpe_var"}
    assert funnel["tested"] == 1
    vals = [funnel[k] for k in _FUNNEL_STAGES]
    assert vals == sorted(vals, reverse=True)
    # ONE registry per run (1 trial recorded); N is floored at the full nightly universe (a narrowed scan is not
    # deflated less than the full one); its parquet exists, and nothing touched trading.db
    assert funnel["run_id"].startswith("tech-") and funnel["n_trials"] == validation.universe_trials() > 1
    assert (tmp_path / "trials" / f"{funnel['run_id']}.parquet").is_file()
    assert (tmp_path / "state" / "trials.sqlite").is_file()
    assert not (tmp_path / "state" / "trading.db").exists()


def test_registry_failure_fails_closed(monkeypatch):
    """If the trial registry cannot be built nothing is order-eligible (like the hold-out store)."""
    prov = FakeProvider(n=LONG_N, start=LONG_START)
    monkeypatch.setattr(scan.sess_mod, "_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan, "_holdout_store", lambda: None)

    def boom(run_id):
        raise RuntimeError("no registry")
    monkeypatch.setattr(scan.trial_runs, "new_registry", boom)
    funnel: dict = {}
    scan.scan_technical({"SPY": prov.frame("SPY")}, [_pattern()], funnel_out=funnel)
    assert funnel["tested"] == 0 and funnel["orders"] == 0


@pytest.fixture
def sched_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    return tmp_path


def test_nightly_stores_funnel_and_results_route_serves_it(sched_env, app_client):
    funnel = {"tested": 780, "min_trades": 300, "oos_positive": 120, "psr": 30, "bh": 4, "orders": 1}
    out = scheduler.run_nightly(modes=["technical"], run_stress=False,
                                scan_fn=lambda **k: {"technical": [], "technical_candidates": [],
                                                     "technical_funnel": funnel},
                                lock_path=sched_env / "l.lock", results_root=None)
    assert out["status"] == "ok" and out["summary"]["funnel"] == funnel
    assert store.read_latest("funnel")[0] == funnel
    r = app_client(FakeProvider()).get("/api/results/technical/latest")
    assert r.status_code == 200 and strict(r)["funnel"] == funnel


def test_results_route_without_funnel_is_null(sched_env, app_client):
    store.write_result("technical", [])
    assert strict(app_client(FakeProvider()).get("/api/results/technical/latest"))["funnel"] is None


def test_proposal_cash_after_is_independent_of_the_reference(app_client, holdings_csv):
    b = strict(app_client(FakeProvider()).get("/api/allocation/proposal?contribution=2000"))
    buys = sum(t["amount"] for t in b["trades"] if t["action"] == "buy")
    sells = sum(t["amount"] for t in b["trades"] if t["action"] == "sell")
    assert sells > 0 or buys > 0
    assert b["cash_after"] == pytest.approx(HOLDINGS["CASH"] + 2000 + sells - buys, abs=0.01)


def test_overview_has_no_other_account_when_all_holdings_are_core_or_trend(app_client, holdings_csv):
    b = strict(app_client(FakeProvider()).get("/api/portfolio/overview"))
    assert "other" not in [a["key"] for a in b["accounts"]]


def test_technical_funnel_only_attached_from_the_same_run(app_client, tmp_results):
    from datetime import datetime, timedelta, timezone
    store.write_result("technical", [])
    store.write_result("funnel", {"tested": 5, "min_trades": 1, "oos_positive": 1, "psr": 1, "bh": 0, "orders": 0})
    assert app_client(FakeProvider()).get("/api/results/technical/latest").json()["funnel"]["tested"] == 5
    old = datetime.now(timezone.utc) - timedelta(days=3)
    store.write_result("funnel", {"tested": 9, "min_trades": 1, "oos_positive": 1, "psr": 1, "bh": 0, "orders": 0}, now=old)
    assert app_client(FakeProvider()).get("/api/results/technical/latest").json()["funnel"] is None
