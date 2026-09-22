from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class Invitation(Base, PrimaryKeyMixin, TimestampMixin):
    """One row per invitation sent (spec 2026-09-17 §2). Not a half-formed `User` row:
    the address frees the moment the invitation is revoked, and nothing downstream
    (the timeline, MCP resources, every foreign key on `users.id`) learns that a
    "user" can be provisional. Only the SHA-256 of the raw token is stored, so a dump
    of this table opens nothing.

    `accepted_at` and `revoked_at` staying on the row (never deleted) is what makes the
    three dead states of a spent link -- scaduto, revocato, già usato -- tellable apart
    after the fact; both being `NULL` is the one property that makes a row "pending"
    for every other check. Expiry is not a column state, it is `expires_at < now()`, so
    an expired row is still inside the unique index below and still claims the address;
    `InvitationService.create` rewrites an expired row in place rather than inserting a
    second one, which is why the index predicate excludes only the two terminal states.
    """

    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    nome: Mapped[str | None] = mapped_column(String(200), default=None)
    ruolo: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    invited_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        # Same functional-index shape as `uq_users_email_lower`, with a partial
        # predicate: at most one *open* invitation exists per address at a time, and an
        # address whose invitation ended (accepted or revoked) can be invited again.
        # This is the race guard behind `InvitationService.create`'s own read.
        Index(
            "uq_invitations_email_lower_open",
            func.lower(email),
            unique=True,
            postgresql_where=text("accepted_at IS NULL AND revoked_at IS NULL"),
        ),
    )
