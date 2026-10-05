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


def _resp(stop="end_turn", parsed=None, **kw):
    return SimpleNamespace(
        stop_reason=stop, parsed_output=parsed, stop_details=kw.get("stop_details"),
        content=kw.get("content", []), usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        _request_id="req_x",
    )


class FakeClient:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []
        self.messages = SimpleNamespace(parse=self._parse)
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
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


def test_thinking_block_and_text_parse(use_client):
    blocks = [SimpleNamespace(type="thinking", thinking="hmm"), SimpleNamespace(type="text", text="{}")]
    b = ci.Briefing(top_opportunities=["AAPL breakout"], red_flags=["low sample"], bias="bullish", notes="n")
    c = use_client(FakeClient(_resp(parsed=b, content=blocks)))
    r = ci.generate_briefing([{"symbol": "AAPL"}], [], [])
    assert r.ok and "AAPL breakout" in r.text and "Bias: bullish" in r.text
    assert r.request_id == "req_x" and r.usage == {"input_tokens": 10, "output_tokens": 5}
    kw = c.calls[0]
    assert kw["output_config"] == {"effort": config.LLM_EFFORT} and kw["output_format"] is ci.Briefing
    assert "temperature" not in kw


def test_summary_backward_compatible(use_client, monkeypatch):
    use_client(FakeClient(_resp(parsed=ci.Briefing(bias="neutral"))))
    assert "Bias: neutral" in ci.generate_summary([], [], [])
    monkeypatch.setattr(ci, "_get_client", lambda: None)
    assert ci.generate_summary([], [], []) is None
    assert ci.generate_briefing([], [], []).error is ci.LLMError.NOT_CONFIGURED


def test_max_tokens_truncated(use_client):
    use_client(FakeClient(_resp(stop="max_tokens")))
    r = ci.generate_briefing([], [], [])
    assert not r.ok and r.error is ci.LLMError.TRUNCATED


def test_refusal(use_client):
    d = SimpleNamespace(category="cyber", explanation="no")
    use_client(FakeClient(_resp(stop="refusal", stop_details=d)))
    r = ci.generate_briefing([], [], [])
    assert r.error is ci.LLMError.REFUSAL and "cyber" in r.message


def test_invalid_output(use_client):
    use_client(FakeClient(_resp(parsed=None)))
    assert ci.generate_briefing([], [], []).error is ci.LLMError.INVALID_OUTPUT


@pytest.mark.parametrize("exc,expected", [
    (_http_exc(anthropic.RateLimitError, 429), ci.LLMError.RATE_LIMITED),
    (_http_exc(anthropic.NotFoundError, 404), ci.LLMError.NOT_FOUND),
    (_http_exc(anthropic.AuthenticationError, 401), ci.LLMError.AUTH),
    (_http_exc(anthropic.BadRequestError, 400), ci.LLMError.API_ERROR),
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), ci.LLMError.TIMEOUT),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), ci.LLMError.CONNECTION),
])
def test_error_mapping(use_client, exc, expected):
    use_client(FakeClient(exc=exc))
    r = ci.generate_briefing([], [], [])
    assert not r.ok and r.error is expected
    assert "error" in ci.generate_summary([], [], []).lower()


def test_prepare_input_finite_capped_topk():
    sigs = [{"symbol": f"S{i:03d}", "profit_factor": 1e10 if i == 0 else 1.5,
             "win_rate": float("nan") if i == 1 else 0.5, "x": float("inf")} for i in range(500)]
    out = ci.prepare_llm_input(sigs, [], [])
    data = json.loads(out)  # also strict: no NaN tokens
    assert "NaN" not in out and "Infinity" not in out
    t = data["technical"]
    assert len(t["signals"]) == 30 and t["omitted"] == 470
    assert all(s["x"] is None for s in t["signals"])
    capped = [s for s in t["signals"] if s.get("profit_factor_capped")]
    assert capped and all(s["profit_factor"] == 10.0 for s in capped)
    assert out == ci.prepare_llm_input(list(reversed(sigs)), [], [])  # deterministic


def test_fallback_switch(use_client, monkeypatch):
    c = use_client(FakeClient(_resp(parsed=ci.Briefing())))
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "default")
    ci.generate_briefing([], [], [])
    assert c.calls[0]["fallbacks"] == "default" and c.calls[0]["betas"] == [ci.FALLBACK_BETA]
    monkeypatch.setattr(config, "LLM_REFUSAL_FALLBACK", "off")
    ci.generate_briefing([], [], [])
    assert "fallbacks" not in c.calls[1]


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
