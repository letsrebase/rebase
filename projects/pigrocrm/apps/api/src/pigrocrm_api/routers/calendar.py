from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.calendar.schemas import MONTH_PATTERN, CalendarMonth
from pigrocrm.core.calendar.service import CalendarService
from pigrocrm.core.modules import enabled_modules
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/calendar",
    tags=["calendario"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("", response_model=CalendarMonth)
def month(
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    month: Annotated[str, Query(pattern=MONTH_PATTERN, description="AAAA-MM")],
    # `all=true` is how an admin asks for the space's whole calendar instead of their
    # own. A boolean and not a `user_id`, because «somebody else's hours» is not a
    # question this screen asks: the register at /app/hours already filters by person.
    all: Annotated[bool, Query()] = False,
    # The sidebar's legal entity (REB-624): the hours through their deal, the invoices by
    # their own column; the activities have no legal entity and are always the space's.
    legal_entity_id: Annotated[
        UUID | None, Query(description="Only this legal entity's hours and invoices; omitted, all")
    ] = None,
) -> CalendarMonth:
    """One month: the hours by day, the activities falling due, the invoices falling due.

    `month` carries the pattern here as well as in `month_bounds`, and both are wanted: on
    the route it makes a malformed month a 422 before a session is touched, and in the
    service it makes the same value safe for the agent, which does not come through
    this router.

    The hours default to the caller's own -- `actor.id` -- because «my calendar» is what
    the page means. An `mcp` actor carries the id of the token's owner (slice 1 §9), so
    an agent asking about the month gets that person's days and not the space's.
    """
    user_id: UUID | None = None if all else actor.id
    return CalendarService(session).month(
        month,
        actor,
        user_id=user_id,
        legal_entity_id=legal_entity_id,
        modules=enabled_modules(settings),
    )
