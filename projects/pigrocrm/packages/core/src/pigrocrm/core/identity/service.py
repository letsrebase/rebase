"""Cross-space identity, proven passwordlessly, one layer above any single space's own
session (design 2026-09-23, REB-345/376).

Two mechanisms, deliberately not one: `request`/`enter` spend a mailed link against
`identities` instead of `users` -- `MagicLinkService`'s own shape, one layer up, for
proving an address nobody has vouched for yet. `upsert_and_issue` is the side effect
three existing entry points (`login`, `enter_with_link`, `accept_invite`,
`apps/api/src/pigrocrm_api/routers/auth.py`) ride on: it records a proof that already
happened one call away, and never asks for one of its own. `signup` is deliberately
not a fourth call site (§2) -- an address nobody has proven anything about must not get
a durable, cross-space cookie.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, update
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

    def upsert_and_issue(self, email: str) -> str | None:
        """Creates the `identities` row if none exists yet, and always mints a fresh,
        revocable identity token -- the write is a pure side effect of a proof that
        already happened at the caller, and its own failure must never turn a working
        space login into a 500. That discipline lives in the caller
        (`_issue_identity_cookie`, `routers/auth.py`), which wraps this in the
        ephemeral-engine/`SQLAlchemyError` shape `_space_link` already established;
        this method itself commits or raises plainly, like every other service in
        this codebase. `None` only for an address that normalises to nothing."""
        normalized = email.strip().lower()
        if not normalized:
            return None
        identity = self._get_by_email(normalized)
        if identity is None:
            identity = Identity(email=normalized)
            self.session.add(identity)
            self.session.flush()
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
