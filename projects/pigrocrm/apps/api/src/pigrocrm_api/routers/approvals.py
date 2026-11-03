from fastapi import APIRouter, Depends, status

from pigrocrm.core.work_units.schemas import ApprovalRecord, ApprovalRecorded
from pigrocrm.core.work_units.service import ApprovalService
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/approvals",
    tags=["work_units"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=ApprovalRecorded, status_code=status.HTTP_201_CREATED)
def record(
    data: ApprovalRecord, session: SessionDep, actor: ActorDep, storage: StorageDep
) -> ApprovalRecorded:
    """Records a client's approval and the workdays it covers, in one transaction:
    the proof (an already archived document, or an already synced mail message that
    the service archives as a contract document), the approval, and the transition of
    every workday (`proposed` becomes `approved`, `worked_without_approval` goes back to
    `worked`). Immutable: a correction is a new approval. Closed to every
    agent-type credential: an agent proposes a workday (`propose_day`), a person
    records the approval."""
    return ApprovalService(session, storage).record(data, actor)
