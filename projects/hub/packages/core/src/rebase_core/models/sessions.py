from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from rebase_core.db import Base, PrimaryKeyMixin
from rebase_core.models.profiles import UtmMixin

# ---- the member area: how anyone gets back in -------------------------------------------

TOKEN_HASH_LENGTH = 64  # sha256, hex


class MagicLinkToken(Base, PrimaryKeyMixin, UtmMixin):
    """One link, one entry. The raw value travels in the mail and nowhere else; the row
    holds its sha256, a deadline (`magic_link_minutes`) and the moment it was spent, so a
    link forwarded or fetched twice opens nothing the second time. Hangs on the person
    with `ON DELETE CASCADE`: deleting them deletes their way in.

    Since REB-278 the link is for anyone with a `users` row, not only a freelancer,
    `user_id` is the owner; migration B (REB-281) drops the `freelancer_id` it replaced,
    once nothing writes it any more.

    Since REB-426 it also holds the attribution the login page arrived with (`UtmMixin`),
    only to hand it to the `Login` the link opens: the page asks for the link, the mail
    opens it, and the token is the one thing both ends share."""

    __tablename__ = "magic_link_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Which public door asked for the link (migration 0048): `login` the login page,
    # `wizard` the «you already have a card» mail of an application, `NULL` an admin's
    # promotion or the command line. Only the first two count against a person's daily
    # cap, and only the wizard's against the public mail budget.
    asked_via: Mapped[str | None] = mapped_column(String(10), default=None)

    __table_args__ = (
        CheckConstraint(
            "asked_via IS NULL OR asked_via IN ('login', 'wizard')",
            name="ck_magic_link_tokens_asked_via",
        ),
    )


class Login(Base, PrimaryKeyMixin, UtmMixin):
    """One row per time somebody entered through a magic link (ORB-158): who, when, and
    since REB-426 the campaign the login page was opened from (`UtmMixin`, copied off the
    token), so an outreach mail's link says who came in from it without a cookie. Nothing
    else -- no address, no user agent. A log rather than the session table,
    which forgets a session on logout and on expiry, so the admin can read who came in
    and when a week later. Written by `UserService.enter` in the commit that opens the
    session. Hangs on the person with `ON DELETE CASCADE`, like the sessions.

    Renamed from `member_logins`/`MemberLogin` in migration B (REB-281), the same
    reasoning as `UserSession` below: `user_id` is the owner for anyone who signs in,
    member or admin, and "member" stopped describing who is in this table the moment
    an admin's own login started landing here too (REB-278). `freelancer_id`, the
    column the rename leaves behind, is dropped in the same migration."""

    __tablename__ = "logins"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GuideDownload(Base, PrimaryKeyMixin):
    """One row per time somebody fetched the guide (ORB-156): who and when, and nothing
    else. A log rather than a counter on the freelancer, so the admin can read a trend
    and see who came back for it; nothing about the file itself is stored, since the
    file is package data and the same for everybody. Hangs on the person with
    `ON DELETE CASCADE`: a deleted person takes their downloads.

    Since REB-278 `user_id` is the owner; migration B (REB-281) drops the
    `freelancer_id` it replaced."""

    __tablename__ = "guide_downloads"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserSession(Base, PrimaryKeyMixin):
    """The session's shape: opaque cookie, hashed at rest, sliding expiry, revoked by
    deleting the row. One table and one cookie for everyone who signs in, member and
    admin alike (REB-278): a role is read off the `users` row it resolves to, not off
    which table the session lives in.

    The table is renamed from `member_sessions` in migration B (REB-281): "member"
    stopped describing who is in it the moment an admin's own session started landing
    here too, the same rule `REB-207`-`REB-212`/`REB-214` already applied elsewhere in
    this codebase (an identifier that actively misleads gets renamed). The Python class
    keeps the `Member` prefix off but stops short of the bare `Session` the table name
    suggests: every service already imports `sqlalchemy.orm.Session` under that exact
    name, and a class sharing it would silently shadow one or the other on whichever
    import runs second. `freelancer_id`, the column the rename leaves behind, is
    dropped in the same migration."""

    __tablename__ = "sessions"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
