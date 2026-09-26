"""The admin area's API: the lists an admin reads, and the promote/demote pair.

One cookie, `orbiters_user`, the same one every signed-in person carries, resolved
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

from fastapi import APIRouter, BackgroundTasks, Query, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from rebase_api.deps import AdminDep, HttpCallDep, LlmDep, SenderDep, SessionDep, SettingsDep
from rebase_api.downloads import cv_response
from rebase_core.admin_tokens import AdminList, AdminRead
from rebase_core.audit import AdminActionRead
from rebase_core.cards import CardWriter
from rebase_core.comments import CommentService
from rebase_core.companies import CompanyService
from rebase_core.freelancers import FreelancerService
from rebase_core.logins import LoginService
from rebase_core.mail import EmailSender, Mail
from rebase_core.models import NAME_MAX_LENGTH
from rebase_core.pagination import CURSOR_MAX_LENGTH
from rebase_core.perks import PerkService
from rebase_core.schemas import (
    CommentCreate,
    CommentRead,
    CompanyList,
    CompanyOverride,
    CompanyRead,
    FreelancerDetail,
    FreelancerDraft,
    FreelancerList,
    FreelancerOverride,
    FreelancerRead,
    GuideStats,
    LoginStats,
    SignupList,
    StatusChange,
    TalentoList,
)
from rebase_core.search import SEARCH_MAX_LENGTH
from rebase_core.service import SignupService
from rebase_core.talenti import TalentiService
from rebase_core.team_schemas import FreelancerCardRead
from rebase_core.users import UserService
from rebase_core.validation import SafeStr

router = APIRouter(prefix="/api/hub", tags=["hub-admin"])

_log = logging.getLogger(__name__)


def _send(sender: EmailSender, mail: Mail) -> None:
    """Runs after the response, same reasoning as `routers/members.py`'s own: a refusal
    is logged without the address or the key."""
    if not sender.send(mail):
        _log.warning("a promotion's magic link mail was refused by the provider")


Limit = Annotated[int, Query(ge=1, le=500)]
# REB-285: shared by every list a cursor and a search box were added to (`/talent`,
# `/companies`, and since REB-313 `/admins` and `/logins`). `SearchQ`'s bound is
# `search.SEARCH_MAX_LENGTH`, `Cursor`'s is `pagination.CURSOR_MAX_LENGTH` -- both
# bounded for the reason every free-text query parameter in this codebase is: an
# unbounded one reaching the database is a denial of service with extra steps.
SearchQ = Annotated[str | None, Query(max_length=SEARCH_MAX_LENGTH)]
Cursor = Annotated[str | None, Query(max_length=CURSOR_MAX_LENGTH)]

# ---- the admins ------------------------------------------------------------------------
#
# Who reads this area, and the one form that grants or revokes the role (ORB-123,
# REB-279): typing an email promotes whatever `users` row already answers to it, or
# creates a bare one from `nome`/`cognome` when none exists yet. No deactivation and no
# deletion here, on purpose: the `attivo` flag exists and nothing in the area changes it
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
    """`POST /admins/promote`'s body: an email, and `nome`/`cognome` for an address
    with no `users` row yet -- ignored, harmlessly, when one already exists."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    nome: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)


@router.post("/admins/promote", response_model=AdminRead)
def promote_admin(
    _: AdminDep,
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
    user, created = users.promote(payload.email, payload.nome, payload.cognome)
    if created and sender is not None:
        mail = users.request_link(user.email)
        if mail is not None:
            background.add_task(_send, sender, mail)
    return AdminRead.model_validate(user)


@router.post("/admins/{user_id}/demote", response_model=AdminRead)
def demote_admin(
    _: AdminDep, session: SessionDep, settings: SettingsDep, user_id: UUID
) -> AdminRead:
    """Sets `role = 'member'`, fully reversible since nothing is deleted."""
    return AdminRead.model_validate(UserService(session, settings).demote(user_id))


# ---- the lists -------------------------------------------------------------------------


@router.get("/freelancers", response_model=FreelancerList)
def list_freelancers(
    _: AdminDep, session: SessionDep, limit: Limit = 100, stato: str | None = None
) -> FreelancerList:
    return FreelancerService(session).list_recent(limit=limit, stato=stato)


@router.get("/freelancers/{freelancer_id}", response_model=FreelancerDetail)
def get_freelancer(
    _: AdminDep,
    session: SessionDep,
    settings: SettingsDep,
    http: HttpCallDep,
    freelancer_id: UUID,
) -> FreelancerDetail:
    """The card, its state and comments, and since REB-284 everywhere else the hub
    already knows this address: the sign-up's own UTM, the last logins and guide
    downloads, and the PigroCRM space when it owns one -- one call, not four."""
    return FreelancerService(session, settings, http).get(freelancer_id)


@router.get("/freelancers/{freelancer_id}/cv")
def download_cv(_: AdminDep, session: SessionDep, freelancer_id: UUID) -> Response:
    cv = FreelancerService(session).cv(freelancer_id)
    return cv_response(cv)


@router.patch("/freelancers/{freelancer_id}", response_model=FreelancerRead)
def move_freelancer(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, change: StatusChange
) -> FreelancerRead:
    return FreelancerService(session).set_status(freelancer_id, change)


@router.patch("/freelancers/{freelancer_id}/override", response_model=FreelancerRead)
def override_freelancer(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, change: FreelancerOverride
) -> FreelancerRead:
    """Sets or clears any profile field beyond `stato`/`note` (REB-347): a name, a
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


@router.get("/freelancers/{freelancer_id}/card", response_model=FreelancerCardRead)
def get_freelancer_card(
    _: AdminDep, session: SessionDep, freelancer_id: UUID
) -> FreelancerCardRead:
    """The anonymous card Claude wrote from the CV (REB-510): the card, the work mode
    read from the profile now, the CV and the model it came from, and the last failure,
    which may sit beside an older card."""
    return CardWriter(session, None).read(freelancer_id)


@router.post("/freelancers/{freelancer_id}/card", response_model=FreelancerCardRead)
def regenerate_freelancer_card(
    _: AdminDep, session: SessionDep, llm: LlmDep, freelancer_id: UUID
) -> FreelancerCardRead:
    """«Rigenera scheda»: the card written again from the current CV, now, even from the
    CV that last failed, which is otherwise not tried again until it changes. A failure
    answers 200 with the previous card and `error`, as the page shows it."""
    return CardWriter(session, llm).write(freelancer_id, force=True)


@router.get("/freelancers/{freelancer_id}/audit", response_model=list[AdminActionRead])
def freelancer_audit(
    _: AdminDep, session: SessionDep, freelancer_id: UUID, limit: int = 50
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


@router.get("/companies", response_model=CompanyList)
def list_companies(
    _: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    stato: str | None = None,
    q: SearchQ = None,
    cursor: Cursor = None,
    budget_min: Decimal | None = None,
    budget_max: Decimal | None = None,
    periodo_da: date | None = None,
    origine: str | None = None,
    creato_da: datetime | None = None,
    creato_a: datetime | None = None,
) -> CompanyList:
    """REB-285 adds `q` (nome_azienda/referente/email/progetto, trigram-ordered),
    `cursor`, and every filter after `origine` beside the original `limit`/`stato`."""
    return CompanyService(session).list_recent(
        limit=limit,
        stato=stato,
        q=q,
        cursor=cursor,
        budget_min=budget_min,
        budget_max=budget_max,
        periodo_da=periodo_da,
        origine=origine,
        creato_da=creato_da,
        creato_a=creato_a,
    )


@router.get("/companies/{company_id}", response_model=CompanyRead)
def get_company(_: AdminDep, session: SessionDep, company_id: UUID) -> CompanyRead:
    return CompanyService(session).get(company_id)


@router.patch("/companies/{company_id}", response_model=CompanyRead)
def move_company(
    _: AdminDep, session: SessionDep, company_id: UUID, change: StatusChange
) -> CompanyRead:
    return CompanyService(session).set_status(company_id, change)


@router.patch("/companies/{company_id}/override", response_model=CompanyRead)
def override_company(
    admin: AdminDep, session: SessionDep, company_id: UUID, change: CompanyOverride
) -> CompanyRead:
    """Sets or clears any request field beyond `stato`/`note` (REB-347): the project
    answers, the company's own name, the referente's identity on the linked `users`
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
    _: AdminDep, session: SessionDep, company_id: UUID, limit: int = 50
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


@router.get("/logins", response_model=LoginStats)
def login_stats(
    _: AdminDep, session: SessionDep, limit: Limit = 100, q: SearchQ = None, cursor: Cursor = None
) -> LoginStats:
    """Who entered the hub and when (ORB-158): every login through a magic link, the
    distinct members behind them, the last week -- unchanged aggregate counters. REB-313
    adds `q` (nome/cognome/email, trigram-ordered once searching) and `cursor` to
    `recenti`, no longer a hardcoded top 20. Written by `POST /auth/enter` and by
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


@router.get("/talent", response_model=TalentoList)
def list_talenti(
    _: AdminDep,
    session: SessionDep,
    limit: Limit = 100,
    stato: str | None = None,
    q: SearchQ = None,
    cursor: Cursor = None,
    posizione: str | None = None,
    remoto: str | None = None,
    tariffa_min: Decimal | None = None,
    tariffa_max: Decimal | None = None,
    origine: str | None = None,
    utm_source: str | None = None,
    has_cv: bool | None = None,
    con_accessi: bool | None = None,
    creato_da: datetime | None = None,
    creato_a: datetime | None = None,
) -> TalentoList:
    """`talenti` (REB-282): every freelancer card and every bare sign-up as one list,
    `stato` «lead» for the bare ones -- the read model «Developer e CTO» and
    «Iscrizioni» read as two overlapping lists, merged. Additive beside both: neither
    changes here. REB-285 adds `q` (nome/cognome/email/posizione, trigram-ordered),
    `cursor`, and every filter after `stato`."""
    return TalentiService(session).list_recent(
        limit=limit,
        stato=stato,
        q=q,
        cursor=cursor,
        posizione=posizione,
        remoto=remoto,
        tariffa_min=tariffa_min,
        tariffa_max=tariffa_max,
        origine=origine,
        utm_source=utm_source,
        has_cv=has_cv,
        con_accessi=con_accessi,
        creato_da=creato_da,
        creato_a=creato_a,
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
    member area. The comment naming the sources is signed by the admin the cookie
    resolves to. 404 for an unknown signup; 422 naming `email` when the person has
    already filled their card, which research never overwrites."""
    return FreelancerService(session).draft_from_signup(signup_id, payload, admin.nome)


# ---- comments --------------------------------------------------------------------------
#
# Append-only, on the same cookie as everything else here. The author is the admin the
# cookie resolves to: the body carries the text alone, so nobody can sign as somebody
# else. No PATCH and no DELETE on purpose: a thread is a record (ORB-59).


@router.get("/freelancers/{freelancer_id}/comments", response_model=list[CommentRead])
def list_freelancer_comments(
    _: AdminDep, session: SessionDep, freelancer_id: UUID
) -> list[CommentRead]:
    return CommentService(session).list("freelancer", freelancer_id)


@router.post(
    "/freelancers/{freelancer_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_freelancer_comment(
    admin: AdminDep, session: SessionDep, freelancer_id: UUID, payload: CommentCreate
) -> CommentRead:
    return CommentService(session).add("freelancer", freelancer_id, payload.testo, admin.nome)


@router.get("/companies/{company_id}/comments", response_model=list[CommentRead])
def list_company_comments(_: AdminDep, session: SessionDep, company_id: UUID) -> list[CommentRead]:
    return CommentService(session).list("company", company_id)


@router.post(
    "/companies/{company_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_company_comment(
    admin: AdminDep, session: SessionDep, company_id: UUID, payload: CommentCreate
) -> CommentRead:
    return CommentService(session).add("company", company_id, payload.testo, admin.nome)
