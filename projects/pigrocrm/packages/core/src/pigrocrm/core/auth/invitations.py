"""A space gains people by invitation (spec 2026-09-17).

`InvitationService` sits beside `MagicLinkService` and `PatService` and imitates both:
the token is the magic link's own shape (SHA-256 of a `token_urlsafe(32)`, spent with a
conditional `UPDATE` so two racing clicks create exactly one user), and the durability
is a PAT's (it outlives the session that minted it, so the minter must have proven
their own address first).

`create` and `resend` answer the raw token; the caller builds the URL and the mail,
because only the router knows which prefix and which public origin the link must wear
-- the same seam `MagicLinkService.request` documents.

Unlike the magic link, whose three failure states are folded into one sentence to
close an oracle (`routers/auth.py::INVALID_LINK`), an invitation's are told apart:
«scaduto», «revocato» and «già usato» read differently to an admin who just revoked
one and to a person who let one sit for eight days, and the guess this guards against
-- whether a 32-byte random token exists -- has nothing behind it. `revoked_at` and
`accepted_at` staying on the row, never deleted, is what makes that possible after
the fact.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.invitation_models import Invitation
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import (
    InvitationCreate,
    InvitationPeek,
    InvitationRead,
    UserCreate,
    UserRead,
)
from pigrocrm.core.auth.service import UserService, require_verified_identity
from pigrocrm.core.errors import Conflict, DomainError, NotFound, ValidationFailed

# Seven days, not fifteen minutes: an invitation is handed to someone who may not open
# their mail until tomorrow, and revocation, not a short fuse, is what takes back one
# sent to the wrong address (spec §1).
INVITATION_TTL_DAYS = 7

# The whole account story of an invited person stays on the `user` timeline
# (`auth/service.py::ENTITY`); the invitation's own lifecycle is what *this* entity is
# for, keyed on the row's own id: created, resent, revoked, accepted.
ENTITY = "invitation"

PENDING = Invitation.accepted_at.is_(None) & Invitation.revoked_at.is_(None)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class InvitationUnknown(DomainError):
    """No row carries this token's hash. 404 (`invitation_unknown` in
    `pigrocrm_api/errors.STATUS_BY_CODE`): the request presented a credential that is
    not one, and no row ever was. Unlike the other three dead states this one is not
    an invitation that ended -- there is nothing to tell apart from a wrong token."""

    code = "invitation_unknown"

    def __init__(self) -> None:
        super().__init__(
            "questo invito non esiste: chiedi a chi amministra lo spazio di rimandarlo"
        )


class InvitationExpired(DomainError):
    code = "invitation_expired"

    def __init__(self) -> None:
        super().__init__("questo invito è scaduto: chiedi a chi amministra lo spazio di rimandarlo")


class InvitationRevoked(DomainError):
    code = "invitation_revoked"

    def __init__(self) -> None:
        super().__init__(
            "questo invito è stato revocato: chiedi a chi amministra lo spazio di rimandarlo"
        )


class InvitationUsed(DomainError):
    code = "invitation_used"

    def __init__(self) -> None:
        super().__init__("questo invito è già stato usato: entra con la tua email")


class InvitationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.activities = ActivityService(session)

    # ---- admin surface ---------------------------------------------------------

    def create(self, data: InvitationCreate, actor: Actor) -> tuple[InvitationRead, str]:
        """The pending invitation and its raw token, or `Conflict`: for an address
        that already has an *active* user in the space, or for an invitation still
        open and not yet expired. An expired open row is rewritten in place instead
        (new token, new week), which is why the re-invite must not answer 409 for it
        even though the partial unique index still covers that row.

        The address claim is checked here before any row is written; the index is the
        race guard behind the read, the same shape `UserService.create` uses."""
        actor.require_admin("invite_user")
        require_verified_identity(self.session, actor, "invite_user")
        email = data.email
        now = datetime.now(UTC)
        # `invited_by` is a real FK: a system actor has no id to leave as the trail,
        # and an invitation is something a person sends, never the provisioner.
        if actor.id is None:
            raise ValidationFailed("invitation", "invited_by", "serve una persona che inviti")
        existing_user = self.users.get_by_email(email)
        if existing_user is not None and existing_user.attivo:
            raise Conflict(
                "invitation",
                "esiste già una persona attiva con questo indirizzo nello spazio",
                email=email,
            )
        open_row = self.session.scalar(select(Invitation).where(Invitation.email == email, PENDING))
        if open_row is not None and open_row.expires_at > now:
            raise Conflict(
                "invitation", "esiste già un invito in attesa per questa email", email=email
            )
        raw = secrets.token_urlsafe(32)
        expires_at = now + timedelta(days=INVITATION_TTL_DAYS)
        if open_row is not None:
            # Expired: overwrite in place exactly as a resend does.
            open_row.email = email
            open_row.nome = data.nome
            open_row.ruolo = data.ruolo
            open_row.token_hash = _hash(raw)
            open_row.invited_by = actor.id
            open_row.expires_at = expires_at
            row = open_row
        else:
            row = Invitation(
                email=email,
                nome=data.nome,
                ruolo=data.ruolo,
                token_hash=_hash(raw),
                invited_by=actor.id,
                expires_at=expires_at,
            )
            self.session.add(row)
        try:
            self.session.flush()
            self.activities.record(
                ENTITY, row.id, "created", actor, {"email": row.email, "ruolo": row.ruolo}
            )
            self.session.commit()
        except IntegrityError as exc:
            # Two concurrent invites of the same address: the partial index is the
            # only authority left, and the rollback is mandatory for the session.
            self.session.rollback()
            raise Conflict(
                "invitation", "esiste già un invito in attesa per questa email", email=email
            ) from exc
        return InvitationRead.model_validate(row), raw

    def list(self, actor: Actor) -> list[InvitationRead]:
        """«Inviti in attesa»: open *and* not yet expired (an expired one is dead; a
        resend or a fresh invite revives it), newest first."""
        actor.require_admin("list_invites")
        rows = self.session.scalars(
            select(Invitation)
            .where(PENDING, Invitation.expires_at > datetime.now(UTC))
            # `id` is UUIDv7: time-ordered, so it breaks the tie between two rows
            # written in one instant without an unstable `created_at` sort.
            .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        ).all()
        return [InvitationRead.model_validate(r) for r in rows]

    def resend(self, invitation_id: UUID, actor: Actor) -> tuple[InvitationRead, str]:
        """A new token on the same row, a week measured from now, the old raw value
        dead the instant the hash it matched is gone -- no separate revocation
        bookkeeping (`MagicLinkService.request` does not distinguish a first request
        from a repeat one either). Another space's id, an unknown id, or a terminal
        row are all 404: there is nothing to resend, and the reason is not the
        caller's business."""
        actor.require_admin("resend_invite")
        require_verified_identity(self.session, actor, "invite_user")
        row = self._pending_row(invitation_id)
        raw = secrets.token_urlsafe(32)
        row.token_hash = _hash(raw)
        row.expires_at = datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS)
        self.activities.record(ENTITY, row.id, "resent", actor, {"email": row.email})
        self.session.commit()
        return InvitationRead.model_validate(row), raw

    def revoke(self, invitation_id: UUID, actor: Actor) -> None:
        """Sets `revoked_at` and nothing else; the row stays, because a revoked
        invitation is exactly the record an admin needs to see was undone. 404 for a
        terminal row: there is nothing left to take back."""
        actor.require_admin("revoke_invite")
        row = self._pending_row(invitation_id)
        row.revoked_at = datetime.now(UTC)
        self.activities.record(ENTITY, row.id, "revoked", actor, {"email": row.email})
        self.session.commit()

    # ---- invitee surface (unauthenticated) --------------------------------------

    def peek(self, raw: str, *, spazio: str) -> InvitationPeek:
        """What the acceptance page shows before spending: the space, who invited,
        the name the invitation carried (or `None`, and the page asks for one).
        Never touches `accepted_at` -- reading must not be able to kill the link.
        `spazio` is the caller's: only the router knows the prefix it is serving."""
        row = self._live_row(raw)
        inviter = self.users.get(row.invited_by)
        return InvitationPeek(
            spazio=spazio,
            invitato_da=inviter.nome if inviter is not None else "",
            nome=row.nome,
        )

    def accept(self, raw: str, nome: str | None) -> UserRead:
        """Spends the token and opens the account: creates the active user with the
        invitation's role, sets `email_verificata_il` (the click proved the address,
        same as the magic link's first entry), and audits the whole thing.

        This is not one transaction and deliberately so (spec §8): `UserService.create`
        commits on its own and rolls the session back on conflict, and `record` must be
        the last thing before a commit. So the writes are ordered around that commit --
        first the duplicate read, then `create` (its commit is the point of no return
        for the user row), then, in a second commit, the conditional `UPDATE` that
        spends the token, `email_verificata_il`, the two activity rows. A `create` that
        raises `Conflict` leaves `accepted_at` untouched, so the link still works once
        the admin sorts the duplicate out.

        The conditional `UPDATE` is what makes two racing clicks create exactly one
        user: both pass the reads, `UserService.create`'s own unique email index lets
        one win, and this is the belt behind that brace. `nome` is required when the
        invitation carried none; an empty effective name never reaches the column.
        """
        row = self._live_row(raw)
        chosen = (nome or row.nome or "").strip()
        if not chosen:
            raise ValidationFailed(
                "invitation", "nome", "obbligatorio quando l'invito non porta un nome"
            )
        user = UserService(self.session).create(
            UserCreate(email=row.email, password=None, nome=chosen, ruolo=row.ruolo),
            Actor.system(),
        )
        now = datetime.now(UTC)
        spent = self.session.execute(
            update(Invitation)
            .where(Invitation.id == row.id, PENDING)
            .values(accepted_at=now)
            .returning(Invitation.id)
        )
        if len(spent.scalars().all()) != 1:
            self.session.rollback()
            raise InvitationUsed
        fresh = self.session.get(User, user.id)
        # `create` cannot set it (`UserCreate` has no such field) and the row is already
        # committed, so this lands in the second commit alongside the spend.
        if fresh is not None and fresh.email_verificata_il is None:
            fresh.email_verificata_il = now
        self.activities.record(ENTITY, row.id, "accepted", Actor.system(), {"email": row.email})
        self.activities.record(
            "user", user.id, "invited", Actor.system(), {"invited_by": row.invited_by}
        )
        self.session.commit()
        return user

    # ---- shared reads -----------------------------------------------------------

    def _pending_row(self, invitation_id: UUID) -> Invitation:
        row = self.session.get(Invitation, invitation_id)
        if row is None or row.accepted_at is not None or row.revoked_at is not None:
            raise NotFound("invitation", invitation_id)
        return row

    def _live_row(self, raw: str) -> Invitation:
        """The row for a spendable token, with the three dead states named apart."""
        if not raw:
            raise InvitationUnknown
        row = self.session.scalar(select(Invitation).where(Invitation.token_hash == _hash(raw)))
        if row is None:
            raise InvitationUnknown
        if row.accepted_at is not None:
            raise InvitationUsed
        if row.revoked_at is not None:
            raise InvitationRevoked
        if row.expires_at <= datetime.now(UTC):
            raise InvitationExpired
        return row
