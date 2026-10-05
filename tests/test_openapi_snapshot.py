"""The committed openapi.json must match the live app schema; contract violations become 500 envelopes."""
import json

import pytest
from fastapi.testclient import TestClient

from api.errors import ContractViolation, reply
from scripts.export_openapi import OPENAPI_PATH, render
from services import models as M

FRONTEND_PATHS = {
    ("get", "/api/data/{symbol}"), ("get", "/api/data/watchlist/symbols"), ("get", "/api/patterns/list"),
    ("post", "/api/patterns/detect"), ("post", "/api/patterns/scan"), ("post", "/api/backtest/run"),
    ("post", "/api/backtest/walk-forward"), ("get", "/api/pairs/configured"), ("post", "/api/pairs/analyze"),
    ("post", "/api/pairs/scan"), ("post", "/api/ml/predict"), ("post", "/api/stress/full"),
    ("get", "/api/config/"),
}


def test_openapi_snapshot_is_current():
    assert OPENAPI_PATH.exists(), "openapi.json missing: run `uv run python scripts/export_openapi.py`"
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render(), (
        "app.openapi() differs from the committed openapi.json. If the API change is intended, regenerate it "
        "with `uv run python scripts/export_openapi.py` and commit the result (and regenerate the frontend types).")


def test_frontend_endpoints_have_response_models_and_error_envelope():
    spec = json.loads(render())
    for method, path in FRONTEND_PATHS:
        op = spec["paths"][path][method]
        assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"], (method, path)
        assert "$ref" in op["responses"]["500"]["content"]["application/json"]["schema"], (method, path)


def test_unmodelled_endpoints_stay_unmodelled():
    spec = json.loads(render())
    for path in ("/api/data/watchlist/fetch", "/api/stress/regimes", "/api/stress/sensitivity"):
        op = next(iter(spec["paths"][path].values()))
        assert "schema" not in op["responses"]["200"].get("content", {}).get("application/json", {}) or \
            "$ref" not in op["responses"]["200"]["content"]["application/json"]["schema"]


def test_reply_maps_nan_to_null_and_violation_raises():
    r = reply(M.MLSignal, {"date": "d", "signal": "BUY", "price": 1.0, "confidence": float("nan")})
    assert json.loads(r.body)["confidence"] is None
    with pytest.raises(ContractViolation):
        reply(M.MLSignal, {"date": "d", "signal": "BUY", "price": float("nan")})


def test_contract_violation_is_a_500_envelope(monkeypatch):
    from server import app
    from services import scan
    monkeypatch.setattr(scan, "list_patterns", lambda: {"patterns": "nope", "count": 1})
    resp = TestClient(app, raise_server_exceptions=False).get("/api/patterns/list")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "contract_violation"
