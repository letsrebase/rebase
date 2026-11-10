"""The freelancer's own card on their contract (brief 2026-11-10, «The freelancer's own view»).

One read-only route, in the freelancer's own space, behind the `contracts` module the contract
belongs to. It asks rebase's space for the engagement the door wrote this contract for
(`core/engagements/own.py`) and answers the closed list of fields a freelancer may read.
"""

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from pigrocrm.core.collaborators.schemas import OwnEngagementRead
from pigrocrm.core.engagements.own import OwnEngagementService
from pigrocrm.core.engagements.service import SpaceUnreachable
from pigrocrm_api.deps import ActorDep, SettingsDep, TenantsRegistryDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.tenancy import tenant_slug

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/contracts",
    tags=["contracts"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("contracts")],
)

NOT_REACHABLE = "Lo stato dell'incarico non è raggiungibile in questo momento: riprova più tardi."


@router.get(
    "/{contract_id}/rebase-engagement",
    response_model=OwnEngagementRead,
    responses={
        503: {
            "description": "rebase's space cannot be reached right now: retry later.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"detail": {"type": "string"}},
                        "required": ["detail"],
                    }
                }
            },
        }
    },
)
def own_engagement(
    contract_id: Annotated[UUID, Path(description="The contract the door wrote from the letter")],
    request: Request,
    actor: ActorDep,
    settings: SettingsDep,
    registry: TenantsRegistryDep,
) -> OwnEngagementRead:
    """The engagement with rebase this contract came from, as its freelancer reads it: the
    letter, their fee and dates, the payment rule and each month's report state, read from
    rebase's space. 404 when the contract did not come from an engagement. A person's read, not
    an agent's: the engagement is rebase's data."""
    bind: Any = registry.get_bind()
    if not isinstance(bind, Engine):
        raise TypeError(f"the registry session is bound to {type(bind).__name__}, not an Engine")
    try:
        return OwnEngagementService(bind, settings).for_contract(
            tenant_slug(request), contract_id, actor
        )
    except (SpaceUnreachable, SQLAlchemyError) as exc:
        logger.warning("own engagement: rebase's space not reachable (%s)", type(exc).__name__)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_REACHABLE) from exc
