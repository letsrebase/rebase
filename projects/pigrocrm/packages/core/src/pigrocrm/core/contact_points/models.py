from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin

CONTACT_KINDS: tuple[str, ...] = ("email", "phone", "linkedin")
CONTACT_LABELS: tuple[str, ...] = ("work", "personal", "billing", "other")
VALUE_MAX_LENGTH = 320
NOTE_MAX_LENGTH = 200


class ContactPoint(Base, PrimaryKeyMixin):
    """One way to reach a person or a customer: a kind, a value, a label and a note.

    An owned child row of exactly one parent (`person_id` xor `customer_id`, a CHECK), the
    shape `DealValueLine` has under `Deal`: no `SoftDeleteMixin`, replaced as a whole list on
    every write (`contact_points.service.write_points`). `position` is the display order.

    `value` is what was typed (a phone keeps its spaces, an address its case); `normalized`
    is what two values are compared by, written only by the service: the lowercased address,
    the digits of a phone with a leading `+` kept, the canonical LinkedIn profile. Every
    lookup (Gmail roster, leads door, search) and both unique indexes read it, never `value`.
    At most one point per owner and kind is primary, and the one that is, is what mail uses.
    """

    __tablename__ = "contact_points"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(person_id, customer_id) = 1", name="ck_contact_points_one_owner"
        ),
        CheckConstraint(f"kind IN {CONTACT_KINDS!r}", name="ck_contact_points_kind"),
        CheckConstraint(f"label IN {CONTACT_LABELS!r}", name="ck_contact_points_label"),
        # The same address or number twice on one owner, refused by the database as well.
        Index(
            "uq_contact_points_person_value",
            "person_id",
            "kind",
            "normalized",
            unique=True,
            postgresql_where=text("person_id IS NOT NULL"),
        ),
        Index(
            "uq_contact_points_customer_value",
            "customer_id",
            "kind",
            "normalized",
            unique=True,
            postgresql_where=text("customer_id IS NOT NULL"),
        ),
        # At most one primary per owner and kind.
        Index(
            "uq_contact_points_person_primary",
            "person_id",
            "kind",
            unique=True,
            postgresql_where=text("is_primary AND person_id IS NOT NULL"),
        ),
        Index(
            "uq_contact_points_customer_primary",
            "customer_id",
            "kind",
            unique=True,
            postgresql_where=text("is_primary AND customer_id IS NOT NULL"),
        ),
        # The Gmail roster and the leads door look an address up by `(kind, normalized)`.
        Index("ix_contact_points_kind_normalized", "kind", "normalized"),
        # One trigram index per column the search ORs together (see `Person.__table_args__`
        # in the earlier schema): what was typed, and what it is compared by.
        Index(
            "ix_contact_points_value_trgm",
            "value",
            postgresql_using="gin",
            postgresql_ops={"value": "gin_trgm_ops"},
        ),
        Index(
            "ix_contact_points_normalized_trgm",
            "normalized",
            postgresql_using="gin",
            postgresql_ops={"normalized": "gin_trgm_ops"},
        ),
    )

    person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), default=None, index=True
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), default=None, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    value: Mapped[str] = mapped_column(String(VALUE_MAX_LENGTH), nullable=False)
    normalized: Mapped[str] = mapped_column(String(VALUE_MAX_LENGTH), nullable=False)
    label: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(String(NOTE_MAX_LENGTH), default=None)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class _Point(Protocol):
    kind: str
    value: str
    is_primary: bool


def primary_value(points: Iterable[_Point], kind: str) -> str | None:
    """The value of the primary point of a kind among a parent's points (in position
    order), or `None` when it has none. The service flags the first point of a kind primary,
    so a kind with points always has one; the first of them answers if a row says otherwise."""
    first: str | None = None
    for point in points:
        if point.kind != kind:
            continue
        if point.is_primary:
            return point.value
        if first is None:
            first = point.value
    return first
