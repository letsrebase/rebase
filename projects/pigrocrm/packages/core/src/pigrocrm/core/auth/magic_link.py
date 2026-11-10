"""A link by mail as the way in (spec 2026-09-12 §6.2), the hub's shape in the CRM.

`request` answers the raw token or `None`; the caller builds the URL and the mail,
because only the router knows which prefix and which public origin the link must wear.
`enter` spends the token with a conditional UPDATE gated on it still being unused, so of
two requests racing on the same raw token (a mail scanner's prefetch against the
person's own click) only one opens a session.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.auth.magic_models import MagicLinkToken
from pigrocrm.core.auth.refresh_service import RefreshTokenService
from pigrocrm.core.auth.repository import UserRepository
from pigrocrm.core.auth.schemas import UserRead
from pigrocrm.core.config import Settings


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class MagicLinkService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)

    def request(self, email: str) -> str | None:
        """The raw token for an active user with this address, or `None`. Sweeps the
        person's spent and expired tokens first: nothing needs a cron."""
        user = self.users.get_by_email(email.strip().lower())
        if user is None or not user.active:
            return None
        now = datetime.now(UTC)
        self.session.execute(
            delete(MagicLinkToken).where(
                MagicLinkToken.user_id == user.id,
                or_(MagicLinkToken.used_at.is_not(None), MagicLinkToken.expires_at <= now),
            )
        )
        raw = secrets.token_urlsafe(32)
        self.session.add(
            MagicLinkToken(
                user_id=user.id,
                token_hash=_hash(raw),
                expires_at=now + timedelta(minutes=self.settings.magic_link_minutes),
            )
        )
        self.session.commit()
        return raw

    def enter(self, raw: str) -> UserRead | None:
        """The user, or `None` for a wrong, spent or expired link, or an inactive user.
        The first entry of a user writes `email_verified_at` and revokes every refresh
        token issued before it: the address was a claim until this click. Refresh tokens
        only: an access token issued before it stays valid for the rest of its
        `access_token_minutes`, and that window is the accepted cost."""
        if not raw:
            return None
        now = datetime.now(UTC)
        token = self.session.scalar(
            select(MagicLinkToken).where(MagicLinkToken.token_hash == _hash(raw))
        )
        if token is None or token.used_at is not None or token.expires_at <= now:
            return None
        spent = self.session.execute(
            update(MagicLinkToken)
            .where(MagicLinkToken.id == token.id, MagicLinkToken.used_at.is_(None))
            .values(used_at=now)
            .returning(MagicLinkToken.id)
        )
        if len(spent.scalars().all()) != 1:
            self.session.rollback()
            return None
        # Spent before the user is checked: a link of a deactivated user must not stay
        # live for the rest of its fifteen minutes in case the account comes back.
        user = self.users.get(token.user_id)
        if user is None or not user.active:
            self.session.commit()
            return None
        if user.email_verified_at is None:
            user.email_verified_at = now
            self.session.flush()
            # Commits, and takes the flush above with it.
            RefreshTokenService(self.session).revoke_all(user.id, now)
        else:
            self.session.commit()
        return UserRead.model_validate(user)
