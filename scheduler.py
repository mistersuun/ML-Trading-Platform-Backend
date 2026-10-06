"""Nightly precompute run, scheduler loop and dead-man heartbeat (WS3.4, trimmed).

* ``run_nightly()``: one locked run. First a read-only IBKR account sync when IBKR_SYNC_ENABLED (failure: alert and
  fall back, never blocks), then scans (technical incl. validation, pairs, ML) via the services, writes the
  results/ files, records a ``runs`` row (kind='nightly'), alerts through the normal alert path, then takes a
  daily SQLite backup into state/backups/ (newest 14 kept) and, after a good scan, writes the advisory Claude
  briefing (services/briefing.py; skipped without an API key or over budget; a failure never fails the run). Alert-only: it never places orders.
* ``schedule_forever(at='17:30', tz='America/New_York')``: runs run_nightly daily at that wall-clock time in `tz`.
  17:30 ET is 90 minutes after the US cash close (16:00 ET), leaving room for the vendor data delay. The time is
  evaluated in the exchange time zone, so it follows daylight-saving changes (it is never a fixed UTC offset).
* ``heartbeat_check(max_age_hours=30)``: (ok, message); not ok when the last successful nightly run is too old or
  the most recent nightly run failed. Meant for a cron job that sends a dead-man alert.
"""
from __future__ import annotations

import fcntl
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import config
from results import store
from services import briefing, ibkr_sync, scan, session
from settings import RiskConfigError, validate_risk_config
from state import db as state_db

logger = logging.getLogger(__name__)

NIGHTLY_KIND = "nightly"
FUNNEL_KIND = "funnel"   # results/funnel/latest.json: counts of the latest technical validation run
DEFAULT_AT = "17:30"
DEFAULT_TZ = "America/New_York"
BACKUP_KEEP = 14
MODES = ("technical", "pairs", "ml")


class LockHeld(RuntimeError):
    """Another nightly run holds the lock."""


def _state_dir() -> Path:
    return Path(config.STATE_DB_PATH).parent


class _RunLock:
    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            raise LockHeld("lock held")
        return self

    def __exit__(self, *exc):
        if self.fh is not None:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()


def backup_state(conn, now: Optional[datetime] = None, dest_dir=None, keep: int = BACKUP_KEEP) -> Path:
    """Daily SQLite backup (state.db.backup) into state/backups/, newest `keep` files kept."""
    now = now or datetime.now(timezone.utc)
    d = Path(dest_dir) if dest_dir is not None else _state_dir() / "backups"
    dest = d / f"trading-{now.strftime('%Y%m%dT%H%M%S')}.db"
    state_db.backup(conn, dest)
    for old in sorted(d.glob("trading-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def run_nightly(modes=MODES, run_stress: bool = True, lock_path=None, results_root=None,
                scan_fn: Optional[Callable] = None, sync_fn: Optional[Callable] = None) -> dict:
    """One locked nightly run. Returns {"status": "ok"|"error"|"locked", ...}; never raises for scan errors."""
    try:
        validate_risk_config(config)
    except RiskConfigError as e:
        session.alert(f"Nightly run refused: {e}", kind="halt",
                      dedup_key=f"nightly_badconfig:{datetime.now(timezone.utc).date().isoformat()}")
        return {"status": "error", "message": str(e)}
    lock = _RunLock(Path(lock_path) if lock_path is not None else _state_dir() / "nightly.lock")
    try:
        lock.__enter__()
    except LockHeld:
        logger.warning("nightly: lock held, another run is in progress; exiting")
        return {"status": "locked", "message": "lock held"}
    started = datetime.now(timezone.utc)
    conn = None
    run_id = None
    try:
        conn = state_db.require_initialized()           # fail closed: no state DB, no nightly run
        run_id = conn.execute("INSERT INTO runs (kind, started_at, status) VALUES (?,?,?)",
                              (NIGHTLY_KIND, started.isoformat(), "running")).lastrowid
        summary: dict = {}
        status = "ok"
        # Read-only IBKR account sync (D14) before the scan. It never raises: a failure alerts and the views fall
        # back to the last snapshot or holdings.csv, so the scan always runs.
        sync_res = ibkr_sync.sync_before_scan(sync_fn, conn)
        if sync_res["status"] != "skipped":
            summary["ibkr_sync"] = {k: sync_res[k] for k in ("status", "source")}
        try:
            fn = scan_fn or scan.run_full_scan
            results = fn(modes=list(modes), run_stress=run_stress, paper_trade=False) or {}
            if not results:
                raise RuntimeError("scan produced no data")
            for kind in modes:
                # technical is stored as typed TechnicalCandidate rows (what /api/results/technical/latest serves)
                rows = results.get("technical_candidates", results.get(kind, [])) if kind == "technical" \
                    else results.get(kind, [])
                store.write_result(kind, [r.model_dump(mode="json") if hasattr(r, "model_dump") else r
                                          for r in rows], root=results_root)
                summary[kind] = len(rows)
                if kind == "technical":
                    # funnel counts {tested, min_trades, oos_positive, psr, bh, dsr, orders} + n_trials, pbo, run_id of the same validation run
                    funnel = results.get("technical_funnel") or {}
                    if funnel:
                        store.write_result(FUNNEL_KIND, funnel, root=results_root)
                        summary["funnel"] = funnel
        except Exception as e:
            status = "error"
            summary["error"] = f"{type(e).__name__}: {e}"[:300]
            logger.exception("nightly scan failed")
            session.alert(f"Nightly run FAILED: {summary['error']}", kind="halt",
                          dedup_key=f"nightly_failed:{started.date().isoformat()}")
        try:
            summary["backup"] = backup_state(conn, started).name
        except Exception as e:
            summary["backup_error"] = f"{type(e).__name__}: {e}"[:200]
            logger.exception("nightly backup failed")
            session.alert(f"Nightly state backup FAILED: {type(e).__name__}", kind="halt",
                          dedup_key=f"backup_failed:{started.date().isoformat()}")
        conn.execute("UPDATE runs SET finished_at=?, status=?, summary_json=? WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), status, json.dumps(summary, default=str), run_id))
        if status == "ok":
            # Advisory Claude briefing from this run's results (D17). It runs after the run row is final, so a slow or
            # killed API call can never leave a successful run "running". It never raises and never changes the status.
            try:
                b = briefing.generate_nightly(results, summary.get("funnel"), conn, root=results_root)
                summary["briefing"] = b["status"] if not b.get("reason") else f"{b['status']}:{b['reason']}"
            except Exception as e:
                summary["briefing"] = f"error:{type(e).__name__}"
                logger.exception("nightly briefing failed")
            conn.execute("UPDATE runs SET summary_json=? WHERE id=?", (json.dumps(summary, default=str), run_id))
        return {"status": status, "run_id": run_id, "summary": summary}
    except state_db.StateNotInitialized as e:
        session.alert(f"Nightly run cannot start: {e}", kind="halt",
                      dedup_key=f"nightly_nostate:{started.date().isoformat()}")
        return {"status": "error", "message": str(e)}
    finally:
        if conn is not None:
            conn.close()
        lock.__exit__()


def heartbeat_check(max_age_hours: float = 30, now: Optional[datetime] = None, conn=None) -> tuple[bool, str]:
    """(ok, message) from the `runs` table: the latest nightly run must have succeeded and be fresh enough."""
    now = now or datetime.now(timezone.utc)
    own = conn is None
    try:
        conn = conn or state_db.require_initialized()
    except Exception as e:
        return False, f"state DB unavailable: {e}"
    try:
        last = conn.execute("SELECT status, started_at, finished_at FROM runs WHERE kind=? "
                            "ORDER BY id DESC LIMIT 1", (NIGHTLY_KIND,)).fetchone()
        if last is None:
            return False, "no nightly run recorded"
        good = conn.execute("SELECT finished_at FROM runs WHERE kind=? AND status='ok' AND finished_at IS NOT NULL "
                            "ORDER BY id DESC LIMIT 1", (NIGHTLY_KIND,)).fetchone()
        if last["status"] == "error":
            return False, f"last nightly run failed (started {last['started_at']})"
        if good is None:
            return False, "no successful nightly run recorded"
        finished = datetime.fromisoformat(good["finished_at"])
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        age_h = (now - finished).total_seconds() / 3600
        if age_h > max_age_hours:
            return False, f"last successful nightly run is {age_h:.1f}h old (max {max_age_hours}h)"
        return True, f"last successful nightly run {age_h:.1f}h ago"
    finally:
        if own:
            conn.close()


def next_run_time(now: datetime, at: str = DEFAULT_AT, tz: str = DEFAULT_TZ) -> datetime:
    """Next occurrence (UTC, aware) of wall-clock `at` in zone `tz` strictly after `now`; DST-safe."""
    zone = ZoneInfo(tz)
    hh, mm = (int(x) for x in at.split(":"))
    local = now.astimezone(zone)
    day = local.date()
    for _ in range(3):
        cand = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone)
        # round-trip through UTC so a nonexistent spring-forward time lands on a valid instant
        cand_utc = cand.astimezone(timezone.utc)
        if cand_utc > now.astimezone(timezone.utc):
            return cand_utc
        day += timedelta(days=1)
    raise RuntimeError("unreachable")


def make_tick(job: Callable, at: str = DEFAULT_AT, tz: str = DEFAULT_TZ,
              clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Callable[[], bool]:
    """A tick function that runs `job` once each time the zone-aware daily due time passes (True if it ran)."""
    due = {"t": next_run_time(clock(), at, tz)}
    logger.info("nightly scheduler: next run %s UTC (%s %s)", due["t"].isoformat(), at, tz)

    def tick() -> bool:
        if clock() < due["t"]:
            return False
        due["t"] = next_run_time(clock(), at, tz)
        try:
            job()
        except Exception:
            logger.exception("nightly job raised")
        return True
    return tick


def schedule_forever(at: str = DEFAULT_AT, tz: str = DEFAULT_TZ, job: Optional[Callable] = None,
                     sleep: Callable = time.sleep, stop: Callable[[], bool] = lambda: False) -> None:
    """Run `job` (default run_nightly) daily at `at` in `tz`. A 30 s `schedule` tick compares against the
    zone-aware due time, so DST shifts never move the run relative to the exchange close."""
    import schedule
    sched = schedule.Scheduler()
    sched.every(30).seconds.do(make_tick(job or run_nightly, at, tz))
    while not stop():
        sched.run_pending()
        sleep(1)
