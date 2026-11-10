from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.due_items.schemas import (
    Direction,
    DueItemDetail,
    DueItemListQuery,
    DueItemPage,
    PayrollTotals,
    PayrollTotalsQuery,
    SettlementCreate,
    SettlementReversal,
    State,
)
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, KeysetParams

# Due items are core, never behind a module: the table and the service are always on because
# an invoice needs them (spec 2026-11-09, due items, § Scope).
router = APIRouter(
    prefix="/api/due-items",
    tags=["due-items"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


class DueItemListParams(KeysetParams):
    state: State | None = Field(
        default=None, description="One state; omitted, the open items (forecast and expected)"
    )
    direction: Direction | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    organization_id: UUID | None = Field(default=None, description="Only this counterparty")
    source_kind: SafeStr | None = Field(
        default=None,
        max_length=30,
        description=(
            "Only this source kind. The fiscal kinds (tax, IVA, withholding, payroll) and "
            "engagement items are an administrator's; without them they are left out of every page"
        ),
    )
    source_id: UUID | None = Field(
        default=None, description="Only the items this record wrote (an engagement, an invoice)"
    )
    due_from: date | None = Field(default=None, description="Due on or after this day")
    due_to: date | None = Field(default=None, description="Due on or before this day")


@router.get("", response_model=DueItemPage)
def list_due_items(
    session: SessionDep, actor: ActorDep, params: Annotated[DueItemListParams, Query()]
) -> DueItemPage:
    """The amounts that fall due, in date order with the undated last."""
    return DueItemService(session).list(params.to_query(DueItemListQuery), actor)


@router.get("/payroll-totals", response_model=PayrollTotals)
def list_payroll_totals(
    session: SessionDep,
    actor: ActorDep,
    legal_entity_id: Annotated[
        UUID | None, Query(description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    ] = None,
    due_from: Annotated[date | None, Query(description="Due on or after this day")] = None,
    due_to: Annotated[date | None, Query(description="Due on or before this day")] = None,
) -> PayrollTotals:
    """The open payroll cash as one «Stipendi» line per due date, with no person and no item:
    what any member reads in place of the payroll items only an administrator sees."""
    query = PayrollTotalsQuery(legal_entity_id=legal_entity_id, due_from=due_from, due_to=due_to)
    return DueItemService(session).list_payroll_totals(query, actor)


@router.get("/{item_id}", response_model=DueItemDetail)
def get_due_item(item_id: UUID, session: SessionDep, actor: ActorDep) -> DueItemDetail:
    """One due item with every settlement it ever had, reversed ones included."""
    return DueItemService(session).get(item_id, actor)


@router.post(
    "/{item_id}/settlements", response_model=DueItemDetail, status_code=status.HTTP_201_CREATED
)
def settle(
    item_id: UUID, data: SettlementCreate, session: SessionDep, actor: ActorDep
) -> DueItemDetail:
    """Records a payment against the item, partial or full. A client's payment is a
    collaborator's; money going out, and every fiscal item, an administrator's."""
    return DueItemService(session).settle(item_id, data, actor)


@router.post("/settlements/{settlement_id}/reverse", response_model=DueItemDetail)
def reverse_settlement(
    settlement_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    data: SettlementReversal | None = None,
) -> DueItemDetail:
    """Undoes a payment: the row stays, stamped, and the item reopens with what it frees.
    The body, with the reason, is optional."""
    return DueItemService(session).reverse_settlement(
        settlement_id, data.reason if data is not None else None, actor
    )
