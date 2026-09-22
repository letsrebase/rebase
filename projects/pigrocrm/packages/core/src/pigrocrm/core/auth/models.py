from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    ruolo: Mapped[str] = mapped_column(String(20), nullable=False, default="collaboratore")
    attivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether the weekly report reaches this person (spec 2026-09-16 §3.3). On by
    # default: the report is the reason to come back, and the mail carries the switch.
    digest_settimanale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # When a link by mail was first used by this user: the moment the address stopped
    # being a claim. Written once by `MagicLinkService.enter`, which also revokes every
    # session issued before it. `None` for the accounts that only ever used a password.
    email_verificata_il: Mapped[datetime | None] = mapped_column(
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
    # 3's `invoice_lines.prezzo_unitario` fixes that precision.
    tariffa_oraria_default: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)
    costo_orario_default: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), default=None)

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)
