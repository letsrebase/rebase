"""The admin area's API: the lists an admin reads, and the promote/demote pair.

One cookie, `rebase_user`, the same one every signed-in person carries, resolved
against `sessions` on every request (`deps.get_admin`, checking `role == 'admin'`).
Everything under this router reads or moves rows other people wrote, or grants the role
to one more person (ORB-123); nothing here writes on an applicant's behalf. The one
thing it writes about a person is the card an admin drafts from a signup (ORB-155), and
that is signed by the admin and refused where the person has already spoken; and, on
request, the anonymous card Claude writes from the CV (REB-510), which the person's own
CV decides.

Since REB-278 `GET /admins` filters `users` on `role == 'admin'`, and the promote/demote
pair is the only way to grant or revoke it: REB-281 dropped the password login and the
create/update pair that posted one, once the SPA that still called them (REB-279)
stopped being their last caller.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from rebase_api.deps import (
    AdminDep,
    CardLlmDep,
    CrmFilingDep,
    CrmLeadsDep,
    CvLlmDep,
    CvRendererDep,
    FreelancerDoorDep,
    HttpCallDep,
    SenderDep,
    SessionDep,
    SettingsDep,
)
from rebase_api.door_errors import door_unavailable
from rebase_api.downloads import cv_response, pdf_response
from rebase_api.params import Cursor, Limit, SearchQ, TalentTerms
from rebase_core.admin_schemas import CrmLinkBody, StatusChange, VettedChange
from rebase_core.admin_tokens import AdminList, AdminRead
from rebase_core.anonymous_cv.render import CvHead, filename
from rebase_core.anonymous_cv.writer import AnonymousCvWriter
from rebase_core.audit import AdminActionRead, AdminActionService
from rebase_core.card_types import Regeneration
from rebase_core.card_writer import CardWriter
from rebase_core.cloud import TalentCloudService
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import (
    CompanyAdminCreate,
    CompanyList,
    CompanyOverride,
    CompanyRead,
    TalentCloudGrantRead,
)
from rebase_core.crm_link import NOT_CONFIGURED as CRM_NOT_CONFIGURED
from rebase_core.duplicates import DuplicateListRead, duplicate_list
from rebase_core.errors import NotFound, TeamBuilderOff
from rebase_core.freelancer_schemas import (
    FreelancerDetail,
    FreelancerDraft,
    FreelancerList,
    FreelancerOverride,
    FreelancerRead,
)
from rebase_core.freelancers import ENTITY as FREELANCER
from rebase_core.freelancers import FreelancerService
from rebase_core.login_schemas import GuideStats, LoginStats
from rebase_core.logins import LoginService
from rebase_core.mail import send_after_response
from rebase_core.merge import MergeRead, MergeRequest, PersonMergeService
from rebase_core.models import NAME_MAX_LENGTH, User
from rebase_core.note_schemas import NoteCreate, NoteRead, NoteUpdate
from rebase_core.notes import NoteService
from rebase_core.perks import PerkService
from rebase_core.pigro_door import PigroUnavailable
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referral_schemas import (
    ReferralLedgerItem,
    ReferralLedgerList,
    ReferralRewardPrice,
    ReferralRewardStateChange,
    ReferralSettingsRead,
    ReferralSettingsUpdate,
)
from rebase_core.referral_settings import ReferralSettingsService
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupList
from rebase_core.talent_schemas import TalentList, TalentRead
from rebase_core.talents import TalentsService, lead_by_id
from rebase_core.team_schemas import FreelancerCardAdminRead
from rebase_core.users import UserService
from rebase_core.validation import SafeStr

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

_log = logging.getLogger(__name__)

# The door an admin's write comes through, recorded on notes and on status changes.
SOURCE = "web"


# ---- the admins ------------------------------------------------------------------------
#
# Who reads this area, and the one form that grants or revokes the role (ORB-123,
# REB-279): typing an email promotes whatever `users` row already answers to it, or
# creates a bare one from `nome`/`last_name` when none exists yet. No deactivation and no
# deletion here, on purpose: the `active` flag exists and nothing in the area changes it
# yet, and demoting is fully reversible since nothing is deleted.


@router.get("/admins", response_model=AdminList)
def list_admins(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    limit: Limit = 100,
    q: SearchQ = None,
    cursor: Cursor = None,
) -> AdminList:
    """REB-313 adds `q` (nome/cognome/email, trigram-ordered once searching) and
    `cursor` beside the oldest-first order ORB-123 gave this list."""
    return UserService(session, settings).list_admins(limit=limit, q=q, cursor=cursor)


class PromoteRequest(BaseModel):
    """`POST /admins/promote`'s body: an email, and `first_name`/`last_name` for an address
    with no `users` row yet -- ignored, harmlessly, when one already exists."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    first_name: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)


@router.post("/admins/promote", response_model=AdminRead)
def promote_admin(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    background: BackgroundTasks,
    payload: PromoteRequest,
) -> AdminRead:
    """Promotes whatever `users` row already answers to this address with one click
    and no form; an address with none yet is created bare, no freelancer card invented
    for it (§1, Nav and Amministratori). A brand-new row gets the same magic link
    everyone else gets, since it has no other way in yet."""
    users = UserService(session, settings)
    user, created = users.promote(
        payload.email, payload.first_name, payload.last_name, admin_id=admin.id
    )
    if created and sender is not None:
        mail = users.request_link(user.email)
        if mail is not None:
            background.add_task(
                send_after_response, sender, mail, f"magic link of the promoted user {user.id}"
            )
    return AdminRead.model_validate(user)


@router.post("/admins/{user_id}/demote", response_model=AdminRead)
def demote_admin(
    admin: AdminDep, session: SessionDep, settings: SettingsDep, user_id: UUID
) -> AdminRead:
    """Sets `role = 'member'`, fully reversible since nothing is deleted. 409 with the
    sentence for the last remaining admin, 422 for an address merged into somebody. Both
    the promotion and the demotion are signed in the trail (`admin_promoted`,
    `admin_demoted`), in the same transaction as the role."""
    return AdminRead.model_validate(
        UserService(session, settings).demote(user_id, admin_id=admin.id)
    )


# ---- the lists -------------------------------------------------------------------------


@router.get("/freelancers", response_model=FreelancerList)
def list_freelancers(
    _: AdminDep, session: SessionDep, limit: Limit = 100, status: str | None = None
) -> FreelancerList:
    return FreelancerService(session).list_recent(limit=limit, status=status)


@router.get("/freelancers/{freelancer_id}", response_model=FreelancerDetail)
def get_freelancer(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpCallDep,
    freelancer_id: UUID,
) -> FreelancerDetail:
    """The card, its state and notes, and since REB-284 everywhere else the hub
    already knows this address: the sign-up's own UTM, the last logins and guide
    downloads, and the PigroCRM space when it owns one -- one call, not four."""
    return FreelancerService(session, settings, http).get(freelancer_id)


@router.get("/freelancers/{freelancer_id}/cv")
def download_cv(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> Response:
    cv = FreelancerService(session).cv(freelancer_id)
    return cv_response(cv)


@router.patch("/freelancers/{freelancer_id}", response_model=FreelancerRead)
def move_freelancer(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, change: StatusChange
) -> FreelancerRead:
    """Moves the card to another state; recorded on `GET .../audit` as `status_changed`
    with the admin and the door (`web`)."""
    return FreelancerService(session).set_status(
        freelancer_id, change, admin_id=admin.id, source=SOURCE
    )


@router.patch("/freelancers/{freelancer_id}/override", response_model=FreelancerRead)
def override_freelancer(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, change: FreelancerOverride
) -> FreelancerRead:
    """Sets or clears any profile field beyond `status` (REB-347): a name, a
    rate, a position, the identity on the linked `users` row. Recorded on
    `GET .../audit`; reversible with `POST .../audit/{action_id}/revert`."""
    return FreelancerService(session).override(freelancer_id, change, admin.id)


@router.delete("/freelancers/{freelancer_id}", response_model=FreelancerRead)
def delete_freelancer(admin: AdminDep, session: SessionDep, freelancer_id: UUID) -> FreelancerRead:
    """Soft-deletes the card: it drops off `GET /freelancers` and `GET /talent`, and
    `POST .../restore` reverses it. Never a hard delete (REB-347)."""
    return FreelancerService(session).soft_delete(freelancer_id, admin.id)


@router.post("/freelancers/{freelancer_id}/restore", response_model=FreelancerRead)
def restore_freelancer(admin: AdminDep, session: SessionDep, freelancer_id: UUID) -> FreelancerRead:
    return FreelancerService(session).restore(freelancer_id, admin.id)


@router.delete("/freelancers/{freelancer_id}/cv", response_model=FreelancerRead)
def clear_freelancer_cv(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID
) -> FreelancerRead:
    """Drops the stored CV, and the anonymous card written from it; the file itself
    never enters the audit trail (`FreelancerService.clear_cv`)."""
    return FreelancerService(session).clear_cv(freelancer_id, admin.id)


@router.post("/freelancers/{freelancer_id}/vetted", response_model=FreelancerRead)
def set_freelancer_vetted(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, change: VettedChange
) -> FreelancerRead:
    """«Segna come verificato» with `{vetted: true}`, «Togli la verifica» with `false`
    (REB-518): the talent cloud's badge, recorded as a `vetted` entry of the trail."""
    return FreelancerService(session).set_vetted(freelancer_id, change.vetted, admin.id)


@router.get("/freelancers/{freelancer_id}/card", response_model=FreelancerCardAdminRead)
def get_freelancer_card(
    _: AdminDep, session: SessionDep, freelancer_id: UUID
) -> FreelancerCardAdminRead:
    """The anonymous card Claude wrote from the CV (REB-510): the card, the work mode
    read from the profile now, the CV and the model it came from, and the last failure,
    which may sit beside an older card; and what it was written from (`provenance`: the
    sources the model says it used, the prompt version, the conflicts), which no other
    reader of a card sees (REB-839)."""
    return CardWriter(session, None).read_admin(freelancer_id)


@router.post("/freelancers/{freelancer_id}/card", response_model=FreelancerCardAdminRead)
def regenerate_freelancer_card(
    admin: AdminDep, session: SessionDep, llm: CardLlmDep, freelancer_id: UUID
) -> FreelancerCardAdminRead:
    """«Rigenera scheda»: the card written again from the current CV, now, even from the
    CV that last failed, which is otherwise not tried again until it changes. A failure
    answers 200 with the previous card and `error`, as the page shows it; no key answers
    the team builder's own 503, rather than the card unchanged as if written again. What
    became of it is signed in the talent's trail (`card_regenerated`, `source` `web`)."""
    if llm is None:
        raise TeamBuilderOff("Il team builder è spento.")
    writer = CardWriter(session, llm, run=Regeneration(admin.id, "web"))
    writer.write(freelancer_id, force=True)
    return writer.read_admin(freelancer_id)


@router.post(
    "/freelancers/{freelancer_id}/anonymous-cv",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def download_anonymous_cv(
    admin: AdminDep,
    session: SessionDep,
    llm: CvLlmDep,
    renderer: CvRendererDep,
    door: FreelancerDoorDep,
    freelancer_id: UUID,
) -> Response:
    """«Scarica CV anonimo» (REB-862): the talent's CV without the person, as a PDF in
    rebase's style, for an admin to send to a client company. The body is written once per
    CV file and kept, so a second press is instant and free, and served even with the task
    off; a CV that has to be written needs the `anonymous_cv` task configured (502 with the
    sentence «Modelli AI» gives otherwise). The page's head carries the card's level, the
    admin's correction over it, and its city, only from a card of the same CV file; a
    seniority the card marks uncertain is left out until an admin says one. One
    `anon_cv_downloaded` entry on success, naming whether the body was kept. A plain
    `def`: writing and printing wait minutes at worst, in the threadpool rather than on the
    event loop. **Fails closed on the CRM** (REB-1107): the redaction list is the hub's tax
    data and the CRM's (`FreelancerDoor.fiscal_identifiers`), and without the CRM's half nothing
    is written and a kept body is not served either: 502 with the seam's sentence when the CRM
    does not answer, 503 when this environment cannot reach it."""
    try:
        written = AnonymousCvWriter(session, llm, door).get_or_write(freelancer_id)
    except PigroUnavailable as exc:
        raise door_unavailable(
            exc, "Il CV anonimo non si scrive né si scarica senza i dati da oscurare del CRM."
        ) from exc
    # The card as the admin reads it: his seniority and years over Claude's. `None` for a
    # talent with no card yet, and for a card of an older CV, which may say another level
    # or city than the CV printed under it.
    read = CardWriter(session, None).read(freelancer_id)
    card = read.card if read.cv_sha256 == written.cv_sha256 else None
    # The id's random tail: its head is a UUIDv7's timestamp, the same for talents created
    # together and telling when the person signed up.
    ref = str(freelancer_id)[-8:]
    head = CvHead(
        seniority=card.seniority if card and not card.seniority_uncertain else None,
        experience_years=card.experience_years if card else None,
        location=card.location if card else None,
        mode=read.mode,
        ref=ref,
    )
    pdf = renderer.render(written.cv, head)
    AdminActionService(session).record(
        FREELANCER, freelancer_id, "anon_cv_downloaded", admin.id, {"reused": written.reused}
    )
    return pdf_response(filename(written.cv, ref), pdf)


@router.get("/freelancers/{freelancer_id}/audit", response_model=list[AdminActionRead])
def freelancer_audit(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, limit: Limit = 50
) -> list[AdminActionRead]:
    """Who overrode, cleared, deleted or restored this card, and when."""
    return FreelancerService(session).audit_timeline(freelancer_id, limit)


@router.post("/freelancers/{freelancer_id}/audit/{action_id}/revert", response_model=FreelancerRead)
def revert_freelancer_action(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, action_id: UUID
) -> FreelancerRead:
    """Puts a field back to the value a past `overridden` entry names in its own
    `before`, itself recorded as a fresh override."""
    return FreelancerService(session).revert(freelancer_id, action_id, admin.id)


@router.get("/talent/duplicates", response_model=DuplicateListRead)
def list_duplicates(_: AdminDep, session: SessionDep) -> DuplicateListRead:
    """Who is on the list twice (REB-800): the pairs that share a LinkedIn profile, which
    can be merged, and the ones that only share a name or a number, which are for an
    admin to look at. The survivor of each pair is the card with the newest CV."""
    return duplicate_list(session)


@router.post("/freelancers/{freelancer_id}/merge", response_model=MergeRead)
def merge_freelancer(
    admin: AdminDep,
    session: SessionDep,
    door: FreelancerDoorDep,
    freelancer_id: UUID,
    body: MergeRequest,
) -> MergeRead:
    """Merges the card `da` into this one (REB-800): its notes, logins, the hub's
    engagement read model, campaign rows, referrals and audit entries move here, the newer of
    the two CVs stays here with its card flagged for regeneration, and the other card is
    soft-deleted and points at this one. Refused with a sentence when the CRM says both have
    a live framework agreement of the same type or an engagement in progress with the same
    client, when the two have different tax codes, or when the CRM cannot be asked
    (REB-1107). Undone from the shell with `rebase talent-duplicates --undo`."""
    return PersonMergeService(session, door).merge(freelancer_id, body.da, admin.id)


@router.get("/companies", response_model=CompanyList)
def list_companies(
    _: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    status: str | None = None,
    q: SearchQ = None,
    cursor: Cursor = None,
    budget_min: Decimal | None = None,
    budget_max: Decimal | None = None,
    period_from: date | None = None,
    origin: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> CompanyList:
    """REB-285 adds `q` (company_name/contact/email/project, trigram-ordered),
    `cursor`, and every filter after `origin` beside the original `limit`/`status`."""
    return CompanyService(session).list_recent(
        limit=limit,
        status=status,
        q=q,
        cursor=cursor,
        budget_min=budget_min,
        budget_max=budget_max,
        period_from=period_from,
        origin=origin,
        created_from=created_from,
        created_to=created_to,
    )


@router.post("/admin/companies", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(
    admin: AdminDep,
    session: SessionDep,
    background: BackgroundTasks,
    crm: CrmLeadsDep,
    crm_filing: CrmFilingDep,
    data: CompanyAdminCreate,
) -> CompanyRead:
    """An admin opens a request for a company met directly (REB-870): the wizard's fields,
    validated the same way, the contact found or created by email, and no mail to anybody.
    Without `deal_id` it is a new request like any other: `to_link`, filed in the CRM in
    the background as a new deal. With `deal_id`, the CRM deal it already has, the CRM links
    it now and creates nothing (no second customer, no second deal): the answer carries
    `linked`, or `rejected` with the CRM's sentence when the CRM does not know the deal.
    The deal stays on the request, so «Riprova» and the sweep ask for it again."""
    service = CompanyService(session)
    created = service.create_by_admin(data, admin.id, source=SOURCE)
    if data.deal_id is None:
        background.add_task(crm_filing, created.id)
        return created
    crm.link(created.id, admin.id)
    return service.get(created.id)


@router.get("/companies/{company_id}", response_model=CompanyRead)
def get_company(_: AdminDep, session: SessionDep, company_id: UUID) -> CompanyRead:
    return CompanyService(session).get(company_id)


@router.patch("/companies/{company_id}", response_model=CompanyRead)
def move_company(
    admin: AdminDep, session: SessionDep, company_id: UUID, change: StatusChange
) -> CompanyRead:
    """Moves the request to another state; recorded on `GET .../audit` as
    `status_changed` with the admin and the door (`web`)."""
    return CompanyService(session).set_status(company_id, change, admin_id=admin.id, source=SOURCE)


@router.post("/companies/{company_id}/crm/link", response_model=CompanyRead)
def link_company_to_crm(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    crm: CrmLeadsDep,
    company_id: UUID,
    body: CrmLinkBody | None = None,
) -> CompanyRead:
    """«Riprova» (REB-868): the request is filed in rebase's CRM now, as the admin, and
    comes back as it stands, `linked` or with the CRM's sentence (`error`, `rejected`).
    It also files a request from before the link existed. With `deal_id`, «Collega a un
    altro deal»: the CRM links that deal and creates nothing; a refusal is a 409 with its
    sentence and the stored link stays as it was. 404 for a request that does not exist or
    is deleted, 503 without the CRM's token."""
    service = CompanyService(session)
    service.get(company_id)
    if not settings.pigro_engagements_token:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, CRM_NOT_CONFIGURED)
    crm.link(company_id, admin.id, body.deal_id if body is not None else None)
    return service.get(company_id)


@router.patch("/companies/{company_id}/override", response_model=CompanyRead)
def override_company(
    admin: AdminDep, session: SessionDep, company_id: UUID, change: CompanyOverride
) -> CompanyRead:
    """Sets or clears any request field beyond `status` (REB-347): the project
    answers, the company's own name, the contact's identity on the linked `users`
    row. Recorded on `GET .../audit`; reversible with
    `POST .../audit/{action_id}/revert`."""
    return CompanyService(session).override(company_id, change, admin.id)


@router.delete("/companies/{company_id}", response_model=CompanyRead)
def delete_company(admin: AdminDep, session: SessionDep, company_id: UUID) -> CompanyRead:
    """Soft-deletes the request: it drops off `GET /companies`, and
    `POST .../restore` reverses it. Never a hard delete (REB-347)."""
    return CompanyService(session).soft_delete(company_id, admin.id)


@router.post("/companies/{company_id}/restore", response_model=CompanyRead)
def restore_company(admin: AdminDep, session: SessionDep, company_id: UUID) -> CompanyRead:
    return CompanyService(session).restore(company_id, admin.id)


@router.get("/companies/{company_id}/audit", response_model=list[AdminActionRead])
def company_audit(
    _: AdminDep, session: SessionDep, company_id: UUID, limit: Limit = 50
) -> list[AdminActionRead]:
    """Who overrode, deleted or restored this request, and when."""
    return CompanyService(session).audit_timeline(company_id, limit)


@router.post("/companies/{company_id}/audit/{action_id}/revert", response_model=CompanyRead)
def revert_company_action(
    admin: AdminDep, session: SessionDep, company_id: UUID, action_id: UUID
) -> CompanyRead:
    """Puts a field back to the value a past `overridden` entry names in its own
    `before`, itself recorded as a fresh override."""
    return CompanyService(session).revert(company_id, action_id, admin.id)


# ---- the talent cloud's door (REB-518) ---------------------------------------------------


@router.post(
    "/companies/{company_id}/cloud",
    response_model=TalentCloudGrantRead,
    status_code=status.HTTP_201_CREATED,
)
def open_talent_cloud(
    admin: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    sender: SenderDep,
    background: BackgroundTasks,
    response: Response,
    company_id: UUID,
) -> TalentCloudGrantRead:
    """«Apri il talent cloud» on a company request's page: 201 with the grant to the
    request's contact, whose mail leaves after this answer; 200 with the grant
    already live for that person and request, a double click included, and no mail.
    Without a mail key the cloud opens all the same and nobody is mailed."""
    read, mail = TalentCloudService(session, settings).grant(company_id, admin.id)
    if mail is None:
        response.status_code = status.HTTP_200_OK
    elif sender is None:
        _log.info("talent cloud grant %s: no mail sender, not mailed", read.id)
    else:
        send_after_response(sender, mail, f"talent cloud grant {read.id}")
    return read


@router.delete("/companies/{company_id}/cloud", response_model=TalentCloudGrantRead)
def revoke_talent_cloud(
    admin: AdminDep, session: SessionDep, company_id: UUID
) -> TalentCloudGrantRead:
    """«Revoca il talent cloud»: closes this request's live grant only; 409 with the
    sentence when none is live."""
    return TalentCloudService(session).revoke(company_id, admin.id)


@router.get("/cloud/grants", response_model=list[TalentCloudGrantRead])
def list_talent_cloud_grants(_: AdminDep, session: SessionDep) -> list[TalentCloudGrantRead]:
    """Every grant, live and closed, newest first; capped (`cloud.LIST_CAP`), not
    paginated: a grant is a company admitted by hand."""
    return TalentCloudService(session).list()


@router.get("/logins", response_model=LoginStats)
def login_stats(
    _: AdminDep, session: SessionDep, limit: Limit = 100, q: SearchQ = None, cursor: Cursor = None
) -> LoginStats:
    """Who entered the hub and when (ORB-158): every login through a magic link, the
    distinct members behind them, the last week -- unchanged aggregate counters. REB-313
    adds `q` (nome/cognome/email, trigram-ordered once searching) and `cursor` to
    `recent`, no longer a hardcoded top 20. Written by `POST /auth/enter` and by
    nothing else."""
    return LoginService(session).stats(limit=limit, q=q, cursor=cursor)


@router.get("/perks/guide", response_model=GuideStats)
def guide_stats(_: AdminDep, session: SessionDep) -> GuideStats:
    """How the guide is doing (ORB-156): every download, the distinct members behind
    them, the last week, and the latest ones by name. Read-only; the rows are written by
    `GET /me/guide` and by nothing else."""
    return PerkService(session).guide_stats()


@router.get("/signups", response_model=SignupList)
def list_signups(_: AdminDep, session: SessionDep, limit: Limit = 100) -> SignupList:
    return SignupService(session).list_recent(limit=limit)


@router.get("/talent", response_model=TalentList)
def list_talents(
    _: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    status: str | None = None,
    q: SearchQ = None,
    cursor: Cursor = None,
    job_title: str | None = None,
    remote: str | None = None,
    min_rate: Decimal | None = None,
    max_rate: Decimal | None = None,
    origin: str | None = None,
    origin_page: str | None = None,
    utm_source: str | None = None,
    has_cv: bool | None = None,
    with_logins: bool | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    skills: TalentTerms = None,
    location: Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)] = None,
    seniority: TalentTerms = None,
    min_years: Annotated[int | None, Query(ge=0)] = None,
    max_years: Annotated[int | None, Query(ge=0)] = None,
    industries: TalentTerms = None,
    languages: TalentTerms = None,
    card: Annotated[str | None, Query(max_length=20)] = None,
    sort: Annotated[str | None, Query(max_length=30)] = None,
) -> TalentList:
    """`talents` (REB-282): every freelancer card and every bare sign-up as one list,
    `status` «lead» for the bare ones -- the read model «Developer e CTO» and
    «Iscrizioni» read as two overlapping lists, merged. Additive beside both: neither
    changes here. REB-285 adds `q` (first_name/last_name/email/job_title, trigram-ordered),
    `cursor`, and every filter after `status`. `origin` is the channel a row shows
    (`form`, `wizard`, `admin`) and `origin_page` the marketing page the person
    started from (REB-798; the page filter was `origin` before). REB-823 gives the screen
    what the MCP tool `list_talents` already took (REB-778, REB-801): `skills`, `location`,
    `seniority`, `min_years`, `max_years`, `industries`, `languages`, `card` and `sort`,
    read by the same service, which refuses a value it does not allow."""
    return TalentsService(session).list_recent(
        limit=limit,
        status=status,
        q=q,
        cursor=cursor,
        job_title=job_title,
        remote=remote,
        min_rate=min_rate,
        max_rate=max_rate,
        origin=origin,
        origin_page=origin_page,
        utm_source=utm_source,
        has_cv=has_cv,
        with_logins=with_logins,
        created_from=created_from,
        created_to=created_to,
        skills=skills,
        location=location,
        seniority=seniority,
        min_years=min_years,
        max_years=max_years,
        industries=industries,
        languages=languages,
        card=card,
        sort=sort,
    )


@router.post(
    "/signups/{signup_id}/card",
    response_model=FreelancerRead,
    status_code=status.HTTP_201_CREATED,
)
def draft_card_from_signup(
    admin: AdminDep, session: SessionDep, signup_id: UUID, payload: FreelancerDraft
) -> FreelancerRead:
    """The freelancer card an admin writes from what the public web says about a signup
    (ORB-155): incomplete until the person adds the CV, the rate and the rest from the
    member area. The note naming the sources is signed by the admin the cookie
    resolves to. 404 for an unknown signup; 422 naming `email` when the person has
    already filled their card, which research never overwrites."""
    return FreelancerService(session).draft_from_signup(signup_id, payload, admin.id, SOURCE)


@router.get("/signups/{signup_id}", response_model=TalentRead)
def get_signup(_: AdminDep, session: SessionDep, signup_id: UUID) -> TalentRead:
    """One bare sign-up as the row the «Talenti» list shows for it (`status` «lead», `origin`
    «form»): the page of a person who has no card yet. 404 for an unknown id, and for a
    person who already has a card, whom the list shows as that card."""
    lead = lead_by_id(session, signup_id)
    if lead is None:
        raise NotFound("talento", signup_id)
    return lead


# ---- notes -----------------------------------------------------------------------------
#
# One thread per card and per request (REB-856), on the same cookie as everything else
# here. The author is the admin the cookie resolves to and the source is `web`: the body
# carries the text alone, so nobody can sign as somebody else. An admin's note may be
# edited and deleted (soft), each recorded in the trail; the person's and the hub's own
# are events and answer 422.


@router.get("/freelancers/{freelancer_id}/notes", response_model=list[NoteRead])
def list_freelancer_notes(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> list[NoteRead]:
    return NoteService(session).list("freelancer", freelancer_id)


@router.post(
    "/freelancers/{freelancer_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
)
def add_freelancer_note(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, payload: NoteCreate
) -> NoteRead:
    return NoteService(session).add(
        "freelancer", freelancer_id, payload.text, author_id=admin.id, source=SOURCE
    )


@router.get("/companies/{company_id}/notes", response_model=list[NoteRead])
def list_company_notes(_: AdminDep, session: SessionDep, company_id: UUID) -> list[NoteRead]:
    return NoteService(session).list("company", company_id)


@router.post(
    "/companies/{company_id}/notes",
    response_model=NoteRead,
    status_code=status.HTTP_201_CREATED,
)
def add_company_note(
    admin: AdminDep, session: SessionDep, company_id: UUID, payload: NoteCreate
) -> NoteRead:
    return NoteService(session).add(
        "company", company_id, payload.text, author_id=admin.id, source=SOURCE
    )


@router.patch("/notes/{note_id}", response_model=NoteRead)
def edit_note(admin: AdminDep, session: SessionDep, note_id: UUID, payload: NoteUpdate) -> NoteRead:
    return NoteService(session).update(note_id, payload.text, admin_id=admin.id, source=SOURCE)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(admin: AdminDep, session: SessionDep, note_id: UUID) -> Response:
    NoteService(session).delete(note_id, admin_id=admin.id, source=SOURCE)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---- referrals (P-REB-44) ----------------------------------------------------------
#
# The ledger every signed-letter reward lands on, and the two rates that decide how
# much: a database row an admin edits here, never an environment variable (design
# record 2026-09-26).


@router.get("/referrals", response_model=ReferralLedgerList)
def list_referrals(
    _: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    status: str | None = None,
    cursor: Cursor = None,
) -> ReferralLedgerList:
    return ReferralLedgerService(session).list_rewards(limit=limit, status=status, cursor=cursor)


@router.post("/referrals/{reward_id}/state", response_model=ReferralLedgerItem)
def set_referral_state(
    admin: AdminDep, session: SessionDep, reward_id: UUID, payload: ReferralRewardStateChange
) -> ReferralLedgerItem:
    """`to_confirm` to `confirmed` to `paid`, one step at a time (`ReferralLedgerService.
    set_state`). Tracking only: nothing here pays anyone."""
    service = ReferralLedgerService(session)
    service.set_state(reward_id, payload.status, admin.id)
    return service.get_ledger_item(reward_id)


@router.post("/referrals/{reward_id}/price", response_model=ReferralLedgerItem)
def set_referral_price(
    admin: AdminDep, session: SessionDep, reward_id: UUID, payload: ReferralRewardPrice
) -> ReferralLedgerItem:
    """What an admin prices by hand for a reward `record_reward_if_signed` left with
    no figure (a `lump_sum` letter with no `expected_days` estimate)."""
    service = ReferralLedgerService(session)
    service.set_price(reward_id, payload.base_amount, payload.reward_amount)
    return service.get_ledger_item(reward_id)


@router.get("/referral-settings", response_model=ReferralSettingsRead)
def get_referral_settings(_: AdminDep, session: SessionDep) -> ReferralSettingsRead:
    row = ReferralSettingsService(session).get_settings()
    updated_by = session.get(User, row.updated_by) if row.updated_by is not None else None
    return ReferralSettingsRead(
        rate_freelancer=row.rate_freelancer,
        rate_company=row.rate_company,
        updated_at=row.updated_at,
        updated_by_name=updated_by.first_name if updated_by is not None else None,
    )


@router.put("/referral-settings", response_model=ReferralSettingsRead)
def save_referral_settings(
    admin: AdminDep, session: SessionDep, payload: ReferralSettingsUpdate
) -> ReferralSettingsRead:
    """Both rates together (`ReferralSettingsUpdate`'s own reasoning): a ledger open
    on stale numbers is a worse mistake than asking for the pair every time."""
    row = ReferralSettingsService(session).save_settings(
        payload.rate_freelancer, payload.rate_company, admin.id
    )
    return ReferralSettingsRead(
        rate_freelancer=row.rate_freelancer,
        rate_company=row.rate_company,
        updated_at=row.updated_at,
        updated_by_name=admin.first_name,
    )
