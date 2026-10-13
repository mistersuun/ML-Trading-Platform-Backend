"""WS3.4: result store, nightly scheduler, heartbeat, concurrency cap, results route."""
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import scheduler
from api import errors
from api.concurrency import heavy_endpoint
from results import store
from routes import results_routes
from state import db as state_db

UTC = timezone.utc


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    return tmp_path


def _scan_ok(**kw):
    return {"technical": [{"symbol": "AAA"}], "pairs": [], "ml": [{"symbol": "BBB"}], "decisions": []}


# ---------------- store ----------------

def test_write_read_roundtrip_and_metadata(env):
    now = datetime(2024, 6, 3, 21, 30, tzinfo=UTC)
    store.write_result("technical", [{"symbol": "AAA", "x": float("nan")}], now=now)
    payload, ts = store.read_latest("technical")
    assert payload == [{"symbol": "AAA", "x": None}] and ts == now
    doc = store.read_document("technical")
    assert doc["config_hash"] and "code_version" in doc
    assert store.read_latest("ml") is None


def test_atomic_write_crash_keeps_old_file(env, monkeypatch):
    store.write_result("pairs", {"v": 1})
    before = (env / "results" / "pairs" / "latest.json").read_text()

    def boom(src, dst):
        raise OSError("simulated crash")
    with monkeypatch.context() as m:
        m.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            store.write_result("pairs", {"v": 2})
    assert (env / "results" / "pairs" / "latest.json").read_text() == before
    assert store.read_latest("pairs")[0] == {"v": 1}
    assert not list((env / "results" / "pairs").glob("*.tmp"))


def test_prune_keeps_newest_30(env):
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(35):
        store.write_result("ml", {"i": i}, now=base + timedelta(days=i))
    copies = [p for p in (env / "results" / "ml").glob("*.json") if p.name != "latest.json"]
    assert len(copies) == 30
    assert store.read_latest("ml")[0] == {"i": 34}


def test_kind_is_validated():
    with pytest.raises(ValueError):
        store.write_result("../evil", {})


# ---------------- nightly ----------------

def test_nightly_records_run_writes_results_and_backup(env):
    out = scheduler.run_nightly(scan_fn=_scan_ok)
    assert out["status"] == "ok"
    assert store.read_latest("technical")[0] == [{"symbol": "AAA"}]
    assert store.read_latest("ml") is not None
    conn = state_db.connect()
    row = conn.execute("SELECT * FROM runs WHERE kind='nightly'").fetchone()
    assert row["status"] == "ok" and row["finished_at"]
    assert json.loads(row["summary_json"])["technical"] == 1
    conn.close()
    assert len(list((env / "state" / "backups").glob("trading-*.db"))) == 1
    assert scheduler.heartbeat_check()[0] is True


def test_nightly_failure_records_error_and_alerts(env, monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler.session, "alert", lambda msg, **k: sent.append(msg) or True)

    def bad(**kw):
        raise ValueError("vendor down")
    out = scheduler.run_nightly(scan_fn=bad)
    assert out["status"] == "error" and sent and "FAILED" in sent[0]
    ok, msg = scheduler.heartbeat_check()
    assert not ok and "failed" in msg


def test_lock_prevents_second_run(env):
    entered = []

    def inner(**kw):
        entered.append(scheduler.run_nightly(scan_fn=_scan_ok))   # re-entrant attempt while lock is held
        return _scan_ok()
    out = scheduler.run_nightly(scan_fn=inner)
    assert entered == [{"status": "locked", "message": "lock held"}]
    assert out["status"] == "ok"
    conn = state_db.connect()
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind='nightly'").fetchone()[0] == 1
    conn.close()


def test_nightly_without_state_db_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "missing.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    out = scheduler.run_nightly(scan_fn=_scan_ok)
    assert out["status"] == "error"
    assert not (tmp_path / "results").exists()


# ---------------- heartbeat ----------------

def _insert(conn, status, finished):
    conn.execute("INSERT INTO runs (kind, started_at, finished_at, status) VALUES ('nightly',?,?,?)",
                 (finished.isoformat(), finished.isoformat(), status))


def test_heartbeat_states(env):
    now = datetime(2024, 6, 10, 12, tzinfo=UTC)
    conn = state_db.connect()
    assert scheduler.heartbeat_check(now=now, conn=conn) == (False, "no nightly run recorded")
    _insert(conn, "ok", now - timedelta(hours=10))
    assert scheduler.heartbeat_check(now=now, conn=conn)[0] is True
    assert scheduler.heartbeat_check(now=now + timedelta(hours=25), conn=conn)[0] is False   # stale (35h > 30h)
    _insert(conn, "error", now - timedelta(hours=1))
    ok, msg = scheduler.heartbeat_check(now=now, conn=conn)
    assert not ok and "failed" in msg
    _insert(conn, "ok", now - timedelta(minutes=5))
    assert scheduler.heartbeat_check(now=now, conn=conn)[0] is True
    conn.close()


# ---------------- backup ----------------

def test_backup_created_and_pruned(env):
    conn = state_db.connect()
    base = datetime(2024, 1, 1, tzinfo=UTC)
    for i in range(17):
        scheduler.backup_state(conn, now=base + timedelta(days=i))
    files = sorted((env / "state" / "backups").glob("trading-*.db"))
    assert len(files) == 14 and files[-1].name.startswith("trading-20240117")
    assert state_db.current_version(state_db.connect(files[-1])) == state_db.latest_version()
    conn.close()


# ---------------- schedule ----------------

def test_next_run_time_is_dst_safe():
    ny = ZoneInfo("America/New_York")
    winter = scheduler.next_run_time(datetime(2024, 1, 10, 12, tzinfo=UTC), "17:30", "America/New_York")
    summer = scheduler.next_run_time(datetime(2024, 7, 10, 12, tzinfo=UTC), "17:30", "America/New_York")
    assert winter.astimezone(ny).hour == 17 and winter.hour == 22      # EST = UTC-5
    assert summer.astimezone(ny).hour == 17 and summer.hour == 21      # EDT = UTC-4
    # just after today's run -> tomorrow
    after = scheduler.next_run_time(datetime(2024, 7, 10, 21, 31, tzinfo=UTC), "17:30", "America/New_York")
    assert after.day == 11
    # across the spring-forward boundary the wall-clock time stays 17:30 ET
    nxt = scheduler.next_run_time(datetime(2024, 3, 9, 23, tzinfo=UTC), "17:30", "America/New_York")
    assert nxt.astimezone(ny).hour == 17 and nxt.day == 10 and nxt.hour == 21


def test_tick_fires_job_once_when_due():
    t = {"now": datetime(2024, 7, 10, 21, 29, 50, tzinfo=UTC)}
    calls = []
    tick = scheduler.make_tick(lambda: calls.append(1), clock=lambda: t["now"])
    assert tick() is False and calls == []
    t["now"] += timedelta(seconds=11)           # 21:30:01 UTC == 17:30 EDT
    assert tick() is True and calls == [1]
    assert tick() is False                      # not again the same day
    t["now"] += timedelta(days=1)
    assert tick() is True and calls == [1, 1]


# ---------------- concurrency ----------------

def _app_with(fn):
    app = FastAPI()
    errors.install(app)
    app.add_api_route("/heavy", fn, methods=["GET"])
    return app


def test_heavy_endpoint_returns_429_when_busy():
    started, release = threading.Event(), threading.Event()

    @heavy_endpoint(limit=1)
    def slow(q: int = 1):
        started.set()
        release.wait(5)
        return {"ok": q}

    client = TestClient(_app_with(slow))
    res = {}
    th = threading.Thread(target=lambda: res.update(r=client.get("/heavy?q=3")))
    th.start()
    assert started.wait(5)
    busy = client.get("/heavy")
    assert busy.status_code == 429 and busy.json()["error"]["code"] == "busy"
    release.set()
    th.join(5)
    assert res["r"].status_code == 200 and res["r"].json() == {"ok": 3}
    assert client.get("/heavy").status_code == 200       # slot released


def test_heavy_endpoint_releases_on_error():
    @heavy_endpoint(limit=1)
    def boom():
        raise RuntimeError("x")

    client = TestClient(_app_with(boom), raise_server_exceptions=False)
    assert client.get("/heavy").status_code == 500
    assert client.get("/heavy").status_code == 500       # not 429: the slot was freed


# ---------------- route ----------------

def _results_app():
    app = FastAPI()
    errors.install(app)
    app.include_router(results_routes.router, prefix="/api/results")
    return TestClient(app)


def test_results_route_serves_file_and_stale_flag(env):
    c = _results_app()
    r = c.get("/api/results/technical/latest")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    row = {"symbol": "AAA", "pattern": "ema_crossover", "signal": "BUY", "signal_date": "2026-01-02",
           "days_ago": 1, "price": 10.0}
    store.write_result("technical", [row])
    j = c.get("/api/results/technical/latest").json()
    assert [p["symbol"] for p in j["payload"]] == ["AAA"] and j["payload"][0]["signal"] == "BUY" and j["stale"] is False and j["generated_at"]

    store.write_result("pairs", [], now=datetime.now(UTC) - timedelta(hours=31))
    assert c.get("/api/results/pairs/latest").json()["stale"] is True
    assert c.get("/api/results/Bad..Kind/latest").status_code in (404, 422)
