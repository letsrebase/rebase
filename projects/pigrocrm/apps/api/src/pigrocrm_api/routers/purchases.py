from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.purchases.capabilities import read_purchase_capabilities
from pigrocrm.core.purchases.schemas import (
    PurchaseCapabilitiesRead,
    PurchaseCreate,
    PurchaseKind,
    PurchaseListQuery,
    PurchasePage,
    PurchaseRead,
    PurchaseStatus,
    PurchaseUpdate,
)
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor, module_guard
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import CustomParams, KeysetParams
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/purchases",
    tags=["purchases"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("purchases")],
)


@router.post("", response_model=PurchaseRead, status_code=status.HTTP_201_CREATED)
def create(data: PurchaseCreate, session: SessionDep, actor: ActorDep) -> PurchaseRead:
    return PurchaseService(session).create(data, actor)


class PurchaseListParams(KeysetParams, CustomParams):
    deal_id: UUID | None = None
    legal_entity_id: UUID | None = Field(
        default=None,
        description="Only this legal entity's purchases; omitted, all, general ones included",
    )
    only_general: bool = Field(
        default=False, description="Only general expenses, that is, without a deal (§7.4)"
    )
    category_id: UUID | None = None
    supplier_id: UUID | None = None
    kind: PurchaseKind | None = None
    status: PurchaseStatus | None = None
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")


@router.get("", response_model=PurchasePage)
def list_purchases(
    session: SessionDep, actor: ActorDep, params: Annotated[PurchaseListParams, Query()]
) -> PurchasePage:
    return PurchaseService(session).list(params.to_query(PurchaseListQuery), actor)


@router.get("/capabilities", response_model=PurchaseCapabilitiesRead)
def capabilities(
    session: SessionDep,
    legal_entity_id: Annotated[
        UUID | None,
        Query(
            description=(
                "The buyer; omitted, the space's only active legal entity, the one a "
                "supplier's document that names none is registered under"
            )
        ),
    ] = None,
) -> PurchaseCapabilitiesRead:
    """What the buyer's fiscal pack lets a purchase carry, for the form to hide the rest.
    Declared before `/{purchase_id}`, which would take `capabilities` for an id."""
    return read_purchase_capabilities(session, legal_entity_id)


@router.get("/{purchase_id}", response_model=PurchaseRead)
def get(purchase_id: UUID, session: SessionDep, actor: ActorDep) -> PurchaseRead:
    return PurchaseService(session).get(purchase_id, actor)


@router.patch("/{purchase_id}", response_model=PurchaseRead)
def update(
    purchase_id: UUID, data: PurchaseUpdate, session: SessionDep, actor: ActorDep
) -> PurchaseRead:
    return PurchaseService(session).update(purchase_id, data, actor)


@router.post("/{purchase_id}/cancel", response_model=PurchaseRead)
def cancel(purchase_id: UUID, session: SessionDep, actor: ActorDep) -> PurchaseRead:
    return PurchaseService(session).cancel(purchase_id, actor)


@router.delete("/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(purchase_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    PurchaseService(session).soft_delete(purchase_id, actor)


@router.post("/{purchase_id}/restore", response_model=PurchaseRead)
def restore(purchase_id: UUID, session: SessionDep, actor: ActorDep) -> PurchaseRead:
    return PurchaseService(session).restore(purchase_id, actor)


def _purchase_visible(session: Session, purchase_id: UUID, actor: Actor) -> None:
    """The timeline of a purchase the caller cannot read (another legal entity's, an unknown
    id) is a 404 and not an empty list: the history of a row is read with the row's own
    visibility."""
    PurchaseService(session).get(purchase_id, actor)


router.include_router(timeline_router("purchase", id_param="purchase_id", guard=_purchase_visible))
