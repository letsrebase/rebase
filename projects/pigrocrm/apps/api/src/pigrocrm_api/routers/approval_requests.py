from uuid import UUID

from fastapi import APIRouter, Depends, status

from pigrocrm.core.work_units.approval_request import (
    ApprovalRequestCompose,
    ApprovalRequestMessage,
    ApprovalRequestPrepare,
    ApprovalRequestPrepared,
    ApprovalRequestService,
)
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/contracts/{contract_id}/approval-request",
    tags=["work_units"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("/preview", response_model=ApprovalRequestMessage)
def preview(
    contract_id: UUID,
    data: ApprovalRequestCompose,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> ApprovalRequestMessage:
    """The text the approval request would carry, for the given dates: subject
    and body, written and not saved. A read."""
    return ApprovalRequestService(session, settings).compose(contract_id, data, actor)


@router.post("", response_model=ApprovalRequestPrepared, status_code=status.HTTP_201_CREATED)
def prepare(
    contract_id: UUID,
    data: ApprovalRequestPrepare,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> ApprovalRequestPrepared:
    """Records the requested workdays as `proposed` and saves the message as the
    client's draft, in one transaction. It does not send: sending is `POST
    /api/email-drafts/{id}/send`, which a person presses after reading the draft. A date on which
    the contract already has a workday answers 409. Closed to every agent-type credential."""
    return ApprovalRequestService(session, settings).prepare(contract_id, data, actor)
