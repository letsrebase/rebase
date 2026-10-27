import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor, Role
from pigrocrm.core.auth.models import User
from pigrocrm.core.auth.pat_models import PersonalAccessToken
from pigrocrm.core.auth.scope import scope_of
from pigrocrm.core.auth.service import ENTITY as USER_ENTITY
from pigrocrm.core.auth.service import require_verified_identity
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed

PAT_PREFIX = "pgc_"
PREFIX_VISIBLE_CHARS = 8
# Used for every way `resolve()` can fail -- unknown token, revoked token, or a token
# whose owning user was deactivated -- so the three are indistinguishable from the
# outside. A leaked PAT must not double as an oracle for "is this still worth using":
# the same discipline `UserService.authenticate` already applies with its dummy hash,
# here applied to error content instead of timing.
INVALID_TOKEN = "token non valido o revocato"

# Every PAT event is recorded against its owning *user*, never against the token --
# see `UserService.ENTITY` for why. What identifies the token inside the payload is
# its row id and the name its owner gave it, and nothing else: not the raw value
# (which exists for exactly one function call and is never stored), not the prefix,
# and above all not `token_hash`, which is the lookup key `resolve()` matches on and
# therefore is the credential for every practical purpose. A timeline is read by more
# people, kept longer and exported more casually than the `personal_access_tokens`
# table it describes; nothing that narrows a search for the token belongs in it.
# `test_pat_audit_never_leaks_the_secret` holds this to the same standard the rest of
# the token handling is held to.


def _audit_payload(record: PersonalAccessToken) -> dict[str, object]:
    return {"token_id": record.id, "nome": record.nome, "full_access": record.full_access}


class PatRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    prefix: str
    # What the owner chose for this token (REB-810). The token reaches the privileged
    # surface only where the space's own switch is open too, which `resolve` decides.
    full_access: bool
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TeamPatRead(PatRead):
    """A colleague's live token as an administrator sees it: whose it is, so that the one
    that should not reach the invoice tools can be closed without asking its owner."""

    user_id: UUID
    owner_nome: str


def _digest(raw: str) -> str:
    """SHA-256, not argon2: lookup is by hash, so it must be deterministic. Safe here
    because the token is 32 random bytes, not a human-chosen password."""
    return hashlib.sha256(raw.encode()).hexdigest()


class PatService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.activities = ActivityService(session)
        # Read here rather than at the point of use, so that what a credential may do is
        # decided once, where the credential is built. A caller may pass its own
        # `Settings` -- a test declaring an installation, chiefly -- instead of
        # inheriting whatever `.env` the process happened to load.
        self.settings = settings or get_settings()

    def create(self, nome: str, actor: Actor, *, full_access: bool = False) -> tuple[PatRead, str]:
        if actor.id is None:
            raise ValidationFailed("token", "actor", "serve un utente autenticato")
        # A token outlives the revocation the first link by mail performs, so an
        # address nobody has proven yet cannot mint one (spec 2026-09-12 §6.4).
        require_verified_identity(self.session, actor, "create_token")
        if full_access:
            # A token that opens the privileged surface is a person's decision, and an
            # agent credential can mint tokens over REST like any other write: without
            # this, the token a person kept closed could mint an open one for itself.
            actor.require_person("create_full_access_token")
            self._require_ceiling_open()

        raw = PAT_PREFIX + secrets.token_urlsafe(32)
        record = PersonalAccessToken(
            user_id=actor.id,
            nome=nome,
            token_hash=_digest(raw),
            prefix=raw[: len(PAT_PREFIX) + PREFIX_VISIBLE_CHARS],
            full_access=full_access,
        )
        self.session.add(record)
        try:
            # Flushed explicitly so that `record.id` exists for the audit payload, and
            # so that a collision surfaces here -- inside the try -- rather than from
            # the audit write that follows it. The entry is written last and before the
            # commit, so the rollback below discards it together with the token it
            # would otherwise claim was issued.
            self.session.flush()
            self.activities.record(
                USER_ENTITY, record.user_id, "pat_created", actor, _audit_payload(record)
            )
            self.session.commit()
        except IntegrityError as exc:
            # token_hash is unique=True. A collision on 32 random bytes is
            # astronomically unlikely -- there is no pre-check here, unlike
            # UserService's email conflict, because one would only add ceremony
            # without meaningfully reducing the risk. But the constraint (not this
            # comment) is what actually guarantees uniqueness, so the same rollback
            # discipline as UserService.create still applies: without it, the
            # session is left poisoned for whatever the caller does next.
            self.session.rollback()
            raise Conflict(
                "personal_access_token", "collisione imprevista sul token, riprova"
            ) from exc
        # The raw value is returned exactly once and never stored.
        return PatRead.model_validate(record), raw

    def revoke(self, pat_id: UUID, actor: Actor) -> None:
        stmt = select(PersonalAccessToken).where(
            PersonalAccessToken.id == pat_id, PersonalAccessToken.user_id == actor.id
        )
        record = self.session.execute(stmt).scalar_one_or_none()
        if record is None:
            raise NotFound("personal_access_token", pat_id)
        record.revoked_at = datetime.now(UTC)
        self.activities.record(
            USER_ENTITY, record.user_id, "pat_revoked", actor, _audit_payload(record)
        )
        self.session.commit()

    def _require_ceiling_open(self) -> None:
        """A token cannot be given what its space does not offer. Stored closed instead
        of refused it would be a flag nobody can see doing anything, and the day an
        administrator opens the switch every such token would open with it."""
        if not self.settings.mcp_full_access:
            raise ValidationFailed(
                "token",
                "full_access",
                "l'accesso completo è chiuso per questo spazio: lo apre un amministratore "
                "in Impostazioni → Spazio",
            )

    def set_full_access(self, pat_id: UUID, full_access: bool, actor: Actor) -> PatRead:
        """Opens or closes one token's privileged surface (REB-810). Its owner may, and so
        may an administrator, who is the one asked to close a token that should not be
        reaching the invoice tools. No agent credential may, not even the owner's own
        token: a token that could open itself would make the choice the owner made
        meaningless, so this is refused before the role is looked at (`require_person`).

        Closing is always allowed; opening needs the space's switch open (the ceiling). A
        revoked token has nothing left to decide. The change is recorded against the
        token's owner, with the actor who made it, so the timeline says whether the owner
        or an administrator did it. A call that changes nothing records nothing.
        """
        actor.require_person("set_token_full_access")
        record = self.session.get(PersonalAccessToken, pat_id)
        if record is None:
            raise NotFound("personal_access_token", pat_id)
        if record.user_id != actor.id:
            # Another member's token is account administration, like changing the member
            # (`UserService.update`): a scoped admin has the role and not the whole space.
            actor.require_unscoped_admin("set_token_full_access")
        if record.revoked_at is not None:
            raise Conflict("personal_access_token", "token revocato: non si può più modificare")
        if record.full_access == full_access:
            return PatRead.model_validate(record)
        if full_access:
            self._require_ceiling_open()
        record.full_access = full_access
        self.session.flush()
        self.activities.record(
            USER_ENTITY,
            record.user_id,
            "pat_full_access_changed",
            actor,
            {**_audit_payload(record), "full_access_prima": not full_access},
        )
        self.session.commit()
        return PatRead.model_validate(record)

    def list_team(self, actor: Actor) -> list[TeamPatRead]:
        """The live tokens of the members other than the caller, for an administrator."""
        actor.require_unscoped_admin("list_team_tokens")
        stmt = (
            select(PersonalAccessToken, User.nome)
            .join(User, User.id == PersonalAccessToken.user_id)
            .where(
                PersonalAccessToken.revoked_at.is_(None),
                PersonalAccessToken.user_id != actor.id,
            )
            .order_by(User.nome, PersonalAccessToken.created_at.desc())
        )
        return [
            TeamPatRead(
                **PatRead.model_validate(record).model_dump(),
                user_id=record.user_id,
                owner_nome=nome,
            )
            for record, nome in self.session.execute(stmt)
        ]

    def revoke_all_for(self, user_id: UUID, actor: Actor) -> None:
        """Ends every live token of one user, inside the caller's transaction: flushes,
        never commits. That is `ActivityService.record`'s contract extended to the rows
        themselves -- the revocation and the change that triggered it (today exactly
        one: the deactivation in `UserService.update`) must survive or roll back as one
        unit, or a rolled-back deactivation would have silently burned the account's
        credentials on its way out.

        The audit trail mirrors `revoke`'s: one `pat_revoked` entry per token, recorded
        against the owning user, carrying the ids and nothing that narrows a search for
        the token value.
        """
        now = datetime.now(UTC)
        rows = self.session.scalars(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user_id,
                PersonalAccessToken.revoked_at.is_(None),
            )
        ).all()
        for record in rows:
            record.revoked_at = now
            self.activities.record(
                USER_ENTITY, record.user_id, "pat_revoked", actor, _audit_payload(record)
            )
        self.session.flush()

    def resolve(self, raw_token: str) -> Actor:
        stmt = select(PersonalAccessToken).where(
            PersonalAccessToken.token_hash == _digest(raw_token)
        )
        record = self.session.execute(stmt).scalar_one_or_none()
        if record is None:
            raise ValidationFailed("token", "token", INVALID_TOKEN)
        if record.revoked_at is not None:
            self._record_use_after_revocation(record)
            raise ValidationFailed("token", "token", INVALID_TOKEN)

        # `populate_existing` forces a fresh read of the owner even when this session
        # already holds it: the role and `attivo` that build the Actor below are the
        # CURRENT ones, never a copy a previous `resolve` cached. Over HTTP each
        # request opens its own session and reads fresh anyway; the flag makes that
        # true for every caller, which is the guarantee hub's `admin_tokens.resolve`
        # was given in REB-278 (#225) and the shape copied here. The behavior is
        # pinned by `test_a_second_resolve_on_the_same_session_sees_the_current_role`.
        user = self.session.get(User, record.user_id, populate_existing=True)
        if user is None or not user.attivo:
            # Same message and details as above, on purpose -- see INVALID_TOKEN.
            raise ValidationFailed("token", "token", INVALID_TOKEN)

        # Captured before the assignment below overwrites it: `None` here means this
        # token has never been presented before, which is the moment the credential
        # stopped being a string in a config file and became a live key to the CRM.
        first_use = record.last_used_at is None
        record.last_used_at = datetime.now(UTC)

        role: Role = user.ruolo  # type: ignore[assignment]
        # type="mcp": a PAT identifies an agent, which is what makes the timeline honest.
        #
        # `full_access` is stamped here, and this is the only place it is decided. It
        # travels on the actor rather than being consulted at the point of use, so a REST
        # request presenting this token behaves exactly like the MCP transport -- the
        # asymmetry that once let a `curl` issue an invoice while the tool was
        # unregistered. Two things must be open (REB-810): the token's own flag, chosen by
        # its owner and closed unless asked for, and the space's `mcp_full_access`, which
        # is the ceiling: an installation that never opened it has no privileged surface
        # at all, whatever a token says. See `AGENT_FORBIDDEN_ACTIONS` for what that
        # opens and why it is a decision about a machine rather than about a role.
        resolved = Actor(
            id=user.id,
            type="mcp",
            role=role,
            full_access=record.full_access and self.settings.mcp_full_access,
            # The owner's scope travels on the credential (REB-634): an agent sees the
            # aziende its person sees, read fresh here like the role.
            aziende=scope_of(user),
        )
        # Only the *first* use is recorded, not every one. `resolve()` runs on every
        # single request an agent makes, so an entry per call would double the write
        # volume of the whole API and bury the account timeline under thousands of rows
        # that all say the same thing. Nothing is lost: `last_used_at` already answers
        # "is this token still in use", and every change the agent then makes writes
        # its own activity with `actor_type="mcp"`. What was genuinely missing -- and
        # is recorded here -- is the transition from issued to in-use.
        if first_use:
            self.activities.record(
                USER_ENTITY, record.user_id, "pat_first_used", resolved, _audit_payload(record)
            )
        self.session.commit()
        return resolved

    def _record_use_after_revocation(self, record: PersonalAccessToken) -> None:
        """Someone presented a token that had already been revoked. That is the single
        most informative event in this file: a revoked credential still being tried is
        either an agent nobody reconfigured or a copy of the token somewhere its owner
        did not intend, and neither is visible anywhere today.

        Recorded once per revocation, not once per attempt, and `last_used_at` is what
        makes that possible without adding a column: it means "when this token was last
        presented", which is just as true of a rejected attempt as of an accepted one.
        Once it has been moved past `revoked_at`, further attempts find it already there
        and stay silent. Without that guard, anyone holding the revoked value could grow
        the table one row per request.

        The guard is a read-modify-write, so two attempts racing each other can both
        pass it and produce two rows. That is deliberate, not overlooked: taking a row
        lock on every rejected token to save one duplicate entry would put a write lock
        in the path of a credential anyone can present. The property that matters is
        bounded growth -- once either commit lands, every later attempt is silent -- and
        a duplicated alarm is a harmless way to fail compared to a suppressed one.

        The actor is `system`: the platform observed this, and honestly does not know
        who presented the token -- claiming it was the owner would be a worse answer
        than `actor_id = NULL`. The commit is this method's own, because the caller
        raises immediately afterwards and an audit entry that only survives when the
        request succeeds would never record a rejection at all.
        """
        already_recorded = (
            record.last_used_at is not None
            and record.revoked_at is not None
            and record.last_used_at > record.revoked_at
        )
        if already_recorded:
            return
        record.last_used_at = datetime.now(UTC)
        self.activities.record(
            USER_ENTITY,
            record.user_id,
            "pat_used_after_revocation",
            Actor.system(),
            {**_audit_payload(record), "revoked_at": record.revoked_at},
        )
        self.session.commit()

    # `list` is the last method in this class on purpose, and must stay that way. A
    # method named `list` rebinds that name in the *class* namespace, so any method
    # defined below it whose return annotation is a bare `list[...]` would resolve
    # `list` to this method instead of the builtin and fail at import time with
    # `TypeError: 'function' object is not subscriptable`. It used to sit in the
    # middle of this class, which was survivable only because nothing below it
    # happened to be annotated that way; adding `_record_use_after_revocation` above
    # is what made keeping the rule cheaper than remembering the exception.
    # `test_module_imports.py` is the real guard.
    def list(self, actor: Actor) -> list[PatRead]:
        stmt = (
            select(PersonalAccessToken)
            .where(PersonalAccessToken.user_id == actor.id)
            .order_by(PersonalAccessToken.created_at.desc())
        )
        return [PatRead.model_validate(r) for r in self.session.execute(stmt).scalars()]
