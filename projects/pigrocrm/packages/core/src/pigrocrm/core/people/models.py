from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, column, desc, nullslast, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class Person(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A contact, who may or may not belong to a `Customer`.

    `customer_id` is nullable on purpose: a contact can exist before anyone knows
    which company they work for. Forcing every person to have a customer at
    creation time would produce phantom customers (e.g. "Freelance vari") just to
    hold the ones who do not yet have one. `PersonService` validates an explicit
    `customer_id` against `customers` whenever one is supplied, and
    `PersonUpdate.detach` is how the association is removed again -- see that field's
    own comment for why it survived A14 being closed.

    `custom_fields` copies `Customer`'s own JSONB column and GIN index, for the same
    reason: `list()`'s containment filter (`custom_fields @> {...}`) must never fall
    back to a full table scan.
    """

    __tablename__ = "people"
    __table_args__ = (
        Index("ix_people_custom_fields", "custom_fields", postgresql_using="gin"),
        # One trigram index per column `PersonRepository.list` ORs together -- see
        # `Customer.__table_args__` for why all three are needed and why the index is
        # partial on `deleted_at IS NULL` with no `lower()` in the expression.
        Index(
            "ix_people_nome_trgm",
            "nome",
            postgresql_using="gin",
            postgresql_ops={"nome": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_people_cognome_trgm",
            "cognome",
            postgresql_using="gin",
            postgresql_ops={"cognome": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The Gmail roster resolves an address with `lower(email) = :needle`, which neither
        # the plain email index nor the trigram one can serve (migration 0067).
        Index(
            "ix_people_email_lower",
            text("lower(email)"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_people_email_trgm",
            "email",
            postgresql_using="gin",
            postgresql_ops={"email": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. See `customers/models.py` for why each admitted sort key costs a
        # `(column, id)` B-tree and why none of them is partial.
        Index("ix_people_created_at_id", "created_at", "id"),
        Index("ix_people_updated_at_id", "updated_at", "id"),
        Index("ix_people_cognome_id", "cognome", "id"),
        # The second index the one nullable sort column in the whole whitelist costs.
        # `order_by` declares NULLS LAST in *both* directions for a nullable column -- and
        # only for a nullable one, because on a NOT NULL column that same spelling matches
        # no index at all. A backward scan of the ascending index above yields NULLS FIRST,
        # so it cannot serve `cognome` `dir=desc`, and without this index Postgres falls
        # back to sorting the table.
        #
        # `column("cognome")` rather than the mapped attribute because `id` comes from
        # `PrimaryKeyMixin` and is not bound in this class body at all. Unlike
        # `func.lower("nome")` -- which would bind a *constant* (see
        # `templates/models.py`) -- `column()` is an explicit column reference, so the
        # string is safe here.
        Index(
            "ix_people_cognome_desc_id",
            nullslast(desc(column("cognome"))),
            desc(column("id")),
        ),
    )

    # Nullable FK: see the class docstring above.
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id"), default=None, index=True
    )
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    cognome: Mapped[str | None] = mapped_column(String(120), default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None, index=True)
    telefono: Mapped[str | None] = mapped_column(String(40), default=None)
    ruolo: Mapped[str | None] = mapped_column(String(120), default=None)
    linkedin: Mapped[str | None] = mapped_column(String(255), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
