"""Service-layer unit tests with an injected fixture provider (no data_fetcher, no network)."""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

import backtester
import config
from api.errors import ApiError, InsufficientHistory, NoData
from data.adapters import Bars, BarsMeta
from data.validate import DataUnavailableError, QualityReport
from services import backtest, data_status, market, models, pairs, scan, session, stress
from services.common import clean
from services.providers import DataProvider, DefaultProvider, default_provider
from tests.fixtures.synthetic import gbm_ohlc, ou_pair


class FixtureProvider:
    """Serves fixed frames; `frames` maps symbol -> DataFrame (missing symbol -> empty frame)."""

    def __init__(self, frames=None, raises=None):
        self.frames = dict(frames or {})
        self.raises = dict(raises or {})
        self.calls = []

    def ohlcv(self, symbol, period_days=None):
        self.calls.append((symbol, period_days))
        if symbol in self.raises:
            raise self.raises[symbol]
        return self.frames.get(symbol, pd.DataFrame()).copy()

    def watchlist(self, markets=None):
        syms = [s for m in (markets or config.WATCHLIST) for s in config.WATCHLIST.get(m, [])]
        return {s: self.frames[s].copy() for s in syms if s in self.frames}

    def pair(self, sym_a, sym_b, period_days=None, min_aligned=60):
        if sym_a in self.frames and sym_b in self.frames:
            return self.frames[sym_a].copy(), self.frames[sym_b].copy()
        return None

    def bars(self, symbol, period_days=None):
        if symbol in self.raises:
            raise self.raises[symbol]
        df = self.frames.get(symbol)
        if df is None or df.empty:
            raise DataUnavailableError(f"no data for {symbol}")
        q = QualityReport(symbol=symbol, n_bars=len(df), first=df.index[0], last=df.index[-1], coverage=0.99,
                          flags=["large_move"])
        return Bars(df, BarsMeta("fixture", True, "XNYS", dt.datetime(2024, 6, 3, tzinfo=dt.timezone.utc), q))


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA", "BBB"])
    return FixtureProvider({"AAA": gbm_ohlc(n=500, seed=1), "BBB": gbm_ohlc(n=500, seed=2),
                            "LONG": gbm_ohlc(n=1800, seed=11, start="2019-01-01")})


# ---------------------------------------------------------------- providers
def test_fixture_and_default_providers_satisfy_the_protocol():
    assert isinstance(FixtureProvider(), DataProvider)
    assert isinstance(default_provider(), DataProvider) and isinstance(default_provider(), DefaultProvider)


def test_default_provider_reads_data_fetcher_at_call_time(monkeypatch):
    import data_fetcher
    seen = []
    monkeypatch.setattr(data_fetcher, "fetch_ohlcv", lambda s, **k: seen.append((s, k)) or pd.DataFrame())
    default_provider().ohlcv("SPY")
    default_provider().ohlcv("SPY", 200)
    assert seen == [("SPY", {}), ("SPY", {"period_days": 200})]


# ---------------------------------------------------------------- models
def test_non_finite_values_become_none_before_validation():
    s = scan.ScanSignal.model_validate(clean({
        "symbol": "A", "pattern": "p", "signal": "BUY", "signal_date": "2024-01-01", "days_ago": 1, "price": 1.0,
        "sharpe": float("nan"), "profit_factor": float("inf"), "win_rate": 0.5}))
    assert s.sharpe is None and s.profit_factor is None and s.win_rate == 0.5
    assert math.isfinite(s.price)


# ---------------------------------------------------------------- market / backtest
def test_market_ohlcv_and_no_data(provider):
    r = market.ohlcv(provider, "AAA", 200)
    assert isinstance(r, models.OhlcvResponse) and r.count == len(r.data) == 500
    with pytest.raises(NoData):
        market.ohlcv(provider, "NOPE", 200)
    assert market.watchlist_fetch(provider, ["unit"]).symbols == ["AAA", "BBB"]


def test_backtest_run_returns_fraction_metrics_and_labels_the_holdout(provider):
    r = backtest.run(provider, "LONG", "ema_crossover", 3000)
    assert isinstance(r, models.BacktestResponse) and r.validation_status == "unvalidated"
    assert r.metrics["total_return"] is None or -1.0 <= r.metrics["total_return"] <= 10
    assert r.holdout.start == config.HOLDOUT_START and r.holdout.bars > 0
    cut = backtest.run(provider, "LONG", "ema_crossover", 3000, include_holdout=False)
    assert cut.holdout.bars == r.holdout.bars                       # the label describes the fetched window
    assert all(not t.in_holdout for t in cut.trades)
    assert provider.calls[0] == ("LONG", 3000)


def test_backtest_walk_forward(provider):
    r = backtest.walk_forward(provider, "AAA", "ema_crossover", n_splits=3, period_days=730)
    assert 1 <= r.n_folds <= 3 and [f.fold for f in r.folds] == list(range(1, r.n_folds + 1))


# ---------------------------------------------------------------- scan (API shape)
def test_scan_patterns_lists_failures_and_sorts_by_recency(provider):
    provider.raises["BBB"] = RuntimeError("boom")
    r = scan.scan_patterns(provider, ["unit"], None, recency_days=10 ** 6)
    assert r.count == len(r.signals) > 0
    assert [s.days_ago for s in r.signals] == sorted(s.days_ago for s in r.signals)
    assert {s.symbol for s in r.signals} == {"AAA"} and all(s.validation_status == "unvalidated" for s in r.signals)
    assert [(f.symbol, f.error, f.message) for f in r.failed] == [("BBB", "RuntimeError", "boom")]


def test_scan_patterns_reports_a_failing_backtest_and_nan_metrics_as_none(provider, monkeypatch):
    class Bt:
        win_rate = float("nan")
        profit_factor = float("inf")
        profit_factor_undefined = False
        sharpe_ratio = float("nan")
        total_return_pct = 0.05
        max_drawdown_pct = float("-inf")
        total_trades = 3
        is_valid = False

    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: Bt())
    r = scan.scan_patterns(provider, ["unit"], ["ema_crossover"], recency_days=10 ** 6)
    assert r.signals and all(s.win_rate is None and s.profit_factor is None and s.max_drawdown is None
                             and s.total_return == s.total_return_pct == 0.05 for s in r.signals)
    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = scan.scan_patterns(provider, ["unit"], ["ema_crossover"], recency_days=10 ** 6)
    assert r.count == 0 and r.failed and all(f.error == "ValueError" for f in r.failed)


def test_scan_unknown_market_is_a_422_api_error(provider):
    with pytest.raises(ApiError) as e:
        scan.scan_patterns(provider, ["nope"])
    assert e.value.status_code == 422


def test_detect_pattern(provider):
    r = scan.detect_pattern(provider, "AAA", "ema_crossover", 500)
    assert r.total_signals == len(r.buys) + len(r.sells)
    with pytest.raises(NoData):
        scan.detect_pattern(provider, "NOPE", "ema_crossover", 500)


def test_in_sample_scan_for_the_dashboard_only_returns_valid_recent_signals(provider):
    out = scan.in_sample_scan({"AAA": provider.frames["AAA"]})
    assert all(s.is_valid for s in out)
    all_out = scan.in_sample_scan({"AAA": provider.frames["AAA"]}, only_valid=False)
    assert len(all_out) >= len(out)


def test_fetch_research_requests_research_length_and_falls_back_to_the_short_frame(provider):
    provider.frames["BBB"] = pd.DataFrame()
    short = {"AAA": gbm_ohlc(n=50, seed=9), "BBB": gbm_ohlc(n=50, seed=8)}
    out = scan.fetch_research(short, provider)
    assert {c for c in provider.calls} == {("AAA", config.RESEARCH_LOOKBACK_DAYS), ("BBB", config.RESEARCH_LOOKBACK_DAYS)}
    assert len(out["AAA"]) == 500 and len(out["BBB"]) == 50


# ---------------------------------------------------------------- pairs
def test_pairs_scan_lists_missing_symbols_and_uses_the_provider(monkeypatch):
    a, b = ou_pair(n=500)
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB"), ("PA", "PC")])
    p = FixtureProvider({"PA": a, "PB": b})
    r = pairs.scan(p)
    assert {"symbol": "PC", "error": "NoData"} in r.failed and r.n_pairs_configured == 2
    assert all(c[1] == pairs.scan_period_days() for c in p.calls)


def test_pairs_analyze_and_configured(monkeypatch):
    a, b = ou_pair(n=500)
    p = FixtureProvider({"PA": a, "PB": b})
    r = pairs.analyze(p, "PA", "PB", 504)
    assert isinstance(r, models.PairAnalysisResponse) and r.spread_data and r.n_obs
    with pytest.raises(NoData):
        pairs.analyze(p, "PA", "ZZ", 504)
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB")])
    assert pairs.configured_pairs().pairs == [{"a": "PA", "b": "PB"}]


# ---------------------------------------------------------------- stress
def test_stress_regimes_and_sensitivity(provider):
    r = stress.regimes(provider, "AAA", 730)
    assert r.symbol == "AAA" and r.regimes
    s = stress.sensitivity(provider, "LONG", "ema_crossover", 100)
    assert provider.calls[-1] == ("LONG", config.RESEARCH_LOOKBACK_DAYS) and s.data
    provider.frames["SHORT"] = gbm_ohlc(n=200, seed=3, start="2025-08-01")
    with pytest.raises(InsufficientHistory):
        stress.sensitivity(provider, "SHORT", "ema_crossover", 100)


# ---------------------------------------------------------------- data status
def test_data_status_reports_what_the_provider_knows(provider):
    provider.raises["BBB"] = DataUnavailableError("down")
    rows = data_status.data_status(provider, ["AAA", "BBB", "ZZZ"])
    by = {r.symbol: r for r in rows}
    assert by["AAA"].status == "ok" and by["AAA"].source == "fixture" and by["AAA"].adjusted is True
    assert by["AAA"].n_bars == 500 and by["AAA"].quality_flags == ["large_move"] and by["AAA"].last_session
    assert by["BBB"].status == "no_data" and by["ZZZ"].status == "no_data"
    names = [r.symbol for r in data_status.data_status(provider)]          # default: watchlist + allocation symbols
    import allocation
    assert set(names) == ({s for v in config.WATCHLIST.values() for s in v} | set(allocation.CORE_TARGETS)
                          | set(allocation.TREND_UNIVERSE) | {allocation.CASH_SYMBOL})


# ---------------------------------------------------------------- session / safety wiring stays in services
def test_open_session_is_alert_only_when_trading_is_off(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "off")
    monkeypatch.setattr(session, "get_broker", lambda: pytest.fail("no broker call when trading is off"))
    assert session.open_session() is None
    assert session.dispatch_intents([]) == []
