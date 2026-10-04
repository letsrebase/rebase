from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from pigrocrm.core.timetracking.schemas import (
    TimeEntryCreate,
    TimeEntryListQuery,
    TimeEntryPage,
    TimeEntryRead,
    TimeEntryUpdate,
    TimerRead,
    TimerStart,
    TimerStop,
    TimerUpdate,
)
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm.core.timetracking.timer import TimerService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CUSTOM_QUERY_DESCRIPTION, parse_custom_filter

router = APIRouter(prefix="/api/time-entries", tags=["time-entries"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=TimeEntryRead, status_code=status.HTTP_201_CREATED)
def create(data: TimeEntryCreate, session: SessionDep, actor: ActorDep) -> TimeEntryRead:
    return TimeEntryService(session).create(data, actor)


@router.get("", response_model=TimeEntryPage)
def list_time_entries(
    session: SessionDep,
    actor: ActorDep,
    deal_id: Annotated[UUID | None, Query()] = None,
    azienda_id: Annotated[
        UUID | None, Query(description="Solo le ore sui deal di questa azienda; omesso, tutte")
    ] = None,
    user_id: Annotated[UUID | None, Query()] = None,
    da: Annotated[date | None, Query(description="Data minima, YYYY-MM-DD")] = None,
    a: Annotated[date | None, Query(description="Data massima, YYYY-MM-DD")] = None,
    fatturabile: Annotated[bool | None, Query()] = None,
    fatturato: Annotated[
        bool | None,
        Query(description="true = già su una riga di fattura; false = da fatturare"),
    ] = None,
    # SafeStr on the parameter itself, not only on a schema field: these are ordinary
    # query parameters, so the guard has to sit here for FastAPI's own validation to
    # reject a NUL byte as a 422 before TimeEntryListQuery is hand-built below --
    # identical to `list_deals` in routers/deals.py.
    custom: Annotated[list[SafeStr] | None, Query(description=CUSTOM_QUERY_DESCRIPTION)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> TimeEntryPage:
    query = TimeEntryListQuery(
        deal_id=deal_id,
        azienda_id=azienda_id,
        user_id=user_id,
        da=da,
        a=a,
        fatturabile=fatturabile,
        fatturato=fatturato,
        custom=parse_custom_filter(custom),
        limit=limit,
        cursor=cursor,
    )
    return TimeEntryService(session).list(query, actor)


# The timer routes sit above `/{entry_id}` on purpose: FastAPI matches in declaration
# order, and `entry_id` is typed `UUID`, so a `/timer` declared after it would be tried
# as an id first and answer 422 before this handler was ever reached.
@router.get("/timer", response_model=TimerRead | None)
def running_timer(session: SessionDep, actor: ActorDep) -> TimerRead | None:
    """The caller's own running timer, or `null`. A 200 with `null` rather than a 404:
    "no clock running" is the ordinary state of the page that asks, not an error."""
    return TimerService(session).current(actor)


@router.post("/timer/start", response_model=TimerRead, status_code=status.HTTP_201_CREATED)
def start_timer(data: TimerStart, session: SessionDep, actor: ActorDep) -> TimerRead:
    return TimerService(session).start(data, actor)


@router.patch("/timer", response_model=TimerRead)
def update_timer(data: TimerUpdate, session: SessionDep, actor: ActorDep) -> TimerRead:
    return TimerService(session).update(data, actor)


@router.post("/timer/stop", response_model=TimeEntryRead, status_code=status.HTTP_201_CREATED)
def stop_timer(data: TimerStop, session: SessionDep, actor: ActorDep) -> TimeEntryRead:
    """Stops the clock and answers with the entry it became -- the row now in the
    register, with its frozen rate, exactly as `POST /api/time-entries` would return it."""
    return TimerService(session).stop(data, actor)


@router.delete("/timer", status_code=status.HTTP_204_NO_CONTENT)
def discard_timer(session: SessionDep, actor: ActorDep) -> None:
    TimerService(session).discard(actor)


@router.get("/{entry_id}", response_model=TimeEntryRead)
def get(entry_id: UUID, session: SessionDep, actor: ActorDep) -> TimeEntryRead:
    return TimeEntryService(session).get(entry_id, actor)


@router.patch("/{entry_id}", response_model=TimeEntryRead)
def update(
    entry_id: UUID, data: TimeEntryUpdate, session: SessionDep, actor: ActorDep
) -> TimeEntryRead:
    return TimeEntryService(session).update(entry_id, data, actor)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(entry_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    TimeEntryService(session).soft_delete(entry_id, actor)


@router.post("/{entry_id}/restore", response_model=TimeEntryRead)
def restore(entry_id: UUID, session: SessionDep, actor: ActorDep) -> TimeEntryRead:
    return TimeEntryService(session).restore(entry_id, actor)
