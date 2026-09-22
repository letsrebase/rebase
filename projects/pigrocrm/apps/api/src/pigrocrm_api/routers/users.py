from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.auth.invitations import INVITATION_TTL_DAYS, InvitationService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import (
    InvitationCreate,
    InvitationRead,
    UserCreate,
    UserRead,
    UserUpdate,
)
from pigrocrm.core.auth.service import UserService
from pigrocrm.core.config import Settings
from pigrocrm.core.mail import Mail, invitation_mail
from pigrocrm.core.timetracking.schemas import UserRatesUpdate
from pigrocrm.core.timetracking.service import TimeEntryService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.sessions import SenderDep, mail_origin
from pigrocrm_api.tenancy import tenant_slug

router = APIRouter(prefix="/api/users", tags=["users"], responses=PROBLEM_RESPONSES)


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create(data: UserCreate, session: SessionDep, actor: ActorDep) -> UserRead:
    return UserService(session).create(data, actor)


@router.get("", response_model=list[UserRead])
def list_users(session: SessionDep, actor: ActorDep) -> list[UserRead]:
    return UserService(session).list(actor)


# ---- invitations (spec 2026-09-17, REB-290) -----------------------------------------
#
# These belong on this router, which already owns the `/api/users` prefix: a second
# `APIRouter` under the same path would only add ceremony (spec §3). The public half of
# the flow (the peek and the accept, `GET`/`POST /api/auth/invite`) lives in
# `routers/auth.py`, beside the link-by-mail routes of the same shape.

NO_MAIL_SENDER = (
    "Questa installazione non ha un mittente email configurato (PIGROCRM_RESEND_API_KEY): "
    "non può mandare inviti."
)


def _invite_url(origin: str, prefix: str, raw: str) -> str:
    return f"{origin}{prefix}/app/invite?t={raw}"


def _invitation_mail(
    request: Request,
    settings: Settings,
    origin: str,
    email: str,
    invitato_da: str,
    nome: str | None,
    raw: str,
) -> Mail:
    """The mail an invitation carries (spec §5): same construction as the link by mail,
    the same origin rule (never the request's `Host`), the same space naming as the
    link's own label. The page it points at is REB-291's; the URL shape is the contract
    it reads. `origin` is resolved by each route before its row is committed: an
    installation that cannot build a link must not half-create an invitation over it."""
    slug = tenant_slug(request)
    spazio = slug or settings.root_slug or "PigroCRM"
    url = _invite_url(origin, f"/{slug}" if slug else "", raw)
    return invitation_mail(email, spazio, invitato_da, url, nome=nome, giorni=INVITATION_TTL_DAYS)


@router.post("/invites", response_model=InvitationRead, status_code=status.HTTP_201_CREATED)
def create_invite(
    data: InvitationCreate,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> InvitationRead:
    """Invite a person into the space (spec 2026-09-17): the row is written first, the
    mail leaves after the response -- never before, and never inside a transaction that
    could still roll back, exactly like `request_link`. 409 for an address that already
    has an active user here, or one with an open, unexpired invitation. Admin-only and
    behind `require_verified_identity` (action `"invite_user"`): an address nobody ever
    proved mints no durable credential for anybody else. 503 while no sender is
    configured: an invitation nobody can receive is not half-created instead, and the
    raw token is never answered, so there is no way to hand the link over by hand."""
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_MAIL_SENDER)
    # Resolved before the row: without a public origin nothing is created at all.
    origin = mail_origin(settings)
    inviter = UserRepository(session).get(actor.id) if actor.id is not None else None
    if inviter is None:  # pragma: no cover - an ActorDep actor always resolves
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utente non trovato")
    invite, raw = InvitationService(session).create(data, actor)
    background.add_task(
        sender.send,
        _invitation_mail(request, settings, origin, invite.email, inviter.nome, invite.nome, raw),
    )
    return invite


@router.get("/invites", response_model=list[InvitationRead])
def list_invites(session: SessionDep, actor: ActorDep) -> list[InvitationRead]:
    """«Inviti in attesa»: open and not yet expired, newest first (spec §3)."""
    return InvitationService(session).list(actor)


@router.post("/invites/{invitation_id}/resend", response_model=InvitationRead)
def resend_invite(
    invitation_id: UUID,
    request: Request,
    background: BackgroundTasks,
    session: SessionDep,
    actor: ActorDep,
    settings: SettingsDep,
    sender: SenderDep,
) -> InvitationRead:
    """A fresh token on the same row and a week measured from now; the old link dies
    the instant the hash it matched is gone. 404 for a row that is not pending, or not
    this space's. The guard runs before the write: a resend into an installation that
    cannot mail would spend the old token to produce a link nobody receives."""
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_MAIL_SENDER)
    origin = mail_origin(settings)
    invite, raw = InvitationService(session).resend(invitation_id, actor)
    inviter = UserRepository(session).get(invite.invited_by)
    background.add_task(
        sender.send,
        _invitation_mail(
            request,
            settings,
            origin,
            invite.email,
            inviter.nome if inviter else "",
            invite.nome,
            raw,
        ),
    )
    return invite


@router.delete("/invites/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(invitation_id: UUID, session: SessionDep, actor: ActorDep) -> None:
    """Revokes an open invitation: `revoked_at`, the row stays, the link stops working
    at once. 404 for a terminal row (there is nothing left to take back)."""
    InvitationService(session).revoke(invitation_id, actor)


@router.patch("/{user_id}", response_model=UserRead)
def update(user_id: UUID, data: UserUpdate, session: SessionDep, actor: ActorDep) -> UserRead:
    return UserService(session).update(user_id, data, actor)


@router.put("/{user_id}/rates", status_code=status.HTTP_204_NO_CONTENT)
def set_user_rates(
    user_id: UUID, data: UserRatesUpdate, session: SessionDep, actor: ActorDep
) -> None:
    """On `TimeEntryService`, not on `UserService`: see the method's own docstring and
    "Contradictions" item 4 -- §11 fixes the audited surface to three service classes
    and this is one of the ten names that must be excluded from MCP by name."""
    TimeEntryService(session).update_user_rates(user_id, data, actor)


@router.get("/{user_id}/timeline", response_model=list[ActivityRead])
def timeline(
    user_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    """The account's whole story: created, renamed, promoted, deactivated -- and every
    personal access token issued, first used, revoked, or still being presented after
    revocation, which `PatService` records against the owning user rather than against
    a per-token entity nobody would think to open.

    Readable by the owner as well as by an administrator, unlike every other endpoint
    on this router. An administrator is the only one who can *change* an account, but
    the owner is the one who needs to see that a token they thought was dead is still
    being tried -- refusing them that would make the alarm useless to the only person
    who can act on it. Anyone else needs to be an administrator: who holds which
    tokens on which account is not a collaborator's business.
    """
    if actor.id != user_id:
        actor.require_admin("read_user_timeline")
    return ActivityService(session).timeline("user", user_id, limit)
