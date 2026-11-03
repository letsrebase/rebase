from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import PeriodLockCreate, PeriodLockRead
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/period-locks",
    tags=["period-locks"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("", response_model=list[PeriodLockRead])
def list_locks(
    session: SessionDep, anno: Annotated[int | None, Query()] = None
) -> list[PeriodLockRead]:
    return PeriodLockService(session).list_locks(anno=anno)


@router.post("", response_model=PeriodLockRead, status_code=status.HTTP_201_CREATED)
def close_period(data: PeriodLockCreate, session: SessionDep, actor: ActorDep) -> PeriodLockRead:
    return PeriodLockService(session).close_period(data, actor)


@router.delete("/{anno}/{mese}", status_code=status.HTTP_204_NO_CONTENT)
def reopen_period(
    session: SessionDep,
    actor: ActorDep,
    anno: Annotated[int, Path(ge=2000, le=2200)],
    mese: Annotated[int, Path(ge=1, le=12)],
) -> None:
    """A period is not reopened by accident: the service requires `admin` and writes an
    activity. The bounds here are the same ones `PeriodLockCreate` carries, so a
    nonsense path segment is a 422 before the service is reached."""
    PeriodLockService(session).reopen_period(anno, mese, actor)
