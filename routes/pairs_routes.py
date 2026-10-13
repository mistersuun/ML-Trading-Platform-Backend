"""Pairs trading endpoints (WS2.6): thin adapters over services.pairs."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from api.serialize import to_native
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_symbol
from services import models as M
from services import pairs
from services.providers import DataProvider, get_provider

router = APIRouter()


class PairRequest(BaseModel):
    symbol_a: str
    symbol_b: str
    period_days: int = Field(504, ge=PERIOD_MIN, le=PERIOD_MAX)

    _a = field_validator("symbol_a")(check_symbol)
    _b = field_validator("symbol_b")(check_symbol)

    @model_validator(mode="after")
    def _distinct(self):
        if self.symbol_a == self.symbol_b:
            raise ValueError("symbol_a and symbol_b must differ")
        return self


_native = to_native  # backwards-compatible names
_sanitize = to_native


@router.get("/configured", response_model=M.PairsConfiguredResponse, responses=ERROR_RESPONSES)
def get_configured_pairs():
    """Get list of configured pairs."""
    return reply(M.PairsConfiguredResponse, pairs.configured_pairs())


@router.post("/analyze", response_model=M.PairAnalysisResponse, responses=ERROR_RESPONSES)
def analyze(req: PairRequest, provider: DataProvider = Depends(get_provider)):
    """Run full cointegration analysis on a pair."""
    return reply(M.PairAnalysisResponse, pairs.analyze(provider, req.symbol_a, req.symbol_b, req.period_days))


@router.post("/scan", response_model=M.PairsScanResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def scan_pairs(provider: DataProvider = Depends(get_provider)):
    """Scan the default pairs (research-only pairs excluded); BH-corrected across all pairs scanned."""
    return reply(M.PairsScanResponse, pairs.scan(provider))
