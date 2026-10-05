"""WS1.4: persisted RiskManager + kill switch. All DBs are tmp_path; no real state/."""
from datetime import datetime, timezone

import pytest

import config
from risk_manager import RiskManager
from state import db as state_db

T0 = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)  # Wednesday
E = 10_000.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    monkeypatch.setattr(config, "STATE_DB_PATH", str(path))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    conn = state_db.connect(path)
    state_db.migrate(conn)
    return path, conn


@pytest.fixture
def rm(env):
    r = RiskManager(env[1])
    r.update_equity(E, T0)
    return r


def test_fresh_allows_full_multiplier(rm):
    c = rm.check(T0)
    assert c.allowed and c.reasons == [] and c.risk_multiplier == 1.0


def test_kill_switch_file(rm, tmp_path):
    (tmp_path / "KILL").write_text("")
    c = rm.check(T0)
    assert not c.allowed and "kill_switch" in c.reasons
    (tmp_path / "KILL").unlink()
    assert rm.check(T0).allowed


def test_kill_switch_env(rm, monkeypatch):
    monkeypatch.setenv("KILL_SWITCH", "1")
    assert not rm.check(T0).allowed


def test_drawdown_halt_persists_and_never_self_clears(rm, env):
    events = rm.update_equity(E * 0.89, T0)
    assert events and events[0]["type"] == "halt"
    assert not rm.check(T0).allowed
    rm.update_equity(E * 1.5, T0)  # recovery does not clear
    c = rm.check(T0)
    assert not c.allowed and any("halted" in r for r in c.reasons)
    assert rm.status()["halted"] and rm.status()["halt_reason"]


def test_restart_stays_halted(rm, env):
    rm.update_equity(E * 0.85, T0)
    rm2 = RiskManager(state_db.connect(env[0]))
    rm2.update_equity(E, T0)
    assert not rm2.check(T0).allowed


def test_resume_requires_confirm_and_clears(rm):
    rm.halt("manual test")
    with pytest.raises(ValueError):
        rm.resume()
    with pytest.raises(ValueError):
        rm.resume(confirm=False)
    rm.resume(confirm=True)
    assert rm.check(T0).allowed


def test_resume_resets_peak_so_no_instant_rehalt(rm):
    rm.update_equity(E * 0.85, T0)
    rm.resume(confirm=True)
    rm.update_equity(E * 0.85, T0)
    assert not rm.status()["halted"]


def test_daily_loss_stop_clears_next_day(rm):
    rm.update_equity(E * (1 - config.DAILY_LOSS_STOP_PCT), T0)
    c = rm.check(T0)
    assert not c.allowed and "daily_loss_stop" in c.reasons
    assert not rm.status()["halted"]
    nxt = datetime(2026, 3, 5, 15, 0, tzinfo=timezone.utc)
    rm.update_equity(E * (1 - config.DAILY_LOSS_STOP_PCT), nxt)
    assert rm.check(nxt).allowed


def test_weekly_loss_stop_and_week_rollover(rm):
    # spread losses so the daily stop is not hit: -1.1% Wed, Thu, Fri
    eq = E
    for d in (4, 5, 6):
        t = datetime(2026, 3, d, 15, tzinfo=timezone.utc)
        eq *= 0.989
        rm.update_equity(eq + 0.0, t)
    t = datetime(2026, 3, 6, 16, tzinfo=timezone.utc)
    c = rm.check(t)
    assert not c.allowed and "weekly_loss_stop" in c.reasons and "daily_loss_stop" not in c.reasons
    mon = datetime(2026, 3, 9, 15, tzinfo=timezone.utc)
    rm.update_equity(eq, mon)
    assert rm.check(mon).allowed


def test_orders_per_day_cap_and_rollover(rm):
    for _ in range(config.MAX_ORDERS_PER_DAY):
        assert rm.check(T0).allowed
        rm.record_order(T0)
    c = rm.check(T0)
    assert not c.allowed and "max_orders_per_day" in c.reasons
    nxt = datetime(2026, 3, 5, 0, 1, tzinfo=timezone.utc)
    assert rm.check(nxt).allowed


@pytest.mark.parametrize("dd,mult", [(0.0, 1.0), (0.049, 1.0), (0.05, 0.5), (0.07, 0.5), (0.08, 0.25), (0.095, 0.25)])
def test_ladder_multipliers(rm, dd, mult):
    # equal-day drawdown would trip loss stops; step down across days
    t = datetime(2026, 3, 4, 15, tzinfo=timezone.utc)
    rm.update_equity(E * (1 - dd), datetime(2026, 3, 4, 15, tzinfo=timezone.utc))
    # neutralise loss stops by evaluating on a later week with fresh baselines
    later = datetime(2026, 3, 16, 15, tzinfo=timezone.utc)
    rm.update_equity(E * (1 - dd), later)
    c = rm.check(later)
    assert c.allowed and c.risk_multiplier == mult


def test_fail_closed_missing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DB_PATH", str(tmp_path / "nope.db"))
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    c = RiskManager().check(T0)
    assert not c.allowed and "state_not_initialized" in c.reasons
    assert not (tmp_path / "nope.db").exists()


def test_fail_closed_unmigrated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    c = RiskManager(state_db.connect(tmp_path / "empty.db")).check(T0)
    assert not c.allowed and "state_not_initialized" in c.reasons


def test_fails_closed_if_equity_unknown_after_restart(rm, env):
    rm2 = RiskManager(state_db.connect(env[0]))
    c = rm2.check(T0)
    assert not c.allowed and "equity_not_updated" in c.reasons


def test_halt_event_and_status(rm):
    ev = rm.halt("manual")
    assert ev["type"] == "halt" and ev["reason"] == "manual"
    assert not rm.check(T0).allowed
    assert rm.status()["halt_reason"] == "manual"


# ── review fixes: equity validation, missing equity, kill switch values, realized P&L ──

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -5.0, None, "10000"])
def test_update_equity_rejects_invalid_readings(env, bad):
    r = RiskManager(env[1])
    with pytest.raises(ValueError):
        r.update_equity(bad, T0)
    row = env[1].execute("SELECT peak_equity, day_start_equity, week_start_equity FROM risk_state").fetchone()
    assert tuple(row) == (None, None, None)  # nothing was seeded from the bad reading
    c = r.check(T0)
    assert not c.allowed and "equity_not_updated" in c.reasons


def test_nan_reading_cannot_reseed_the_peak_and_hide_a_loss(rm):
    with pytest.raises(ValueError):
        rm.update_equity(float("nan"), T0)
    assert rm.check(T0).allowed is False  # the stale reading is dropped, not trusted
    rm.update_equity(E * 0.5, T0)  # a 50% loss afterwards still halts against the real peak
    c = rm.check(T0)
    assert not c.allowed and any(x.startswith("halted") for x in c.reasons)


def test_fresh_db_without_equity_fails_closed(env):
    c = RiskManager(env[1]).check(T0)
    assert not c.allowed and "equity_not_updated" in c.reasons and c.risk_multiplier == 0.0


@pytest.mark.parametrize("val,blocked", [("1", True), ("true", True), ("YES", True), (" on ", True),
                                         ("0", False), ("", False), ("false", False)])
def test_kill_switch_env_values(rm, monkeypatch, val, blocked):
    monkeypatch.setenv("KILL_SWITCH", val)
    assert (not rm.check(T0).allowed) is blocked


def test_realized_loss_persists_after_the_position_is_gone(env, monkeypatch):
    """A stop fills (position closed, no open P&L) after a >10% loss: the sleeve must still halt."""
    r = RiskManager(env[1])
    sleeve0 = r.sleeve_equity(account_equity=100_000.0, open_pnl=0.0)
    assert sleeve0 == config.SIGNAL_SLEEVE_EQUITY
    r.update_equity(sleeve0, T0)
    # position opened and stopped out for -11% of the sleeve: account equity down, no open position left
    loss = 0.11 * config.SIGNAL_SLEEVE_EQUITY
    r2 = RiskManager(env[1])  # a new run: baseline comes from the DB, not memory
    sleeve1 = r2.sleeve_equity(account_equity=100_000.0 - loss, open_pnl=0.0)
    assert sleeve1 == pytest.approx(config.SIGNAL_SLEEVE_EQUITY - loss)
    events = r2.update_equity(sleeve1, T0)
    assert events and events[0]["type"] == "halt"
    c = r2.check(T0)
    assert not c.allowed and any(x.startswith("halted") for x in c.reasons)


def test_baseline_excludes_open_pnl_at_first_session(env):
    r = RiskManager(env[1])
    assert r.sleeve_equity(100_500.0, open_pnl=500.0) == config.SIGNAL_SLEEVE_EQUITY + 500.0
    assert RiskManager(env[1]).sleeve_equity(100_000.0, open_pnl=0.0) == config.SIGNAL_SLEEVE_EQUITY  # P&L gone, baseline fixed


def test_sleeve_equity_refuses_non_finite(env):
    with pytest.raises(ValueError):
        RiskManager(env[1]).sleeve_equity(float("nan"), 0.0)


def test_refresh_sleeve_equity_halts_a_depleted_sleeve(env):
    from brokers.alpaca import AlpacaBroker
    from brokers.fake import FakeBroker
    import execution
    fb = FakeBroker(equity=100_000.0)
    r = RiskManager(env[1])
    execution.refresh_sleeve_equity(r, AlpacaBroker(fb), now=T0)  # baseline at 100k
    fb.account.equity = str(100_000.0 - config.SIGNAL_SLEEVE_EQUITY - 1)  # lost the whole sleeve and more
    ev = execution.refresh_sleeve_equity(r, AlpacaBroker(fb), now=T0)
    assert ev and ev[0]["type"] == "halt"
    assert not RiskManager(env[1]).check(T0).allowed
