import subprocess
import sqlite3
import sys
import textwrap
import threading
from pathlib import Path

import pytest

import config
from state import db

ROOT = Path(__file__).resolve().parent.parent
TABLES = {"risk_state", "signal_ledger", "orders", "alerts_sent", "runs"}


@pytest.fixture
def dbpath(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setattr(config, "STATE_DB_PATH", str(p))
    return p


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_connect_pragmas(dbpath):
    c = db.connect()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.row_factory is sqlite3.Row


def test_migrate_idempotent(dbpath):
    c = db.connect()
    v = db.migrate(c)
    assert v == db.latest_version() >= 1
    assert TABLES <= _tables(c)
    assert db.migrate(c) == v
    assert c.execute("SELECT count(*) FROM risk_state").fetchone()[0] == 1


def test_risk_state_singleton(dbpath):
    c = db.connect()
    db.migrate(c)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO risk_state (id) VALUES (2)")


def test_duplicate_signal_key_two_threads(dbpath):
    c0 = db.connect()
    db.migrate(c0)
    barrier = threading.Barrier(2)
    results = []

    def worker():
        c = db.connect()
        barrier.wait()
        try:
            c.execute(
                "INSERT INTO signal_ledger (signal_key, created_at, status) VALUES ('k1','now','new')"
            )
            results.append("ok")
        except sqlite3.IntegrityError:
            results.append("integrity")

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["integrity", "ok"]
    assert c0.execute("SELECT count(*) FROM signal_ledger").fetchone()[0] == 1


def test_crash_mid_transaction(dbpath):
    c = db.connect()
    db.migrate(c)
    code = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from state import db
        c = db.connect({str(dbpath)!r})
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO signal_ledger (signal_key, created_at, status) VALUES ('crash','now','new')")
        os._exit(1)
        """
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT)
    assert r.returncode == 1
    c2 = db.connect()
    assert c2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert c2.execute("SELECT count(*) FROM signal_ledger WHERE signal_key='crash'").fetchone()[0] == 0


def test_require_initialized_missing(dbpath):
    with pytest.raises(db.StateNotInitialized):
        db.require_initialized()
    assert not dbpath.exists()


def test_require_initialized_unmigrated_and_ok(dbpath):
    c = db.connect()
    with pytest.raises(db.StateNotInitialized):
        db.require_initialized(c)
    db.migrate(c)
    assert db.require_initialized(c) is c
    assert db.require_initialized().execute("SELECT 1").fetchone()[0] == 1


def test_backup(dbpath, tmp_path):
    c = db.connect()
    db.migrate(c)
    c.execute("INSERT INTO alerts_sent (dedup_key, sent_at) VALUES ('a','now')")
    dest = tmp_path / "bk" / "copy.db"
    db.backup(c, dest)
    d = sqlite3.connect(dest)
    assert d.execute("SELECT dedup_key FROM alerts_sent").fetchone()[0] == "a"


def test_relative_state_paths_resolve_against_the_repo_not_the_cwd(monkeypatch, tmp_path):
    import importlib
    from pathlib import Path
    import config as cfg
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STATE_DB_PATH", "state/trading.db")
    monkeypatch.setenv("KILL_SWITCH_FILE", "state/KILL")
    try:
        c = importlib.reload(cfg)
        repo = Path(cfg.__file__).parent
        assert c.STATE_DB_PATH == str(repo / "state" / "trading.db")
        assert c.KILL_SWITCH_FILE == str(repo / "state" / "KILL")
        monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "abs" / "KILL"))
        assert importlib.reload(cfg).KILL_SWITCH_FILE == str(tmp_path / "abs" / "KILL")  # absolute is kept
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)
