"""Cumulative pooled-trial ledger and pooled hold-out read cap (decision D18, proposal sections 6 and 10).

Lives in ``trials.sqlite`` next to the per-symbol registry but in its OWN tables, created here with
``CREATE TABLE IF NOT EXISTS`` (like ``state/holdout.py``): it is NOT a state-DB migration, because a new migration
would make every existing state DB fail closed until migrated.

* ``pooled_trials_ledger``: one row per pooled trial VERSION, primary key ``(holdout_start, version)``.
  N for the pooled gate = ``COUNT(*) WHERE holdout_start = current``. Re-running the same version on one more day of
  data is the same trial (INSERT OR IGNORE). A new version of any part (pattern source, exits, universe, asset
  split, pooling method) adds one row and keeps the old ones: they were looked at. Retired patterns stay counted.
* ``pooled_universes``: every universe ever registered (hash and symbols), so a universe built by DROPPING symbols
  from an earlier one (a per-symbol-OOS-winners universe) can be recognised and refused.
* ``pooled_holdout_reads``: one row per (holdout_start, pattern, version) whose pooled hold-out was read. At most one
  read per pattern per ``HOLDOUT_START`` unless a re-read was disclosed (``pooled_disclosed_rereads``, written only by
  ``record_disclosed_reread``, i.e. by an owner decision entry); every read of a pattern is counted.

Every method raises on failure: the caller fails closed (``pooled_trials_unavailable`` /
``holdout_store_unavailable``) and nothing pooled validates.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS pooled_trials_ledger (
        holdout_start TEXT NOT NULL, version TEXT NOT NULL, pattern TEXT NOT NULL, params_hash TEXT NOT NULL,
        universe_hash TEXT NOT NULL, asset_class TEXT NOT NULL, method_version TEXT NOT NULL,
        first_run_id TEXT NOT NULL, first_seen TEXT NOT NULL, duplicate_of TEXT, retired INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (holdout_start, version))""",
    """CREATE TABLE IF NOT EXISTS pooled_universes (
        universe_hash TEXT PRIMARY KEY, symbols_json TEXT NOT NULL, first_seen TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS pooled_holdout_reads (
        holdout_start TEXT NOT NULL, pattern TEXT NOT NULL, version TEXT NOT NULL, read_at TEXT NOT NULL,
        PRIMARY KEY (holdout_start, pattern, version))""",
    """CREATE TABLE IF NOT EXISTS pooled_disclosed_rereads (
        holdout_start TEXT NOT NULL, pattern TEXT NOT NULL, decision_ref TEXT NOT NULL, recorded_at TEXT NOT NULL,
        PRIMARY KEY (holdout_start, pattern, decision_ref))""",
)


class PooledLedgerError(RuntimeError):
    """The ledger cannot be read or written: N cannot be established, nothing pooled may validate."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PooledLedger:
    def __init__(self, path: Optional[str] = None):
        if path is None:
            from services import trial_runs
            path = trial_runs.trials_db_path()
        self.path = str(path)
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None, check_same_thread=False)
            for ddl in _SCHEMA:
                self._conn.execute(ddl)
        except Exception as e:
            raise PooledLedgerError(f"pooled ledger unavailable: {e}") from e

    def _x(self, sql: str, args: tuple = ()):
        try:
            return self._conn.execute(sql, args)
        except Exception as e:
            raise PooledLedgerError(f"pooled ledger failure: {e}") from e

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- trials (N)
    def register_trial(self, holdout_start: str, version: str, *, pattern: str, params_hash: str,
                       universe_hash: str, asset_class: str, method_version: str, run_id: str,
                       duplicate_of: Optional[str] = None, retired: bool = False) -> bool:
        """Count this version once per HOLDOUT_START. True when it was new (N grew by one)."""
        cur = self._x("INSERT OR IGNORE INTO pooled_trials_ledger (holdout_start, version, pattern, params_hash,"
                      " universe_hash, asset_class, method_version, first_run_id, first_seen, duplicate_of, retired)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (str(holdout_start), version, pattern, params_hash, universe_hash, asset_class, method_version,
                       run_id, _now(), duplicate_of, int(bool(retired))))
        return cur.rowcount == 1

    def set_duplicate(self, holdout_start: str, version: str, duplicate_of: str) -> None:
        self._x("UPDATE pooled_trials_ledger SET duplicate_of=? WHERE holdout_start=? AND version=?"
                " AND duplicate_of IS NULL", (duplicate_of, str(holdout_start), version))

    def count(self, holdout_start: str) -> int:
        return int(self._x("SELECT COUNT(*) FROM pooled_trials_ledger WHERE holdout_start=?",
                           (str(holdout_start),)).fetchone()[0])

    def rows(self, holdout_start: str) -> list[dict]:
        cur = self._x("SELECT * FROM pooled_trials_ledger WHERE holdout_start=? ORDER BY first_seen, version",
                      (str(holdout_start),))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---------------------------------------------------------------- universes
    def register_universe(self, universe_hash: str, symbols: Iterable[str]) -> None:
        self._x("INSERT OR IGNORE INTO pooled_universes (universe_hash, symbols_json, first_seen) VALUES (?,?,?)",
                (universe_hash, json.dumps(sorted(symbols)), _now()))

    def known_universes(self) -> dict[str, list[str]]:
        return {h: json.loads(s) for h, s in self._x("SELECT universe_hash, symbols_json FROM pooled_universes")}

    def derived_from(self, symbols: Iterable[str]) -> Optional[str]:
        """Hash of an earlier universe that ``symbols`` is a STRICT subset of (a universe narrowed after the
        earlier one's per-symbol results were stored), else None."""
        s = set(symbols)
        for h, prev in self.known_universes().items():
            if s < set(prev):
                return h
        return None

    # ---------------------------------------------------------------- hold-out read cap
    def holdout_read_versions(self, holdout_start: str, pattern: str) -> list[str]:
        return [r[0] for r in self._x("SELECT version FROM pooled_holdout_reads WHERE holdout_start=? AND pattern=?"
                                      " ORDER BY read_at", (str(holdout_start), pattern))]

    def disclosed_rereads(self, holdout_start: str, pattern: str) -> int:
        return int(self._x("SELECT COUNT(*) FROM pooled_disclosed_rereads WHERE holdout_start=? AND pattern=?",
                           (str(holdout_start), pattern)).fetchone()[0])

    def read_allowed(self, holdout_start: str, pattern: str, version: str) -> tuple[bool, int]:
        """(allowed, k) where k is the number of reads of this pattern INCLUDING this one. A version already read is
        always allowed (it reuses the stored read, no new exposure). A new version needs a disclosed re-read."""
        reads = self.holdout_read_versions(holdout_start, pattern)
        if version in reads:
            return True, len(reads)
        allowed = len(reads) < 1 + self.disclosed_rereads(holdout_start, pattern)
        return allowed, len(reads) + 1

    def record_read(self, holdout_start: str, pattern: str, version: str) -> None:
        self._x("INSERT OR IGNORE INTO pooled_holdout_reads (holdout_start, pattern, version, read_at)"
                " VALUES (?,?,?,?)", (str(holdout_start), pattern, version, _now()))

    def record_disclosed_reread(self, holdout_start: str, pattern: str, decision_ref: str) -> None:
        """Called only when an owner decision entry discloses a re-read of this pattern's pooled hold-out."""
        if not decision_ref or not decision_ref.strip():
            raise PooledLedgerError("a disclosed re-read needs a decision reference")
        self._x("INSERT OR IGNORE INTO pooled_disclosed_rereads (holdout_start, pattern, decision_ref, recorded_at)"
                " VALUES (?,?,?,?)", (str(holdout_start), pattern, decision_ref.strip(), _now()))
