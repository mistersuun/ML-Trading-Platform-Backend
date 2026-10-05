"""WS3.1 parity: the CLI scan service and POST /api/patterns/scan return identical candidates and metrics."""
import pytest
from fastapi.testclient import TestClient

import config
import validation
from services import providers, scan, session
from services.models import ScanSignal
from tests.test_services import FixtureProvider
from tests.fixtures.synthetic import gbm_ohlc

BIG = 10 ** 6   # recency window wide enough that every pattern's last signal counts, for both paths


@pytest.fixture
def parity_env(monkeypatch):
    from server import app
    monkeypatch.setitem(config.WATCHLIST, "unit", ["AAA", "BBB"])
    prov = FixtureProvider({"AAA": gbm_ohlc(n=500, seed=1), "BBB": gbm_ohlc(n=500, seed=2)})
    app.dependency_overrides[providers.get_provider] = lambda: prov
    # every (symbol, pattern) is OOS-positive, so the CLI gate lets the whole in-sample candidate set through
    monkeypatch.setattr(validation, "evaluate_candidates", lambda frames, patterns, *a, **k: [
        validation.CandidateResult(symbol=s, pattern=p, params={}, n_oos_trades=config.MIN_TRADES_OOS + 5,
                                   oos={"sharpe": 1.0}) for s in frames for p in patterns])
    monkeypatch.setattr(scan, "_holdout_store", lambda: None)
    yield prov, TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(providers.get_provider, None)


def _key(d):
    return (d["symbol"], d["pattern"])


def test_cli_scan_and_api_scan_return_identical_candidates_and_metrics(parity_env, monkeypatch):
    prov, client = parity_env
    api = client.post("/api/patterns/scan", json={"markets": ["unit"], "recency_days": BIG})
    assert api.status_code == 200, api.text
    api_signals = {_key(s): s for s in api.json()["signals"]}
    assert api_signals, "no signals on the fixture data: the parity check would be vacuous"

    data = scan.fetch_research(prov.watchlist(["unit"]), prov)
    cands = {_key(c.model_dump()): c.model_dump() for c in scan.technical_candidates(data, recency_days=BIG)}
    assert set(cands) == set(api_signals)

    for k, c in cands.items():
        shared = {f: c[f] for f in ScanSignal.model_fields}
        assert shared == api_signals[k], k            # identical metrics, status, dates, prices


def test_legacy_summary_path_reports_the_same_candidate_set(parity_env, monkeypatch):
    prov, client = parity_env
    monkeypatch.setattr(session, "send_alert", lambda *a, **k: True)
    api = client.post("/api/patterns/scan", json={"markets": ["unit"], "recency_days": BIG}).json()["signals"]
    data = scan.fetch_research(prov.watchlist(["unit"]), prov)
    summaries = scan.scan_technical(data, recency_days=BIG)
    assert sorted((s["symbol"], s["pattern"]) for s in summaries) == sorted(_key(s) for s in api)


def test_the_gate_removes_candidates_the_api_still_lists(parity_env, monkeypatch):
    """The ONLY difference between the two paths is the validation gate (API shows every signal)."""
    prov, client = parity_env
    monkeypatch.setattr(validation, "evaluate_candidates", lambda *a, **k: [])
    data = scan.fetch_research(prov.watchlist(["unit"]), prov)
    assert scan.technical_candidates(data, recency_days=BIG) == []
    assert client.post("/api/patterns/scan", json={"markets": ["unit"], "recency_days": BIG}).json()["count"] > 0
