"""Phase 3 review fixes: CLI entry point, typed nightly payload, stale-data gating, pins, config gate, token masking."""
import logging
import os
import subprocess
import sys
import types
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import config
import scheduler
import settings as settings_mod
from data import adapters, store as bar_store
from results import store as result_store
from services import models as M, scan as scan_svc, session as session_svc
from tests.bugs.test_orders_bugs import _frame, paper_env, scan_env  # noqa: F401  (fixtures)
from tests.test_bar_store import DAY1, DAY2, put  # noqa: F401
from tests.test_data_layer import FakeResp, alpaca, alpaca_bars, sessions, yf_fake  # noqa: F401
from tests.test_results_scheduler import env  # noqa: F401  (fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- blocker 1: main.py runs as a script
def test_main_py_runs_as_a_script(tmp_path):
    r = subprocess.run([sys.executable, "main.py", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "usage" in r.stdout.lower() and "heartbeat" in r.stdout
    hb = subprocess.run([sys.executable, "main.py", "heartbeat"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                        env={**os.environ, "STATE_DB_PATH": str(tmp_path / "none" / "t.db")})
    assert hb.returncode == 1 and "FAILED" in hb.stdout


# ---------------------------------------------------------------- blocker 2: typed nightly payload
class _Provider:
    def __init__(self, df):
        self.df = df

    def ohlcv(self, symbol, period_days=None):
        return self.df

    def watchlist(self, markets=None):
        return {"AAPL": self.df}

    def pair(self, *a, **k):
        return None


def test_nightly_stores_typed_technical_candidates_the_route_serves(env, scan_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(scan_svc, "send_daily_summary", lambda *a, **k: None)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    provider = _Provider(_frame(last_signal=1))
    out = scheduler.run_nightly(modes=("technical",), run_stress=False,
                                scan_fn=lambda **kw: scan_svc.run_full_scan(provider=provider, **kw))
    assert out["status"] == "ok" and out["summary"]["technical"] >= 1
    payload, _ = result_store.read_latest("technical")
    rows = [M.TechnicalCandidate.model_validate(p) for p in payload]          # the stored rows ARE the model
    assert rows and rows[0].signal == "BUY" and isinstance(rows[0].price, float) and rows[0].days_ago >= 0
    from fastapi import FastAPI
    from api import errors
    from routes import results_routes
    app = FastAPI()
    errors.install(app)
    app.include_router(results_routes.router, prefix="/api/results")
    body = TestClient(app).get("/api/results/technical/latest").json()
    M.LatestTechnicalResult.model_validate(body)
    assert body["payload"][0]["signal"] == "BUY" and "signal_date" in body["payload"][0]


# ---------------------------------------------------------------- major: stale / not-current data is alert-only
def test_orderable_frame_rules():
    df = _frame()
    assert scan_svc.orderable_frame(df, "2024-03-22")                       # no store attrs: not second-guessed
    df.attrs.update(stale=True, last_closed="2024-03-22")
    assert not scan_svc.orderable_frame(df, "2024-03-22")                   # served stale
    df.attrs.update(stale=False, last_closed="2024-03-25")
    assert not scan_svc.orderable_frame(df, "2024-03-22")                   # not the last closed session
    assert scan_svc.orderable_frame(df, "2024-03-25")


@pytest.mark.parametrize("attrs", [{"stale": True}, {"last_closed": "2024-04-30"}])
def test_stale_or_old_signal_makes_no_order_intent(scan_env, attrs):  # noqa: F811
    fresh = _frame(last_signal=1)
    fresh.attrs.update(stale=False, last_closed=str(fresh.index[-1].date()))
    ok_intents = []
    scan_svc.scan_technical({"AAPL": fresh}, intents=ok_intents)
    assert ok_intents                                                        # control: a current frame orders
    bad = _frame(last_signal=1)
    bad.attrs.update(attrs)
    intents = []
    got = scan_svc.scan_technical({"AAPL": bad}, intents=intents)
    assert got and intents == []                                             # still alerted, never ordered


def test_store_flags_stale_served_frames(yf_fake):  # noqa: F811
    put(yf_fake, "2024-06-04")
    fresh = bar_store.get_bars("AAPL", 150, now=DAY1)
    assert fresh.df.attrs["stale"] is False
    yf_fake.frames.clear()
    stale = bar_store.get_bars("AAPL", 150, now=DAY2)
    assert stale.stale and stale.df.attrs["stale"] is True
    assert pd.Timestamp(stale.df.attrs["last_closed"]) == pd.Timestamp("2024-06-05")


# ---------------------------------------------------------------- major: the research refresh never re-pins default
def test_research_incremental_refresh_leaves_the_default_pin_alone(yf_fake):  # noqa: F811
    put(yf_fake, "2024-06-04")
    days = config.RESEARCH_LOOKBACK_DAYS
    bar_store.get_bars("AAPL", days, now=DAY1)
    assert adapters.pinned_source("AAPL", days) == "yfinance"
    adapters._set_pin("AAPL", 150, "alpaca")                                 # default window pinned elsewhere
    put(yf_fake, "2024-06-05")
    bar_store.get_bars("AAPL", days, now=DAY2)                               # research incremental refresh
    assert adapters.pinned_source("AAPL", days) == "yfinance"
    assert adapters.pinned_source("AAPL", 150) == "alpaca"


def test_empty_overlap_counts_as_readjusted():
    a = pd.DataFrame({c: [1.0] for c in ("Open", "High", "Low", "Close")}, index=pd.to_datetime(["2024-01-02"]))
    b = pd.DataFrame({c: [1.0] for c in ("Open", "High", "Low", "Close")}, index=pd.to_datetime(["2024-02-02"]))
    assert bar_store._overlap_differs(a, b) is True


def test_switching_the_alpaca_feed_refetches(alpaca, yf_fake, monkeypatch):  # noqa: F811
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(sessions("XNYS", "2023-09-01", "2024-06-04"))})
    monkeypatch.setattr(config, "ALPACA_FEED", "iex")
    bar_store.get_bars("AAPL", 150, now=DAY1)
    n = len(alpaca.calls)
    bar_store.get_bars("AAPL", 150, now=DAY1)
    assert len(alpaca.calls) == n                                            # same feed: served from the cache
    monkeypatch.setattr(config, "ALPACA_FEED", "sip")
    bar_store.get_bars("AAPL", 150, now=DAY1)
    assert len(alpaca.calls) > n                                             # feed changed: refetched


# ---------------------------------------------------------------- major: risk config gates the order path
def _bad(monkeypatch):
    monkeypatch.setattr(config, "EQUITY_JUMP_HALT_PCT", 5.0)                 # disables the equity-jump halt


def test_cli_scan_paper_refuses_a_bad_config_and_makes_no_broker_call(monkeypatch, capsys):
    import main
    _bad(monkeypatch)
    monkeypatch.setattr(session_svc, "get_broker", lambda: pytest.fail("broker constructed"))
    monkeypatch.setattr(main, "run_full_scan", lambda **k: pytest.fail("scan ran"))
    assert main.main(["scan", "--paper"]) == 2
    assert "EQUITY_JUMP_HALT_PCT" in capsys.readouterr().err


def test_cli_heartbeat_still_runs_on_a_bad_config(monkeypatch, tmp_path):
    import main
    _bad(monkeypatch)
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "x" / "t.db"))
    monkeypatch.setattr(session_svc, "alert", lambda *a, **k: True)
    assert main.main(["heartbeat"]) == 1                                     # reports the missing run, not exit 2


def test_open_session_and_nightly_refuse_a_bad_config(monkeypatch, env):  # noqa: F811
    _bad(monkeypatch)
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
    monkeypatch.setattr(session_svc, "get_broker", lambda: pytest.fail("broker constructed"))
    monkeypatch.setattr(session_svc, "_alert", lambda *a, **k: True)
    assert session_svc.open_session() is None
    out = scheduler.run_nightly(scan_fn=lambda **k: pytest.fail("scan ran"))
    assert out["status"] == "error" and "EQUITY_JUMP_HALT_PCT" in out["message"]


# ---------------------------------------------------------------- major: the API token is masked in logs
def test_api_token_is_masked_in_logs(monkeypatch, caplog):
    import logging_setup
    secret = "tok-9f8e7d6c5b4a3210"
    logging_setup.register_secret(secret)
    f = logging_setup.install_redaction()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "token=%s", (secret,), None)
    f.filter(rec)
    assert secret not in rec.getMessage()
    assert "hunter2hunter2" not in logging_setup.redact("Authorization: Bearer hunter2hunter2")
    monkeypatch.setenv("API_TOKEN", "envtoken-123456")
    assert "envtoken-123456" not in logging_setup.redact("sent envtoken-123456 here")


# ---------------------------------------------------------------- minor: auth covers every method of every route
def test_auth_is_enforced_on_every_api_route_and_method(monkeypatch):
    import server
    monkeypatch.setattr(server, "settings", settings_mod.Settings(API_TOKEN="tk-abcdef", _env_file=None))
    c = TestClient(server.app, raise_server_exceptions=False)
    seen = 0
    for path, ops in server.app.openapi()["paths"].items():
        for method in ops:
            url = path.replace("{symbol}", "AAPL").replace("{kind}", "technical")
            r = c.request(method.upper(), url, json={} if method != "get" else None)
            expect = 200 if path == "/api/health" else 401
            assert r.status_code == expect, (method, path, r.status_code)
            seen += 1
    assert seen >= 20


# --- _stored_or_fresh leakage guard (lead follow-up to review round 2) -------------------------------
class _FakeDetector:
    retrain_days = 30

    def __init__(self, meta):
        self._meta, self.model, self.trained_on = meta, None, None

    def load_latest(self, symbol):
        return self._meta

    def train(self, df):
        self.trained_on, self.model = df, object()
        return {"fresh": True}

    def load_or_train(self, df, symbol):
        raise AssertionError("stale path not expected here")


def _train_frame():
    import pandas as pd
    return pd.DataFrame({"Close": range(10)}, index=pd.bdate_range("2024-01-01", periods=10))


def _fresh_meta(end):
    import datetime as _dt
    return {"trained_at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "data": {"end": end}, "metrics": {"stored": True}}


import pytest as _pytest


@_pytest.mark.parametrize("end, reused", [("2024-01-05", True), ("2024-03-01", False), (None, False)])
def test_stored_model_reused_only_when_trained_before_the_view(monkeypatch, end, reused):
    from services import ml as svc
    monkeypatch.setattr(svc.ml_store, "is_stale", lambda meta, days: False)
    det = _FakeDetector(_fresh_meta(end))
    out = svc._stored_or_fresh(det, _train_frame(), "AAA")
    assert (out.get("metrics") == {"stored": True}) is reused
    assert (det.trained_on is None) is reused
