"""One trial registry per research run (technical scan, pairs scan), kept in its OWN SQLite file.

The registry lives beside the state DB (``trials.sqlite``, like ``holdout_reads.sqlite``), never in the state DB:
opening a registry must not create or migrate ``trading.db``, because a missing state DB is the signal that
``risk init`` has not been run and order routing refuses on it (state.db.require_initialized)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import config


def trials_db_path() -> str:
    p = str(config.STATE_DB_PATH)
    return p if p == ":memory:" else str(Path(p).with_name("trials.sqlite"))


def new_run_id(prefix: str, tag: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{prefix}-{stamp}" + (f"-{tag}" if tag else "")


def new_registry(run_id: str):
    from trials import TrialRegistry
    return TrialRegistry(run_id, db_path=trials_db_path())
