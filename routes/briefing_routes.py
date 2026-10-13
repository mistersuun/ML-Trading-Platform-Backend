"""Claude nightly briefing (mount at /api/briefing). Advisory text only: no order is ever placed."""
from fastapi import APIRouter

from api.concurrency import HEAVY, HEAVY_RESPONSES, heavy_endpoint
from api.errors import ERROR_RESPONSES, reply
from services import briefing
from services import models as M

router = APIRouter()


@router.get("", response_model=M.BriefingResponse, responses=ERROR_RESPONSES)
def get_briefing():
    """The latest stored briefing (headline, observations, risks, what changed), its model, token usage and cost,
    and today's / this month's LLM spend against the caps. status is `none` until one exists, `skipped` (budget,
    no_api_key, disabled) or `error` when the last attempt did not produce text. Advisory only."""
    return reply(M.BriefingResponse, briefing.latest())


@router.post("/regenerate", response_model=M.BriefingResponse, responses=HEAVY_RESPONSES)
@heavy_endpoint(semaphore=HEAVY)
def regenerate_briefing():
    """Re-run the briefing from the latest stored scan results. Heavy (shares the cap: 429 when busy) and subject
    to the same budget: over budget it returns status `skipped`, reason `budget`, and calls nothing."""
    return reply(M.BriefingResponse, briefing.regenerate())
