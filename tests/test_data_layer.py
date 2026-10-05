"""WS2.1 data layer: adapters, calendars, validation, pinning, pair alignment. Fully offline."""

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import config
import data_fetcher
from data import adapters, calendars
from data.validate import DataQualityError, DataUnavailableError, validate_ohlcv

NOW_AFTER_CLOSE = datetime(2024, 6, 4, 21, 0, tzinfo=timezone.utc)  # Tue 17:00 ET, NYSE closed
NOW_MID_SESSION = datetime(2024, 6, 4, 15, 0, tzinfo=timezone.utc)  # Tue 11:00 ET, session open


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    adapters.reset_pins()
    monkeypatch.setattr(config, "ALPACA_API_KEY", "")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "")
    yield
    adapters.reset_pins()


def frame(index, base=100.0):
    n = len(index)
    c = base + np.arange(n) * 0.1
    return pd.DataFrame({"Open": c * 0.995, "High": c * 1.01, "Low": c * 0.98, "Close": c,
                         "Volume": np.full(n, 1000.0)}, index=pd.DatetimeIndex(index))


def sessions(cal, start, end):
    return calendars.get_calendar(cal).sessions(start, end)


class FakeTicker:
    frames: dict = {}
    calls: list = []

    def __init__(self, symbol):
        self.symbol = symbol

    def history(self, **kw):
        FakeTicker.calls.append((self.symbol, kw))
        f = FakeTicker.frames.get(self.symbol)
        return pd.DataFrame() if f is None else f.copy()


@pytest.fixture
def yf_fake(monkeypatch):
    FakeTicker.frames, FakeTicker.calls = {}, []
    monkeypatch.setattr(adapters.yf, "Ticker", FakeTicker)
    return FakeTicker


def yf_frame(cal, start, end, tz="America/New_York", **kw):
    f = frame(sessions(cal, start, end), **kw)
    f.index = f.index.tz_localize(tz)
    return f


class FakeResp:
    def __init__(self, payload, status=200, text=""):
        self._p, self.status_code, self.text = payload, status, text

    def json(self):
        return self._p


@pytest.fixture
def alpaca(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_API_KEY", "k")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "s")
    st = type("A", (), {})()
    st.calls, st.responder = [], None

    def fake_get(url, headers=None, params=None, **kw):
        st.calls.append((url, dict(params or {})))
        return st.responder(url, params)

    monkeypatch.setattr(adapters.requests, "get", fake_get)
    return st


def alpaca_bars(index, stamp=None):
    """Alpaca daily stamps: midnight New York (04:00Z/05:00Z) for stocks, 00:00Z for crypto."""
    f = frame(index)

    def ts(d):
        if stamp:
            return d.strftime("%Y-%m-%d") + stamp
        return d.tz_localize("America/New_York").tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")

    return [{"t": ts(d), "o": r.Open, "h": r.High, "l": r.Low, "c": r.Close, "v": r.Volume}
            for d, r in f.iterrows()]


# ------------------------------------------------------------------ calendars

def test_nyse_holiday_not_a_session_and_close_time():
    cal = calendars.get_calendar("XNYS")
    assert not cal.is_session("2024-01-01") and not cal.is_session("2024-06-01")  # New Year, Saturday
    assert cal.is_session("2024-06-03")
    assert cal.session_close("2024-06-03") == pd.Timestamp("2024-06-03 20:00", tz="UTC")  # 16:00 EDT
    assert cal.session_close("2024-01-02") == pd.Timestamp("2024-01-02 21:00", tz="UTC")  # 16:00 EST


def test_last_closed_session_respects_close():
    cal = calendars.get_calendar("XNYS")
    assert cal.last_closed_session(NOW_MID_SESSION) == pd.Timestamp("2024-06-03")
    assert cal.last_closed_session(NOW_AFTER_CLOSE) == pd.Timestamp("2024-06-04")
    assert cal.last_closed_session(datetime(2024, 6, 8, 12, tzinfo=timezone.utc)) == pd.Timestamp("2024-06-07")  # Sat


def test_crypto_and_fx_and_futures_calendars():
    crypto = calendars.get_calendar("24/7")
    assert len(crypto.sessions("2024-06-01", "2024-06-09")) == 9
    assert crypto.last_closed_session(datetime(2024, 6, 2, 0, 0, 1, tzinfo=timezone.utc)) == pd.Timestamp("2024-06-01")
    fx = calendars.get_calendar("FX")
    assert len(fx.sessions("2024-06-01", "2024-06-09")) == 5
    cmes = calendars.get_calendar("CMES")
    assert cmes.is_session("2024-06-03") and not cmes.is_session("2024-06-01")
    with pytest.raises(KeyError):
        calendars.get_calendar("MOON")


# ------------------------------------------------------------------ validator

def good(n=300):
    return frame(sessions("XNYS", "2023-01-03", "2024-03-01")[:n])


def test_validator_accepts_clean_frame():
    rep = validate_ohlcv(good(), "XNYS")
    assert rep.ok and rep.coverage == 1.0


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.iloc[::-1], "monotonic"),
    (lambda d: pd.concat([d, d.iloc[[5]]]).sort_index(), "duplicate"),
    (lambda d: d.assign(Close=d.Close.where(d.index != d.index[10], 0.0)), "non-positive"),
    (lambda d: d.assign(Open=d.Open.where(d.index != d.index[10], -1.0)), "non-positive"),
    (lambda d: d.assign(High=d.High.where(d.index != d.index[20], d.Close.iloc[20] * 0.9)), "High <"),
    (lambda d: d.assign(Low=d.Low.where(d.index != d.index[20], d.Close.iloc[20] * 1.1)), "Low >"),
    (lambda d: d.assign(Volume=d.Volume.where(d.index != d.index[30], -5.0)), "negative Volume"),
    (lambda d: d.assign(Close=d.Close.where(d.index != d.index[30], np.nan)), "NaN"),
])
def test_validator_hard_failures_raise(mutate, needle):
    with pytest.raises(DataQualityError, match=needle):
        validate_ohlcv(mutate(good()), "XNYS")


def test_validator_nonraising_mode_returns_report():
    rep = validate_ohlcv(good().iloc[::-1], "XNYS", raise_on_failure=False)
    assert not rep.ok and rep.failures


def test_validator_coverage_threshold_replaces_len_30():
    d = good()
    gappy = d.drop(d.index[50:80])  # ~10% missing
    with pytest.raises(DataQualityError, match="coverage"):
        validate_ohlcv(gappy, "XNYS")
    few = d.drop(d.index[50:52])  # 2 of 300 missing: still above DATA_COVERAGE_MIN
    assert validate_ohlcv(few, "XNYS").coverage >= config.DATA_COVERAGE_MIN
    short_but_complete = d.iloc[:20]  # fewer than 30 bars is fine when coverage is complete
    assert validate_ohlcv(short_but_complete, "XNYS").ok


def test_validator_flags_large_return_without_failing():
    d = good()
    d.loc[d.index[100:], ["Open", "High", "Low", "Close"]] *= 2.0
    rep = validate_ohlcv(d, "XNYS")
    assert rep.ok and any("abs return" in f for f in rep.flags)


def test_validator_staleness_vs_calendar():
    d = good()
    end = pd.Timestamp("2024-04-01")
    with pytest.raises(DataQualityError, match="stale|coverage"):
        validate_ohlcv(d, "XNYS", start=d.index[0], end=end, now=datetime(2024, 4, 2, tzinfo=timezone.utc))


# ------------------------------------------------------------------ yfinance adapter

def test_yfinance_called_with_auto_adjust_true(yf_fake):
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    b = adapters.fetch_bars("AAPL", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert yf_fake.calls[0][1]["auto_adjust"] is True
    assert b.source == "yfinance" and b.adjusted is True and b.calendar == "XNYS"
    assert b.df.attrs["source"] == "yfinance" and b.quality.ok and b.fetched_at.tzinfo is not None
    assert isinstance(b.df.index, pd.DatetimeIndex) and b.df.index.tz is None
    assert (b.df.index == b.df.index.normalize()).all()


def test_in_progress_session_dropped_then_kept_after_close(yf_fake):
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    mid = adapters.fetch_bars("AAPL", 150, end_date=NOW_MID_SESSION, now=NOW_MID_SESSION)
    assert mid.df.index[-1] == pd.Timestamp("2024-06-03") and mid.quality.dropped_in_progress == 1
    adapters.reset_pins()
    after = adapters.fetch_bars("AAPL", 150, end_date=NOW_AFTER_CLOSE, now=NOW_AFTER_CLOSE)
    assert after.df.index[-1] == pd.Timestamp("2024-06-04")


def test_shim_fetch_ohlcv_drops_today_with_frozen_clock(yf_fake, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW_MID_SESSION if tz is not None else NOW_MID_SESSION.replace(tzinfo=None)

    monkeypatch.setattr(data_fetcher, "datetime", Frozen)
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    df = data_fetcher.fetch_ohlcv("AAPL", period_days=150)
    assert isinstance(df, pd.DataFrame) and df.index[-1].date() < NOW_MID_SESSION.date()


def test_non_session_dates_dropped_and_reported(yf_fake):
    f = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    extra = frame([pd.Timestamp("2024-05-27")]).set_axis(pd.DatetimeIndex(["2024-05-27"]).tz_localize("America/New_York"))
    yf_fake.frames["AAPL"] = pd.concat([f, extra]).sort_index()  # Memorial Day bar
    b = adapters.fetch_bars("AAPL", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert pd.Timestamp("2024-05-27") not in b.df.index and b.quality.dropped_non_session == 1


def test_adjusted_inconsistency_raises_not_returned(yf_fake):
    """DATA-2 equivalent: mixed adjusted-close / raw O,H,L (High < Close) is a hard failure."""
    f = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    f.iloc[:30, f.columns.get_loc("Close")] *= 3.0  # close no longer inside [Low, High]
    yf_fake.frames["AAPL"] = f
    with pytest.raises(DataQualityError):
        adapters.fetch_bars("AAPL", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert data_fetcher.fetch_ohlcv("AAPL", 150, end_date=datetime(2024, 6, 4)).empty


def test_no_data_raises_unavailable_and_shim_returns_empty(yf_fake):
    with pytest.raises(DataUnavailableError):
        adapters.fetch_bars("AAPL", 150, now=NOW_AFTER_CLOSE)
    assert data_fetcher.fetch_ohlcv("AAPL", 150).empty


# ------------------------------------------------------------------ Alpha Vantage is gone

def test_alpha_vantage_never_called_and_futures_not_sent_as_equity(yf_fake, alpaca, monkeypatch):
    """DATA-1/DATA-2 by construction: no AV adapter, no equity call for a futures symbol."""
    monkeypatch.setattr(config, "ALPHA_VANTAGE_KEY", "AVKEY", raising=False)
    assert not hasattr(adapters, "fetch_alphavantage") and not hasattr(data_fetcher, "_fetch_alphavantage")
    alpaca.responder = lambda url, p: FakeResp({"bars": []})
    yf_fake.frames["GC=F"] = yf_frame("CMES", "2024-01-02", "2024-06-04")
    df = data_fetcher.fetch_ohlcv("GC=F", 150, end_date=datetime(2024, 6, 4), sources=["alphavantage"])
    assert df.empty
    assert not any("alphavantage" in u for u, _ in alpaca.calls)
    b = adapters.fetch_bars("GC=F", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert alpaca.calls == []  # futures never routed to Alpaca (equity endpoint or otherwise)
    assert b.source == "yfinance" and b.calendar == "CMES"


# ------------------------------------------------------------------ Alpaca adapter

def test_alpaca_params_feed_adjustment_end_clamp_and_session_dates(alpaca, monkeypatch):
    monkeypatch.setattr(config, "ALPACA_FEED", "iex")
    idx = sessions("XNYS", "2024-01-02", "2024-06-04")
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(idx), "next_page_token": None})
    now = datetime(2024, 6, 4, 21, 0, tzinfo=timezone.utc)
    b = adapters.fetch_bars("BRK-B", 150, end_date=now, now=now)
    url, p = alpaca.calls[0]
    assert url.endswith("/v2/stocks/BRK.B/bars")
    assert p["adjustment"] == "all" and p["feed"] == "iex" and p["timeframe"] == "1Day"
    assert p["end"] == "2024-06-04T20:44:00Z"  # now - 16 min
    assert b.source == "alpaca" and b.df.index[-1] == pd.Timestamp("2024-06-04")


def test_alpaca_pagination_and_crypto_endpoint(alpaca):
    idx = pd.date_range("2024-01-01", "2024-06-03", freq="D")
    half = len(idx) // 2
    pages = [{"bars": {"BTC/USD": alpaca_bars(idx[:half], "T00:00:00Z")}, "next_page_token": "p2"},
             {"bars": {"BTC/USD": alpaca_bars(idx[half:], "T00:00:00Z")}, "next_page_token": None}]
    alpaca.responder = lambda url, p: FakeResp(pages[1 if "page_token" in p else 0])
    b = adapters.fetch_bars("BTC-USD", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert "/v1beta3/crypto/us/bars" in alpaca.calls[0][0] and alpaca.calls[0][1]["symbols"] == "BTC/USD"
    assert "feed" not in alpaca.calls[0][1]
    assert len(b.df) == len(idx[idx >= pd.Timestamp('2024-01-06')]) and b.calendar == "24/7"
    assert (b.df.index.dayofweek >= 5).any()  # weekends kept


def test_alpaca_rejection_logged_at_warning_and_falls_back(alpaca, yf_fake, caplog):
    alpaca.responder = lambda url, p: FakeResp({}, status=403, text="feed not permitted")
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    with caplog.at_level(logging.WARNING):
        b = adapters.fetch_bars("AAPL", 150, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert b.source == "yfinance"
    assert any(r.levelno == logging.WARNING and "403" in r.getMessage() and "feed not permitted" in r.getMessage()
               for r in caplog.records)
    assert "APCA" not in caplog.text


# ------------------------------------------------------------------ pinning

def test_source_pinned_per_symbol_and_not_switched(alpaca, yf_fake):
    idx = sessions("XNYS", "2024-01-02", "2024-06-04")
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(idx)})
    yf_fake.frames["AAPL"] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    kw = dict(end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert adapters.fetch_bars("AAPL", 150, **kw).source == "alpaca"
    assert adapters.pinned_source("AAPL") == "alpaca"
    alpaca.responder = lambda url, p: FakeResp({"bars": []})  # alpaca now fails; yfinance has data
    with pytest.raises(DataUnavailableError):  # no silent vendor switch mid-process
        adapters.fetch_bars("AAPL", 150, **kw)
    assert yf_fake.calls == []
    adapters.reset_pins("AAPL")
    assert adapters.fetch_bars("AAPL", 150, **kw).source == "yfinance"


def test_research_window_is_pinned_separately_from_the_short_window(alpaca, yf_fake):
    """Alpaca (short IEX-like history) covers 730 days but not the 8-year research window: the research fetch
    must fall through to yfinance instead of inheriting the short window's Alpaca pin and failing coverage."""
    short_idx = sessions("XNYS", "2022-06-06", "2024-06-04")
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(short_idx)})
    long_start = (pd.Timestamp(NOW_AFTER_CLOSE).tz_localize(None) - pd.Timedelta(days=config.RESEARCH_LOOKBACK_DAYS))
    yf_fake.frames["AAPL"] = yf_frame("XNYS", long_start.strftime("%Y-%m-%d"), "2024-06-04")
    kw = dict(end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    short = adapters.fetch_bars("AAPL", 730, **kw)
    assert short.source == "alpaca" and adapters.pinned_source("AAPL") == "alpaca"
    research = adapters.fetch_bars("AAPL", config.RESEARCH_LOOKBACK_DAYS, **kw)
    assert research.source == "yfinance" and len(research.df) > 1500
    assert adapters.pinned_source("AAPL", config.RESEARCH_LOOKBACK_DAYS) == "yfinance"
    assert adapters.pinned_source("AAPL") == "alpaca"                       # the short pin is untouched
    assert adapters.fetch_bars("AAPL", 730, **kw).source == "alpaca"        # and neither pin is switched later


# ------------------------------------------------------------------ pairs

def test_fetch_pair_same_source_both_legs(alpaca, yf_fake, monkeypatch):
    idx = sessions("XNYS", "2023-04-01", "2024-06-04")
    # Alpaca has A only; B is missing there -> BOTH legs must come from yfinance
    alpaca.responder = lambda url, p: FakeResp({"bars": alpaca_bars(idx)}) if "/AAA/" in url else FakeResp({"bars": []})
    for s in ("AAA", "BBB"):
        yf_fake.frames[s] = yf_frame("XNYS", "2023-04-01", "2024-06-04")
    ba, bb = adapters.fetch_pair_bars("AAA", "BBB", 400, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert ba.source == bb.source == "yfinance"
    assert adapters.pinned_source("AAA") == adapters.pinned_source("BBB") == "yfinance"
    assert ba.df.index.equals(bb.df.index)


def test_fetch_pair_crypto_keeps_weekends(yf_fake):
    for s in ("BTC-USD", "ETH-USD"):
        f = frame(pd.date_range("2023-04-01", "2024-06-03", freq="D"))
        f.index = f.index.tz_localize("UTC")
        yf_fake.frames[s] = f
    ba, bb = adapters.fetch_pair_bars("BTC-USD", "ETH-USD", 400, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert (ba.df.index.dayofweek >= 5).sum() > 50 and ba.df.index.equals(bb.df.index)


def test_fetch_pair_mixed_calendar_aligns_on_stricter_with_warning(yf_fake, caplog):
    yf_fake.frames["SPY"] = yf_frame("XNYS", "2023-04-01", "2024-06-04")
    f = frame(pd.date_range("2023-04-01", "2024-06-03", freq="D"))
    f.index = f.index.tz_localize("UTC")
    yf_fake.frames["BTC-USD"] = f
    with caplog.at_level(logging.WARNING):
        ba, bb = adapters.fetch_pair_bars("SPY", "BTC-USD", 400, end_date=datetime(2024, 6, 4), now=NOW_AFTER_CLOSE)
    assert (ba.df.index.dayofweek < 5).all() and ba.df.index.equals(bb.df.index)
    assert any("mixes calendars" in r.getMessage() for r in caplog.records)


def test_shim_fetch_pair_signature_and_none_on_failure(yf_fake, monkeypatch):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW_AFTER_CLOSE

    monkeypatch.setattr(data_fetcher, "datetime", Frozen)
    assert data_fetcher.fetch_pair("AAA", "BBB") is None
    for s in ("AAA", "BBB"):
        yf_fake.frames[s] = yf_frame("XNYS", "2024-01-02", "2024-06-04")
    adapters.reset_pins()
    out = data_fetcher.fetch_pair("AAA", "BBB", period_days=150)
    assert out is not None and out[0].index.equals(out[1].index)
