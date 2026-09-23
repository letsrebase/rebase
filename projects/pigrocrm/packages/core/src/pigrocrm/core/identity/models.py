"""Cross-space identity: one row per proven email address, in the registry database
(design 2026-09-23, REB-345/376). `Identity` answers "is this a person we have seen
before"; `IdentityLinkToken` and `IdentitySession` are its own passwordless-entry
pair, built the same way `MagicLinkToken`/`RefreshToken` build one space's own
session -- see `IdentityService` for the shape each mirrors.

All three on `TenantsBase` (`tenants/models.py`), the registry's own metadata, not a
space's `Base`: this needs no Alembic revision at all, because the registry is
provisioned as a sidecar database whose `ensure_tenants_database` runs
`metadata.create_all` on every boot -- idempotent and additive only (design §6).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db.base import PrimaryKeyMixin
from pigrocrm.core.tenants.models import TenantsBase


class Identity(TenantsBase, PrimaryKeyMixin):
    """One row per lowercase email that has proven itself once, anywhere in this
    installation (§1). No name, no password, no role: those already live once each,
    on `users.nome`/`password_hash`/`ruolo` inside whichever space actually knows
    this person -- duplicating any of them here would be the second copy
    `projects/pigrocrm/AGENTS.md`'s "no duplication" principle exists to rule out."""

    __tablename__ = "identities"

    # No unique=True here, for the exact reason `User.email`'s own comment gives: a
    # plain unique index on the raw column is case-sensitive and would let
    # "a@b.it" and "A@B.it" both in. The functional index below is
    # `uq_users_email_lower`'s (auth/models.py) own shape, one layer up.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("uq_identities_email_lower", func.lower(email), unique=True),)


class IdentityLinkToken(TenantsBase, PrimaryKeyMixin):
    """Mirrors `MagicLinkToken` (`auth/magic_models.py`) one layer up: `identity_id`
    instead of `user_id`, and living on `TenantsBase` beside `Identity` rather than on
    a space's own `Base`, because this proves an identity, never one space's user.
    Only the SHA-256 of the raw token is stored, so a dump of this table opens
    nothing."""

    __tablename__ = "identity_link_tokens"

    identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("identities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class IdentitySession(TenantsBase, PrimaryKeyMixin):
    """What makes the `pigrocrm_identity` cookie's JWT `jti` claim mean something --
    the same reason `RefreshToken` exists (`auth/refresh_models.py`): a bare signed
    token has no server-side presence, so without a row to mark revoked there is
    nothing to stop it being replayed for the rest of its ~180-day life (§2). Unlike a
    refresh token, this is read repeatedly rather than spent once: there is no
    `consumed_at`, only `revoked_at`, checked whenever a caller needs to know "is this
    jti still live"."""

    __tablename__ = "identity_sessions"

    jti: Mapped[UUID] = mapped_column(unique=True, index=True, nullable=False)
    identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("identities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
