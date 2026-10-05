"""WS3.3 persistent bar store: parquet cache, incremental refresh, atomic writes, retries, pins, stale serving."""
import logging
from datetime import datetime, timezone

import pandas as pd
import pytest

import config
import data_fetcher
from data import adapters, store
from data.validate import DataUnavailableError
from services import data_status
from tests.fixtures.fake_broker import init_state
from tests.test_data_layer import (FakeResp, _clean, alpaca, alpaca_bars, frame, sessions, yf_fake,  # noqa: F401
                                   yf_frame)

DAY1 = datetime(2024, 6, 4, 21, 0, tzinfo=timezone.utc)    # Tue, NYSE closed
SAME = datetime(2024, 6, 4, 23, 30, tzinfo=timezone.utc)   # same session, later
DAY2 = datetime(2024, 6, 5, 21, 0, tzinfo=timezone.utc)    # Wed, NYSE closed
SATURDAY = datetime(2024, 6, 8, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(adapters, "_sleep", slept.append)
    return slept


def put(yf, end, symbol="AAPL", **kw):
    yf.frames[symbol] = yf_frame("XNYS", "2015-06-01", end, **kw)


# ------------------------------------------------------------------ cache behaviour

def test_first_call_downloads_same_session_makes_no_request_next_session_one_incremental(yf_fake):
    put(yf_fake, "2024-06-04")
    a = store.get_bars("AAPL", 150, now=DAY1)
    assert len(yf_fake.calls) == 1 and not a.meta.from_cache and not a.stale
    path = store.bar_path("AAPL", "1d", "yfinance")
    assert path.exists() and path.parent.name == "equity" and path.name == "AAPL__1d__yfinance__adj.parquet"

    b = store.get_bars("AAPL", 150, now=SAME)
    assert len(yf_fake.calls) == 1 and b.meta.from_cache
    pd.testing.assert_frame_equal(a.df, b.df, check_freq=False)
    assert len(yf_fake.calls) == 1
    weekend = store.get_bars("AAPL", 150, now=SATURDAY)             # no new session closed since... Wed-Fri did
    assert weekend is not None

    yf_fake.calls.clear()
    put(yf_fake, "2024-06-05")
    c = store.get_bars("AAPL", 150, now=DAY2)
    assert len(yf_fake.calls) == 1
    assert yf_fake.calls[0][1]["start"] == "2024-05-28"            # last cached session (06-04) minus 5 sessions
    assert c.df.index[-1] == pd.Timestamp("2024-06-05") and len(c.df) == len(a.df) + 1 and not c.stale


def test_refresh_forces_a_fetch(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    store.get_bars("AAPL", 150, now=SAME, refresh=True)
    assert len(yf_fake.calls) == 2


def test_larger_window_than_cached_refetches_and_smaller_is_served_from_cache(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 120, now=DAY1)
    store.get_bars("AAPL", 60, now=DAY1)
    assert len(yf_fake.calls) == 1
    store.get_bars("AAPL", 200, now=DAY1)
    assert len(yf_fake.calls) == 2


def test_end_date_and_intraday_bypass_the_store(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, end_date=datetime(2024, 6, 4), now=DAY1)
    assert not store.bar_path("AAPL", "1d", "yfinance").exists()


def test_readjusted_history_triggers_full_refetch_and_is_logged(yf_fake, caplog):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    yf_fake.calls.clear()
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2015-06-01", "2024-06-05", base=50.0)   # a new split/dividend factor
    with caplog.at_level(logging.WARNING):
        c = store.get_bars("AAPL", 150, now=DAY2)
    assert len(yf_fake.calls) == 2                                   # incremental, then the full history
    assert any("re-adjusted" in r.getMessage() for r in caplog.records)
    new = yf_fake.frames["AAPL"]["Close"]
    assert c.df["Close"].iloc[0] == pytest.approx(new.loc[new.index.tz_localize(None) == c.df.index[0]].iloc[0])  # new basis throughout


def test_three_views_of_one_symbol_cause_one_provider_call(yf_fake, monkeypatch):
    put(yf_fake, "2024-06-04")
    monkeypatch.setattr(store.adapters, "_utc_now", lambda now=None: pd.Timestamp(DAY1))
    for _ in range(3):
        data_fetcher.fetch_ohlcv("AAPL", period_days=150)
    assert len(yf_fake.calls) == 1


def test_crash_mid_write_leaves_the_old_file_intact(yf_fake, monkeypatch):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    path = store.bar_path("AAPL", "1d", "yfinance")
    before = path.read_bytes()

    def boom(table, fh, *a, **k):
        fh.write(b"PAR1-partial")
        raise OSError("killed mid-write")

    monkeypatch.setattr(store.pq, "write_table", boom)
    put(yf_fake, "2024-06-05")
    got = store.get_bars("AAPL", 150, now=DAY2)                       # write fails -> still served, file untouched
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))
    assert store.read_file(path) is not None
    assert got.stale                                                  # refresh failed -> flagged
    with pytest.raises(OSError):
        store.write_atomic(path, got.df, {})
    assert path.read_bytes() == before and not list(path.parent.glob("*.tmp"))


def test_corrupt_file_is_a_cache_miss(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    store.bar_path("AAPL", "1d", "yfinance").write_bytes(b"garbage")
    store.get_bars("AAPL", 150, now=DAY1)
    assert len(yf_fake.calls) == 2


# ------------------------------------------------------------------ stale serving / pins

def test_failing_pinned_source_serves_cache_flagged_stale_and_never_switches(alpaca, yf_fake):
    put(yf_fake, "2024-06-04")
    alpaca.responder = lambda url, p: FakeResp({"bars": []})         # alpaca configured but empty: yfinance gets pinned
    store.get_bars("AAPL", 150, now=DAY1)
    assert adapters.pinned_source("AAPL") == "yfinance"
    alpaca.calls.clear()
    yf_fake.frames.clear()                                            # pinned source now has nothing
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(sessions("XNYS", "2023-09-01", "2024-06-05"))})
    b = store.get_bars("AAPL", 150, now=DAY2)
    assert b.stale and b.source == "yfinance" and b.meta.from_cache
    assert alpaca.calls == []                                         # no silent provider switch
    assert adapters.pinned_source("AAPL") == "yfinance"


def test_pin_lives_in_bar_sources_table_when_state_db_exists(yf_fake, monkeypatch, tmp_path):
    from state import db
    conn = db.connect(init_state(monkeypatch, tmp_path))
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    rows = [tuple(r) for r in conn.execute("SELECT symbol, window_class, source FROM bar_sources")]
    assert rows == [("AAPL", "default", "yfinance")]
    adapters._PINNED.clear()                                          # a fresh process: the table remembers
    assert adapters.pinned_source("AAPL") == "yfinance"
    store.get_bars("AAPL", config.RESEARCH_LOOKBACK_DAYS, now=DAY1)
    assert {r[0] for r in conn.execute("SELECT window_class FROM bar_sources")} == {"default", "research"}
    adapters.reset_pins("AAPL")
    assert conn.execute("SELECT COUNT(*) FROM bar_sources").fetchone()[0] == 0


def test_pins_fall_back_to_memory_without_a_state_db(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    assert adapters.pinned_source("AAPL") == "yfinance"
    adapters.reset_pins()
    assert adapters.pinned_source("AAPL") is None


# ------------------------------------------------------------------ HTTP retries

def test_429_with_retry_after_then_200_succeeds_in_three_requests(alpaca, yf_fake, _no_real_sleep):
    idx = sessions("XNYS", "2023-09-01", "2024-06-04")
    seq = [FakeResp({}, status=429), FakeResp({}, status=429), FakeResp({"bars": alpaca_bars(idx)})]
    seq[0].headers = {"Retry-After": "7"}
    seq[1].headers = {"Retry-After": "2"}
    alpaca.responder = lambda url, p: seq[len(alpaca.calls) - 1]
    b = store.get_bars("AAPL", 150, now=DAY1)
    assert b.source == "alpaca" and len(alpaca.calls) == 3 and yf_fake.calls == []
    assert _no_real_sleep == [7.0, 2.0]                               # honoured Retry-After, injected sleep


def test_5xx_backs_off_exponentially_with_jitter_and_gives_up(alpaca, yf_fake, _no_real_sleep):
    alpaca.responder = lambda url, p: FakeResp({}, status=503, text="down")
    put(yf_fake, "2024-06-04")
    b = store.get_bars("AAPL", 150, now=DAY1)
    assert len(alpaca.calls) == adapters.RETRY_ATTEMPTS and b.source == "yfinance"
    assert len(_no_real_sleep) == adapters.RETRY_ATTEMPTS - 1 and all(s >= 1.0 for s in _no_real_sleep)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_not_found_are_never_retried(alpaca, yf_fake, _no_real_sleep, status):
    alpaca.responder = lambda url, p: FakeResp({}, status=status, text="no")
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    assert len(alpaca.calls) == 1 and _no_real_sleep == []


def test_timeouts_are_retried(monkeypatch, _no_real_sleep):
    calls = []

    def flaky(url, **kw):
        calls.append(url)
        if len(calls) < 3:
            raise adapters.requests.Timeout("slow")
        return FakeResp({"ok": 1})

    monkeypatch.setattr(adapters.requests, "get", flaky)
    assert adapters.http_get("http://x").status_code == 200 and len(calls) == 3


# ------------------------------------------------------------------ data status

def test_data_status_lists_watchlist_and_allocation_symbols_with_cache_fields(yf_fake, monkeypatch):
    import allocation
    put(yf_fake, "2024-06-04")
    monkeypatch.setattr(store.adapters, "_utc_now", lambda now=None: pd.Timestamp(DAY1))
    monkeypatch.setattr(config, "WATCHLIST", {"us": ["AAPL"]})
    monkeypatch.setattr(allocation, "CORE_TARGETS", {"AAPL": 1.0})
    monkeypatch.setattr(allocation, "TREND_UNIVERSE", ["MSFT"])
    from services.providers import DefaultProvider
    rows = {r.symbol: r for r in data_status.data_status(DefaultProvider())}
    assert set(rows) == {"AAPL", "MSFT", allocation.CASH_SYMBOL}
    assert rows["MSFT"].status == "no_data"
    store.get_bars("AAPL", config.LOOKBACK_DAYS, now=DAY1)
    again = data_status.data_status(DefaultProvider(), ["AAPL"])[0]
    assert again.source == "yfinance" and again.adjusted is True and again.stale is False
    assert again.cache_age_seconds is not None and again.last_session.startswith("2024-06-04")


def test_cache_info_reads_metadata_without_network(yf_fake):
    put(yf_fake, "2024-06-04")
    store.get_bars("AAPL", 150, now=DAY1)
    yf_fake.calls.clear()
    info = store.cache_info("AAPL", now=SAME)
    assert info[0]["source"] == "yfinance" and info[0]["cache_age_seconds"] == pytest.approx(9000.0) and yf_fake.calls == []
