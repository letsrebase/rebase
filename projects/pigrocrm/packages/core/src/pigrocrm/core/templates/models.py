from typing import Any

from sqlalchemy import Boolean, Index, String, Text, func
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

    __table_args__ = (Index("uq_templates_name", func.lower(name), unique=True),)
