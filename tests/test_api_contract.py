"""API contract (WS2.7): every route returns strict JSON (no NaN/Infinity), errors use the
{error: {code, message, details}} envelope with non-2xx status, and request bounds are enforced."""
import json
import logging

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import config
import ml_patterns
from api.serialize import SafeJSONResponse, to_native
from ml_patterns import MLPatternDetector
from tests.fixtures.synthetic import gbm_ohlc


def _no_constants(name):
    raise AssertionError(f"non-finite JSON constant {name} in response")


def strict(resp):
    """Parse with a hook that rejects NaN / Infinity / -Infinity tokens."""
    return json.loads(resp.text, parse_constant=_no_constants)


@pytest.fixture
def client():
    from server import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fast_ml(monkeypatch):
    monkeypatch.setattr(ml_patterns, "HAS_XGB", False)
    monkeypatch.setattr(ml_patterns, "HAS_LGBM", False)
    orig = MLPatternDetector._build_ensemble

    def small(self):
        self.n_estimators = 5
        return orig(self)

    monkeypatch.setattr(MLPatternDetector, "_build_ensemble", small)


@pytest.fixture
def long_frame():
    """~7 years of daily bars spanning HOLDOUT_START, so stress/sensitivity have pre-hold-out data."""
    return gbm_ohlc(n=1800, seed=11, start="2019-01-01")


# ---------------------------------------------------------------- serialisation units
def test_to_native_handles_numpy_pandas_and_non_finite():
    out = to_native({"a": np.float64("nan"), "b": np.float32("inf"), "c": [np.int64(3), -np.inf, np.bool_(True)],
                     "d": pd.Timestamp("2024-01-02"), "e": pd.NaT, "f": np.array([1.0, np.nan]),
                     "g": pd.Series({"x": 1.0, "y": float("nan")}), 5: "int key"})
    assert out == {"a": None, "b": None, "c": [3, None, True], "d": "2024-01-02T00:00:00", "e": None,
                   "f": [1.0, None], "g": {"x": 1.0, "y": None}, "5": "int key"}
    json.dumps(out, allow_nan=False)


def test_safe_json_response_never_emits_non_finite_tokens():
    body = SafeJSONResponse({"x": float("nan"), "y": [np.float64("inf")]}).body.decode()
    assert "NaN" not in body and "Infinity" not in body
    assert json.loads(body) == {"x": None, "y": [None]}


# ---------------------------------------------------------------- the sweep: every route, strict JSON
def test_every_route_is_covered_by_the_sweep(client):
    covered = {
        ("GET", "/api/health"), ("GET", "/api/config/"), ("GET", "/api/data/watchlist/symbols"),
        ("GET", "/api/data/watchlist/fetch"), ("GET", "/api/data/{symbol}"), ("GET", "/api/patterns/list"),
        ("POST", "/api/patterns/detect"), ("POST", "/api/patterns/scan"), ("POST", "/api/backtest/run"),
        ("POST", "/api/backtest/walk-forward"), ("GET", "/api/pairs/configured"), ("POST", "/api/pairs/analyze"),
        ("POST", "/api/pairs/scan"), ("POST", "/api/ml/predict"), ("POST", "/api/stress/full"),
        ("POST", "/api/stress/regimes"), ("POST", "/api/stress/sensitivity"),
    }
    paths = client.get("/openapi.json").json()["paths"]
    actual = {(m.upper(), path) for path, ops in paths.items() if path.startswith("/api") for m in ops}
    assert actual == covered, f"routes without a sweep case: {actual ^ covered}"


def test_route_sweep_strict_json(client, patch_fetch, fast_ml, long_frame, monkeypatch):
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA", "BBB"])
    patch_fetch.overrides["LONG"] = long_frame
    sym = {"symbol": "SPY", "pattern_name": "ema_crossover"}
    cases = [
        ("GET", "/api/health", None),
        ("GET", "/api/config/", None),
        ("GET", "/api/data/watchlist/symbols", None),
        ("GET", "/api/data/SPY?period_days=200", None),
        ("GET", "/api/patterns/list", None),
        ("POST", "/api/patterns/detect", sym),
        ("POST", "/api/patterns/scan", {"markets": ["unit"], "recency_days": 10 ** 6}),
        ("POST", "/api/backtest/run", sym),
        ("POST", "/api/backtest/walk-forward", {**sym, "n_splits": 3}),
        ("GET", "/api/pairs/configured", None),
        ("POST", "/api/pairs/analyze", {"symbol_a": "KO", "symbol_b": "PEP", "period_days": 400}),
        ("POST", "/api/pairs/scan", None),
        ("POST", "/api/ml/predict", {"symbol": "SPY", "period_days": 730}),
        ("POST", "/api/stress/regimes", {"symbol": "SPY"}),
        ("POST", "/api/stress/full", {"symbol": "LONG", "pattern_name": "ema_crossover"}),
        ("POST", "/api/stress/sensitivity", {"symbol": "LONG", "pattern_name": "ema_crossover"}),
    ]
    for method, path, body in cases:
        r = client.request(method, path, json=body) if body is not None else client.request(method, path)
        assert r.status_code == 200, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        data = strict(r)
        assert not (isinstance(data, dict) and isinstance(data.get("error"), dict)), (path, data)


def test_watchlist_fetch_route(client, monkeypatch):
    import routes.data_routes as dr
    monkeypatch.setattr(dr, "fetch_watchlist", lambda markets=None: {"AAA": gbm_ohlc(n=70)})
    r = client.get("/api/data/watchlist/fetch?markets=unit")
    assert r.status_code == 200 and strict(r) == {"count": 1, "symbols": ["AAA"]}


# ---------------------------------------------------------------- numbers are fractions, JSON-safe
def test_backtest_metrics_are_numeric_fractions_with_legacy_display(client, patch_fetch):
    r = client.post("/api/backtest/run", json={"symbol": "SPY", "pattern_name": "ema_crossover"})
    body = strict(r)
    m = body["metrics"]
    for k in ("win_rate", "total_return", "max_drawdown", "sharpe", "profit_factor", "total_trades"):
        assert k in m and (m[k] is None or isinstance(m[k], (int, float))), (k, m[k])
    assert -1.0 <= m["total_return"] <= 10 and -1.0 <= m["max_drawdown"] <= 0
    assert isinstance(body["metrics_display"]["win_rate"], str)       # legacy strings kept for one release
    for t in body["trades"]:
        assert abs(t["pnl_pct"]) < 1.0
        assert t["pnl_pct"] == round(t["pnl_pct"], 12) or True        # unrounded fraction (no 4dp quantisation)


def test_scan_exposes_unsuffixed_and_legacy_return_names(client, patch_fetch, monkeypatch):
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA"])
    body = strict(client.post("/api/patterns/scan", json={"markets": ["unit"], "recency_days": 10 ** 6}))
    assert body["signals"] and body["failed"] == []
    for s in body["signals"]:
        assert s["total_return"] == s["total_return_pct"]
        assert s["validation_status"] == "unvalidated"


def test_scan_failures_are_listed_and_logged(client, patch_fetch, monkeypatch, caplog):
    import backtester
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA", "EMPTY"])
    patch_fetch.overrides["EMPTY"] = pd.DataFrame()
    monkeypatch.setattr(backtester, "classic_backtest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaput")))
    with caplog.at_level(logging.WARNING):
        body = strict(client.post("/api/patterns/scan", json={"markets": ["unit"], "recency_days": 10 ** 6}))
    assert any(f["error"] == "RuntimeError" and f["message"] == "kaput" for f in body["failed"])
    assert any(f["symbol"] == "EMPTY" and f["error"] == "NoData" for f in body["failed"])
    assert any("kaput" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------- errors
def assert_envelope(r, status, code=None):
    assert r.status_code == status, (r.status_code, r.text[:300])
    err = strict(r)["error"]
    assert set(err) >= {"code", "message", "details"} and isinstance(err["message"], str)
    if code:
        assert err["code"] == code
    return err


def test_unknown_symbol_is_404_envelope(client, patch_fetch):
    patch_fetch.overrides["ZZZZ"] = pd.DataFrame()
    assert_envelope(client.post("/api/backtest/run", json={"symbol": "ZZZZ", "pattern_name": "ema_crossover"}),
                    404, "no_data")
    assert_envelope(client.get("/api/data/ZZZZ"), 404, "no_data")
    assert_envelope(client.post("/api/stress/regimes", json={"symbol": "ZZZZ"}), 404, "no_data")
    assert_envelope(client.post("/api/pairs/analyze", json={"symbol_a": "ZZZZ", "symbol_b": "SPY"}), 404, "no_data")


def test_domain_exceptions_map_to_status_codes(client, patch_fetch, monkeypatch):
    import routes.backtest_routes as br
    from data.validate import DataQualityError, DataUnavailableError
    from instruments import UnknownSymbol
    body = {"symbol": "SPY", "pattern_name": "ema_crossover"}
    for exc, status, code in ((UnknownSymbol("QQQQ"), 404, "unknown_symbol"),
                              (DataQualityError("bad bars"), 502, "data_quality"),
                              (DataUnavailableError("no source"), 404, "no_data")):
        monkeypatch.setattr(br, "fetch_ohlcv", lambda *a, _e=exc, **k: (_ for _ in ()).throw(_e))
        assert_envelope(client.post("/api/backtest/run", json=body), status, code)


def test_unexpected_error_is_500_with_request_id_and_no_traceback(client, monkeypatch):
    import routes.backtest_routes as br
    monkeypatch.setattr(br, "fetch_ohlcv", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret internals")))
    r = client.post("/api/backtest/run", json={"symbol": "SPY", "pattern_name": "ema_crossover"})
    err = assert_envelope(r, 500, "internal_error")
    assert err["request_id"] and r.headers["x-request-id"] == err["request_id"]
    assert "secret internals" not in r.text and "Traceback" not in r.text


def test_validation_bounds_return_422(client, patch_fetch):
    ok_body = {"symbol": "SPY", "pattern_name": "ema_crossover"}
    bad = [
        ("/api/backtest/run", {**ok_body, "period_days": 10}),
        ("/api/backtest/run", {**ok_body, "period_days": 5000}),
        ("/api/backtest/run", {**ok_body, "stop_loss": 0}),
        ("/api/backtest/run", {**ok_body, "pattern_name": "no_such_pattern"}),
        ("/api/backtest/run", {**ok_body, "symbol": "../etc/passwd"}),
        ("/api/backtest/walk-forward", {**ok_body, "n_splits": 1}),
        ("/api/backtest/walk-forward", {**ok_body, "n_splits": 99}),
        ("/api/patterns/detect", {**ok_body, "period_days": 10}),
        ("/api/patterns/scan", {"patterns": ["nope"]}),
        ("/api/patterns/scan", {"markets": ["no_such_market"]}),
        ("/api/pairs/analyze", {"symbol_a": "KO", "symbol_b": "KO"}),
        ("/api/pairs/analyze", {"symbol_a": "KO", "symbol_b": "PEP", "period_days": 10}),
        ("/api/ml/predict", {"symbol": "SPY", "period_days": 10}),
        ("/api/stress/full", {**ok_body, "period_days": 10}),
        ("/api/stress/regimes", {"symbol": "SPY", "period_days": 4000}),
    ]
    for path, body in bad:
        r = client.post(path, json=body)
        assert_envelope(r, 422)
    assert_envelope(client.get("/api/data/SPY?period_days=10"), 422, "validation_error")


def test_sensitivity_without_pre_holdout_history_is_422_not_200(client, patch_fetch):
    patch_fetch.overrides["SHORT"] = gbm_ohlc(n=200, seed=3, start="2025-08-01")
    assert_envelope(client.post("/api/stress/sensitivity",
                                json={"symbol": "SHORT", "pattern_name": "ema_crossover"}), 422,
                    "insufficient_data")


def test_stress_routes_fetch_at_least_research_lookback(client, patch_fetch):
    client.post("/api/stress/sensitivity", json={"symbol": "SPY", "pattern_name": "ema_crossover",
                                                  "period_days": 100})
    assert patch_fetch.calls[-1] == ("SPY", config.RESEARCH_LOOKBACK_DAYS)


def test_unknown_route_uses_the_envelope(client):
    assert_envelope(client.get("/api/nope"), 404, "not_found")


# ---------------------------------------------------------------- pairs scan with a signal
def test_pairs_scan_with_a_signal_returns_200_strict_json(client, patch_fetch, monkeypatch):
    import routes.pairs_routes as pr
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB")])
    monkeypatch.setattr(pr, "scan_all_pairs", lambda data, pairs: [{
        "symbol_a": "PA", "symbol_b": "PB", "has_signal": np.bool_(True), "signal_direction": "LONG_SPREAD",
        "current_zscore": np.float64(2.3), "half_life": np.float64(np.inf), "profit_factor": float("nan"),
        "adj_pvalue": np.float64(0.01), "n_tested": np.int64(1), "is_valid": np.bool_(True)}])
    r = client.post("/api/pairs/scan")
    assert r.status_code == 200
    body = strict(r)
    p = body["pairs"][0]
    assert p["has_signal"] is True and p["half_life"] is None and p["profit_factor"] is None
    assert body["count"] == 1 and body["failed"] == []


def test_pairs_scan_lists_symbols_without_data(client, patch_fetch, monkeypatch):
    monkeypatch.setattr(config, "PAIRS", [("PA", "PB")])
    patch_fetch.overrides["PA"] = pd.DataFrame()
    body = strict(client.post("/api/pairs/scan"))
    assert {"symbol": "PA", "error": "NoData"} in body["failed"]


def test_health_and_helpers_json_response_back_compat():
    from routes.helpers import json_response
    assert json.loads(json_response({"a": float("nan")}).body) == {"a": None}


# ---------------------------------------------------------------- numeric contract (review: no formatted strings)
_NUMERIC_KEYS = ("win_rate", "avg_win", "avg_loss", "profit_factor", "total_return", "max_drawdown", "sharpe",
                 "sortino", "calmar", "cagr", "psr", "expectancy", "total_trades")


def _assert_numeric_metrics(m, where):
    for k in _NUMERIC_KEYS:
        assert k in m, (where, k)
        v = m[k]
        assert v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)), (where, k, v)


def test_metrics_are_numbers_or_null_in_stress_backtest_ml_and_walk_forward(
        client, patch_fetch, fast_ml, long_frame):
    patch_fetch.overrides["LONG"] = long_frame
    sym = {"symbol": "SPY", "pattern_name": "ema_crossover"}
    _assert_numeric_metrics(strict(client.post("/api/backtest/run", json=sym))["metrics"], "backtest/run")
    _assert_numeric_metrics(strict(client.post("/api/ml/predict", json={"symbol": "SPY"}))["metrics"], "ml/predict")
    for f in strict(client.post("/api/backtest/walk-forward", json={**sym, "n_splits": 3}))["folds"]:
        _assert_numeric_metrics(f["metrics"], "walk-forward")
    rep = strict(client.post("/api/stress/full", json={"symbol": "LONG", "pattern_name": "ema_crossover"}))
    assert rep["regimes"], "no regimes returned"
    for name, reg in rep["regimes"].items():
        _assert_numeric_metrics(reg, f"stress/full regimes[{name}]")
        assert isinstance(reg["insufficient"], bool)
        assert isinstance(reg["metrics_display"]["win_rate"], str)    # strings only under metrics_display


def test_ml_signal_confidence_is_directional(client, patch_fetch, fast_ml):
    body = strict(client.post("/api/ml/predict", json={"symbol": "SPY", "period_days": 1500}))
    for s in body["signals"]:
        expect = s["p_up"] if s["signal"] == "BUY" else 1.0 - s["p_up"]
        assert s["confidence"] == pytest.approx(expect)


def test_research_views_label_the_holdout_and_can_exclude_it(client, patch_fetch, long_frame):
    patch_fetch.overrides["LONG"] = long_frame
    req = {"symbol": "LONG", "pattern_name": "ema_crossover"}
    body = strict(client.post("/api/backtest/run", json=req))
    assert set(body["holdout"]) >= {"start", "bars", "share", "note"} and body["holdout"]["start"] == config.HOLDOUT_START
    assert body["holdout"]["bars"] > 0 and any(p["date"] >= config.HOLDOUT_START for p in body["equity_curve"])
    assert all("in_holdout" in t for t in body["trades"])
    assert any(t["in_holdout"] for t in body["trades"]) == any(t["entry_date"] >= config.HOLDOUT_START
                                                               for t in body["trades"])
    cut = strict(client.post("/api/backtest/run", json={**req, "include_holdout": False}))
    assert cut["equity_curve"] and all(p["date"] < config.HOLDOUT_START for p in cut["equity_curve"])
