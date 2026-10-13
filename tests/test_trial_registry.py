"""WS4.1: trial registry (N honest per run, aligned (date x trial) matrix, deterministic, never drops)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import config
import validation as V
from state import db as state_db
from tests.test_validation import GRID, PATTERNS, make_frame
from trials import TrialRegistry, TrialRegistryError, load_run, matrix_hash

KW = dict(draws=60, seed=config.SEED, train=250, test=60, step=60)


def _frames():
    return {s: make_frame(seed, 900) for s, seed in (("AAA", 1), ("BBB", 7), ("CCC", 11))}


def _run(tmp_path, run_id="r1", frames=None, **extra):
    reg = TrialRegistry(run_id, trials_dir=tmp_path / "trials")
    res = V.evaluate_candidates(frames or _frames(), PATTERNS, GRID, trials=reg, end="2030-01-01", **KW, **extra)
    return reg, res


def test_three_symbols_four_patterns_two_params_is_24_trials(tmp_path):
    reg, res = _run(tmp_path)
    assert len(res) == 24 and reg.n_trials == 24
    m = reg.returns_matrix()
    assert m.shape[1] == 24 and m.index.is_monotonic_increasing and not m.index.has_duplicates
    assert len(set(m.columns)) == 24
    loaded = load_run("r1", trials_dir=tmp_path / "trials")
    assert loaded.n_trials == 24
    assert matrix_hash(loaded.returns) == matrix_hash(m)
    assert {(t.symbol, t.pattern) for t in loaded.trials} == {(s, p) for s in _frames() for p in PATTERNS}
    assert all(t.strategy_version and t.data_hash and t.params_hash for t in loaded.trials)
    assert all(t.n_obs == int(m[t.trial_id].notna().sum()) for t in loaded.trials)
    rows = state_db.connect(config.STATE_DB_PATH).execute("SELECT COUNT(*) FROM trials WHERE run_id='r1'")
    assert rows.fetchone()[0] == 24


def test_same_seed_and_data_give_identical_matrix_hash(tmp_path):
    a, _ = _run(tmp_path, "ra")
    b, _ = _run(tmp_path, "rb")
    assert matrix_hash(a.returns_matrix()) == matrix_hash(b.returns_matrix())
    assert [r.data_hash for r in a.records] == [r.data_hash for r in b.records]
    assert [r.strategy_version for r in a.records] == [r.strategy_version for r in b.records]


def test_invalid_trials_are_recorded_too(tmp_path):
    frames = {"AAA": make_frame(1, 900), "SHORT": make_frame(2, 60)}   # SHORT: insufficient history
    reg, res = _run(tmp_path, frames=frames)
    assert reg.n_trials == len(res) == 16
    short = [r for r in reg.records if r.symbol == "SHORT"]
    assert len(short) == 8 and all(r.n_obs == 0 for r in short)


def test_validation_without_registry_is_unchanged(tmp_path):
    plain = V.evaluate_candidates(_frames(), PATTERNS, GRID, end="2030-01-01", **KW)
    _, with_reg = _run(tmp_path)
    assert V.results_to_json(plain) == V.results_to_json(with_reg)


def test_duplicate_trial_raises(tmp_path):
    reg = TrialRegistry("d", trials_dir=tmp_path)
    s = pd.Series([0.01, -0.01], index=pd.bdate_range("2020-01-01", periods=2))
    kw = dict(symbol="A", pattern="p", params={"x": 1}, returns=s, strategy_version="v", data_hash="h")
    reg.record(**kw)
    with pytest.raises(TrialRegistryError):
        reg.record(**kw)
    assert reg.n_trials == 1


def test_bad_series_and_reused_run_raise(tmp_path):
    reg = TrialRegistry("e", trials_dir=tmp_path)
    kw = dict(symbol="A", pattern="p", params={}, strategy_version="v", data_hash="h")
    with pytest.raises(TrialRegistryError):
        reg.record(returns=pd.Series([0.1, 0.2]), **kw)                      # no dates
    dup = pd.Series([0.1, 0.2], index=pd.DatetimeIndex(["2020-01-01", "2020-01-01"]))
    with pytest.raises(TrialRegistryError):
        reg.record(returns=dup, **kw)
    assert reg.n_trials == 0
    reg.record(returns=pd.Series([0.1], index=pd.DatetimeIndex(["2020-01-01"])), **kw)
    with pytest.raises(TrialRegistryError):
        TrialRegistry("e", trials_dir=tmp_path)                              # run already has trials


def test_db_failure_raises_not_swallowed(tmp_path):
    reg = TrialRegistry("f", trials_dir=tmp_path)
    reg._conn.execute("DROP TABLE trials")
    with pytest.raises(TrialRegistryError):
        reg.record(symbol="A", pattern="p", params={}, returns=pd.Series(dtype=float), strategy_version="v",
                   data_hash="h")
    assert reg.n_trials == 0


def test_flush_failure_and_mismatch_raise(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("x")                                                   # a file where the dir must go
    reg = TrialRegistry("g", trials_dir=blocker)
    reg.record(symbol="A", pattern="p", params={}, returns=pd.Series(dtype=float), strategy_version="v",
               data_hash="h")
    with pytest.raises(TrialRegistryError):
        reg.flush()
    ok = TrialRegistry("h", trials_dir=tmp_path / "t")
    ok.record(symbol="A", pattern="p", params={}, returns=pd.Series(dtype=float), strategy_version="v",
              data_hash="h")
    ok._conn.execute("DELETE FROM trials WHERE run_id='h'")                   # DB lost a trial
    with pytest.raises(TrialRegistryError):
        ok.flush()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    reg, _ = _run(tmp_path)
    assert [p.name for p in (tmp_path / "trials").iterdir()] == ["r1.parquet"]


def test_load_unknown_or_corrupt_run_raises(tmp_path):
    with pytest.raises(TrialRegistryError):
        load_run("nope", trials_dir=tmp_path)
    reg, _ = _run(tmp_path)
    reg.path.write_bytes(b"garbage")
    with pytest.raises(TrialRegistryError):
        load_run("r1", trials_dir=tmp_path / "trials")
