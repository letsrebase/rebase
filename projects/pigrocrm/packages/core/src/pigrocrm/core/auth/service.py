from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.diff import field_changes
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.passwords import dummy_hash, hash_password, verify_password
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import MIN_PASSWORD_LENGTH, UserCreate, UserRead, UserUpdate
from pigrocrm.core.errors import Conflict, DomainError, NotFound, ValidationFailed
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

INVALID_CREDENTIALS = "credenziali non valide"

# The whole account is one timeline: `created`/`updated` here, and the `pat_*` kinds
# `PatService` writes against this same entity. A personal access token is not
# something a user browses as an object of its own -- the question it raises is always
# about an *account* ("who gave an agent the keys to this one, and is that key still
# live?"), so its lifecycle belongs on the owner's timeline rather than on a per-token
# one nobody would think to open. `activities.kind` is an open string by design, which
# is what makes hanging a second family of events off this entity free.
ENTITY = "user"

# What `update` may touch, mirroring `UserUpdate`'s own fields. `ruolo` and `attivo`
# are the two that matter: between them they are the answer to "who made this account
# an administrator" and "who turned this account off", which is the entire reason this
# audit exists. `password_hash` is not here and must never be -- it is not reachable
# through `UserUpdate` at all, and the absence tests pin that.
_AUDITED_FIELDS = ("nome", "ruolo", "attivo")

# `update` refuses a change that would take the space's ability to administer itself
# away. Two refusals, both in service of one invariant -- there is always at least one
# active admin -- and each is its own problem code because they answer differently: the
# first is about the space's state ("somebody else can do this, after you promote or
# reactivate them"), the second is about the actor ("nobody can do this to their own
# account, and asking a bigger admin would be asking yourself").
LAST_ACTIVE_ADMIN = "lo spazio deve avere almeno un amministratore attivo"
SELF_ACCOUNT_CHANGE = (
    "non puoi cambiare ruolo o stato del tuo stesso account: chiedilo a un altro amministratore"
)


class LastActiveAdmin(DomainError):
    code = "last_active_admin"

    def __init__(self, field: str) -> None:
        super().__init__(LAST_ACTIVE_ADMIN, entity="user", field=field, reason=LAST_ACTIVE_ADMIN)


class SelfAccountChange(DomainError):
    """A person changing the privileged fields of their own account, refused whatever
    the headcount is. The count check cannot cover this case: two admins can each lock
    out the other, and a session that demotes or deactivates itself leaves the person
    who did it looking at a screen they cannot reopen. `createadmin` stays the
    operator's way back in, which is why the sentence points at another administrator
    rather than at a retry."""

    code = "self_account_change"

    def __init__(self, field: str) -> None:
        super().__init__(
            SELF_ACCOUNT_CHANGE, entity="user", field=field, reason=SELF_ACCOUNT_CHANGE
        )


def _snapshot(user: User) -> dict[str, object]:
    return {name: getattr(user, name) for name in _AUDITED_FIELDS}


UNVERIFIED_IDENTITY = (
    "prima conferma il tuo indirizzo: entra dal link che ti abbiamo mandato per email"
)


def require_verified_identity(session: Session, actor: Actor, action: str) -> None:
    """An account that never had a password and never used a link by mail is an address
    somebody typed (spec 2026-09-12 §6.4): it may work in its space for the length of an
    access token, and nothing more durable than that. Minting a personal token or
    creating a user would outlive the revocation the first link performs, so both wait
    for the address to be proven. The system and every account with a password or a
    verified address pass."""
    if actor.id is None:
        return
    user = UserRepository(session).get(actor.id)
    if user is None:
        return
    if user.password_hash is None and user.email_verificata_il is None:
        raise ValidationFailed("user", "email", UNVERIFIED_IDENTITY, expected=action)


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = UserRepository(session)
        self.activities = ActivityService(session)

    def create(self, data: UserCreate, actor: Actor) -> UserRead:
        actor.require_admin("create_user")
        require_verified_identity(self.session, actor, "create_user")
        if data.password is None:
            # A user with no password enters with a link by mail (spec 2026-09-12 §6.2).
            # Only the provisioning of a space may create one: an admin adding a
            # colleague still hands them a password, as before.
            if actor.type != "system":
                raise ValidationFailed(
                    "user",
                    "password",
                    "obbligatoria",
                    expected=f">= {MIN_PASSWORD_LENGTH} caratteri",
                )
        elif len(data.password) < MIN_PASSWORD_LENGTH:
            raise ValidationFailed(
                "user",
                "password",
                f"deve avere almeno {MIN_PASSWORD_LENGTH} caratteri",
                expected=f">= {MIN_PASSWORD_LENGTH} caratteri",
            )
        if self.repo.get_by_email(data.email):
            raise Conflict("user", "esiste già un utente con questa email", email=data.email)

        user = User(
            email=data.email,
            password_hash=hash_password(data.password) if data.password is not None else None,
            nome=data.nome,
            ruolo=data.ruolo,
            attivo=True,
            tariffa_oraria_default=data.tariffa_oraria_default,
            costo_orario_default=data.costo_orario_default,
        )
        try:
            self.repo.add(user)
            # `email` and `ruolo` only. The password never appears -- not the plaintext
            # the caller sent, not the argon2 hash stored on the row -- because a
            # timeline entry is read by more people, and kept for longer, than the
            # column it would have been copied from.
            self.activities.record(
                ENTITY, user.id, "created", actor, {"email": user.email, "ruolo": user.ruolo}
            )
            self.session.commit()
        except IntegrityError as exc:
            # The pre-check above cannot cover a race between two concurrent requests:
            # both can pass the SELECT before either has committed. Here the database
            # constraint is the only authority left, and the rollback is mandatory --
            # without it the session stays unusable for whatever the caller does next.
            self.session.rollback()
            raise Conflict(
                "user", "esiste già un utente con questa email", email=data.email
            ) from exc
        return UserRead.model_validate(user)

    def update(self, user_id: UUID, data: UserUpdate, actor: Actor) -> UserRead:
        actor.require_admin("update_user")
        user = self.repo.get(user_id)
        if user is None:
            raise NotFound("user", user_id)
        # Taken before the loop, because `field_changes` below compares it against the
        # object *after* the writes and an entry is recorded only if the two differ.
        before = _snapshot(user)
        # Not listed among task 4B-1's files, converted anyway: leaving two services on
        # `exclude_none` would mean the codebase has two update contracts, which is how
        # A14 survived four slices in the first place. It matters here on its own terms
        # too -- `tariffa_oraria_default` and `costo_orario_default` are nullable, and an
        # unclearable default rate is a number nobody chose staying in force forever.
        #
        # `supplied_changes`, never `model_dump(exclude_none=True)`: under `exclude_none`
        # a field cleared to `null` is indistinguishable from a field the caller never
        # mentioned, so the audit entry would report nothing for exactly the change most
        # worth recording -- somebody removing a default rate.
        changes = supplied_changes(data)
        reject_cleared_columns("user", User, changes)
        # REB-292, both refusals before the first write, and neither outside the two
        # privileged fields: nome and the rate defaults are ordinary edits, whoever
        # makes them. The count check is about THIS row's contribution to the space's
        # admins, so it runs only when the target is an active admin and the patch
        # would stop that: an edit to a readonly row in a space that happens to have
        # zero active admins (only reachable from before this guard existed) must not
        # answer 409 about a change that touches no admin at all.
        # A system actor (the CLI) has no id to compare against, so its writes see
        # only the count rule; `pigrocrm createadmin` stays the operator's way back.
        privileged = sorted({"ruolo", "attivo"} & changes.keys())
        if privileged:
            # The state the row WOULD hold, predicted rather than written: raising
            # after `setattr` would leave the pending change on the session's
            # identity map for whoever reads this row next in the same request.
            final_ruolo = changes.get("ruolo", user.ruolo)
            final_attivo = changes.get("attivo", user.attivo)
            if (
                user.ruolo == "admin"
                and user.attivo
                and (final_ruolo != "admin" or not final_attivo)
            ):
                other_active_admins = self.session.scalar(
                    select(func.count(User.id)).where(
                        User.id != user_id, User.ruolo == "admin", User.attivo.is_(True)
                    )
                )
                if not other_active_admins:
                    raise LastActiveAdmin(privileged[0])
            if actor.id == user_id:
                raise SelfAccountChange(privileged[0])
        for field, value in changes.items():
            setattr(user, field, value)

        # Nothing is recorded when the patch changed nothing: a deactivation that was
        # already in force is not a decision anyone took today. See `field_changes`.
        delta = field_changes(before, _snapshot(user))
        # A deactivation ends the account's agent credentials in the same transaction
        # (REB-295): `resolve()` already refuses a token whose owner is inactive, but
        # refusing on every call left the rows live -- reactivating the account would
        # silently hand the old tokens back. Revoking here makes the decision final:
        # an agent has to be re-keyed by somebody, on purpose. Keyed to the real
        # delta, not to the patch, so a no-op edit to an already-inactive user
        # revokes nothing (same line the `updated` entry below draws). A demotion is
        # NOT here on purpose: a demoted owner's tokens keep working at the new role,
        # because `resolve()` carries the CURRENT role on every request.
        # `pat_service` imports this module, so the import is local -- the same
        # cycle-avoidance `templates/service.py` applies to `gmail`.
        if "attivo" in delta.get("changed", []) and not user.attivo:
            from pigrocrm.core.auth.pat_service import PatService

            PatService(self.session).revoke_all_for(user.id, actor)
        if delta:
            self.activities.record(
                ENTITY, user.id, "updated", actor, {"email": user.email, **delta}
            )
        self.session.commit()
        return UserRead.model_validate(user)

    def update_own_digest(self, actor: Actor, value: bool) -> UserRead:
        """No `actor.require_admin` here, deliberately -- `update` above gates every
        write on it, but whether the weekly digest reaches a person is not an
        administrator's decision about them, it is theirs. Spec 2026-09-16 §3.6: the
        mail's own opt-out link must work for whoever received the mail, not only for
        an admin of the space, which is also why this takes `actor` rather than a
        `user_id` -- there is no parameter through which it could touch anyone else's
        row.

        Recorded on the same timeline `update` writes to, with the same "updated"
        kind, but a payload of the flag alone -- unlike `update`'s own entry, this
        carries no email: nothing else about whose row this is needs repeating on a
        timeline the owner already knows is theirs.

        A value that is already the one stored writes nothing at all. The switch is a
        control a person clicks twice to see what it does, and every browser that
        re-sends the form it just sent would otherwise fill a timeline with entries
        recording that nothing changed -- `update` above draws the same line with its
        `delta`, and this is that line for a single field. The commit stays outside the
        guard: it costs nothing when there is nothing to write and it releases the read
        transaction `repo.get` opened.
        """
        if actor.id is None:
            raise NotFound("user", "anonimo")
        user = self.repo.get(actor.id)
        if user is None:
            raise NotFound("user", actor.id)
        if user.digest_settimanale != value:
            user.digest_settimanale = value
            self.activities.record(ENTITY, user.id, "updated", actor, {"digest_settimanale": value})
        self.session.commit()
        return UserRead.model_validate(user)

    def reset_password(self, email: str, password: str, actor: Actor) -> UserRead:
        """A new password for an existing account, set by an admin -- in practice by the
        operator at the server's terminal (`pigrocrm resetpassword`), since the product has
        no e-mail flow to hand a link to anyone. Same floor as `create`; the timeline
        records that it happened and for whom, never what was set."""
        actor.require_admin("reset_password")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationFailed(
                "user",
                "password",
                f"deve avere almeno {MIN_PASSWORD_LENGTH} caratteri",
                expected=f">= {MIN_PASSWORD_LENGTH} caratteri",
            )
        user = self.repo.get_by_email(email)
        if user is None:
            raise NotFound("user", email)
        user.password_hash = hash_password(password)
        self.activities.record(ENTITY, user.id, "password_reset", actor, {"email": user.email})
        self.session.commit()
        return UserRead.model_validate(user)

    def list(self, actor: Actor) -> list[UserRead]:
        actor.require_admin("list_users")
        return [UserRead.model_validate(u) for u in self.repo.list_all()]

    def count(self) -> int:
        return self.repo.count()

    def authenticate(self, email: str, password: str) -> UserRead:
        user = self.repo.get_by_email(email)
        # Compare against a precomputed constant hash when the user is missing or has
        # never had a password (a link-by-mail account, spec 2026-09-12 §6.2), so every
        # path costs exactly one verify and timing does not reveal which case it was.
        stored = user.password_hash if user is not None else None
        reference = stored if stored is not None else dummy_hash()
        ok = verify_password(password, reference)
        if user is None or stored is None or not ok or not user.attivo:
            raise ValidationFailed("user", "credentials", INVALID_CREDENTIALS)
        # `last_login_at` is written here and nowhere else on this path (REB-297): the
        # moment a password actually opens a session, not the moment credentials were
        # merely checked -- the branch above already returned for every way this call
        # fails.
        user.last_login_at = datetime.now(UTC)
        self.session.commit()
        return UserRead.model_validate(user)
