"""Cross-space identity, proven passwordlessly, one layer above any single space's own
session (design 2026-09-23, REB-345/376).

Two mechanisms, deliberately not one: `request`/`enter` spend a mailed link against
`identities` instead of `users` -- `MagicLinkService`'s own shape, one layer up, for
proving an address nobody has vouched for yet. `upsert_and_issue` is the side effect
three existing entry points (`login`, `enter_with_link`, `accept_invite`,
`apps/api/src/pigrocrm_api/routers/auth.py`) ride on: it records a proof that already
happened one call away, and never asks for one of its own. `signup` is deliberately
not a fourth call site (§2) -- an address nobody has proven anything about must not get
a durable, cross-space cookie. `resolve` is the read the chooser gates on (design §3,
REB-377): the "checked-on-every-read" liveness §2 describes, one more `SELECT` like
`RefreshTokenService`'s own revocation check.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.auth.tokens import issue_identity_token
from pigrocrm.core.config import Settings
from pigrocrm.core.identity.models import Identity, IdentityLinkToken, IdentitySession
from pigrocrm.core.identity.schemas import IdentityRead


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class IdentityService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _get_by_email(self, email: str) -> Identity | None:
        """`email` must already be normalised (`.strip().lower()`) by the caller --
        every write path here normalises before it stores, the same discipline
        `UserRepository.get_by_email` follows, so a plain equality is enough and the
        functional unique index is only defense in depth against a caller that
        forgets."""
        return self.session.scalar(select(Identity).where(Identity.email == email))

    # ---- proving an address nobody has vouched for yet --------------------------

    def request(self, email: str) -> str | None:
        """The raw link token for an identity that already exists, or `None`: unlike
        `upsert_and_issue`, this never creates the row -- there is nothing yet to
        prove entry into, the same "no account, nothing to send" reasoning
        `MagicLinkService.request` already applies to an unknown user. Sweeps the
        identity's spent and expired tokens first: nothing needs a cron."""
        identity = self._get_by_email(email.strip().lower())
        if identity is None:
            return None
        now = datetime.now(UTC)
        self.session.execute(
            delete(IdentityLinkToken).where(
                IdentityLinkToken.identity_id == identity.id,
                or_(
                    IdentityLinkToken.used_at.is_not(None),
                    IdentityLinkToken.expires_at <= now,
                ),
            )
        )
        raw = secrets.token_urlsafe(32)
        self.session.add(
            IdentityLinkToken(
                identity_id=identity.id,
                token_hash=_hash(raw),
                expires_at=now + timedelta(minutes=self.settings.magic_link_minutes),
            )
        )
        self.session.commit()
        return raw

    def enter(self, raw: str) -> IdentityRead | None:
        """The identity, or `None` for a wrong, spent or expired link. Spends the
        token with a conditional `UPDATE`, the same race guard `MagicLinkService.enter`
        uses, so two racing clicks on the same raw token still open exactly one
        session."""
        if not raw:
            return None
        now = datetime.now(UTC)
        token = self.session.scalar(
            select(IdentityLinkToken).where(IdentityLinkToken.token_hash == _hash(raw))
        )
        if token is None or token.used_at is not None or token.expires_at <= now:
            return None
        spent = self.session.execute(
            update(IdentityLinkToken)
            .where(IdentityLinkToken.id == token.id, IdentityLinkToken.used_at.is_(None))
            .values(used_at=now)
            .returning(IdentityLinkToken.id)
        )
        if len(spent.scalars().all()) != 1:
            self.session.rollback()
            return None
        identity = self.session.get(Identity, token.identity_id)
        self.session.commit()
        return IdentityRead.model_validate(identity) if identity is not None else None

    # ---- recording a proof that already happened one call away ------------------

    def _get_or_create(self, normalized: str) -> Identity:
        """`normalized` must already be `.strip().lower()`d by the caller. Not atomic
        on its own -- two callers racing for the same brand-new address, whether two
        first logins (`upsert_and_issue`) or two overlapping `rebuild-identity-index`
        runs (`get_or_create`), can both miss the `SELECT` and both try to insert.
        That is not a real failure, only two winners racing for one row: the loser's
        `flush` hits the functional unique index (`IntegrityError`, caught here
        specifically, never the broader `SQLAlchemyError` callers already guard
        with), and the fix is to read the winner's row rather than raise over a
        collision this method caused itself."""
        identity = self._get_by_email(normalized)
        if identity is None:
            identity = Identity(email=normalized)
            self.session.add(identity)
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                identity = self._get_by_email(normalized)
                if identity is None:
                    raise
        return identity

    def get_or_create(self, email: str) -> Identity | None:
        """The `identities` row for this address, creating it if none exists yet, and
        committing on its own -- the backfill's own primitive (`pigrocrm
        rebuild-identity-index`, REB-379). Unlike `upsert_and_issue`, this never mints
        a session or a token: it records that an address exists, without pretending
        anyone has just logged in, which is the whole point of a backfill that only
        makes an already-correct-eventually index complete sooner (design
        2026-09-23 §6, §8). `None` only for an address that normalises to nothing."""
        normalized = email.strip().lower()
        if not normalized:
            return None
        identity = self._get_or_create(normalized)
        self.session.commit()
        return identity

    def upsert_and_issue(self, email: str) -> str | None:
        """Creates the `identities` row if none exists yet, and always mints a fresh,
        revocable identity token -- the write is a pure side effect of a proof that
        already happened at the caller, and its own failure must never turn a working
        space login into a 500. That discipline lives in the caller
        (`_issue_identity_cookie`, `routers/auth.py`), which wraps this in the
        ephemeral-engine/`SQLAlchemyError` shape `_space_link` already established;
        this method itself commits or raises plainly, like every other service in
        this codebase. `None` only for an address that normalises to nothing.

        The get-or-create is `_get_or_create`, shared with the backfill's own
        `get_or_create` above -- see its docstring for the race the retry-once
        handles."""
        normalized = email.strip().lower()
        if not normalized:
            return None
        identity = self._get_or_create(normalized)
        jti = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=self.settings.identity_token_days)
        self.session.add(IdentitySession(identity_id=identity.id, jti=jti, expires_at=expires_at))
        self.session.commit()
        return issue_identity_token(identity.id, self.settings, jti=jti, issued_at=now)

    def revoke_all(self, identity_id: UUID) -> None:
        """Every live `IdentitySession` of `identity_id` is revoked -- not only the one
        the caller presented (§2's own "signs out of the identity everywhere it was
        used"). Idempotent: revoking an identity with nothing live left to revoke is
        the goal state already reached, not an error. `POST /api/identity/logout`'s
        only use."""
        now = datetime.now(UTC)
        self.session.execute(
            update(IdentitySession)
            .where(IdentitySession.identity_id == identity_id, IdentitySession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        self.session.commit()

    def resolve(self, identity_id: UUID, jti: UUID | None) -> str | None:
        """The proven email behind a live identity session, or `None`: an absent
        `jti` (never minted by `issue_identity_token`, so a decoded token missing one
        is malformed), an unknown identity, an unknown or already-revoked
        `IdentitySession`, or one past its own `expires_at` -- the same liveness §2
        describes, checked on every read rather than trusted from the JWT's own `exp`
        alone, because a *revoked* session's token still decodes fine (revocation is
        a database fact, not a cryptographic one). Never raises: an absent or dead
        session is not a database error, it is the 401 the caller answers with."""
        if jti is None:
            return None
        now = datetime.now(UTC)
        live = self.session.scalar(
            select(IdentitySession).where(
                IdentitySession.jti == jti,
                IdentitySession.identity_id == identity_id,
                IdentitySession.revoked_at.is_(None),
                IdentitySession.expires_at > now,
            )
        )
        if live is None:
            return None
        identity = self.session.get(Identity, identity_id)
        return identity.email if identity is not None else None
