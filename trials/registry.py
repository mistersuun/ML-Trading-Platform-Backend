"""Trial registry: one record per evaluated (symbol x pattern x params) trial of a run.

* SQLite row per trial (``trials`` table, migration 007) written at ``record`` time.
* OOS daily-return series as a (date x trial_id) parquet per run, ``data/trials/<run_id>.parquet``, written
  atomically by ``flush``. Dates are the union of all trials' dates; a trial has NaN where it has no observation.
* N for a run = number of trials recorded. Nothing is ever dropped silently: a duplicate trial, a bad series,
  a DB / parquet failure or a DB-vs-parquet mismatch raises ``TrialRegistryError``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import config
from state import db as state_db


class TrialRegistryError(RuntimeError):
    """A trial could not be recorded / loaded faithfully. Callers must not continue as if N were intact."""


def default_trials_dir() -> Path:
    return Path(config.BASE_DIR) / "data" / "trials"


def params_hash(params: dict) -> str:
    blob = json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def data_hash(df: pd.DataFrame) -> str:
    """Content hash of a bar frame (index + every numeric column), independent of memory layout."""
    h = hashlib.sha256()
    h.update(np.asarray(df.index.values).astype("datetime64[ns]").astype("int64").tobytes())
    for col in sorted(map(str, df.columns)):
        s = pd.to_numeric(df[col], errors="coerce") if df[col].dtype != object else df[col]
        h.update(col.encode())
        h.update(np.ascontiguousarray(s.to_numpy(dtype="float64", na_value=np.nan)).tobytes())
    return h.hexdigest()[:32]


def matrix_hash(m: pd.DataFrame) -> str:
    """Hash of a returns matrix: dates, trial ids (in column order) and float64 values (NaN canonical)."""
    h = hashlib.sha256()
    h.update(np.asarray(m.index.values).astype("datetime64[ns]").astype("int64").tobytes())
    h.update("|".join(map(str, m.columns)).encode())
    v = np.ascontiguousarray(m.to_numpy(dtype="float64"))
    h.update(np.where(np.isnan(v), np.nan, v + 0.0).tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    run_id: str
    ordinal: int
    symbol: str
    pattern: str
    params_hash: str
    params: dict
    strategy_version: str
    data_hash: str
    n_obs: int


def trial_id_for(symbol: str, pattern: str, phash: str) -> str:
    """Run-independent id (unique within a run), so identical runs give identical matrices / hashes."""
    return hashlib.sha1(f"{symbol}|{pattern}|{phash}".encode()).hexdigest()[:16]


def _norm_series(returns, label: str) -> pd.Series:
    s = returns if isinstance(returns, pd.Series) else pd.Series(returns)
    if not isinstance(s.index, pd.DatetimeIndex):
        if len(s):
            raise TrialRegistryError(f"{label}: returns must be indexed by date")
        s = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    idx = s.index.tz_localize(None) if s.index.tz is not None else s.index
    s = pd.Series(s.to_numpy(dtype="float64"), index=idx.normalize())
    if s.index.has_duplicates:
        raise TrialRegistryError(f"{label}: duplicate dates in return series")
    if np.isinf(s.to_numpy()).any():
        raise TrialRegistryError(f"{label}: non-finite returns")
    return s.sort_index()


def _conn(db_path=None) -> sqlite3.Connection:
    try:
        conn = state_db.connect(db_path)
        state_db.migrate(conn)
        return conn
    except Exception as e:
        raise TrialRegistryError(f"trial DB unavailable: {e}") from e


class TrialRegistry:
    def __init__(self, run_id: str, *, db_path=None, trials_dir=None, conn: Optional[sqlite3.Connection] = None):
        if not run_id or any(c in run_id for c in "/\\") or run_id.startswith("."):
            raise TrialRegistryError(f"invalid run_id {run_id!r}")
        self.run_id = run_id
        self.trials_dir = Path(trials_dir) if trials_dir is not None else default_trials_dir()
        self._conn = conn if conn is not None else _conn(db_path)
        self._series: dict[str, pd.Series] = {}
        self._records: list[TrialRecord] = []
        existing = self._conn.execute("SELECT COUNT(*) FROM trials WHERE run_id=?", (run_id,)).fetchone()[0]
        if existing:
            raise TrialRegistryError(f"run {run_id!r} already has {existing} recorded trials; use a new run_id "
                                     f"or load_run()")

    @property
    def path(self) -> Path:
        return self.trials_dir / f"{self.run_id}.parquet"

    @property
    def n_trials(self) -> int:
        return len(self._records)

    @property
    def records(self) -> list[TrialRecord]:
        return list(self._records)

    def record(self, *, symbol: str, pattern: str, params: dict, returns, strategy_version: str,
               data_hash: str) -> TrialRecord:
        phash = params_hash(params)
        tid = trial_id_for(symbol, pattern, phash)
        if tid in self._series:
            raise TrialRegistryError(f"duplicate trial {symbol}/{pattern}/{params} in run {self.run_id}")
        s = _norm_series(returns, f"{symbol}/{pattern}")
        rec = TrialRecord(tid, self.run_id, len(self._records), symbol, pattern, phash, dict(params),
                          strategy_version, data_hash, int(s.notna().sum()))
        try:
            self._conn.execute(
                "INSERT INTO trials (run_id, trial_id, ordinal, symbol, pattern, params_hash, params_json,"
                " strategy_version, data_hash, n_obs) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (rec.run_id, tid, rec.ordinal, symbol, pattern, phash,
                 json.dumps(params, sort_keys=True, default=str), strategy_version, data_hash, rec.n_obs))
        except Exception as e:
            raise TrialRegistryError(f"could not record trial {symbol}/{pattern}: {e}") from e
        self._series[tid] = s
        self._records.append(rec)
        return rec

    def returns_matrix(self) -> pd.DataFrame:
        """(date x trial_id) matrix, columns in record order, rows the sorted union of dates (NaN = no obs)."""
        if not self._records:
            return pd.DataFrame(index=pd.DatetimeIndex([], name="date"), dtype="float64")
        idx = pd.DatetimeIndex(sorted(set().union(*(s.index for s in self._series.values()))), name="date")
        m = pd.DataFrame({r.trial_id: self._series[r.trial_id].reindex(idx) for r in self._records}, index=idx)
        m.columns.name = "trial_id"
        return m

    def flush(self) -> Path:
        """Atomically write the parquet; verify it against the SQLite count. Raises on any mismatch."""
        m = self.returns_matrix()
        try:
            self.trials_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + f".tmp{os.getpid()}")
            m.to_parquet(tmp)
            os.replace(tmp, self.path)
        except Exception as e:
            raise TrialRegistryError(f"could not write {self.path}: {e}") from e
        n_db = self._conn.execute("SELECT COUNT(*) FROM trials WHERE run_id=?", (self.run_id,)).fetchone()[0]
        if n_db != self.n_trials or m.shape[1] != self.n_trials:
            raise TrialRegistryError(f"trial count mismatch: memory {self.n_trials}, db {n_db}, "
                                     f"matrix {m.shape[1]}")
        return self.path


@dataclass
class LoadedRun:
    run_id: str
    trials: list[TrialRecord]
    returns: pd.DataFrame

    @property
    def n_trials(self) -> int:
        return len(self.trials)

    def returns_matrix(self) -> pd.DataFrame:
        return self.returns


def load_run(run_id: str, *, db_path=None, trials_dir=None, conn: Optional[sqlite3.Connection] = None
             ) -> LoadedRun:
    conn = conn if conn is not None else _conn(db_path)
    rows = conn.execute("SELECT * FROM trials WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
    if not rows:
        raise TrialRegistryError(f"no trials recorded for run {run_id!r}")
    p = (Path(trials_dir) if trials_dir is not None else default_trials_dir()) / f"{run_id}.parquet"
    try:
        m = pd.read_parquet(p)
    except Exception as e:
        raise TrialRegistryError(f"cannot read {p}: {e}") from e
    recs = [TrialRecord(r["trial_id"], r["run_id"], r["ordinal"], r["symbol"], r["pattern"], r["params_hash"],
                        json.loads(r["params_json"]), r["strategy_version"], r["data_hash"], r["n_obs"])
            for r in rows]
    if list(m.columns) != [r.trial_id for r in recs]:
        raise TrialRegistryError(f"run {run_id!r}: parquet columns do not match the {len(recs)} recorded trials")
    m.index = pd.DatetimeIndex(m.index, name="date")
    m.columns.name = "trial_id"
    return LoadedRun(run_id, recs, m)
