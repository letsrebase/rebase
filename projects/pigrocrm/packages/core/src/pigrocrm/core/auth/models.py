from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class User(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    # No unique=True here: a plain unique index on the raw column is case-sensitive and
    # would let "a@b.it" and "A@B.it" both in. Uniqueness is enforced below by a
    # functional index on lower(email) instead -- a real database constraint, not just
    # the app-level lowering that UserCreate and UserRepository.get_by_email also do.
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    # `None` for a user who has only ever entered with a link by mail (spec 2026-09-12
    # §6.2); `UserService.authenticate` refuses them exactly like a wrong password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="collaborator")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether the weekly report reaches this person (spec 2026-09-16 §3.3). On by
    # default: the report is the reason to come back, and the mail carries the switch.
    weekly_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # When a link by mail was first used by this user: the moment the address stopped
    # being a claim. Written once by `MagicLinkService.enter`, which also revokes every
    # session issued before it. `None` for the accounts that only ever used a password.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # The last time this account opened a session, whether by password login or by
    # accepting an invitation (REB-297): both are the moment an account first starts
    # being used, and neither happens through the other. `None` for an account that
    # has never done either -- an invitation sent but not yet accepted, for instance.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Level 3 of slice 4 §5.1's resolution order, for both numbers. There is
    # deliberately no `costo_orario` on `deals`: an hour's cost is a property of who
    # works it, not of the client they work it for, and adding the level would let
    # somebody declare that the same person costs differently on two projects -- an
    # accounting entry, not CRM data. Both Numeric(12,6): they are factors, and slice
    # 3's `invoice_lines.unit_price` fixes that precision.
    default_hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    default_hourly_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)

    # REB-615, spec 2026-10-03 §1.11: the flag, not the presence of `user_legal_entities`
    # rows, is what says «scoped». False for everyone until milestone 6 writes it;
    # `server_default` so every row that exists is unscoped without an ORM pass.
    limited_scope: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # The rows of the scope, loaded with the user (REB-633): one query per read of a
    # list of users, never one per row, and `legal_entities` below is what every reader
    # answers with. `delete-orphan`, so clearing the list deletes the rows.
    scopes: Mapped[list["UserLegalEntity"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", order_by="UserLegalEntity.azienda_id"
    )

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)

    @property
    def legal_entities(self) -> list[UUID] | None:
        """The scope as the API and the actor carry it (spec 2026-10-03 §1.11): `None`
        for an unscoped member, the ids otherwise, an empty list for a scope whose every
        legal entity was deactivated, which sees nothing and is never read as «tutte»."""
        if not self.limited_scope:
            return None
        return [row.legal_entity_id for row in self.scopes]


class UserLegalEntity(Base):
    """One row per legal entity a scoped member may see (spec 2026-10-03 §1.11, §2 step
    6). Empty until milestone 6: the table exists now so the migration that gives
    every later table its `legal_entity_id` finds it, and so `users.limited_scope` has
    the rows it will point at. `ON DELETE CASCADE` both ways: a scope dies with its
    user and with its legal entity, and nothing else references it."""

    __tablename__ = "user_legal_entities"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    legal_entity_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_entities.id", ondelete="CASCADE"), primary_key=True
    )
