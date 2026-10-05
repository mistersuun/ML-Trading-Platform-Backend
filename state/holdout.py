"""Persistent hold-out reads: the first hold-out evaluation per (strategy version, candidate key) is stored
and every later scan gates on THAT stored result (decision D11: a hold-out is read once per strategy version).

A separate SQLite file next to the state DB (``holdout_reads.sqlite``) so it needs no state-DB migration.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config

_SCHEMA = """CREATE TABLE IF NOT EXISTS holdout_reads (
    version TEXT NOT NULL, candidate_key TEXT NOT NULL, payload TEXT NOT NULL, read_at TEXT NOT NULL,
    PRIMARY KEY (version, candidate_key))"""


def default_path() -> str:
    p = str(config.STATE_DB_PATH)
    return p if p == ":memory:" else str(Path(p).with_name("holdout_reads.sqlite"))


class HoldoutStore:
    """First-write-wins store. ``path=":memory:"`` gives a throwaway store (tests)."""

    def __init__(self, path: Optional[str] = None):
        self.path = str(path) if path is not None else default_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
        self._conn.execute(_SCHEMA)

    def get(self, version: str, key: str) -> Optional[dict]:
        row = self._conn.execute("SELECT payload FROM holdout_reads WHERE version=? AND candidate_key=?",
                                 (version, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, version: str, key: str, payload: dict) -> bool:
        """Store `payload` unless a read already exists. Returns True when this call stored it."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO holdout_reads (version, candidate_key, payload, read_at) VALUES (?,?,?,?)",
            (version, key, json.dumps(payload, sort_keys=True), datetime.now(timezone.utc).isoformat()))
        return cur.rowcount == 1

    def versions_read(self, key_prefix: str) -> int:
        """Distinct strategy versions with a stored read for candidate keys starting with `key_prefix`."""
        row = self._conn.execute("SELECT COUNT(DISTINCT version) FROM holdout_reads WHERE candidate_key LIKE ?",
                                 (key_prefix + "%",)).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()
