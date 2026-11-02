from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.customers.schemas import (
    CustomerCreate,
    CustomerListQuery,
    CustomerPage,
    CustomerRead,
    CustomersFromSuggestions,
    CustomerUpdate,
    PersonFromSuggestion,
)
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.people.schemas import PersonRead
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import (
    AZIENDA_QUERY_DESCRIPTION,
    CustomParams,
    SearchParams,
    SortedParams,
    sort_field,
)
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/customers",
    tags=["customers"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create(data: CustomerCreate, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).create(data, actor)


# The customers a person ticked among the Gmail proposals (REB-223), created with their
# people in one transaction. Its own path rather than a list body on `POST ""`: the
# analytics table counts it as `clienti_importati`, not as one `cliente_creato`.
@router.post(
    "/from-suggestions", response_model=list[CustomerRead], status_code=status.HTTP_201_CREATED
)
def create_from_suggestions(
    data: CustomersFromSuggestions, session: SessionDep, actor: ActorDep
) -> list[CustomerRead]:
    return CustomerService(session).create_from_suggestions(data, actor)


# One address the mailbox found at this customer's domain (`GET
# /api/gmail/customers/{id}/correspondents`), put on the customer as a person (REB-766).
# It reaches no Gmail: the address comes from the body, checked against the customer's
# own domain.
@router.post(
    "/{customer_id}/people-from-mail",
    response_model=PersonRead,
    status_code=status.HTTP_201_CREATED,
)
def add_person_from_mail(
    customer_id: UUID, data: PersonFromSuggestion, session: SessionDep, actor: ActorDep
) -> PersonRead:
    return CustomerService(session).add_person_from_mail(customer_id, data, actor)


class CustomerListParams(SortedParams, SearchParams, CustomParams):
    stato: SafeStr | None = None
    azienda_id: UUID | None = Field(default=None, description=AZIENDA_QUERY_DESCRIPTION)
    sort: SafeStr | None = sort_field("created_at", "updated_at", "ragione_sociale")


@router.get("", response_model=CustomerPage)
def list_customers(
    session: SessionDep, actor: ActorDep, params: Annotated[CustomerListParams, Query()]
) -> CustomerPage:
    return CustomerService(session).list(params.to_query(CustomerListQuery), actor)


@router.get("/{customer_id}", response_model=CustomerRead)
def get(customer_id: UUID, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).get(customer_id, actor)


@router.patch("/{customer_id}", response_model=CustomerRead)
def update(
    customer_id: UUID, data: CustomerUpdate, session: SessionDep, actor: ActorDep
) -> CustomerRead:
    return CustomerService(session).update(customer_id, data, actor)


@router.delete("/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(customer_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Sets deleted_at. Nothing in this slice removes a row."""
    CustomerService(session).soft_delete(customer_id, actor)


@router.post("/{customer_id}/restore", response_model=CustomerRead)
def restore(customer_id: UUID, session: SessionDep, actor: ActorDep) -> CustomerRead:
    return CustomerService(session).restore(customer_id, actor)


router.include_router(timeline_router("customer", id_param="customer_id"))
