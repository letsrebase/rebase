from typing import Any
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, column, desc, nullslast, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pigrocrm.core.contact_points.models import ContactPoint, primary_value
from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin, TimestampMixin


class Person(Base, PrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A contact, who may or may not belong to a `Organization`.

    `customer_id` is nullable on purpose: a contact can exist before anyone knows
    which company they work for. Forcing every person to have a customer at
    creation time would produce phantom organizations (e.g. "Freelance vari") just to
    hold the ones who do not yet have one. `PersonService` validates an explicit
    `customer_id` against `organizations` whenever one is supplied, and
    `PersonUpdate.detach` is how the association is removed again -- see that field's
    own comment for why it survived A14 being closed.

    `custom_fields` copies `Organization`'s own JSONB column and GIN index, for the same
    reason: `list()`'s containment filter (`custom_fields @> {...}`) must never fall
    back to a full table scan.
    """

    __tablename__ = "people"
    __table_args__ = (
        Index("ix_people_custom_fields", "custom_fields", postgresql_using="gin"),
        # One trigram index per column `PersonRepository.list` ORs together -- see
        # `Organization.__table_args__` for why all three are needed and why the index is
        # partial on `deleted_at IS NULL` with no `lower()` in the expression.
        Index(
            "ix_people_name_trgm",
            "first_name",
            postgresql_using="gin",
            postgresql_ops={"first_name": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_people_last_name_trgm",
            "last_name",
            postgresql_using="gin",
            postgresql_ops={"last_name": "gin_trgm_ops"},
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Residuo R9. See `organizations/models.py` for why each admitted sort key costs a
        # `(column, id)` B-tree and why none of them is partial.
        Index("ix_people_created_at_id", "created_at", "id"),
        Index("ix_people_updated_at_id", "updated_at", "id"),
        Index("ix_people_last_name_id", "last_name", "id"),
        # The second index the one nullable sort column in the whole whitelist costs.
        # `order_by` declares NULLS LAST in *both* directions for a nullable column -- and
        # only for a nullable one, because on a NOT NULL column that same spelling matches
        # no index at all. A backward scan of the ascending index above yields NULLS FIRST,
        # so it cannot serve `last_name` `dir=desc`, and without this index Postgres falls
        # back to sorting the table.
        #
        # `column("last_name")` rather than the mapped attribute because `id` comes from
        # `PrimaryKeyMixin` and is not bound in this class body at all. Unlike
        # `func.lower("first_name")` -- which would bind a *constant* (see
        # `templates/models.py`) -- `column()` is an explicit column reference, so the
        # string is safe here.
        Index(
            "ix_people_last_name_desc_id",
            nullslast(desc(column("last_name"))),
            desc(column("id")),
        ),
    )

    # Nullable FK: see the class docstring above.
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None, index=True
    )
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(120), default=None)
    role: Mapped[str | None] = mapped_column(String(120), default=None)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # The ways to reach this person (REB-939), loaded with the row: one query per read of a
    # list of people, never one per row. A row is born with its points through the cascade;
    # after that `contact_points.service.write_points` replaces the list, which a
    # `delete-orphan` cascade could not do without tripping the unique indexes.
    contact_points: Mapped[list[ContactPoint]] = relationship(
        lazy="selectin",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ContactPoint.position",
    )

    @property
    def email(self) -> str | None:
        """The primary email, which is where the CRM's mail to this person goes."""
        return primary_value(self.contact_points, "email")

    @property
    def phone(self) -> str | None:
        return primary_value(self.contact_points, "phone")

    @property
    def linkedin(self) -> str | None:
        return primary_value(self.contact_points, "linkedin")
