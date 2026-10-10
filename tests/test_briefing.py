"""Nightly Claude briefing (D17): fake client, no network."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import claude_integration as ci
import config
import scheduler
from api import errors
from api.concurrency import HEAVY
from results import store
from routes import briefing_routes
from services import briefing
from state import db as state_db

NOW = datetime(2026, 10, 6, 22, 0, tzinfo=timezone.utc)
GOOD = ci.NightlyBriefing(headline="2 candidates, none validated", observations=["AAA oos_psr 0.4"],
                          risks=["no halt"], what_changed=[])


def _resp(stop="end_turn", parsed=GOOD, usage=None, **kw):
    u = usage or dict(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=None,
                      cache_read_input_tokens=2000)
    text = kw.get("text", parsed.model_dump_json() if parsed is not None else "")
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                           stop_details=kw.get("stop_details"), usage=SimpleNamespace(**u), _request_id="req_n",
                           model=kw.get("model", config.CLAUDE_MODEL))


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []
        self.messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def with_options(self, **kw):
        return self

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return _Stream(self.resp)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(config, "LLM_BRIEFING_ENABLED", True)
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_USD", 0.50)
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 5.00)
    monkeypatch.setattr(briefing, "_allocation_context", lambda: None)
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    return tmp_path


@pytest.fixture
def client(env, monkeypatch):
    def _use(c):
        monkeypatch.setattr(ci, "_get_client", lambda: c)
        return c
    return _use


def _gen(root=None):
    return briefing.generate(lambda: ([{"symbol": "AAA", "validation_status": "unvalidated"}], [], [], {}),
                             root=root, now=NOW)


def _ledger():
    return [r for r in briefing._ledger_rows() if r["status"] != "reserved"]


# ---------------- cost math ----------------

def test_cost_math_known_model_and_none_as_zero():
    u = dict(input_tokens=1_000_000, output_tokens=1_000_000, cache_creation_input_tokens=1_000_000,
             cache_read_input_tokens=1_000_000)
    assert ci.compute_cost("claude-opus-5-5", u) == (4.00 + 20.00 + 0.20 + 5.00, False)
    assert ci.compute_cost("claude-opus-5-5", dict(input_tokens=500, output_tokens=None)) == (0.002, False)
    assert ci.compute_cost("claude-opus-5-5", None) == (0.0, False)


def test_cost_unknown_model_is_estimated_at_the_dearest_prices():
    cost, est = ci.compute_cost("claude-future-9", dict(input_tokens=1_000_000))
    assert cost == 5.00 and est is True


# ---------------- generate ----------------

def test_success_request_shape_ledger_and_store(env, client):
    c = client(FakeClient(_resp()))
    doc = _gen()
    assert doc["status"] == "ok" and doc["briefing"]["headline"].startswith("2 candidates")
    assert doc["request_id"] == "req_n" and doc["model"] == config.CLAUDE_MODEL
    assert doc["usage"] == dict(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                                cache_read_input_tokens=2000)
    assert doc["cost_usd"] == pytest.approx((4000 + 10000 + 400) / 1e6) and doc["cost_estimated"] is False
    kw = c.calls[0]
    assert kw["model"] == config.CLAUDE_MODEL and "output_format" not in kw
    assert kw["system"] == [{"type": "text", "text": ci.STABLE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    assert kw["output_config"]["effort"] == config.LLM_EFFORT
    assert kw["output_config"]["format"]["type"] == "json_schema" \
        and kw["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert not {"temperature", "top_p", "thinking"} & set(kw) and kw["messages"][-1]["role"] == "user"
    assert "2026-10-06" in kw["messages"][0]["content"] and "2026" not in ci.STABLE_SYSTEM_PROMPT
    rows = _ledger()
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["cost_usd"] == doc["cost_usd"]
    r = briefing.latest(now=NOW)
    assert r.status == "ok" and r.briefing.headline and r.budget.spent_today_usd == doc["cost_usd"] \
        and "instruction to trade" in r.advisory


def test_refusal_records_category(env, client):
    client(FakeClient(_resp(stop="refusal", stop_details=SimpleNamespace(category="cyber"))))
    doc = _gen()
    assert doc["status"] == "error" and doc["reason"] == "refusal" and "cyber" in doc["error"]
    assert _ledger()[0]["status"] == "refusal"


def test_max_tokens_is_truncated(env, client):
    client(FakeClient(_resp(stop="max_tokens", parsed=None, text='{"headline": "cut off', usage=dict(
        input_tokens=1000, output_tokens=6000, cache_creation_input_tokens=0, cache_read_input_tokens=0))))
    doc = _gen()
    assert doc["status"] == "error" and doc["reason"] == "truncated" and doc["briefing"] is None
    assert doc["cost_usd"] == pytest.approx((4000 + 120000) / 1e6)      # the output tokens are billed and recorded
    assert _ledger()[0]["status"] == "truncated" and _ledger()[0]["cost_usd"] == doc["cost_usd"]


def test_truncated_real_beta_message_is_costed(env, client):
    from anthropic.types.beta import BetaMessage
    m = BetaMessage.model_construct(
        id="msg_1", type="message", role="assistant", model=config.CLAUDE_MODEL, stop_reason="max_tokens",
        stop_sequence=None, content=[SimpleNamespace(type="text", text='{"headline": "x", "observ')],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=6000, cache_creation_input_tokens=None,
                              cache_read_input_tokens=None))
    client(FakeClient(m))
    doc = _gen()
    assert doc["reason"] == "truncated" and doc["cost_usd"] > 0 and _ledger()[0]["cost_usd"] == doc["cost_usd"]


def test_invalid_json_keeps_usage_and_cost(env, client):
    client(FakeClient(_resp(parsed=None, text="not json")))
    doc = _gen()
    assert doc["reason"] == "invalid_output" and doc["cost_usd"] > 0


def test_fallback_model_is_costed_as_served_and_flagged(env, client):
    client(FakeClient(_resp(model="claude-other-9")))
    doc = _gen()
    assert doc["status"] == "ok" and doc["cost_estimated"] is True and _ledger()[0]["model"] == "claude-other-9"


def test_reservation_blocks_a_second_call_until_released(env):
    briefing.record_call("m", "reserved", None, 0.49, True, row_id="r1")
    assert briefing.spend()[0] == 0.49
    briefing.record_call("m", "ok", None, 0.01, False, releases="r1")
    assert briefing.spend()[0] == 0.01


def test_ledger_write_failure_fails_closed(env, client, monkeypatch):
    c = client(FakeClient(_resp()))
    monkeypatch.setattr(briefing, "record_call", lambda *a, **k: False)
    doc = _gen()
    assert doc["status"] == "skipped" and doc["reason"] == "ledger" and c.calls == []


def test_failed_attempt_keeps_the_last_good_briefing(env, client):
    client(FakeClient(_resp()))
    _gen()
    client(FakeClient(exc=RuntimeError("down")))
    doc = _gen()
    assert doc["status"] == "error"
    r = briefing.latest()
    assert r.status == "ok" and r.briefing.headline and r.last_attempt.status == "error" \
        and r.last_attempt.reason == "api_error"
    client(FakeClient(_resp()))
    assert briefing.latest().last_attempt is not None
    _gen()
    assert briefing.latest().last_attempt is None


def test_technical_candidate_models_reach_the_prompt(env, client):
    from services.models import TechnicalCandidate
    c = client(FakeClient(_resp()))
    cand = TechnicalCandidate.model_construct(symbol="ZZZQ", pattern="p", validation_status="deflated_validated")
    briefing.generate_nightly({"technical_candidates": [cand], "pairs": [], "ml": []}, conn=None, now=NOW)
    text = c.calls[0]["messages"][0]["content"]
    assert "ZZZQ" in text and '"deflated_validated":1' in text


def test_rate_limit_error_is_stored_not_raised(env, client):
    r = httpx2.Response(429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    client(FakeClient(exc=anthropic.RateLimitError("slow down", response=r, body=None)))
    doc = _gen()
    assert doc["status"] == "error" and doc["reason"] == "rate_limited"
    assert _ledger()[0]["status"] == "rate_limited" and _ledger()[0]["cost_usd"] == 0


def test_timeout_keeps_the_reservation_so_the_cap_still_trips(env, client):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    c = client(FakeClient(exc=anthropic.APITimeoutError(request=req)))
    doc = _gen()
    assert doc["status"] == "error" and doc["reason"] == "timeout" and doc["cost_estimated"] is True
    today, _ = briefing.spend(now=NOW)
    assert today >= doc["cost_usd"] > 0 and len(c.calls) == 1
    for _ in range(5):
        _gen()                      # the unbilled-looking failures add up until a call is skipped
    assert _gen()["reason"] == "budget" and len(c.calls) < 6


def test_lock_file_is_taken_around_the_reservation(env, client):
    client(FakeClient(_resp()))
    _gen()
    assert (env / "results" / briefing.LOCK_NAME).exists()


def test_no_api_key_skips_without_a_call(env, monkeypatch):
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(ci, "_client", None)
    doc = _gen()
    assert doc["status"] == "skipped" and doc["reason"] == "no_api_key" and _ledger() == []


def test_disabled_skips(env, client, monkeypatch):
    c = client(FakeClient(_resp()))
    monkeypatch.setattr(config, "LLM_BRIEFING_ENABLED", False)
    assert _gen()["reason"] == "disabled" and c.calls == []


def test_budget_skip_daily_and_monthly(env, client, monkeypatch):
    c = client(FakeClient(_resp()))
    briefing.record_call("claude-opus-5-5", "ok", None, 0.45, False, root=None, now=NOW)
    doc = _gen()
    assert doc["status"] == "skipped" and doc["reason"] == "budget" and c.calls == []
    monkeypatch.setattr(config, "LLM_DAILY_BUDGET_USD", 100.0)       # daily fine, month exhausted
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 0.50)
    assert _gen()["reason"] == "budget" and c.calls == []
    monkeypatch.setattr(config, "LLM_MONTHLY_BUDGET_USD", 5.0)
    assert _gen()["status"] == "ok" and len(c.calls) == 1


def test_spend_windows(env):
    briefing.record_call("m", "ok", None, 0.10, False, now=datetime(2026, 10, 5, 12, tzinfo=timezone.utc))
    briefing.record_call("m", "ok", None, 0.20, False, now=NOW)
    briefing.record_call("m", "ok", None, 9.00, False, now=datetime(2026, 9, 30, 12, tzinfo=timezone.utc))
    assert briefing.spend(now=NOW) == (0.20, pytest.approx(0.30))


def test_previous_briefing_is_fed_back(env, client):
    c = client(FakeClient(_resp()))
    _gen()
    briefing.generate(lambda: ([], [], [], {}), now=datetime(2026, 10, 7, 22, tzinfo=timezone.utc))
    assert "previous_briefing" in c.calls[1]["messages"][0]["content"]
    assert "AAA oos_psr 0.4" in c.calls[1]["messages"][0]["content"]


def test_context_failure_is_a_stored_error(env, client):
    client(FakeClient(_resp()))

    def boom():
        raise RuntimeError("ctx")
    doc = briefing.generate(boom, now=NOW)
    assert doc["status"] == "error" and doc["reason"] == "internal"


def test_context_has_numbers_only_no_account_ids(env):
    from account.model import AccountSnapshot  # noqa: F401
    t, p, m, extras = briefing.build_context([{"symbol": "AAA", "validation_status": "deflated_validated",
                                               "noise": 1}], [], [], {"tested": 5}, include_allocation=False)
    assert t == [{"symbol": "AAA", "validation_status": "deflated_validated"}]
    assert extras["technical_status_counts"] == {"deflated_validated": 1} and extras["risk"]["halted"] is False
    assert extras["account"] is None and extras["funnel"] == {"tested": 5}


# ---------------- endpoints ----------------

@pytest.fixture
def api(env):
    app = FastAPI()
    errors.install(app)
    app.include_router(briefing_routes.router, prefix="/api/briefing")
    return TestClient(app, raise_server_exceptions=False)


def test_get_none_then_ok(api, client):
    r = api.get("/api/briefing")
    assert r.status_code == 200 and r.json()["status"] == "none" and r.json()["briefing"] is None
    assert r.json()["budget"]["daily_limit_usd"] == 0.50
    client(FakeClient(_resp()))
    _gen()
    j = api.get("/api/briefing").json()
    assert j["status"] == "ok" and j["briefing"]["observations"] == ["AAA oos_psr 0.4"] and j["cost_usd"] > 0


def test_regenerate_uses_stored_results_and_budget(api, client, monkeypatch):
    store.write_result("technical", [{"symbol": "AAA", "validation_status": "unvalidated"}])
    c = client(FakeClient(_resp()))
    j = api.post("/api/briefing/regenerate").json()
    assert j["status"] == "ok" and len(c.calls) == 1
    briefing.record_call("claude-opus-5-5", "ok", None, 0.5, False)
    j = api.post("/api/briefing/regenerate").json()
    assert j["status"] == "ok" and j["briefing"] is not None and len(c.calls) == 1
    assert j["last_attempt"]["status"] == "skipped" and j["last_attempt"]["reason"] == "budget"


def test_regenerate_busy_429(api):
    assert HEAVY.acquire(blocking=False)
    try:
        r = api.post("/api/briefing/regenerate")
    finally:
        HEAVY.release()
    assert r.status_code == 429 and r.json()["error"]["code"] == "busy"


# ---------------- nightly isolation ----------------

def _scan_ok(**kw):
    return {"technical": [{"symbol": "AAA"}], "pairs": [], "ml": [], "decisions": []}


def test_nightly_writes_briefing_and_survives_its_failure(env, client, monkeypatch):
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    client(FakeClient(_resp()))
    out = scheduler.run_nightly(scan_fn=_scan_ok, lock_path=env / "l.lock", run_stress=False)
    assert out["status"] == "ok" and out["summary"]["briefing"] == "ok"
    assert store.read_latest("briefing")[0]["status"] == "ok"

    def boom(*a, **k):
        raise RuntimeError("anything")
    monkeypatch.setattr(scheduler.briefing, "generate_nightly", boom)
    out = scheduler.run_nightly(scan_fn=_scan_ok, lock_path=env / "l.lock", run_stress=False)
    assert out["status"] == "ok" and out["summary"]["briefing"].startswith("error:")


def test_full_scan_path_makes_exactly_one_api_call(env, client, monkeypatch):
    """run_nightly with the real run_full_scan: the legacy second Claude call is gone."""
    from services import scan
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "k")
    import pandas as pd
    fake = SimpleNamespace(watchlist=lambda markets=None: {"AAA": pd.DataFrame({"Close": [1.0]})},
                           ohlcv=lambda sym, *a, **k: pd.DataFrame({"Close": [1.0]}))
    monkeypatch.setattr(scan, "default_provider", lambda: fake)
    monkeypatch.setattr(scan, "fetch_research", lambda *a, **k: {})
    monkeypatch.setattr(scan, "scan_technical", lambda *a, **k: [{"symbol": "AAA"}])
    monkeypatch.setattr(scan, "scan_pairs", lambda *a, **k: [])
    monkeypatch.setattr(scan, "scan_ml", lambda *a, **k: [])
    monkeypatch.setattr(scan, "send_daily_summary", lambda *a, **k: None)
    c = client(FakeClient(_resp()))
    out = scheduler.run_nightly(lock_path=env / "l.lock", run_stress=False)
    assert out["status"] == "ok" and out["summary"].get("briefing") == "ok" and len(c.calls) == 1


def test_nightly_api_failure_does_not_fail_run(env, client, monkeypatch):
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    client(FakeClient(exc=RuntimeError("down")))
    out = scheduler.run_nightly(scan_fn=_scan_ok, lock_path=env / "l.lock", run_stress=False)
    assert out["status"] == "ok" and out["summary"]["briefing"] == "error:api_error"
