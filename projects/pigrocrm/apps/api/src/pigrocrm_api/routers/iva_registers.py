"""The IVA periods of a legal entity's year and their registers (REB-1024, suppliers design § 6),
read only.

The periods carry the settlement's ladder (stored, or the running figures of an open period) and
the registers list the documents behind it; nothing here computes, and `admin` is enforced by the
service: the figures are the fiscal position, and a router containing an authorisation `if` is a
router the MCP adapter cannot reuse. Closing a period is not here: the IVA period lock has no
table yet.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from pigrocrm.core.fiscal.iva_register import IvaRegisterService, iva_entities
from pigrocrm.core.fiscal.schemas import (
    IvaEntityRead,
    IvaPeriodRead,
    IvaPurchaseRegisterRead,
    IvaSalesRegisterRead,
)
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(
    prefix="/api",
    tags=["iva-registers"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("purchases")],
)

YearQuery = Annotated[int, Query(ge=2000, le=2200)]
NumberQuery = Annotated[
    int, Query(ge=1, le=12, description="The month or the quarter, by the entity's frequency")
]
LegalEntityQuery = Annotated[
    UUID | None,
    Query(
        description=(
            "The legal entity to read: required when the space has more than one (a 422 "
            "response names `legal_entity_id`), otherwise it is the only one."
        )
    ),
]
LimitQuery = Annotated[int, Query(ge=1, le=200)]
OffsetQuery = Annotated[int, Query(ge=0)]


@router.get("/iva-periods/entities", response_model=list[IvaEntityRead])
def list_iva_entities(session: SessionDep, actor: ActorDep) -> list[IvaEntityRead]:
    """The legal entities whose pack settles IVA by period: the ones that have registers."""
    return iva_entities(session, actor)


@router.get("/iva-periods", response_model=list[IvaPeriodRead])
def list_iva_periods(
    session: SessionDep,
    actor: ActorDep,
    year: YearQuery,
    legal_entity_id: LegalEntityQuery = None,
) -> list[IvaPeriodRead]:
    """Every period of `year` with its ladder and state; empty when the entity's pack settles no
    IVA by period in that year."""
    return IvaRegisterService(session).list_periods(year, actor, legal_entity_id=legal_entity_id)


@router.get("/iva-registers/sales", response_model=IvaSalesRegisterRead)
def read_sales_register(
    session: SessionDep,
    actor: ActorDep,
    year: YearQuery,
    number: NumberQuery,
    legal_entity_id: LegalEntityQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> IvaSalesRegisterRead:
    """One period's sales register, by number, with the summary by rate and the period's totals."""
    return IvaRegisterService(session).read_sales_register(
        year, number, actor, legal_entity_id=legal_entity_id, limit=limit, offset=offset
    )


@router.get("/iva-registers/purchases", response_model=IvaPurchaseRegisterRead)
def read_purchase_register(
    session: SessionDep,
    actor: ActorDep,
    year: YearQuery,
    number: NumberQuery,
    legal_entity_id: LegalEntityQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> IvaPurchaseRegisterRead:
    """One period's purchase register at each group's deductible share, with the summary by
    rate and the period's totals."""
    return IvaRegisterService(session).read_purchase_register(
        year, number, actor, legal_entity_id=legal_entity_id, limit=limit, offset=offset
    )
