"""D18: the pooled shadow run is isolated from the nightly run and from the per-symbol path.

* per-symbol results, trial counts, hold-out reads and the stored funnel are byte-identical with POOLED_VALIDATION off
  and shadow on (regression test on fixture data);
* any pooled failure is contained (it never raises, never changes the run's status);
* the forward test and the 'off' mode skip it entirely;
* the typed endpoint serves it (404 envelope, stale flag) and nothing in it is order-eligible."""
import json
import sqlite3

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import pooled_validation as PV
import scheduler
import validation
from api import errors
from results import store
from routes import results_routes
from services import models as M
from services import pooled as pooled_service
from services import scan as scan_svc
from services import session as session_svc
from state import db as state_db
from tests import pooled_helpers as H

SYMS = H.SYMBOLS10[:6]


class _Provider:
    def __init__(self, frames):
        self.frames = frames

    def ohlcv(self, symbol, period_days=None):
        return self.frames[symbol]

    def watchlist(self, markets=None):
        return dict(self.frames)

    def pair(self, *a, **k):
        return None


@pytest.fixture
def scan_env(monkeypatch):
    monkeypatch.setattr(session_svc, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(scan_svc, "send_daily_summary", lambda *a, **k: None)
    monkeypatch.setattr(scan_svc, "PATTERN_REGISTRY", {"planted": H.planted_pattern(1), "alt": H.planted_pattern(5, p=0.05),
                                                       "slow": H.planted_pattern(6, p=0.04, hold=6)})
    H.use_universe(monkeypatch, SYMS)


def _run_scan(tmp, monkeypatch, mode):
    monkeypatch.setattr(config, "POOLED_VALIDATION", mode)
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp / "state" / "trading.db"))
    return scan_svc.run_full_scan(modes=["technical"], provider=_Provider(H.planted_universe(SYMS)),
                                  pooled_shadow=True)


def _digest(tmp, results):
    """Everything the per-symbol path produced, as plain comparable data (run ids and timestamps dropped)."""
    from trials import load_run
    from trials.registry import matrix_hash
    funnel = dict(results["technical_funnel"])
    run_id = funnel.pop("run_id")
    hold = sqlite3.connect(tmp / "state" / "holdout_reads.sqlite")
    rows = [r for r in hold.execute("SELECT version, candidate_key, payload FROM holdout_reads ORDER BY 1, 2")
            if not r[1].startswith("pooled|")]
    tr = sqlite3.connect(tmp / "state" / "trials.sqlite")
    trials = tr.execute("SELECT ordinal, trial_id, symbol, pattern, params_hash, strategy_version, data_hash, n_obs "
                        "FROM trials WHERE run_id NOT LIKE 'pooled-%' ORDER BY ordinal").fetchall()
    run = load_run(run_id, db_path=str(tmp / "state" / "trials.sqlite"))
    return {"technical": json.dumps(results["technical"], sort_keys=True, default=str),
            "candidates": json.dumps([c.model_dump(mode="json") for c in results["technical_candidates"]], sort_keys=True),
            "funnel": funnel, "holdout_reads": rows, "trials": trials, "matrix": matrix_hash(run.returns_matrix()),
            "decisions": results["decisions"]}


def test_per_symbol_path_is_byte_identical_with_pooled_off_and_shadow(tmp_path, monkeypatch, scan_env):
    off = _run_scan(tmp_path / "off", monkeypatch, "off")
    on = _run_scan(tmp_path / "on", monkeypatch, "shadow")
    assert off.get("pooled") is None
    assert on["pooled"] and on["pooled"]["unit"] == "pooled" and on["pooled"]["status"] == "ok"
    d_off, d_on = _digest(tmp_path / "off", off), _digest(tmp_path / "on", on)
    assert d_off["trials"] and d_off["funnel"]["tested"] == 18               # 6 symbols x 3 patterns, N floored at the universe
    assert d_off == d_on
    # the pooled work lives only in its own namespaces
    tr = sqlite3.connect(tmp_path / "on" / "state" / "trials.sqlite")
    assert tr.execute("SELECT COUNT(*) FROM pooled_trials_ledger").fetchone()[0] > 0
    assert sqlite3.connect(tmp_path / "off" / "state" / "trials.sqlite").execute(
        "SELECT name FROM sqlite_master WHERE name='pooled_trials_ledger'").fetchone() is None


def test_per_symbol_results_json_is_byte_identical_whatever_the_pooled_mode(monkeypatch):
    frames = H.planted_universe(SYMS[:3], n=800)
    pats = {"planted": H.planted_pattern(1)}
    out = []
    for mode in ("off", "shadow", "off"):
        monkeypatch.setattr(config, "POOLED_VALIDATION", mode)
        out.append(validation.results_to_json(validation.evaluate_candidates(
            frames, pats, executable_variant=True, draws=100, min_trials=60)))
    assert out[0] == out[1] == out[2]


def test_pooled_never_changes_the_scan_when_it_is_narrowed(tmp_path, monkeypatch, scan_env):
    monkeypatch.setattr(config, "POOLED_VALIDATION", "shadow")
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))
    res = scan_svc.run_full_scan(modes=["technical"], symbol_filter="AAPL", pooled_shadow=True,
                                 provider=_Provider(H.planted_universe(SYMS)))
    assert "pooled" not in res or res.get("pooled") is None                    # a narrowed scan never computes pooled
    assert not (tmp_path / "state" / "trials.sqlite").exists() or sqlite3.connect(
        tmp_path / "state" / "trials.sqlite").execute(
        "SELECT name FROM sqlite_master WHERE name='pooled_trials_ledger'").fetchone() is None


# ------------------------------------------------------------------ switches
def test_off_and_forward_test_skip_pooled_entirely(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))
    frames = H.planted_universe(SYMS)
    monkeypatch.setattr(config, "POOLED_VALIDATION", "off")
    assert pooled_service.enabled() is False and pooled_service.run_shadow(frames) is None
    monkeypatch.setattr(config, "POOLED_VALIDATION", "shadow")
    assert pooled_service.enabled() is True
    monkeypatch.setattr(config, "FORWARD_TEST", True)
    assert pooled_service.enabled() is False and pooled_service.run_shadow(frames) is None     # the forward week
    monkeypatch.setattr(config, "POOLED_SKIP_IN_FORWARD_TEST", False)
    assert pooled_service.enabled() is True                                    # the owner can switch it on afterwards
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").glob("*.sqlite"))


# ------------------------------------------------------------------ failure isolation
def test_a_pooled_failure_becomes_an_error_payload_never_an_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POOLED_VALIDATION", "shadow")
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))

    def boom(*a, **k):
        raise RuntimeError("pooled blew up")

    monkeypatch.setattr(PV, "evaluate_pooled", boom)
    out = pooled_service.run_shadow({})
    assert out["status"] == pooled_service.ERROR_STATUS and "pooled blew up" in out["message"]
    assert out["order_eligible"] is False and out["shadow_only"] is True
    M.PooledPayload.model_validate(out)                                        # still a valid typed payload


def test_the_nightly_run_stays_ok_when_pooled_fails_or_cannot_be_stored(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    err = pooled_service.error_payload(RuntimeError("x"))

    def scan(**kw):
        return {"technical": [{"symbol": "AAA"}], "pairs": [], "ml": [], "decisions": [], "pooled": err}

    out = scheduler.run_nightly(modes=("technical",), scan_fn=scan)
    assert out["status"] == "ok" and out["summary"]["pooled"]["status"] == "pooled_error"
    assert store.read_latest("pooled")[0]["status"] == "pooled_error"
    assert store.read_latest("technical")[0] == [{"symbol": "AAA"}]
    # now the pooled result cannot be written at all: still ok, error recorded in the summary only
    monkeypatch.setattr(pooled_service, "store_result", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    out = scheduler.run_nightly(modes=("technical",), scan_fn=scan)
    assert out["status"] == "ok" and "disk full" in out["summary"]["pooled_error"]


def test_run_shadow_end_to_end_is_fast_enough_on_a_small_universe(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(config, "POOLED_VALIDATION", "shadow")
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))
    H.use_universe(monkeypatch, SYMS)
    t = time.perf_counter()
    out = pooled_service.run_shadow(H.planted_universe(SYMS), patterns={"planted": H.planted_pattern(1)})
    assert out["status"] == "ok" and out["elapsed_s"] < 60 and time.perf_counter() - t < 60


# ------------------------------------------------------------------ endpoint
def _app():
    app = FastAPI()
    errors.install(app)
    app.include_router(results_routes.router, prefix="/api/results")
    return TestClient(app)


def test_pooled_endpoint_404_envelope_and_typed_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    c = _app()
    r = c.get("/api/results/pooled/latest")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    H.use_universe(monkeypatch, SYMS)
    H.use_small_family(monkeypatch, ["planted"])
    from state.holdout import HoldoutStore
    from trials.pooled_ledger import PooledLedger
    payload = PV.evaluate_pooled(H.planted_universe(SYMS), {"planted": H.planted_pattern(1)},
                                 ledger=PooledLedger(str(tmp_path / "t.sqlite")), holdout_store=HoldoutStore(":memory:"),
                                 registry=False, write_components=False, draws=100)
    pooled_service.track_health(payload, None)
    store.write_result("pooled", payload)
    body = c.get("/api/results/pooled/latest").json()
    M.LatestPooledResult.model_validate(body)
    sh = body["payload"]["shadow_health"]            # the D18 phase-2 counters are served, not dropped
    assert sh["ok_sessions"] == 1 and sh["required_ok_sessions"] == 20 and sh["required_calendar_days"] == 28
    assert sh["complete"] is False and sh["first_ok_date"] and sh["ok_nights"] == 1
    assert body["kind"] == "pooled" and body["stale"] is False
    assert body["payload"]["shadow_only"] is True and body["payload"]["order_eligible"] is False
    assert all(p["order_eligible"] is False for p in body["payload"]["patterns"])
    assert body["payload"]["funnel"]["orders"] == 0 and body["payload"]["universe"]["hash"]
    row = body["payload"]["patterns"][0]
    assert row["pattern"] == "planted" and row["symbols"] and row["concentration"]["max_symbol_share"] is not None
    assert "possibly seen" in body["payload"]["disclosure"]
    # stale flag: a result older than 30 hours
    store.write_result("pooled", payload, now=pd.Timestamp.now(tz="UTC").to_pydatetime() - pd.Timedelta(hours=40))
    assert c.get("/api/results/pooled/latest").json()["stale"] is True


def test_old_funnel_without_a_unit_still_renders():
    old = {"tested": 340, "min_trades": 0, "oos_positive": 0, "psr": 0, "bh": 0, "dsr": None, "orders": 0,
           "n_trials": 780, "pbo": None, "sharpe_var": None, "run_id": "tech-1"}
    M.Funnel.model_validate(old)                                                # a missing `unit` means per-symbol
    M.LatestTechnicalResult.model_validate({"kind": "technical", "generated_at": "2026-10-05T22:00:00+00:00",
                                            "age_hours": 1.0, "stale": False, "payload": [], "funnel": old})


# ------------------------------------------------------------------ review round 1: scope, health, isolation, retention
def test_a_manual_scan_never_runs_the_pooled_shadow_only_the_nightly_scope_does(tmp_path, monkeypatch, scan_env):
    monkeypatch.setattr(config, "POOLED_VALIDATION", "shadow")
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "trading.db"))
    prov = _Provider(H.planted_universe(SYMS))
    manual = scan_svc.run_full_scan(modes=["technical"], provider=prov)           # what `main.py scan` does
    assert manual.get("pooled") is None
    assert not (tmp_path / "state" / "trials.sqlite").exists() or sqlite3.connect(
        tmp_path / "state" / "trials.sqlite").execute(
        "SELECT name FROM sqlite_master WHERE name='pooled_trials_ledger'").fetchone() is None
    assert not pooled_service.in_nightly_scope()
    with pooled_service.nightly_scope():
        assert pooled_service.in_nightly_scope()
        nightly = scan_svc.run_full_scan(modes=["technical"], provider=prov)
    assert not pooled_service.in_nightly_scope()
    assert nightly["pooled"]["status"] == "ok"
    forced_off = None
    with pooled_service.nightly_scope():
        forced_off = scan_svc.run_full_scan(modes=["technical"], provider=prov, pooled_shadow=False)
    assert forced_off.get("pooled") is None


def test_the_nightly_run_enters_the_pooled_scope_around_the_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    monkeypatch.setattr(scheduler.session, "alert", lambda *a, **k: True)
    seen = []

    def scan(**kw):
        seen.append(pooled_service.in_nightly_scope())
        return {"technical": [], "pairs": [], "ml": [], "decisions": []}

    out = scheduler.run_nightly(modes=("technical",), scan_fn=scan)
    assert out["status"] == "ok" and seen == [True] and not pooled_service.in_nightly_scope()


def _health_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "state" / "t.db"))
    monkeypatch.setattr(store, "RESULTS_DIR", tmp_path / "results")
    conn = state_db.connect()
    state_db.migrate(conn)
    conn.close()
    alerts = []
    monkeypatch.setattr(scheduler.session, "alert", lambda msg, kind="signal", dedup_key=None: alerts.append(
        (kind, dedup_key, msg)) or True)
    return alerts


def _nightly(payload):
    def scan(**kw):
        return {"technical": [], "pairs": [], "ml": [], "decisions": [], "pooled": dict(payload)}
    return scheduler.run_nightly(modes=("technical",), scan_fn=scan)


def test_consecutive_pooled_failures_raise_a_deduplicated_non_halt_alert(tmp_path, monkeypatch):
    alerts = _health_env(tmp_path, monkeypatch)
    err = pooled_service.error_payload(RuntimeError("x"))
    out = _nightly(err)
    assert out["summary"]["pooled"]["failed_streak"] == 1 and not alerts       # one bad night: no alert yet
    out = _nightly(err)                                                         # same UTC day again: counted once
    assert out["summary"]["pooled"]["failed_streak"] == 1 and not alerts
    # carry a prior stored payload that says "1 failed night, last counted yesterday"
    prior = dict(err, shadow_health={"ok_nights": 3, "consecutive_failed_nights": 1,
                                     "last_counted_date": "2000-01-01", "ok_dates": []})
    store.write_result("pooled", prior)
    out = _nightly(err)
    assert out["summary"]["pooled"]["failed_streak"] == 2 and out["summary"]["pooled"]["ok_nights"] == 3
    assert len(alerts) == 1 and alerts[0][0] == "pooled_shadow" and alerts[0][0] != "halt"
    assert alerts[0][1].startswith("pooled_shadow_failing:") and "2 nights in a row" in alerts[0][2]
    assert store.read_latest("pooled")[0]["shadow_health"]["consecutive_failed_nights"] == 2


def test_only_ok_complete_nights_count_toward_the_shadow_period(tmp_path):
    ok = {"status": "ok", "universe": {"complete": True}}
    partial = {"status": "ok", "universe": {"complete": False}}
    inc = {"status": PV.RUN_INCOMPLETE, "universe": {"complete": False}}
    from datetime import datetime, timedelta, timezone
    d0 = datetime(2026, 10, 12, 21, 47, tzinfo=timezone.utc)
    prior = None
    seq = [ok, ok, inc, partial, ok, ok]
    for i, payload in enumerate(seq):
        # the same-day re-run adds nothing
        for _ in range(2):
            pooled_service.track_health(payload, prior, now=d0 + timedelta(days=i))
            prior = dict(payload)
    h = prior["shadow_health"]
    assert h["ok_nights"] == 4 and h["consecutive_failed_nights"] == 0
    assert pooled_service.failure_alert_message({"shadow_health": {"consecutive_failed_nights": 1}}) is None
    assert pooled_service.failure_alert_message(
        {"status": "pooled_error", "shadow_health": {"consecutive_failed_nights": 2, "ok_nights": 4}})


def test_injecting_a_ledger_or_store_requires_an_explicit_registry_choice(tmp_path):
    from state.holdout import HoldoutStore
    from trials.pooled_ledger import PooledLedger
    frames, pats = H.planted_universe(SYMS), {"planted": H.planted_pattern(1)}
    for kw in ({"ledger": PooledLedger(str(tmp_path / "t.sqlite"))}, {"holdout_store": HoldoutStore(":memory:")}):
        with pytest.raises(ValueError, match="registry"):
            PV.evaluate_pooled(frames, pats, **kw)
    assert not (tmp_path / "trials").exists()


def test_the_research_helper_writes_nothing_to_the_live_trial_files(tmp_path, monkeypatch):
    from services import trial_runs
    H.use_universe(monkeypatch, SYMS)
    H.use_small_family(monkeypatch, ["planted"])
    for forbidden in ({"registry": None}, {"write_components": True}):
        with pytest.raises(TypeError):
            PV.evaluate_pooled_research(H.planted_universe(SYMS), {"planted": H.planted_pattern(1)}, **forbidden)
    out = PV.evaluate_pooled_research(H.planted_universe(SYMS), {"planted": H.planted_pattern(1)}, draws=100)
    assert out["status"] == "ok" and out["patterns"][0]["status"] != ""
    from trials.registry import default_trials_dir
    assert not list(default_trials_dir().glob("pooled-*")) if default_trials_dir().exists() else True
    live = trial_runs.trials_db_path()
    assert not __import__("os").path.exists(live) or sqlite3.connect(live).execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'pooled_%' OR name='trials'").fetchone()[0] == 0


def test_pooled_component_files_are_pruned_to_the_newest_n(tmp_path, monkeypatch):
    from trials.registry import default_trials_dir
    d = default_trials_dir()
    d.mkdir(parents=True, exist_ok=True)
    names = [f"pooled-20261001T0000{i:02d}.components.parquet" for i in range(20)]
    for n in names:
        (d / n).write_bytes(b"x")
    keep_other = [d / "pooled-20261001T000000.parquet", d / "tech-1.components.parquet", d / "tech-1.parquet"]
    for f in keep_other:
        f.write_bytes(b"x")
    monkeypatch.setattr(config, "POOLED_COMPONENTS_KEEP", 14)
    removed = PV.prune_components()
    assert sorted(removed) == sorted(names[:6])
    assert sorted(f.name for f in d.glob("pooled-*.components.parquet")) == sorted(names[6:])
    assert all(f.exists() for f in keep_other)                                  # the audit trail is never pruned


def test_shadow_signals_skip_a_pattern_that_failed_on_a_symbol():
    sd = PV.SymbolData("AAPL", pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.bdate_range("2026-10-01", periods=2)))
    sd.sigs = {"good": pd.Series([0, 1]), "bad": pd.Series([0, 1])}
    res = {"good": {"status": "unvalidated", "reasons": []},
           "bad": {"status": "unvalidated", "reasons": ["pattern_failed"]}}
    sig = PV._shadow_signals({"AAPL": sd}, ["AAPL"], res, ["good", "bad"])
    assert [s["pattern"] for s in sig] == ["good"]


# ------------------------------------------------------------------ review round 2
def test_pooled_modules_never_touch_the_order_path():
    """Static: no pooled module imports execution / brokers / paper_trader or names the order API."""
    import ast
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    banned_mods = {"execution", "brokers", "paper_trader"}
    banned_names = {"OrderIntent", "submit_intent", "dispatch_intents"}
    for rel in ("pooled_validation.py", "services/pooled.py", "stats/pooled.py", "trials/pooled_ledger.py",
                "patterns_prereg.py"):
        tree = ast.parse((root / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods = {(node.module or "").split(".")[0]}
                mods |= {a.name for a in node.names} & banned_mods
            else:
                mods = set()
            assert not (mods & banned_mods), f"{rel} imports {mods & banned_mods}"
            names = {node.id} if isinstance(node, ast.Name) else {node.attr} if isinstance(node, ast.Attribute) else set()
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {a.name for a in node.names}
            assert not (names & banned_names), f"{rel} names {names & banned_names}"


def _ok_payload(asof):
    return {"status": "ok", "universe": {"complete": True}, "asof": asof}


def test_the_shadow_clock_counts_sessions_not_calendar_nights():
    from datetime import datetime, timedelta, timezone
    d0 = datetime(2026, 10, 9, 21, 47, tzinfo=timezone.utc)          # a Friday
    prior = None
    # seven nightly runs spanning a weekend: the weekend runs see Friday's bar again
    asofs = ["2026-10-09", "2026-10-09", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"]
    for i, a in enumerate(asofs):
        payload = pooled_service.track_health(_ok_payload(a), prior, now=d0 + timedelta(days=i))
        prior = dict(payload)
    h = prior["shadow_health"]
    assert h["ok_nights"] == 7 and h["ok_sessions"] == 5 and h["first_ok_date"] == "2026-10-09"
    assert h["complete"] is False


def test_twenty_sessions_in_under_28_days_is_not_complete_and_28_days_is():
    from datetime import datetime, timedelta, timezone
    d0 = datetime(2026, 10, 12, 21, 47, tzinfo=timezone.utc)
    prior = None
    for i in range(20):                              # 20 distinct sessions on 20 consecutive days
        payload = pooled_service.track_health(_ok_payload(f"2026-09-{i + 1:02d}"), prior, now=d0 + timedelta(days=i))
        prior = dict(payload)
    h = prior["shadow_health"]
    assert h["ok_sessions"] == 20 and h["calendar_days"] == 19 and h["complete"] is False
    payload = pooled_service.track_health(_ok_payload("2026-11-01"), prior, now=d0 + timedelta(days=28))
    h = payload["shadow_health"]
    assert h["ok_sessions"] == 21 and h["calendar_days"] == 28 and h["complete"] is True


def test_the_nightly_scope_carries_the_results_root_to_the_prior(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(pooled_service, "enabled", lambda: True)
    monkeypatch.setattr(pooled_service, "_prior", lambda root=None: seen.setdefault("root", root) and None)
    monkeypatch.setattr(pooled_service, "_holdout_store", lambda: None)
    with pooled_service.nightly_scope(tmp_path / "forward-results"):
        assert pooled_service.nightly_root() == tmp_path / "forward-results"
        pooled_service.run_shadow({})
    assert seen["root"] == tmp_path / "forward-results" and pooled_service.nightly_root() is None


def test_intent_problem_reads_the_given_results_root(tmp_path, monkeypatch):
    seen = []
    from results import store as rs
    monkeypatch.setattr(rs, "read_latest", lambda kind, root=None: seen.append(root))
    monkeypatch.setattr(PV, "registered_universe", lambda: {"AAPL"})
    assert PV.intent_problem("AAPL", "x:planted", "v", "h", root=tmp_path) == "pooled_version_mismatch"
    assert seen == [tmp_path]
