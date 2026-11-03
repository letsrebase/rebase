from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, column, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin


class Activity(Base, PrimaryKeyMixin):
    """One table for the whole timeline.

    Later slices append emails, documents, invoices and time entries by writing new
    `kind` values — no migration. That is what makes the unified timeline free.
    """

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_entity", "entity_type", "entity_id", "occurred_at"),
        # Slice 6 §6.1: a *global* feed ordered by date cannot use the index above, whose
        # ordering column is third -- residuo R9 does not cover this, because the timeline
        # it does cover always filters on the two leading columns first. DESC on both, so
        # the feed's own `ORDER BY occurred_at DESC, id DESC` is a forward scan of this
        # index rather than a backwards read of an ascending one; `id` breaks the tie,
        # because entries written in one transaction share `occurred_at` to the microsecond
        # and a feed that reorders between two reads looks like data changing.
        #
        # `column("occurred_at")` and `column("id")` rather than the mapped attributes,
        # matching `ix_people_last_name_desc_id`: `id` comes from `PrimaryKeyMixin` and is not
        # bound in this class body at all. `column()` is an explicit column reference, not a
        # string literal that would bind as a constant.
        Index(
            "ix_activities_recent",
            desc(column("occurred_at")),
            desc(column("id")),
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(default=None)
    actor_type: Mapped[str] = mapped_column(String(10), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
