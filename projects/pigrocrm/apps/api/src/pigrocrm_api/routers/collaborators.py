"""Engagements with freelancers and their availability (collaborators design § 1 and § 3).

Every route is an administrator's, behind the `collaborators` module. Moving an engagement
through its states is one route: the service asks for the action the target state needs, so
cancelling and closing are what a full-access token may do and activating is a person's act.
"""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.schemas import (
    AVAILABILITY_MAX_WEEKS,
    EngagementCreate,
    EngagementListQuery,
    EngagementPage,
    EngagementRead,
    EngagementStateChange,
    EngagementUpdate,
    FrameworkAgreementRequest,
    FreelancerAvailability,
    FreelancerTaxData,
    LetterPreview,
    LetterRead,
    LetterRequest,
)
from pigrocrm.core.collaborators.service import EngagementService, FreelancerService
from pigrocrm.core.organizations.schemas import OrganizationRead
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import (
    ActorDep,
    SessionDep,
    SettingsDep,
    StorageDep,
    get_actor,
    module_guard,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams

router = APIRouter(
    prefix="/api",
    tags=["collaborators"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("collaborators")],
)


class EngagementListParams(KeysetParams):
    freelancer_id: UUID | None = Field(default=None, description="Only this freelancer")
    state: SafeStr | None = Field(
        default=None,
        max_length=20,
        description="draft | awaiting_signature | active | concluded | cancelled",
    )
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None


class AvailabilityParams(BaseModel):
    freelancer_id: UUID | None = Field(default=None, description="The freelancer's organization")
    hub_freelancer_id: UUID | None = Field(
        default=None, description="Or the freelancer's id in the hub; one of the two"
    )
    date_from: dt.date | None = Field(
        default=None, description="The week of this day is the first; default this week"
    )
    date_to: dt.date | None = Field(
        default=None,
        description=f"Default twelve weeks on; at most {AVAILABILITY_MAX_WEEKS} weeks in all",
    )


@router.get("/engagements", response_model=EngagementPage)
def list_engagements(
    session: SessionDep, actor: ActorDep, params: Annotated[EngagementListParams, Query()]
) -> EngagementPage:
    """The engagements with freelancers, newest first."""
    return EngagementService(session).list(params.to_query(EngagementListQuery), actor)


@router.post("/engagements", response_model=EngagementRead, status_code=status.HTTP_201_CREATED)
def create_engagement(
    data: EngagementCreate, session: SessionDep, actor: ActorDep
) -> EngagementRead:
    """A draft engagement. An overlap with another live engagement of the freelancer is
    saved with a warning in the answer."""
    return EngagementService(session).create(data, actor)


@router.get("/engagements/{engagement_id}", response_model=EngagementRead)
def get_engagement(engagement_id: UUID, session: SessionDep, actor: ActorDep) -> EngagementRead:
    return EngagementService(session).get(engagement_id, actor)


@router.patch("/engagements/{engagement_id}", response_model=EngagementRead)
def update_engagement(
    engagement_id: UUID, data: EngagementUpdate, session: SessionDep, actor: ActorDep
) -> EngagementRead:
    """A draft changes freely; once sent for signature only `days_per_week` and the client,
    deal and contract change."""
    return EngagementService(session).update(engagement_id, data, actor)


@router.post("/engagements/{engagement_id}/state", response_model=EngagementRead)
def change_engagement_state(
    engagement_id: UUID, data: EngagementStateChange, session: SessionDep, actor: ActorDep
) -> EngagementRead:
    """Moves an engagement along draft, awaiting_signature, active and concluded, or cancels
    it from draft or awaiting_signature. `ended_on` is for concluding."""
    return EngagementService(session).transition(
        engagement_id, data.state, actor, ended_on=data.ended_on
    )


@router.get("/freelancers/availability", response_model=FreelancerAvailability)
def freelancer_availability(
    session: SessionDep, actor: ActorDep, params: Annotated[AvailabilityParams, Query()]
) -> FreelancerAvailability:
    """The days a week a freelancer is committed to, by ISO week, from their active
    engagements; `sconosciuta` where an engagement has no `days_per_week`."""
    return FreelancerService(session).availability(
        actor,
        freelancer_id=params.freelancer_id,
        hub_freelancer_id=params.hub_freelancer_id,
        date_from=params.date_from,
        date_to=params.date_to,
    )


@router.put("/freelancers/{freelancer_id}/tax-data", response_model=OrganizationRead)
def set_freelancer_tax_data(
    freelancer_id: UUID, data: FreelancerTaxData, session: SessionDep, actor: ActorDep
) -> OrganizationRead:
    """The tax data the letters print, and the fiscal kind. Only what is sent changes."""
    return FreelancerService(session).set_tax_data(freelancer_id, data, actor)


@router.post("/engagements/{engagement_id}/letter/preview", response_model=LetterPreview)
def preview_engagement_letter(
    engagement_id: UUID,
    data: LetterRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> LetterPreview:
    """The engagement's letter as it would be written now, from the space's template for its
    regime, with no client price in it. Nothing is stored; `blank` lists the fields the page
    prints as blank lines and `draft` says the text itself is still a draft."""
    return LetterService(session, storage, settings).preview_letter(
        engagement_id, data.variables, actor
    )


@router.post(
    "/engagements/{engagement_id}/letter/preview/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def preview_engagement_letter_pdf(
    engagement_id: UUID,
    data: LetterRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    """The PDF of `.../letter/preview`."""
    preview = LetterService(session, storage, settings).preview_letter(
        engagement_id, data.variables, actor
    )
    return Response(content=preview.pdf, media_type="application/pdf")


@router.post(
    "/engagements/{engagement_id}/letter",
    response_model=LetterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_engagement_letter(
    engagement_id: UUID,
    data: LetterRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> LetterRead:
    """Writes the engagement's letter as a document of the freelancer. Once per engagement, while
    it is a draft, from a final text and the freelancer's complete tax data."""
    return LetterService(session, storage, settings).create_letter(
        engagement_id, data.variables, actor
    )


@router.post(
    "/freelancers/{freelancer_id}/framework-agreement/preview", response_model=LetterPreview
)
def preview_framework_agreement(
    freelancer_id: UUID,
    data: FrameworkAgreementRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> LetterPreview:
    """The freelancer's framework agreement as it would be written now. Nothing is stored."""
    return LetterService(session, storage, settings).preview_framework_agreement(
        freelancer_id, data.letter_type, actor
    )


@router.post(
    "/freelancers/{freelancer_id}/framework-agreement",
    response_model=LetterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_framework_agreement(
    freelancer_id: UUID,
    data: FrameworkAgreementRequest,
    session: SessionDep,
    actor: ActorDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> LetterRead:
    """Writes the freelancer's framework agreement as a document. It hangs on no engagement; a
    freelancer holds one of each regime."""
    return LetterService(session, storage, settings).create_framework_agreement(
        freelancer_id, data.letter_type, actor
    )
