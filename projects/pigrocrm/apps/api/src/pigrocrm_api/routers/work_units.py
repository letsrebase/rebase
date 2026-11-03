from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from pigrocrm.core.work_units.dispute_bundle import build_dispute_bundle
from pigrocrm.core.work_units.schemas import (
    LIST_LIMIT_DEFAULT,
    WorkUnitCreate,
    WorkUnitDetail,
    WorkUnitList,
    WorkUnitListQuery,
    WorkUnitRead,
    WorkUnitRecord,
    WorkUnitStatus,
    WorkUnitTransitionIn,
)
from pigrocrm.core.work_units.service import WorkUnitService
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import PAGE_LIMIT_MAX

router = APIRouter(
    prefix="/api/work-units",
    tags=["work_units"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=WorkUnitRead, status_code=status.HTTP_201_CREATED)
def create(data: WorkUnitRecord, session: SessionDep, actor: ActorDep) -> WorkUnitRead:
    """Records a workday. A `worked` workday on a contract that requires
    prior approval, without approval, is born `worked_without_approval`: the
    trigger flags it instead of refusing it. Closed to every agent-type credential: an
    agent proposes (`propose_day`), a person confirms. A `proposed` or
    `worked` workday is recorded: `approved` is written by `POST /api/approvals`, with the proof."""
    return WorkUnitService(session).create(WorkUnitCreate(**data.model_dump()), actor)


@router.get("", response_model=WorkUnitList)
def list_work_units(
    session: SessionDep,
    actor: ActorDep,
    contract_id: Annotated[UUID | None, Query()] = None,
    legal_entity_id: Annotated[
        UUID | None, Query(description="Only this legal entity's contracts; omitted, all")
    ] = None,
    date_from: Annotated[
        date | None, Query(alias="from", description="From the day, YYYY-MM-DD, inclusive")
    ] = None,
    date_to: Annotated[
        date | None, Query(alias="to", description="To the day, YYYY-MM-DD, inclusive")
    ] = None,
    status: Annotated[WorkUnitStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=PAGE_LIMIT_MAX)] = LIST_LIMIT_DEFAULT,
) -> WorkUnitList:
    """The workdays, most recent first. At least a contract or a date (`from`, `to`) is required:
    a space's whole register is not a question a screen asks."""
    query = WorkUnitListQuery(
        contract_id=contract_id,
        legal_entity_id=legal_entity_id,
        date_from=date_from,
        date_to=date_to,
        status=status,
        limit=limit,
    )
    return WorkUnitService(session).list(query, actor)


@router.get(
    "/{work_unit_id}/dispute-bundle",
    response_class=Response,
    responses={
        200: {"content": {"application/zip": {}}, "description": "The day's dispute bundle"}
    },
)
def dispute_bundle(
    work_unit_id: UUID, session: SessionDep, actor: ActorDep, storage: StorageDep
) -> Response:
    """The file of a disputed workday (REB-759): a ZIP archive with `summary.txt`
    (the workday, the approval and the excerpt, the history, the month's register, the
    invoice line) and, under `source/`, the archived original the approval rests on, byte
    for byte. Open to every role, like the workday's page."""
    filename, content = build_dispute_bundle(session, storage, work_unit_id, actor)
    return attachment_response(content, "application/zip", filename)


@router.get("/{work_unit_id}", response_model=WorkUnitDetail)
def get(work_unit_id: UUID, session: SessionDep, actor: ActorDep) -> WorkUnitDetail:
    return WorkUnitService(session).detail(work_unit_id, actor)


@router.post("/{work_unit_id}/transitions", response_model=WorkUnitDetail)
def transition(
    work_unit_id: UUID, data: WorkUnitTransitionIn, session: SessionDep, actor: ActorDep
) -> WorkUnitDetail:
    """Moves a workday along its lifecycle, with a move its page would offer
    (`actions` of `GET /api/work-units/{id}`): `approved` is written by recording
    an approval and `invoiced` by the invoice, so here they answer 409 like any transition
    the lifecycle does not allow."""
    service = WorkUnitService(session)
    service.move(work_unit_id, data.status, actor, data.reason)
    return service.detail(work_unit_id, actor)
