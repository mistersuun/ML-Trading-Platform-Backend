"""Offline tests for claude_integration (fake client, no network)."""
import ast
import json
import pathlib
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

import claude_integration as ci
import config


def _resp(stop="end_turn", **kw):
    return SimpleNamespace(
        stop_reason=stop, stop_details=kw.get("stop_details"), model=config.CLAUDE_MODEL,
        content=[SimpleNamespace(type="text", text=ci.NightlyBriefing().model_dump_json())],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5), _request_id="req_x",
    )


class FakeClient:
    """Stands in for anthropic.Anthropic: records stream() kwargs, optionally raises."""
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls, self.options = resp, exc, [], []
        self.messages = SimpleNamespace(stream=self._stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    def _stream(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return _Stream(self.resp)


class _Stream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.resp


@pytest.fixture
def use_client(monkeypatch):
    def _use(client):
        monkeypatch.setattr(ci, "_get_client", lambda: client)
        return client
    return _use


def _http_exc(cls, status):
    r = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls("boom", response=r, body=None)


def test_nightly_is_streamed_once_without_sdk_retries(use_client):
    c = use_client(FakeClient(_resp()))
    r = ci.generate_nightly("x")
    assert r.ok and r.request_id == "req_x" and c.options == [{"max_retries": 0}]
    assert "temperature" not in c.calls[0]


def test_not_configured(monkeypatch):
    monkeypatch.setattr(ci, "_get_client", lambda: None)
    assert ci.generate_nightly("x").error is ci.LLMError.NOT_CONFIGURED


@pytest.mark.parametrize("exc,err,maybe_billed", [
    (_http_exc(anthropic.RateLimitError, 429), ci.LLMError.RATE_LIMITED, False),
    (_http_exc(anthropic.NotFoundError, 404), ci.LLMError.NOT_FOUND, False),
    (_http_exc(anthropic.AuthenticationError, 401), ci.LLMError.AUTH, False),
    (_http_exc(anthropic.BadRequestError, 400), ci.LLMError.API_ERROR, False),
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), ci.LLMError.TIMEOUT, True),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), ci.LLMError.CONNECTION, True),
])
def test_exception_mapping(use_client, exc, err, maybe_billed):
    use_client(FakeClient(exc=exc))
    r = ci.generate_nightly("x")
    assert not r.ok and r.error is err and r.maybe_billed is maybe_billed


def test_prepare_input_finite_capped_topk():
    sigs = [{"symbol": f"S{i:03d}", "profit_factor": 1e10 if i == 0 else 1.5,
             "win_rate": float("nan") if i == 1 else 0.5, "x": float("inf")} for i in range(500)]
    out = ci.prepare_nightly_input(sigs, [], [])
    data = json.loads(out)  # also strict: no NaN tokens
    assert "NaN" not in out and "Infinity" not in out
    t = data["technical"]
    assert len(t["signals"]) == 30 and t["omitted"] == 470
    assert all(s["x"] is None for s in t["signals"])
    capped = [s for s in t["signals"] if s.get("profit_factor_capped")]
    assert capped and all(s["profit_factor"] == 10.0 for s in capped)
    assert out == ci.prepare_nightly_input(list(reversed(sigs)), [], [])  # deterministic


def test_fallback_switch(use_client, monkeypatch):
    c = use_client(FakeClient(_resp()))
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "default")
    ci.generate_nightly("x")
    assert c.calls[0]["fallbacks"] == "default" and c.calls[0]["betas"] == [ci.FALLBACK_BETA]
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "off")
    ci.generate_nightly("x")
    assert "fallbacks" not in c.calls[1]


def test_fallback_iterations_are_summed_per_model(use_client, monkeypatch):
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "default")
    refused = SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=1_000_000, output_tokens=0,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0)
    fb = SimpleNamespace(type="fallback_message", model="claude-opus-5", input_tokens=0, output_tokens=1_000_000,
                         cache_creation_input_tokens=0, cache_read_input_tokens=0)
    resp = _resp()
    resp.model = "claude-opus-5"
    resp.usage = SimpleNamespace(input_tokens=0, output_tokens=1_000_000, iterations=[refused, fb])
    use_client(FakeClient(resp))
    r = ci.generate_nightly("x")
    assert r.cost_usd == 4.00 + 25.00 and r.cost_estimated is True   # declined attempt billed; served != configured
    unknown = SimpleNamespace(**{**refused.__dict__, "model": "claude-future-9"})
    resp.usage = SimpleNamespace(input_tokens=0, output_tokens=0, iterations=[unknown])
    assert ci.generate_nightly("x").cost_estimated is True


def test_unknown_model_costed_at_the_most_expensive_row():
    assert ci.compute_cost("claude-future-9", {"output_tokens": 1_000_000}) == (25.00, True)


def test_estimate_reserves_two_attempts_with_default_fallback(monkeypatch):
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "off")
    one = ci.estimate_max_cost("claude-opus-5-5", "x", 6000)
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "default")
    assert ci.estimate_max_cost("claude-opus-5-5", "x", 6000) == pytest.approx(2 * one)


def test_check_llm(monkeypatch):
    monkeypatch.setattr(ci, "_get_client", lambda: None)
    assert ci.check_llm()[0] is False
    ok = SimpleNamespace(models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))
    monkeypatch.setattr(ci, "_get_client", lambda: ok)
    assert ci.check_llm()[0] is True
    bad = SimpleNamespace(models=SimpleNamespace(retrieve=lambda m: (_ for _ in ()).throw(_http_exc(anthropic.NotFoundError, 404))))
    monkeypatch.setattr(ci, "_get_client", lambda: bad)
    ok_, msg = ci.check_llm()
    assert not ok_ and msg.startswith("not_found")


def test_no_forbidden_imports():
    tree = ast.parse(pathlib.Path(ci.__file__).read_text())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module.split(".")[0])
    assert not names & {"execution", "brokers", "paper_trader", "requests"}
