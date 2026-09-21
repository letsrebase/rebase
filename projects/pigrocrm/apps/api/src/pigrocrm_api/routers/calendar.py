from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from pigrocrm.core.calendario.schemas import MESE_PATTERN, CalendarMonth
from pigrocrm.core.calendario.service import CalendarService
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/calendar", tags=["calendario"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=CalendarMonth)
def month(
    session: SessionDep,
    actor: ActorDep,
    mese: Annotated[str, Query(pattern=MESE_PATTERN, description="AAAA-MM")],
    # `tutti=true` is how an admin asks for the space's whole calendar instead of their
    # own. A boolean and not a `user_id`, because «somebody else's hours» is not a
    # question this screen asks: the register at /app/hours already filters by person.
    tutti: Annotated[bool, Query()] = False,
) -> CalendarMonth:
    """One month: the hours by day, the activities falling due, the invoices falling due.

    `mese` carries the pattern here as well as in `month_bounds`, and both are wanted: on
    the route it makes a malformed month a 422 before a session is touched, and in the
    service it makes the same value safe for the agent, which does not come through
    this router.

    The hours default to the caller's own -- `actor.id` -- because «my calendar» is what
    the page means. An `mcp` actor carries the id of the token's owner (slice 1 §9), so
    an agent asking about the month gets that person's days and not the space's.
    """
    user_id: UUID | None = None if tutti else actor.id
    return CalendarService(session).month(mese, actor, user_id=user_id)
