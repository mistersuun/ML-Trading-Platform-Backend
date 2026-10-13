"""Pattern detection endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from routes.helpers import PERIOD_MAX, PERIOD_MIN, check_pattern, check_symbol
from services import models as M
from services import scan
from services.providers import DataProvider, get_provider

router = APIRouter()


class PatternRequest(BaseModel):
    symbol: str
    pattern_name: str
    period_days: int = Field(730, ge=PERIOD_MIN, le=PERIOD_MAX)

    _sym = field_validator("symbol")(check_symbol)
    _pat = field_validator("pattern_name")(check_pattern)


@router.get("/list", response_model=M.PatternListResponse, responses=ERROR_RESPONSES)
def list_patterns():
    """List all available patterns."""
    return reply(M.PatternListResponse, scan.list_patterns())


@router.post("/detect", response_model=M.PatternDetectResponse, responses=ERROR_RESPONSES)
def detect_pattern(req: PatternRequest, provider: DataProvider = Depends(get_provider)):
    """Detect a specific pattern on a symbol."""
    return reply(M.PatternDetectResponse, scan.detect_pattern(provider, req.symbol, req.pattern_name, req.period_days))


class ScanRequest(BaseModel):
    markets: Optional[list[str]] = None
    patterns: Optional[list[str]] = None
    recency_days: int = Field(30, ge=1, le=10 ** 7)
    period_days: Optional[int] = Field(None, ge=PERIOD_MIN, le=PERIOD_MAX)  # None = config.LOOKBACK_DAYS

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, v):
        if v is not None:
            for name in v:
                check_pattern(name)
        return v


@router.post("/scan", response_model=M.PatternScanResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def scan_patterns(req: ScanRequest, provider: DataProvider = Depends(get_provider)):
    """Scan the watchlist for recent pattern signals.

    The statistics attached to each signal are IN-SAMPLE display numbers (validation_status 'unvalidated');
    only `python main.py scan` runs the out-of-sample validation. Anything that failed is listed in `failed`."""
    return reply(M.PatternScanResponse,
                 scan.scan_patterns(provider, req.markets, req.patterns, req.recency_days, req.period_days))
