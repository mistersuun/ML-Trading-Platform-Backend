"""API-level settings (pydantic-settings) and the startup validation of the risk config (WS3.6, trimmed).

Only the API's own knobs live here (token, CORS, bind host). The rest of the configuration stays in config.py;
`validate_risk_config` checks the risk values there and fails startup with a clear message when one is unsafe.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLM_EFFORTS = ("low", "medium", "high", "xhigh", "max")
TRADING_MODES = ("off", "paper")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    API_TOKEN: SecretStr | None = None
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    API_BIND_HOST: str = "127.0.0.1"

    @field_validator("API_TOKEN", mode="before")
    @classmethod
    def _blank_token_is_none(cls, v: Any):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: Any):
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json
                return json.loads(s)
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    @property
    def token(self) -> str | None:
        return self.API_TOKEN.get_secret_value() if self.API_TOKEN else None


def get_settings() -> Settings:
    return Settings()


class RiskConfigError(ValueError):
    """The risk configuration is unsafe or malformed; the message lists every problem."""


def validate_risk_config(config_module) -> None:
    """Raise RiskConfigError (listing all problems) unless the risk limits are inside their safe ranges."""
    c = config_module
    problems: list[str] = []

    def get(name):
        if not hasattr(c, name):
            problems.append(f"{name} is missing")
            return None
        return getattr(c, name)

    def num(name):
        v = get(name)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
            problems.append(f"{name}={v!r} must be a number")
            return None
        return float(v)

    r = num("RISK_PER_TRADE_PCT")
    if r is not None and not 0 < r <= 0.01:
        problems.append(f"RISK_PER_TRADE_PCT={r} must satisfy 0 < value <= 0.01")
    sym, gross = num("MAX_SYMBOL_PCT"), num("MAX_GROSS_EXPOSURE_PCT")
    if sym is not None and gross is not None and not 0 < sym <= gross <= 1:
        problems.append(f"MAX_SYMBOL_PCT={sym} and MAX_GROSS_EXPOSURE_PCT={gross} must satisfy "
                        "0 < MAX_SYMBOL_PCT <= MAX_GROSS_EXPOSURE_PCT <= 1")
    heat = num("MAX_PORTFOLIO_HEAT_PCT")
    if heat is not None and not 0 < heat <= 0.1:
        problems.append(f"MAX_PORTFOLIO_HEAT_PCT={heat} must satisfy 0 < value <= 0.1")
    halt = num("DRAWDOWN_HALT_PCT")
    ladder = get("DRAWDOWN_LADDER")
    if ladder is not None:
        try:
            thresholds = [float(t) for t, _ in ladder]
        except (TypeError, ValueError):
            problems.append(f"DRAWDOWN_LADDER={ladder!r} must be a sequence of (threshold, multiplier) pairs")
            thresholds = None
        if thresholds is not None:
            if any(b <= a for a, b in zip(thresholds, thresholds[1:])) or any(t <= 0 for t in thresholds):
                problems.append(f"DRAWDOWN_LADDER thresholds {thresholds} must be positive and strictly increasing")
            if halt is not None and any(t >= halt for t in thresholds):
                problems.append(f"DRAWDOWN_LADDER thresholds {thresholds} must all be below DRAWDOWN_HALT_PCT={halt}")
    note = num("MAX_ORDER_NOTIONAL")
    if note is not None and not note > 0:
        problems.append(f"MAX_ORDER_NOTIONAL={note} must be > 0")
    atr = num("ATR_STOP_MULT")
    if atr is not None and not atr > 0:
        problems.append(f"ATR_STOP_MULT={atr} must be > 0")
    sleeve = num("SIGNAL_SLEEVE_EQUITY")
    if sleeve is not None and not sleeve > 0:
        problems.append(f"SIGNAL_SLEEVE_EQUITY={sleeve} must be > 0")
    jump = num("EQUITY_JUMP_HALT_PCT")
    if jump is not None and not 0 < jump < 1:
        problems.append(f"EQUITY_JUMP_HALT_PCT={jump} must satisfy 0 < value < 1 (otherwise the halt is disabled)")
    daily, weekly = num("DAILY_LOSS_STOP_PCT"), num("WEEKLY_LOSS_STOP_PCT")
    if daily is not None and not 0 < daily <= 0.5:
        problems.append(f"DAILY_LOSS_STOP_PCT={daily} must satisfy 0 < value <= 0.5")
    if weekly is not None and not 0 < weekly <= 1:
        problems.append(f"WEEKLY_LOSS_STOP_PCT={weekly} must satisfy 0 < value <= 1")
    for name in ("MAX_OPEN_POSITIONS", "MAX_ORDERS_PER_DAY"):
        n = num(name)
        if n is not None and not (n >= 1 and n == int(n)):
            problems.append(f"{name}={n} must be a whole number >= 1")
    mode = get("TRADING_MODE")
    if mode is not None and mode not in TRADING_MODES:
        problems.append(f"TRADING_MODE={mode!r} must be one of {list(TRADING_MODES)}")
    eff = get("LLM_EFFORT")
    if eff is not None and eff not in LLM_EFFORTS:
        problems.append(f"LLM_EFFORT={eff!r} must be one of {list(LLM_EFFORTS)}")

    if problems:
        raise RiskConfigError("Invalid risk configuration:\n  - " + "\n  - ".join(problems))
