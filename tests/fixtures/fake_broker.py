"""Re-export of brokers.fake.FakeBroker plus the paper_trader install helper (legacy tests)."""
from __future__ import annotations

from typing import Optional

from brokers.fake import FakeBroker

__all__ = ["FakeBroker", "install_fake_broker"]


def install_fake_broker(monkeypatch, broker: Optional[FakeBroker] = None, enable_trading: bool = True) -> FakeBroker:
    """Install `broker` as paper_trader's client (module global `_client`)."""
    import config
    import paper_trader

    broker = broker or FakeBroker()
    monkeypatch.setattr(paper_trader, "_client", broker)
    if enable_trading:
        monkeypatch.setattr(config, "PAPER_TRADE_ENABLED", True)
        monkeypatch.setattr(config, "TRADING_MODE", "paper")
    return broker


def init_state(monkeypatch, tmp_path):
    """Point config at a fresh, migrated tmp state DB (and a kill-switch file that does not exist)."""
    import config
    from state import db

    path = tmp_path / "state" / "trading.db"
    monkeypatch.setattr(config, "STATE_DB_PATH", str(path))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "state" / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    return path


def pretend_validated(monkeypatch, status: str = "unvalidated") -> None:
    """TEST ONLY: make `status` order-eligible so routing/sizing can be exercised. In production nothing is."""
    import config

    monkeypatch.setattr(config, "ORDER_ELIGIBLE_STATUSES", tuple(config.ORDER_ELIGIBLE_STATUSES) + (status,))
