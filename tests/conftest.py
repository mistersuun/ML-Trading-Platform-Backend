"""Offline pytest harness: blocked sockets, fake env, synthetic market data."""
from __future__ import annotations

import datetime as _dt
import importlib
import os
import sys
import zlib
from pathlib import Path

# Make backend modules importable and keep config.py independent of real keys / .env.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "POLYGON_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[_k] = ""
os.environ["PAPER_TRADE_ENABLED"] = "false"
os.environ["TRADING_MODE"] = "off"
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALPHA_VANTAGE_KEY", "DISCORD_WEBHOOK_URL",
           "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_PASS"):
    os.environ[_k] = ""
try:  # a developer's .env must never leak into tests
    import dotenv as _dotenv
    _dotenv.load_dotenv = lambda *a, **k: False
except ImportError:
    pass

import pytest  # noqa: E402
from pytest_socket import disable_socket, enable_socket  # noqa: E402

from tests.fixtures import synthetic  # noqa: E402
from tests.fixtures.fake_broker import FakeBroker, install_fake_broker  # noqa: E402

FROZEN_NOW = _dt.datetime(2024, 6, 3, 12, 0, 0)

_ROUTE_MODULES = ["main", "routes.backtest_routes", "routes.data_routes", "routes.ml_routes",
                  "routes.pairs_routes", "routes.pattern_routes", "routes.stress_routes"]


@pytest.fixture(autouse=True)
def _block_network():
    """No real network in any test (unix sockets stay allowed for asyncio/TestClient)."""
    disable_socket(allow_unix_socket=True)
    yield
    enable_socket()


@pytest.fixture(autouse=True)
def _isolated_state_paths(tmp_path, monkeypatch):
    """Never touch the real state/ dir: point the state DB and kill switch at tmp_path (DB not created).

    Tests that need an initialised DB call tests.fixtures.fake_broker.init_state."""
    import config

    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "state" / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)


@pytest.fixture
def frozen_now():
    return FROZEN_NOW


@pytest.fixture
def gbm_frame():
    return synthetic.gbm_ohlc(n=500, seed=42)


@pytest.fixture
def synth():
    """The synthetic generators module."""
    return synthetic


@pytest.fixture
def fake_broker(monkeypatch):
    return install_fake_broker(monkeypatch, FakeBroker())


def _frame_for_symbol(symbol: str, period_days: int | None = None):
    seed = zlib.crc32(symbol.encode()) % (2 ** 31)
    n = 500 if not period_days else max(60, min(int(period_days), 1500))
    if symbol.endswith("-USD"):
        return synthetic.crypto_weekend_frame(n=n, seed=seed)
    if symbol.endswith("=X"):
        return synthetic.zero_volume_fx_frame(n=n, seed=seed)
    return synthetic.gbm_ohlc(n=n, seed=seed)


@pytest.fixture
def patch_fetch(monkeypatch):
    """Replace data_fetcher.fetch_ohlcv and every imported alias with a synthetic, symbol-keyed frame.

    Returns a dict `overrides` (symbol -> DataFrame) to pin specific frames, and a list `calls`
    is available as `patch_fetch.calls`.
    """
    import data_fetcher

    overrides: dict = {}
    calls: list = []

    def fake_fetch_ohlcv(symbol, period_days=None, *args, **kwargs):
        calls.append((symbol, period_days))
        if symbol in overrides:
            return overrides[symbol].copy()
        return _frame_for_symbol(symbol, period_days)

    monkeypatch.setattr(data_fetcher, "fetch_ohlcv", fake_fetch_ohlcv)
    for name in _ROUTE_MODULES:
        try:
            mod = sys.modules.get(name) or importlib.import_module(name)
        except Exception:
            continue
        if hasattr(mod, "fetch_ohlcv"):
            monkeypatch.setattr(mod, "fetch_ohlcv", fake_fetch_ohlcv)
    overrides_proxy = overrides
    overrides_proxy_calls = calls
    fake_fetch_ohlcv.overrides = overrides
    fake_fetch_ohlcv.calls = calls
    return fake_fetch_ohlcv
