"""The IVA settlements of a legal entity's year (REB-1021, suppliers design § 6), read only.

They are written by the daily `pigrocrm automations` pass and by `IvaSettlementService`; this
route computes nothing. `admin`, enforced by the service: the figures are the fiscal position,
and a router containing an authorisation `if` is a router the MCP adapter cannot reuse.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.fiscal.iva_settlement import IvaSettlementService
from pigrocrm.core.fiscal.schemas import IvaSettlementRead
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api/iva-settlements",
    tags=["iva-settlements"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("purchases")],
)


@router.get("", response_model=list[IvaSettlementRead])
def list_iva_settlements(
    session: SessionDep,
    actor: ActorDep,
    year: Annotated[int, Query(ge=2000, le=2200)],
    legal_entity_id: Annotated[
        UUID | None,
        Query(
            description=(
                "The legal entity to read: required when the space has more than one (a 422 "
                "response names `legal_entity_id`), otherwise it is the only one."
            )
        ),
    ] = None,
) -> list[IvaSettlementRead]:
    """The settlements stored for `year`, in period order, each with the due item it wrote."""
    return IvaSettlementService(session).list_iva_settlements(
        year, actor, legal_entity_id=legal_entity_id
    )
