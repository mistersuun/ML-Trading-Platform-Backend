"""Operational state store (SQLite, WAL). Fail-closed: a missing DB is never auto-created by require_initialized."""
import sqlite3
from pathlib import Path

import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class StateNotInitialized(RuntimeError):
    """State DB is missing or not fully migrated; trading must not proceed."""


def _db_path(path=None) -> str:
    return str(path if path is not None else config.STATE_DB_PATH)


def connect(path=None) -> sqlite3.Connection:
    p = _db_path(path)
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrations() -> list[tuple[int, Path]]:
    out = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        out.append((int(f.name.split("_", 1)[0]), f))
    return out


def latest_version() -> int:
    m = _migrations()
    return m[-1][0] if m else 0


def current_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn) -> int:
    """Apply pending migrations in order; idempotent. Returns resulting user_version."""
    for version, f in _migrations():
        if version <= current_version(conn):
            continue
        conn.executescript(
            "BEGIN;\n" + f.read_text() + f"\nPRAGMA user_version = {version};\nCOMMIT;"
        )
    return current_version(conn)


def require_initialized(conn=None, path=None) -> sqlite3.Connection:
    """Return a connection to an existing, fully migrated DB, else raise StateNotInitialized.

    Never creates the file: a deleted DB must not silently resume trading.
    """
    p = _db_path(path)
    if conn is None:
        if p != ":memory:" and not Path(p).is_file():
            raise StateNotInitialized(f"state DB missing: {p}")
        conn = connect(p)
    if current_version(conn) < latest_version():
        raise StateNotInitialized(
            f"state DB not migrated (user_version={current_version(conn)} < {latest_version()})"
        )
    return conn


def backup(conn, dest_path) -> None:
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    dest = sqlite3.connect(str(dest_path))
    try:
        conn.backup(dest)
    finally:
        dest.close()
