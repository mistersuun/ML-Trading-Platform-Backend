"""Read-only nightly results (mount at /api/results): one typed route per kind."""
from datetime import datetime, timezone

from fastapi import APIRouter

from api.errors import ERROR_RESPONSES, ApiError, reply
from results import store
from services import models as M

router = APIRouter()

STALE_AFTER_HOURS = 30


def _latest(kind: str, model: type):
    got = store.read_latest(kind)
    if got is None:
        raise ApiError(f"No stored result for '{kind}'", {"kind": kind}, status_code=404, code="not_found")
    payload, generated_at = got
    age_h = (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600
    return reply(model, {"kind": kind, "payload": payload, "generated_at": generated_at.isoformat(),
                         "age_hours": round(age_h, 2), "stale": age_h > STALE_AFTER_HOURS})


@router.get("/technical/latest", response_model=M.LatestTechnicalResult, responses=ERROR_RESPONSES)
def latest_technical():
    """Latest nightly technical scan (validated / OOS-positive candidates): payload, generated_at, stale flag."""
    return _latest("technical", M.LatestTechnicalResult)


@router.get("/pairs/latest", response_model=M.LatestPairsResult, responses=ERROR_RESPONSES)
def latest_pairs():
    """Latest nightly pairs scan."""
    return _latest("pairs", M.LatestPairsResult)


@router.get("/ml/latest", response_model=M.LatestMLResult, responses=ERROR_RESPONSES)
def latest_ml():
    """Latest nightly ML scan."""
    return _latest("ml", M.LatestMLResult)
