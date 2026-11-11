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

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, text
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
    # `accountant` (the period bundle) or `payroll_changes` (the payroll consultant's
    # «variazioni paghe»), validated in code.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="accountant", server_default=text("'accountant'")
    )
    # Whether the files carry pay: the payroll consultant's export, or an accountant's export
    # with the payslips ticked. Such an export never travels on a plain link (design § 3).
    payroll: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    period_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    period_to: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # The folder the parts sit in: `commercialista/<entity>/<period>/<timestamp>`.
    prefix: Mapped[str] = mapped_column(String(255), nullable=False)
    # One object per ZIP part: `{"key", "filename", "size_bytes", "sha256"}`.
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    # What the index said when it was built: the parts built, left out or not available, the
    # documents that could not be attached and, under `readiness`, the checklist as the period
    # stood (spec § 2), which a later fix never rewrites.
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


class ExportShareLink(Base, PrimaryKeyMixin, TimestampMixin):
    """A share link for one stored export (spec accountant export § 3): the credential of
    someone with no account, so it is held only as the SHA-256 of a 256-bit random token and
    the raw value exists once, in the answer that creates it.

    `legal_entity_id` repeats the export's, so the scope policy that hides an export hides its
    links (`NULL` for the export of every entity, an unscoped administrator's). A link is never
    deleted: `revoked_at` ends it early, `expires_at` ends it on its day, and the openings stay
    as the audit.
    """

    __tablename__ = "accountant_export_links"

    export_id: Mapped[UUID] = mapped_column(
        ForeignKey("accountant_exports.id"), nullable=False, index=True
    )
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_by_type: Mapped[str] = mapped_column(String(20), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked_by: Mapped[UUID | None] = mapped_column(nullable=True, default=None)
    # The address a one-time code is sent to before the files open: set on every link of an
    # export that carries pay, and on any other the administrator asked a code for. `NULL` is a
    # plain link, which the service refuses for such an export.
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True, default=None)
    # How many files were handed out through the link; written only by `UPDATE ... + 1`.
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class ExportShareOpening(Base, PrimaryKeyMixin):
    """One opening of a share link: a file handed to whoever held it, when and from which
    address (the client address nginx reports), and which ZIP of the export it was."""

    __tablename__ = "accountant_export_link_openings"

    link_id: Mapped[UUID] = mapped_column(
        ForeignKey("accountant_export_links.id"), nullable=False, index=True
    )
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=True
    )
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    part: Mapped[int] = mapped_column(Integer, nullable=False)


class ExportShareCode(Base, PrimaryKeyMixin):
    """One one-time code sent to the recipient of a link with a code (spec § 3): held only as a
    salted hash, good for a few minutes and a few tries, spent by the first right answer, which
    opens a short session (`access_hash`) the downloads then ask for. A code that was sent and
    never used stays as the trace of the send."""

    __tablename__ = "accountant_export_link_codes"

    link_id: Mapped[UUID] = mapped_column(
        ForeignKey("accountant_export_links.id"), nullable=False, index=True
    )
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Wrong answers so far; the code is burnt at the limit.
    failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    consumed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # The session the right code opened: the SHA-256 of a random value only the recipient's
    # browser holds, and when it ends.
    access_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    access_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class ExportDriveWrite(Base, PrimaryKeyMixin):
    """A copy of a stored export written to the space's Drive by a person, under
    `commercialista/<entity-slug>/<period>/<timestamp>/`: where it is and when it was written."""

    __tablename__ = "accountant_export_drive_writes"

    export_id: Mapped[UUID] = mapped_column(
        ForeignKey("accountant_exports.id"), nullable=False, index=True
    )
    legal_entity_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("legal_entities.id"), nullable=True
    )
    prefix: Mapped[str] = mapped_column(String(255), nullable=False)
    # One object per ZIP written: `{"key", "filename", "size_bytes"}`.
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    written_by: Mapped[UUID | None] = mapped_column(nullable=True)
    written_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
