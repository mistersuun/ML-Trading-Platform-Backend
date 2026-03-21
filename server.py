"""FastAPI backend for Trading Bot v2."""

import logging
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import json

from routes.data_routes import router as data_router
from routes.pattern_routes import router as pattern_router
from routes.backtest_routes import router as backtest_router
from routes.pairs_routes import router as pairs_router
from routes.ml_routes import router as ml_router
from routes.stress_routes import router as stress_router
from routes.config_routes import router as config_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.float64, np.float32)):
            if np.isnan(obj) or np.isinf(obj):
                return None
            return float(obj)
        return super().default(obj)


class NumpySafeResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, cls=NumpyEncoder, ensure_ascii=False).encode("utf-8")


app = FastAPI(
    title="Trading Bot v2 API",
    version="2.0.0",
    default_response_class=NumpySafeResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router, prefix="/api/data", tags=["data"])
app.include_router(pattern_router, prefix="/api/patterns", tags=["patterns"])
app.include_router(backtest_router, prefix="/api/backtest", tags=["backtest"])
app.include_router(pairs_router, prefix="/api/pairs", tags=["pairs"])
app.include_router(ml_router, prefix="/api/ml", tags=["ml"])
app.include_router(stress_router, prefix="/api/stress", tags=["stress"])
app.include_router(config_router, prefix="/api/config", tags=["config"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
