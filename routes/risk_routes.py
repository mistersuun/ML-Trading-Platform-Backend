"""Signal-sleeve risk status (mount at /api/risk). Read-only."""
from fastapi import APIRouter

from api.errors import ERROR_RESPONSES, ErrorEnvelope, reply
from services import models as M
from services import riskview

router = APIRouter()


@router.get("/status", response_model=M.RiskStatusResponse,
            responses={**ERROR_RESPONSES, 503: {"model": ErrorEnvelope, "description": "state_not_initialized"}})
def get_risk_status():
    """Sleeve value, peak, drawdown ladder with dollars of room, halt / kill switch / reconcile / mode, limits used
    vs maximum, equity history and the order decisions of the last 30 days. 503 `state_not_initialized` until
    `main.py risk init` has been run."""
    return reply(M.RiskStatusResponse, riskview.status())
