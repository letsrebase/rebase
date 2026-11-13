"""Engagements with freelancers and their availability (collaborators design § 1 and § 3).

Every route is an administrator's, behind the `collaborators` module. Moving an engagement
through its states is one route: the service asks for the action the target state needs, so
cancelling and closing are what a full-access token may do and activating is a person's act.
"""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Engine

from pigrocrm.core.collaborators.days_report import DaysReportService
from pigrocrm.core.collaborators.letter_service import LetterService
from pigrocrm.core.collaborators.monthly_reports import MonthlyReportService
from pigrocrm.core.collaborators.payment_read import EngagementPaymentService
from pigrocrm.core.collaborators.schemas import (
    AVAILABILITY_MAX_WEEKS,
    EngagementBucket,
    EngagementCounts,
    EngagementCreate,
    EngagementDaysReport,
    EngagementListQuery,
    EngagementPage,
    EngagementPaymentPage,
    EngagementRead,
    EngagementStateChange,
    EngagementUpdate,
    FrameworkAgreementRequest,
    FreelancerAvailability,
    FreelancerTaxData,
    LetterPreview,
    LetterRead,
    LetterRequest,
    MonthlyReportApproval,
    MonthlyReportPage,
    MonthlyReportRead,
    MonthlyReportRequest,
    SendReport,
    SignatureRead,
    SignedCopyRequest,
)
from pigrocrm.core.collaborators.service import EngagementService, FreelancerService
from pigrocrm.core.documents.schemas import SIZE_MAX
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.organizations.schemas import OrganizationRead
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import (
    ActorDep,
    SessionDep,
    SettingsDep,
    StorageDep,
    TenantsRegistryDep,
    get_actor,
    module_guard,
)
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import KeysetParams
from pigrocrm_api.signing import SigningDep
from pigrocrm_api.tenancy import tenant_slug
from pigrocrm_api.uploads import read_upload_with_fields, single_file_body

router = APIRouter(
    prefix="/api",
    tags=["collaborators"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor), module_guard("collaborators")],
)


class EngagementListParams(KeysetParams):
    freelancer_id: UUID | None = Field(default=None, description="Only this freelancer")
    hub_match_id: UUID | None = Field(
        default=None, description="Only the engagement the hub opened under this id"
    )
    state: SafeStr | None = Field(
        default=None,
        max_length=20,
        description="draft | awaiting_signature | active | concluded | cancelled",
    )
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    bucket: EngagementBucket | None = Field(
        default=None,
        description=(
            "todo (waits for a person) | ongoing (out for signature or active, nothing waiting) "
            "| closed (concluded or cancelled, nothing waiting)"
        ),
    )


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
    excluding_engagement_id: UUID | None = Field(
        default=None, description="Leave this engagement out, as its own page does for a draft"
    )


@router.get("/engagements", response_model=EngagementPage)
def list_engagements(
    session: SessionDep, actor: ActorDep, params: Annotated[EngagementListParams, Query()]
) -> EngagementPage:
    """The engagements with freelancers, newest first."""
    return EngagementService(session).list(params.to_query(EngagementListQuery), actor)


@router.get("/engagements/counts", response_model=EngagementCounts)
def count_engagements(session: SessionDep, actor: ActorDep) -> EngagementCounts:
    """How many engagements wait for a person, are under way and are closed: the three tabs of
    the list. The count on the «Incarichi» entry is `todo`."""
    return EngagementService(session).counts(actor)


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


@router.get("/engagements/{engagement_id}/payments", response_model=EngagementPaymentPage)
def engagement_payments(
    engagement_id: UUID, session: SessionDep, actor: ActorDep
) -> EngagementPaymentPage:
    """What the engagement pays the freelancer, month by month: the due items it wrote, with
    the month each pays for and the client's invoice a date still follows. Read-only: a payment
    is settled on the due item."""
    return EngagementPaymentService(session).for_engagement(engagement_id, actor)


@router.patch("/engagements/{engagement_id}", response_model=EngagementRead)
def update_engagement(
    engagement_id: UUID, data: EngagementUpdate, session: SessionDep, actor: ActorDep
) -> EngagementRead:
    """A draft changes freely; once sent for signature only `days_per_week` and the client,
    deal and contract change."""
    return EngagementService(session).update(engagement_id, data, actor)


@router.post("/engagements/{engagement_id}/state", response_model=EngagementRead)
def change_engagement_state(
    engagement_id: UUID,
    data: EngagementStateChange,
    session: SessionDep,
    actor: ActorDep,
    signing: SigningDep,
) -> EngagementRead:
    """Moves an engagement along draft, awaiting_signature, active and concluded, or cancels
    it from draft or awaiting_signature. `ended_on` is for concluding. Cancelling also cancels
    the letter's envelope on Documenso, so the link the freelancer got stops working."""
    # Only a cancellation reaches an envelope; every other state needs no Documenso.
    service = (
        signing.engagement_service() if data.state == "cancelled" else EngagementService(session)
    )
    return service.transition(engagement_id, data.state, actor, ended_on=data.ended_on)


class ReportParams(BaseModel):
    date_from: dt.date | None = Field(default=None, description="Default: the engagement's start")
    date_to: dt.date | None = Field(
        default=None, description="Default: the engagement's end, or today"
    )


@router.get("/engagements/{engagement_id}/report", response_model=EngagementDaysReport)
def engagement_report(
    engagement_id: UUID,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    registry: TenantsRegistryDep,
    params: Annotated[ReportParams, Query()],
) -> EngagementDaysReport:
    """The days of the engagement, read live from the freelancer's space, with each month's
    «Rendiconto» and what is payable: a day counts only in a month approved by a person or by
    the tacit date passing, and not held. Only from the space that holds the engagements."""
    bind = registry.get_bind()
    return DaysReportService(
        session,
        settings,
        space_slug=tenant_slug(request),
        registry_engine=bind if isinstance(bind, Engine) else None,
    ).report(engagement_id, actor, params.date_from, params.date_to)


@router.get("/engagements/{engagement_id}/monthly-reports", response_model=MonthlyReportPage)
def list_monthly_reports(
    engagement_id: UUID, session: SessionDep, actor: ActorDep, settings: SettingsDep
) -> MonthlyReportPage:
    """The monthly reports recorded for the engagement, oldest month first."""
    return MonthlyReportService(session, settings).for_engagement(engagement_id, actor)


@router.post(
    "/engagements/{engagement_id}/monthly-reports",
    response_model=MonthlyReportRead,
    status_code=status.HTTP_201_CREATED,
)
def request_monthly_report(
    request: Request,
    engagement_id: UUID,
    data: MonthlyReportRequest,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MonthlyReportRead:
    """Records that the freelancer's report of a finished month reached rebase. With no reasoned
    objection within seven working days it counts as approved. A person's act, never an agent's."""
    return MonthlyReportService(session, settings, space_slug=tenant_slug(request)).request(
        engagement_id, data, actor
    )


@router.post(
    "/engagements/{engagement_id}/monthly-reports/{month}/approval",
    response_model=MonthlyReportRead,
)
def approve_monthly_report(
    request: Request,
    engagement_id: UUID,
    month: Annotated[str, Path(description="The month the report covers, 2026-10")],
    data: MonthlyReportApproval,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
) -> MonthlyReportRead:
    """Approves the month: its days read payable, except the `held_dates` the client contested
    (with the reason in `objection`). A person's act, never an agent's, whatever the token."""
    return MonthlyReportService(session, settings, space_slug=tenant_slug(request)).approve(
        engagement_id, month, data, actor
    )


@router.get("/freelancers/availability", response_model=FreelancerAvailability)
def freelancer_availability(
    session: SessionDep, actor: ActorDep, params: Annotated[AvailabilityParams, Query()]
) -> FreelancerAvailability:
    """The days a week a freelancer is committed to, by ISO week, from their live engagements
    (draft, out for signature and active: the ones the overlap warning counts);
    `sconosciuta` where an engagement has no `days_per_week`."""
    return FreelancerService(session).availability(
        actor,
        freelancer_id=params.freelancer_id,
        hub_freelancer_id=params.hub_freelancer_id,
        date_from=params.date_from,
        date_to=params.date_to,
        excluding_engagement_id=params.excluding_engagement_id,
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


# ---- signing (REB-1019): the documents go out through Documenso and come back signed ----------


@router.post("/engagements/{engagement_id}/send", response_model=SendReport)
def send_engagement(engagement_id: UUID, signing: SigningDep, actor: ActorDep) -> SendReport:
    """Sends the engagement for signature. At most one document leaves at a time: the freelancer's
    framework agreement when they have no active one (the letter waits for it), else the letter.
    Only the freelancer signs. Answers 503 `signing_unavailable` on an installation without
    Documenso or mail, where the letter is downloaded, signed by hand and uploaded instead."""
    return signing.send_engagement(engagement_id, actor)


@router.get("/documents/{document_id}/signature", response_model=SignatureRead)
def get_signature(document_id: UUID, signing: SigningDep, actor: ActorDep) -> SignatureRead:
    """Where a framework agreement or a letter stands: its signing state, the dates and whether
    the signed PDF is stored. The signing link is never in the answer."""
    return signing.get(document_id, actor)


@router.post("/documents/{document_id}/signature/refresh", response_model=SignatureRead)
def refresh_signature(document_id: UUID, signing: SigningDep, actor: ActorDep) -> SignatureRead:
    """Asks Documenso where the envelope stands and applies its answer, the way the webhook does,
    then finishes what a signature leaves to do (the sealed copy, the mails)."""
    return signing.refresh(document_id, actor)


@router.post("/documents/{document_id}/signature/resend-mail", response_model=SignatureRead)
def resend_signing_mail(document_id: UUID, signing: SigningDep, actor: ActorDep) -> SignatureRead:
    """Mails the freelancer the same signing link again, for a document that still waits for the
    signature."""
    return signing.resend_mail(document_id, actor)


@router.post("/documents/{document_id}/signature/cancel", response_model=SignatureRead)
def cancel_framework_agreement(
    document_id: UUID, signing: SigningDep, actor: ActorDep
) -> SignatureRead:
    """Cancels a framework agreement that is not signed yet, its envelope on Documenso included.
    A letter is cancelled with its engagement (`POST /engagements/{id}/state`), not here."""
    return signing.cancel_framework_agreement(document_id, actor)


@router.post("/documents/{document_id}/signature/notice", response_model=SignatureRead)
def record_framework_notice(
    document_id: UUID, signing: SigningDep, actor: ActorDep
) -> SignatureRead:
    """Records a notice or a withdrawal on an active framework agreement: from now the freelancer
    has none active and their next engagement writes a new one."""
    return signing.record_framework_notice(document_id, actor)


def _signed_on(raw: str | None) -> dt.date | None:
    """The `signed_on` part of the upload, a day as `YYYY-MM-DD`; absent or empty is today."""
    try:
        return SignedCopyRequest.model_validate({"signed_on": raw or None}).signed_on
    except ValidationError as error:
        raise ValidationFailed(
            "document", "signed_on", "il giorno della firma è una data", expected="AAAA-MM-GG"
        ) from error


@router.post(
    "/documents/{document_id}/signed-copy",
    response_model=SignatureRead,
    openapi_extra=single_file_body(
        "The signed PDF.",
        {
            "signed_on": {
                "type": "string",
                "format": "date",
                "description": "The day it was signed, `YYYY-MM-DD`. Default: today.",
            }
        },
    ),
)
async def upload_signed_copy(
    document_id: UUID, request: Request, signing: SigningDep, actor: ActorDep
) -> SignatureRead:
    """Records a document signed by hand: the signed PDF becomes its next version and the document
    is `signed`. A person's act (an agent never records a signature), and only for a document that
    never went out through Documenso. The path sits under `/api/documents` so the proxy's upload
    limit covers it. The role is checked before the body is read (`uploads.py`); the service
    checks it again."""
    actor.require_admin("upload_signed_document")
    data, content_type, fields = await read_upload_with_fields(request, SIZE_MAX, "signed_on")
    signed_on = _signed_on(fields.get("signed_on"))
    return await run_in_threadpool(
        signing.upload_signed_copy,
        document_id,
        data,
        content_type or "application/octet-stream",
        signed_on,
        actor,
    )
