"""Closing and reopening an IVA period (REB-1036, suppliers design § 6).

While a period is closed no purchase and no sales document that changes its registers is written,
over this API and over MCP: the refusal is a 409 whose detail names the period. The period's
closed state is read on `GET /api/iva-periods`. The rule (an unscoped administrator, a trace on the
timeline) is the service's: a router containing an authorisation `if` is a router the MCP adapter
cannot reuse.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from pigrocrm.core.fiscal.iva_lock import IvaPeriodLockService
from pigrocrm.core.fiscal.schemas import IvaPeriodLockCreate, IvaPeriodLockRead
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/iva-period-locks",
    tags=["iva-period-locks"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("purchases")],
)


@router.post("", response_model=IvaPeriodLockRead, status_code=status.HTTP_201_CREATED)
def close_iva_period(
    data: IvaPeriodLockCreate, session: SessionDep, actor: ActorDep
) -> IvaPeriodLockRead:
    """Closes a period that has ended: it is settled first, then locked, in one transaction."""
    return IvaPeriodLockService(session).close_iva_period(data, actor)


@router.delete("/{year}/{number}", status_code=status.HTTP_204_NO_CONTENT)
def reopen_iva_period(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Path(ge=2000, le=2200)],
    number: Annotated[int, Path(ge=1, le=12, description="The month or the quarter")],
    legal_entity_id: Annotated[
        UUID | None,
        Query(
            description=(
                "The legal entity: required when the space has more than one (a 422 response "
                "names `legal_entity_id`), otherwise it is the only one."
            )
        ),
    ] = None,
) -> None:
    """Reopens a closed period and leaves a trace: writes dated in it are allowed again."""
    IvaPeriodLockService(session).reopen_iva_period(
        year, number, actor, legal_entity_id=legal_entity_id
    )
