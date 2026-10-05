"""Bug-pinning tests for API / data fetching / alerts / LLM (WS0.3: API-1..4, DATA-1..3, ALR-1..2, LLM-1).

xfail(strict) tests assert the CORRECT behaviour and currently fail with AssertionError.
Everything runs offline: requests.get / requests.post are replaced with fakes.
"""
import importlib
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

import alerts
import claude_integration
import config
import data_fetcher
from routes.helpers import json_response

BUG = dict(strict=True, raises=AssertionError)


# --------------------------------------------------------------------------- helpers
def _client():
    from server import app
    return TestClient(app, raise_server_exceptions=False)


def _av_equity_payload(dates, close, adj_close, volume=1000):
    """Recorded-shape Alpha Vantage TIME_SERIES_DAILY_ADJUSTED response."""
    series = {}
    for d, c, a in zip(dates, close, adj_close):
        series[d.strftime("%Y-%m-%d")] = {
            "1. open": f"{c * 0.99:.4f}", "2. high": f"{c * 1.01:.4f}", "3. low": f"{c * 0.98:.4f}",
            "4. close": f"{c:.4f}", "5. adjusted close": f"{a:.4f}", "6. volume": str(volume),
            "7. dividend amount": "0.0000", "8. split coefficient": "1.0",
        }
    return {"Meta Data": {"2. Symbol": "X"}, "Time Series (Daily)": series}


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error")

    def json(self):
        return self._payload


@pytest.fixture
def av(monkeypatch):
    """Alpha Vantage fake: records every params dict; serves `av.payload`."""
    monkeypatch.setattr(config, "ALPHA_VANTAGE_KEY", "AVKEY")
    state = type("AV", (), {})()
    state.calls = []
    state.payload = {}

    def fake_get(url, params=None, **kw):
        state.calls.append(dict(params or {}))
        return _FakeResp(state.payload)

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)
    return state


@pytest.fixture
def telegram(monkeypatch):
    """Fake Telegram: 400 when parse_mode == 'Markdown' and text has an odd number of '_'."""
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-abc")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    state = type("TG", (), {})()
    state.sent = []

    def fake_post(url, json=None, **kw):
        resp = requests.Response()
        resp.url = url
        bad = json.get("parse_mode") == "Markdown" and json["text"].count("_") % 2 == 1
        if bad:
            resp.status_code = 400
            resp.reason = "Bad Request"
        else:
            resp.status_code = 200
            state.sent.append(json["text"])
        return resp

    monkeypatch.setattr(alerts.requests, "post", fake_post)
    return state


# --------------------------------------------------------------------------- API-1
@pytest.mark.xfail(reason="BUG-API-1: json_response 500s on NaN/inf (python/np floats bypass the encoder)", **BUG)
def test_API_1_json_response_nan_inf_does_not_500():
    app = FastAPI()

    @app.get("/x")
    def x():
        return json_response({"a": float("nan"), "b": np.float64("inf"), "c": [1.0, float("-inf")], "ok": 1})

    r = TestClient(app, raise_server_exceptions=False).get("/x")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] == 1 and body["a"] is None and body["b"] is None


# --------------------------------------------------------------------------- API-2
@pytest.mark.xfail(reason="BUG-API-2: error conditions return HTTP 200 {error: ...}", **BUG)
def test_API_2_errors_are_not_http_200(patch_fetch):
    c = _client()
    r = c.post("/api/backtest/run", json={"symbol": "SPY", "pattern_name": "no_such_pattern"})
    assert r.status_code >= 400, f"unknown pattern returned {r.status_code} {r.json()}"
    r = c.post("/api/patterns/detect", json={"symbol": "SPY", "pattern_name": "no_such_pattern"})
    assert r.status_code >= 400
    patch_fetch.overrides["NODATA"] = pd.DataFrame()
    r = c.post("/api/patterns/detect", json={"symbol": "NODATA", "pattern_name": "rsi_mean_reversion"})
    assert r.status_code >= 400


# --------------------------------------------------------------------------- API-3
@pytest.mark.xfail(reason="BUG-API-3: pattern scan pre-rounds total_return_pct to 2 decimals", **BUG)
def test_API_3_scan_total_return_not_prerounded(patch_fetch, monkeypatch):
    from backtester import classic_backtest
    from patterns import PATTERN_REGISTRY
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA"])
    r = _client().post("/api/patterns/scan",
                       json={"markets": ["unit"], "patterns": list(PATTERN_REGISTRY), "recency_days": 10 ** 6})
    assert r.status_code == 200
    sigs = r.json()["signals"]
    assert sigs, "no signals produced on synthetic data"
    from tests.conftest import _frame_for_symbol
    df = _frame_for_symbol("AAA")
    mismatches = 0
    for s in sigs:
        bt = classic_backtest(PATTERN_REGISTRY[s["pattern"]](df), "AAA", s["pattern"])
        raw = float(bt.total_return_pct)
        if abs(s["total_return_pct"] - raw) > 1e-6:
            mismatches += 1
    assert mismatches == 0, f"{mismatches}/{len(sigs)} scan results were rounded to 2 decimals"


# --------------------------------------------------------------------------- API-4 (SIG-2)
@pytest.mark.xfail(reason="BUG-API-4/SIG-2: pattern scan swallows per-pattern exceptions silently", **BUG)
def test_API_4_scan_does_not_swallow_exceptions(patch_fetch, monkeypatch, caplog):
    import backtester
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA"])

    def boom(*a, **k):
        raise RuntimeError("backtest exploded")

    monkeypatch.setattr(backtester, "classic_backtest", boom)
    with caplog.at_level(logging.DEBUG):
        r = _client().post("/api/patterns/scan",
                           json={"markets": ["unit"], "recency_days": 10 ** 6})
    body = r.json() if r.status_code == 200 else {}
    surfaced = (r.status_code >= 400 or bool(body.get("errors")) or bool(body.get("failed"))
                or any(rec.levelno >= logging.WARNING and "exploded" in rec.getMessage() for rec in caplog.records))
    assert surfaced, "scan hid RuntimeError: HTTP 200, no errors field, nothing logged"


# --------------------------------------------------------------------------- DATA-1
@pytest.mark.xfail(reason="BUG-DATA-1: '=F' futures symbol stripped and sent to Alpha Vantage as an equity", **BUG)
def test_DATA_1_futures_symbol_not_sent_as_equity(av):
    av.payload = _av_equity_payload(pd.bdate_range("2024-01-01", periods=60), [100.0] * 60, [100.0] * 60)
    df = data_fetcher.fetch_ohlcv("GC=F", period_days=200, end_date=datetime(2024, 6, 1), sources=["alphavantage"])
    equity_calls = [c for c in av.calls if c.get("function") == "TIME_SERIES_DAILY_ADJUSTED"]
    assert not equity_calls, f"sent futures symbol as equity: {equity_calls}"
    assert df.empty


# --------------------------------------------------------------------------- DATA-2
@pytest.mark.xfail(reason="BUG-DATA-2: adjusted Close mixed with unadjusted O/H/L breaks High >= max(Open, Close)", **BUG)
def test_DATA_2_adjusted_close_consistent_with_ohl(av):
    dates = pd.bdate_range("2024-01-01", periods=60)
    close = np.full(60, 100.0)
    adj = np.where(np.arange(60) < 30, 50.0, 100.0)  # 2:1 split adjustment on the early half
    av.payload = _av_equity_payload(dates, close, adj)
    df = data_fetcher.fetch_ohlcv("AAPL", period_days=200, end_date=datetime(2024, 6, 1), sources=["alphavantage"])
    assert len(df) == 60
    assert (df["High"] >= df[["Open", "Close"]].max(axis=1) - 1e-9).all()
    assert (df["Low"] <= df[["Open", "Close"]].min(axis=1) + 1e-9).all()


# --------------------------------------------------------------------------- DATA-3
@pytest.mark.xfail(reason="BUG-DATA-3: in-progress (today's) bar is returned as if final", **BUG)
def test_DATA_3_in_progress_bar_dropped(av, frozen_now, monkeypatch):
    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now if tz is None else frozen_now.replace(tzinfo=tz)

    monkeypatch.setattr(data_fetcher, "datetime", _FrozenDT)  # clock frozen for now()-based fixes
    dates = pd.bdate_range(end=frozen_now.strftime("%Y-%m-%d"), periods=60)  # last bar == "today"
    assert dates[-1].date() == frozen_now.date()
    av.payload = _av_equity_payload(dates, [100.0] * 60, [100.0] * 60)
    df = data_fetcher.fetch_ohlcv("AAPL", period_days=200, sources=["alphavantage"])  # end_date -> frozen now()
    assert not df.empty
    assert df.index[-1].date() < frozen_now.date(), f"today's unfinished bar present: {df.index[-1]}"


# --------------------------------------------------------------------------- ALR-1
@pytest.mark.xfail(reason="BUG-ALR-1: Telegram sent with parse_mode=Markdown, underscores make the alert fail", **BUG)
def test_ALR_1_telegram_message_with_underscore_delivered(telegram, capsys):
    msg = alerts.format_signal_alert({"symbol": "SPY", "pattern": "bull_flag"}, "BUY", 100.0)
    assert msg.count("_") % 2 == 1  # sanity: triggers the Telegram Markdown parse error
    assert alerts.send_alert(msg, method="telegram") is True
    assert len(telegram.sent) == 1


# --------------------------------------------------------------------------- ALR-2
@pytest.mark.xfail(reason="BUG-ALR-2: bot token leaks into logs via the HTTPError URL", **BUG)
def test_ALR_2_bot_token_not_in_logs_on_http_error(telegram, monkeypatch, caplog):
    def fail_post(url, json=None, **kw):
        resp = requests.Response()
        resp.url = url
        resp.status_code = 401
        resp.reason = "Unauthorized"
        return resp

    monkeypatch.setattr(alerts.requests, "post", fail_post)
    with caplog.at_level(logging.DEBUG):
        assert alerts.send_alert("hello", method="telegram") is False
    assert caplog.records, "expected the failure to be logged"
    assert config.TELEGRAM_BOT_TOKEN not in caplog.text
    assert "SECRET-TOKEN" not in caplog.text


# --------------------------------------------------------------------------- LLM-1
@pytest.fixture
def reloaded_config_with_model(monkeypatch):
    saved = os.environ.get("ANTHROPIC_MODEL")
    os.environ["ANTHROPIC_MODEL"] = "claude-test-model-9"
    importlib.reload(config)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    yield "claude-test-model-9"
    if saved is None:
        os.environ.pop("ANTHROPIC_MODEL", None)
    else:
        os.environ["ANTHROPIC_MODEL"] = saved
    monkeypatch.undo()
    importlib.reload(config)


@pytest.mark.xfail(reason="BUG-LLM-1: Claude model id hard-coded in config.py, ANTHROPIC_MODEL env ignored", **BUG)
def test_LLM_1_model_id_configurable_via_env(reloaded_config_with_model, monkeypatch):
    seen = {}

    def fake_post(url, json=None, **kw):
        seen["model"] = json["model"]
        return _FakeResp({"content": [{"text": "ok"}]})

    monkeypatch.setattr(claude_integration.requests, "post", fake_post)
    claude_integration.generate_summary([], [], [])
    assert seen["model"] == reloaded_config_with_model


@pytest.mark.xfail(reason="BUG-LLM-1: Claude API failure is swallowed (returns None, indistinguishable from no key)", **BUG)
def test_LLM_1b_api_failure_is_surfaced(monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(claude_integration.requests, "post",
                        lambda *a, **k: _FakeResp({"error": "model not found"}, status=404))
    try:
        out = claude_integration.generate_summary([], [], [])
    except Exception:
        return  # raising is an acceptable way of surfacing the failure
    assert out is not None and ("fail" in out.lower() or "error" in out.lower())
