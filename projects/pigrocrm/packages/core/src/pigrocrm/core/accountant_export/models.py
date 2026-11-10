"""A stored export: the row a built bundle leaves behind (spec accountant export § 3).

One row per build, never per period: building the same period twice keeps both, because the
second is not told apart from the first except by when it was built and from what. The files
sit in the storage under `prefix`, one or several ZIP parts, and the row says where each is,
how big and what it hashes to.

`legal_entity_id` is `NULL` for the all-entities export, which the scope policy then shows to
an unscoped administrator only: the row carries the same `legal_entity_visible` policy as
every table that reaches a legal entity.

An export is kept 13 months (`expires_at`) and then deleted: its files leave the storage,
`deleted_at` says when, and the row stays as the trace, with an `activities` entry beside it.
"""

import datetime as dt
from typing import Any
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db.base import Base, PrimaryKeyMixin, TimestampMixin


class AccountantExportRow(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "accountant_exports"

    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=True
    )
    # `entity` or `all_entities`, validated in code.
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    period_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_to: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # The folder the parts sit in: `commercialista/<entity>/<period>/<timestamp>`.
    prefix: Mapped[str] = mapped_column(String(255), nullable=False)
    # One object per ZIP part: `{"key", "filename", "size_bytes", "sha256"}`.
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # What the index said when it was built: the parts built, left out or not available, and
    # the documents that could not be attached.
    checklist: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    built_by: Mapped[UUID | None] = mapped_column(nullable=True)
    built_by_type: Mapped[str] = mapped_column(String(20), nullable=False)
    built_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
