from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, TimestampMixin


class Template(Base, PrimaryKeyMixin, TimestampMixin):
    """A document body plus the variables it declares.

    `declared_variables` is declared, never deduced (spec 4.3): the compilation
    form shows each variable with its label, type and whether it is required, and the
    render fails with a precise error if a required one is missing -- instead of
    producing a PDF with a hole in it.

    The unique index is on `lower(name)`, not on `name`: a plain `unique=True` on a
    text column is case-sensitive, and lowercasing in a Pydantic validator would
    protect only the paths that go through it. `func.lower(name)` below references the
    `name` column attribute (bound in this class body above), the same pattern
    `auth/models.py`'s `uq_users_email_lower` uses -- not a string literal, which
    `func.lower` would otherwise bind as a constant rather than a column reference.
    """

    __tablename__ = "templates"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="offer")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    declared_variables: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The regime a framework agreement or an engagement letter is written for: `ordinary` (the
    # freelancer has a VAT number, paid on an invoice) or `occasional` (paid on a receipt). Set
    # for those two types and for no other (collaborators design § 1, «The documents»).
    letter_type: Mapped[str | None] = mapped_column(String(20), default=None)

    __table_args__ = (
        Index("uq_templates_name", func.lower(name), unique=True),
        CheckConstraint(
            "(type IN ('framework_agreement', 'engagement_letter')) = (letter_type IS NOT NULL) "
            "AND (letter_type IS NULL OR letter_type IN ('ordinary', 'occasional'))",
            name="ck_templates_letter_type",
        ),
        # One live text per kind and regime: a letter reads the space's template, so two active
        # ones for the same kind would make the choice arbitrary.
        Index(
            "uq_templates_letter_kind",
            "type",
            "letter_type",
            unique=True,
            postgresql_where=text("letter_type IS NOT NULL AND active"),
        ),
    )
