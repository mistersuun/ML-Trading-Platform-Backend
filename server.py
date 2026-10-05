"""FastAPI backend for Trading Bot v2.

Every route returns strict JSON (api.serialize.SafeJSONResponse, no NaN/Infinity) and every failure is a
non-2xx response with the envelope {"error": {"code", "message", "details"}} (api.errors).
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import errors
from api.serialize import SafeJSONResponse, ok
from logging_setup import install_redaction
from routes.backtest_routes import router as backtest_router
from routes.config_routes import router as config_router
from routes.data_routes import router as data_router
from routes.ml_routes import router as ml_router
from routes.pairs_routes import router as pairs_router
from routes.pattern_routes import router as pattern_router
from routes.stress_routes import router as stress_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
install_redaction()

app = FastAPI(
    title="Trading Bot v2 API",
    version="2.0.0",
    default_response_class=SafeJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
errors.install(app)

app.include_router(data_router, prefix="/api/data", tags=["data"])
app.include_router(pattern_router, prefix="/api/patterns", tags=["patterns"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["backtest"])
app.include_router(pairs_router, prefix="/api/pairs", tags=["pairs"])
app.include_router(ml_router, prefix="/api/ml", tags=["ml"])
app.include_router(stress_router, prefix="/api/stress", tags=["stress"])
app.include_router(config_router, prefix="/api/config", tags=["config"])


@app.get("/api/health")
def health():
    return ok({"status": "ok"})
