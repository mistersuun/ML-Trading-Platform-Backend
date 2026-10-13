"""D18 bypass resistance: narrowing the universe or the pattern set cannot lower the bar, re-registering a universe
costs trials, a stale / incomplete verdict never confers eligibility, the execution chokepoint checks the universe.
The guards live in tests/pooled_guards.py (the mutation meta-test reuses them)."""
import pytest
from hypothesis import HealthCheck, example, given, settings, strategies as st

import config
import pooled_validation as PV
from tests import pooled_guards as G
from tests import pooled_helpers as H


@pytest.mark.parametrize("name", sorted(G.GUARDS))
def test_guard(name, tmp_path, monkeypatch):
    G.GUARDS[name](tmp_path, monkeypatch)


@pytest.fixture(scope="module")
def _frames():
    return H.planted_universe()


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(missing=st.sets(st.sampled_from(H.SYMBOLS10), min_size=1, max_size=9))
@example(missing={"DIA"})
@example(missing={"AAPL", "MSFT"})
def test_property_a_strict_subset_never_produces_a_fresh_validated_status(missing, _frames, tmp_path_factory, monkeypatch):
    """Test 12: any strict subset of the registered universe is pooled_universe_incomplete (or, at <= 10% missing,
    flagged partial and unable to read the hold-out): never a fresh validated status."""
    tmp = tmp_path_factory.mktemp("subset")
    frames = {s: f for s, f in _frames.items() if s not in missing}
    out = G.run_small(tmp, monkeypatch, frames=frames, family=["planted"])
    frac = len(missing) / len(H.SYMBOLS10)
    if frac > config.POOLED_MAX_MISSING:
        assert out["status"] == PV.RUN_INCOMPLETE
    else:
        assert out["status"] == "ok" and out["universe"]["partial"]
    assert not out["universe"]["complete"]
    assert all(p["status"] == "unvalidated" for p in out["patterns"])
    assert G._holdout_rows(tmp) == 0
    assert out["order_eligible"] is False


def test_shadow_statuses_cannot_reach_submit_intent_as_eligible(tmp_path, monkeypatch):
    """A pooled status is rejected by the chokepoint (not_validated) however the intent is labelled."""
    from execution import OrderIntent
    from tests.test_interlocks import fresh_env, go
    with fresh_env() as env:
        for status in PV.POOLED_STATUSES:
            for unit in ("per_symbol", "pooled"):
                d = go(env, OrderIntent("AAPL", 1, f"2024-02-{1 + len(status) % 5:02d}", "pooled:planted", 1.0, status,
                                        1.0, 100.0, validation_unit=unit))
                assert d.status == "rejected" and d.reasons == ["not_validated"], (status, unit, d)
        assert env.fake.calls_to("submit_order") == []


def test_pooled_modes_and_defaults():
    assert config.POOLED_VALIDATION == "shadow"                              # default
    assert config.POOLED_RETIRED_PATTERNS == {"macd_hist_reversal": "macd_crossover"}
    assert config.POOLED_APPROVED_UNIVERSES == {}


def test_invalid_pooled_mode_falls_back_to_off(monkeypatch):
    import importlib
    monkeypatch.setenv("POOLED_VALIDATION", "live")
    try:
        import config as cfg
        mod = importlib.reload(cfg)
        assert mod.POOLED_VALIDATION == "off"
        monkeypatch.setenv("POOLED_VALIDATION", "SHADOW ")
        assert importlib.reload(cfg).POOLED_VALIDATION == "shadow"
    finally:
        monkeypatch.delenv("POOLED_VALIDATION", raising=False)
        importlib.reload(config)
