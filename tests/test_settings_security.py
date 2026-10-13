"""WS3.6: API settings, risk-config validation, bearer-token auth, secret redaction."""
import logging
import types

import pytest
from fastapi.testclient import TestClient

import config
import settings as settings_mod
from settings import RiskConfigError, Settings, validate_risk_config

SECRET = "s3cr3t-token-value-123"


def _cfg(**over):
    base = {k: getattr(config, k) for k in (
        "RISK_PER_TRADE_PCT", "MAX_SYMBOL_PCT", "MAX_GROSS_EXPOSURE_PCT", "MAX_PORTFOLIO_HEAT_PCT",
        "DRAWDOWN_LADDER", "DRAWDOWN_HALT_PCT", "MAX_ORDER_NOTIONAL", "ATR_STOP_MULT", "TRADING_MODE", "LLM_EFFORT",
        "SIGNAL_SLEEVE_EQUITY", "EQUITY_JUMP_HALT_PCT", "DAILY_LOSS_STOP_PCT", "WEEKLY_LOSS_STOP_PCT",
        "MAX_OPEN_POSITIONS", "MAX_ORDERS_PER_DAY")}
    base.update(over)
    return types.SimpleNamespace(**base)


def test_real_config_is_valid():
    validate_risk_config(config)


@pytest.mark.parametrize("over", [
    {"RISK_PER_TRADE_PCT": 0}, {"RISK_PER_TRADE_PCT": 0.02}, {"RISK_PER_TRADE_PCT": -0.01},
    {"MAX_SYMBOL_PCT": 0}, {"MAX_SYMBOL_PCT": 0.5, "MAX_GROSS_EXPOSURE_PCT": 0.4},
    {"MAX_GROSS_EXPOSURE_PCT": 1.5},
    {"MAX_PORTFOLIO_HEAT_PCT": 0}, {"MAX_PORTFOLIO_HEAT_PCT": 0.2},
    {"DRAWDOWN_LADDER": ((0.08, 0.5), (0.05, 0.25))},
    {"DRAWDOWN_LADDER": ((0.05, 0.5), (0.12, 0.25))},
    {"DRAWDOWN_HALT_PCT": 0.06},
    {"MAX_ORDER_NOTIONAL": 0}, {"ATR_STOP_MULT": 0},
    {"TRADING_MODE": "live"}, {"LLM_EFFORT": "ultra"},
    {"SIGNAL_SLEEVE_EQUITY": 0}, {"EQUITY_JUMP_HALT_PCT": 0}, {"EQUITY_JUMP_HALT_PCT": 1.0},
    {"EQUITY_JUMP_HALT_PCT": 5}, {"DAILY_LOSS_STOP_PCT": 0}, {"WEEKLY_LOSS_STOP_PCT": -1},
    {"MAX_OPEN_POSITIONS": 0}, {"MAX_ORDERS_PER_DAY": 2.5},
])
def test_bad_risk_config_rejected(over):
    with pytest.raises(RiskConfigError):
        validate_risk_config(_cfg(**over))


def test_error_lists_valid_values_and_names_field():
    with pytest.raises(RiskConfigError) as e:
        validate_risk_config(_cfg(LLM_EFFORT="ultra", TRADING_MODE="live"))
    msg = str(e.value)
    assert "LLM_EFFORT" in msg and "xhigh" in msg and "max" in msg
    assert "TRADING_MODE" in msg and "paper" in msg


def test_missing_value_rejected():
    c = _cfg()
    del c.ATR_STOP_MULT
    with pytest.raises(RiskConfigError, match="ATR_STOP_MULT"):
        validate_risk_config(c)


def test_settings_defaults_and_parsing(monkeypatch):
    for k in ("API_TOKEN", "CORS_ORIGINS", "API_BIND_HOST"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.API_TOKEN is None and s.token is None
    assert s.CORS_ORIGINS == ["http://localhost:5173"]
    assert s.API_BIND_HOST == "127.0.0.1"
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example")
    monkeypatch.setenv("API_TOKEN", "  ")
    s = Settings(_env_file=None)
    assert s.CORS_ORIGINS == ["http://a.example", "http://b.example"]
    assert s.API_TOKEN is None


def test_repr_hides_secret():
    s = Settings(API_TOKEN=SECRET, _env_file=None)
    assert s.token == SECRET
    assert SECRET not in repr(s) and SECRET not in str(s) and SECRET not in s.model_dump_json()


@pytest.fixture
def make_client(monkeypatch):
    import server

    def make(token):
        monkeypatch.setattr(server, "settings", Settings(API_TOKEN=token, _env_file=None))
        return TestClient(server.app, raise_server_exceptions=False)
    return make


def test_no_token_configured_allows_everything(make_client):
    c = make_client(None)
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/config/").status_code != 401


def test_token_required_on_api_routes(make_client):
    c = make_client(SECRET)
    assert c.get("/api/health").status_code == 200  # health stays open
    for path in ("/api/config/", "/api/data/AAPL"):
        r = c.get(path)
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["code"] == "unauthorized" and "details" in body["error"]
        assert SECRET not in r.text
    for h in ({"Authorization": "Bearer wrong"}, {"Authorization": SECRET}, {"Authorization": "Basic " + SECRET},
              {"Authorization": "Bearer "}):
        assert c.get("/api/config/", headers=h).status_code == 401


def test_valid_token_passes(make_client):
    c = make_client(SECRET)
    r = c.get("/api/config/", headers={"Authorization": f"Bearer {SECRET}"})
    assert r.status_code != 401


def test_cors_preflight_not_blocked_by_auth(make_client):
    c = make_client(SECRET)
    r = c.options("/api/config/", headers={"Origin": "http://localhost:5173",
                                           "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200


def test_cors_from_settings():
    import server
    cors = [m for m in server.app.user_middleware if m.cls.__name__ == "CORSMiddleware"][0]
    assert cors.kwargs["allow_origins"] == server.settings.CORS_ORIGINS


def test_secrets_redacted_in_logs(monkeypatch, caplog):
    import logging_setup
    fake_key = "FAKE-anthropic-key-" + "x" * 12   # built at runtime so secret scanners don't flag a fixture
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", fake_key)
    f = logging_setup.install_redaction()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "key=%s", (fake_key,), None)
    f.filter(rec)
    assert fake_key not in rec.getMessage()


def test_server_import_validates_risk_config(monkeypatch):
    import importlib
    import server
    monkeypatch.setattr(config, "RISK_PER_TRADE_PCT", 0.5)
    with pytest.raises(RiskConfigError):
        importlib.reload(server)
    monkeypatch.undo()
    importlib.reload(server)
