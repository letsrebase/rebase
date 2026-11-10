from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from pigrocrm.core.db import Base, PrimaryKeyMixin, SoftDeleteMixin

NOTE_ENTITY_TYPES: tuple[str, ...] = ("deal", "organization", "person")
# Where a note came from, derived from the `Actor` that wrote it and never from the caller:
# `web` a member in the browser, `mcp` an agent holding a member's token, `system` a job
# of the space, `rebase` the engagements door. A note moved over from the old `note`
# column (migration 0064) has no source at all: `NULL`, shown as «Nota precedente».
NOTE_SOURCES: tuple[str, ...] = ("web", "mcp", "system", "rebase")
# The two sources that are an event of the record's life, not somebody's remark: nobody
# edits or deletes them, an administrator included.
NOTE_READ_ONLY_SOURCES: tuple[str, ...] = ("system", "rebase")
AUTHOR_MAX_LENGTH = 200


class Note(Base, PrimaryKeyMixin, SoftDeleteMixin):
    """One remark on a deal, a customer or a person (REB-879): the thread that replaced
    their single overwritten `note` column.

    Polymorphic like `activities` and the Gmail links: `entity_type` names the table
    `entity_id` points at, so there is no foreign key, and the row-level policy reaches
    the legal entity through `entity_visible` (migration 0064), the way the timeline's does.

    It says who wrote it and when, so its text carries neither: `author_id` is the member
    (the owner of the token, for an agent) and `author` their name as it read then, kept
    so the thread still names them if the name changes. A row written by a job or by
    rebase has neither. An edit overwrites the text and keeps who and when in
    `modificata_*`; the text it replaced is in the parent's timeline entry. A delete is
    soft, like the three tables it hangs on.
    """

    __tablename__ = "notes"
    __table_args__ = (
        # The thread of one record, newest first: the only way a note is read.
        Index("ix_notes_entity", "entity_type", "entity_id", "created_at"),
        CheckConstraint(
            "entity_type IN ('deal', 'organization', 'person')", name="ck_notes_entity_type"
        ),
        CheckConstraint(
            "source IS NULL OR source IN ('web', 'mcp', 'system', 'rebase')",
            name="ck_notes_source",
        ),
    )

    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    author: Mapped[str | None] = mapped_column(String(AUTHOR_MAX_LENGTH), default=None)
    source: Mapped[str | None] = mapped_column(String(10), default=None)
    # The moment of the write, from the clock and not from the transaction (`now()` is the
    # moment the transaction began): a thread is ordered by it.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    edited_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)


class NoteRevision(Base, PrimaryKeyMixin):
    """The text a note had before one edit, whole (REB-879).

    The timeline entry of an edit cannot hold it: `sanitize_payload` cuts a string at 2000
    characters and a note may be five times that. One row per edit, written in the
    transaction of the edit, by whom and through which door; the row of the note itself
    keeps the latest text only. Read-only: nothing edits or deletes a revision, and the
    row-level policy is the note's own (migration 0064).
    """

    __tablename__ = "note_revisions"
    __table_args__ = (Index("ix_note_revisions_note", "note_id", "edited_at"),)

    note_id: Mapped[UUID] = mapped_column(ForeignKey("notes.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    edited_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), default=None)
    edited_by: Mapped[str | None] = mapped_column(String(AUTHOR_MAX_LENGTH), default=None)
    source: Mapped[str | None] = mapped_column(String(10), default=None)
