from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field

from pigrocrm.core.organizations.schemas import (
    OrganizationCreate,
    OrganizationListQuery,
    OrganizationPage,
    OrganizationRead,
    OrganizationsFromSuggestions,
    OrganizationUpdate,
    PersonFromSuggestion,
)
from pigrocrm.core.organizations.service import OrganizationService
from pigrocrm.core.people.schemas import PersonRead
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, get_actor
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import (
    LEGAL_ENTITY_QUERY_DESCRIPTION,
    CustomParams,
    SearchParams,
    SortedParams,
    sort_field,
)
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/organizations",
    tags=["organizations"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create(data: OrganizationCreate, session: SessionDep, actor: ActorDep) -> OrganizationRead:
    return OrganizationService(session).create(data, actor)


# The organizations a person ticked among the Gmail proposals (REB-223), created with their
# people in one transaction. Its own path rather than a list body on `POST ""`: the
# analytics table counts it as `customers_imported`, not as one `customer_created`.
@router.post(
    "/from-suggestions", response_model=list[OrganizationRead], status_code=status.HTTP_201_CREATED
)
def create_from_suggestions(
    data: OrganizationsFromSuggestions, session: SessionDep, actor: ActorDep
) -> list[OrganizationRead]:
    return OrganizationService(session).create_from_suggestions(data, actor)


# One address the mailbox found at this organization's domain (`GET
# /api/gmail/organizations/{id}/correspondents`), put on the organization as a person (REB-766).
# It reaches no Gmail: the address comes from the body, checked against the organization's
# own domain.
@router.post(
    "/{organization_id}/people-from-mail",
    response_model=PersonRead,
    status_code=status.HTTP_201_CREATED,
)
def add_person_from_mail(
    organization_id: UUID, data: PersonFromSuggestion, session: SessionDep, actor: ActorDep
) -> PersonRead:
    return OrganizationService(session).add_person_from_mail(organization_id, data, actor)


class OrganizationListParams(SortedParams, SearchParams, CustomParams):
    status: SafeStr | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    sort: SafeStr | None = sort_field("created_at", "updated_at", "legal_name")


@router.get("", response_model=OrganizationPage)
def list_organizations(
    session: SessionDep, actor: ActorDep, params: Annotated[OrganizationListParams, Query()]
) -> OrganizationPage:
    return OrganizationService(session).list(params.to_query(OrganizationListQuery), actor)


@router.get("/{organization_id}", response_model=OrganizationRead)
def get(organization_id: UUID, session: SessionDep, actor: ActorDep) -> OrganizationRead:
    return OrganizationService(session).get(organization_id, actor)


@router.patch("/{organization_id}", response_model=OrganizationRead)
def update(
    organization_id: UUID, data: OrganizationUpdate, session: SessionDep, actor: ActorDep
) -> OrganizationRead:
    return OrganizationService(session).update(organization_id, data, actor)


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(organization_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Sets deleted_at. Nothing in this slice removes a row."""
    OrganizationService(session).soft_delete(organization_id, actor)


@router.post("/{organization_id}/restore", response_model=OrganizationRead)
def restore(organization_id: UUID, session: SessionDep, actor: ActorDep) -> OrganizationRead:
    return OrganizationService(session).restore(organization_id, actor)


router.include_router(timeline_router("organization", id_param="organization_id"))
