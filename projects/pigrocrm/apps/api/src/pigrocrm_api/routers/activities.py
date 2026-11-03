from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection
from pigrocrm.core.tasks.schemas import (
    TaskCreate,
    TaskListQuery,
    TaskPage,
    TaskRead,
    TaskStatus,
    TaskToday,
    TaskTodayQuery,
    TaskUpdate,
)
from pigrocrm.core.tasks.service import TaskService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/activities",
    tags=["tasks"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create(data: TaskCreate, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).create(data, actor)


@router.get("", response_model=TaskPage)
def list_tasks(
    session: SessionDep,
    actor: ActorDep,
    status: Annotated[TaskStatus | None, Query()] = None,
    assigned_to: Annotated[UUID | None, Query()] = None,
    include_unassigned: Annotated[
        bool,
        Query(description="Con `assigned_to`: anche le attività senza assegnatario"),
    ] = False,
    customer_id: Annotated[UUID | None, Query()] = None,
    person_id: Annotated[UUID | None, Query()] = None,
    deal_id: Annotated[UUID | None, Query()] = None,
    invoice_id: Annotated[UUID | None, Query()] = None,
    due_within: Annotated[date | None, Query()] = None,
    # Three states and not two: `None` is «both», which is what a caller who does not
    # care has to be able to say. See `TaskListQuery`.
    without_due_date: Annotated[bool | None, Query()] = None,
    archived: Annotated[
        bool, Query(description="Solo le archiviate (per ripristinarle); di default, le vive")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    # `str` and not `UUID` since slice 6: the cursor is opaque and carries the sort
    # value as well as the id.
    cursor: Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)] = None,
    # A plain string and not a `Literal`, like every other list in this API: the
    # whitelist lives in `TASK_SORTS` and an unknown key resolves to the default
    # there, in one place.
    sort: Annotated[SafeStr | None, Query(description="created_at | scadenza")] = None,
    dir: Annotated[SortDirection, Query()] = "asc",
) -> TaskPage:
    query = TaskListQuery(
        status=status,
        assigned_to=assigned_to,
        include_unassigned=include_unassigned,
        customer_id=customer_id,
        person_id=person_id,
        deal_id=deal_id,
        invoice_id=invoice_id,
        due_within=due_within,
        without_due_date=without_due_date,
        archived=archived,
        limit=limit,
        cursor=cursor,
        sort=sort,
        dir=dir,
    )
    return TaskService(session).list(query, actor)


# Declared before `/{task_id}`: «oggi» is not a UUID, and the path parameter would
# otherwise claim it and answer 422.
@router.get("/today", response_model=TaskToday)
def today(
    session: SessionDep,
    actor: ActorDep,
    assigned_to: Annotated[
        UUID | None,
        Query(description="Solo le attività di questa persona; senza, di tutto lo spazio"),
    ] = None,
    include_unassigned: Annotated[
        bool,
        Query(description="Con `assigned_to`: anche le attività senza assegnatario"),
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200, description="Righe per sezione")] = 100,
) -> TaskToday:
    return TaskService(session).today(
        actor,
        TaskTodayQuery(assigned_to=assigned_to, include_unassigned=include_unassigned, limit=limit),
    )


@router.get("/{task_id}", response_model=TaskRead)
def get(task_id: UUID, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).get(task_id, actor)


@router.patch("/{task_id}", response_model=TaskRead)
def update(task_id: UUID, data: TaskUpdate, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).update(task_id, data, actor)


# The three closures, each its own route rather than a `PATCH {"stato": ...}`. Two
# reasons: the state machine is the service's business (a cancelled activity does not
# become completed by writing a column), and «done» has a side effect -- the day it was
# done -- that a caller must not be able to supply.
@router.post("/{task_id}/complete", response_model=TaskRead)
def complete(task_id: UUID, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).complete(task_id, actor)


@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel(task_id: UUID, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).cancel(task_id, actor)


@router.post("/{task_id}/reopen", response_model=TaskRead)
def reopen(task_id: UUID, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).reopen(task_id, actor)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive(task_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Archiving, which is for the typo. The change of plan is `cancel` -- and the two
    being different operations is the whole reason there are three states."""
    TaskService(session).soft_delete(task_id, actor)


@router.post("/{task_id}/restore", response_model=TaskRead)
def restore(task_id: UUID, session: SessionDep, actor: ActorDep) -> TaskRead:
    return TaskService(session).restore(task_id, actor)
