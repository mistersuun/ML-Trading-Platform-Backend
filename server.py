"""FastAPI backend for Trading Bot v2.

Every route returns strict JSON (api.serialize.SafeJSONResponse, no NaN/Infinity) and every failure is a
non-2xx response with the envelope {"error": {"code", "message", "details"}} (api.errors).

Run: ``uv run uvicorn server:app --host 127.0.0.1`` (API_BIND_HOST in settings.py documents the default; uvicorn's
own --host decides the bind). Set API_TOKEN to require a bearer token on /api/* (except /api/health).
"""

import logging
import secrets

from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

import config
from api import errors
from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from api.serialize import SafeJSONResponse, ok
from logging_setup import install_redaction, register_secret
from routes.backtest_routes import router as backtest_router
from routes.config_routes import router as config_router
from routes.data_routes import router as data_router
from routes.ml_routes import router as ml_router
from routes.pairs_routes import router as pairs_router
from routes.pattern_routes import router as pattern_router
from routes.results_routes import router as results_router
from routes.stress_routes import router as stress_router
from services.data_status import StoreSymbolStatus, data_status
from services.providers import DataProvider, get_provider
from settings import get_settings, validate_risk_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
install_redaction()
validate_risk_config(config)  # fail startup with a clear message on an unsafe risk configuration
settings = get_settings()
register_secret(settings.token)  # the API token must never appear in a log line

app = FastAPI(
    title="Trading Bot v2 API",
    version="2.0.0",
    default_response_class=SafeJSONResponse,
    # With a token set, the interactive docs and the schema are not served unauthenticated.
    docs_url=None if settings.token else "/docs",
    redoc_url=None if settings.token else "/redoc",
    openapi_url=None if settings.token else "/openapi.json",
)

@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """If API_TOKEN is set, every /api/* route except /api/health needs 'Authorization: Bearer <token>'."""
    token = settings.token
    path = request.url.path
    if (token and request.method != "OPTIONS" and path.startswith("/api/")
            and path.rstrip("/") != "/api/health"):
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(supplied.strip().encode(), token.encode()):
            rid = getattr(request.state, "request_id", None)
            return SafeJSONResponse(
                errors.envelope("unauthorized", "Missing or invalid API token", None, rid),
                status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


# Added after the auth middleware, so CORS headers and the request id also cover 401 responses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
errors.install(app)


class DataStatusResponse(BaseModel):
    symbols: list[StoreSymbolStatus]


# Registered before the data router so it is not captured by GET /api/data/{symbol}.
@app.get("/api/data/status", response_model=DataStatusResponse, responses=HEAVY_RESPONSES, tags=["data"])
@heavy_endpoint(semaphore=HEAVY)
def get_data_status(symbol: str | None = Query(default=None, pattern=r"^[A-Za-z0-9.\-=^]{1,20}$"),
                    provider: DataProvider = Depends(get_provider)):
    """Per-symbol data source, coverage, staleness and quality (may fetch bars, so it shares the heavy-endpoint cap: 429 when busy; default: watchlist + allocation)."""
    rows = data_status(provider, [symbol.upper()] if symbol else None)
    return reply(DataStatusResponse, {"symbols": [r.dump() for r in rows]})


app.include_router(data_router, prefix="/api/data", tags=["data"])
app.include_router(pattern_router, prefix="/api/patterns", tags=["patterns"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["backtest"])
app.include_router(pairs_router, prefix="/api/pairs", tags=["pairs"])
app.include_router(ml_router, prefix="/api/ml", tags=["ml"])
app.include_router(stress_router, prefix="/api/stress", tags=["stress"])
app.include_router(results_router, prefix="/api/results", tags=["results"])
app.include_router(config_router, prefix="/api/config", tags=["config"])


@app.get("/api/health")
def health():
    return ok({"status": "ok"})
