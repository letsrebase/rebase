from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.validation import SafeStr

NoteEntityType = Literal["deal", "customer", "person"]
NoteSource = Literal["web", "mcp", "system", "rebase"]

NOTE_MAX_LENGTH = 10_000


def _clean(value: str) -> str:
    """The text trimmed, and a note of nothing but whitespace refused: it would be an
    empty row in the thread."""
    text = value.strip()
    if not text:
        raise ValueError("una nota non può essere vuota")
    return text


class NoteCreate(BaseModel):
    """What a note says. Nothing about who wrote it or when: both are read from the actor
    and the clock, so a caller cannot sign a note with another name."""

    model_config = ConfigDict(extra="forbid")

    text: SafeStr = Field(min_length=1, max_length=NOTE_MAX_LENGTH)

    _trimmed = field_validator("text")(_clean)


class NoteUpdate(NoteCreate):
    """The new text of a note; the same shape as a new one."""


class NoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_type: NoteEntityType
    entity_id: UUID
    text: str
    # The member's name as it read when they wrote it; absent for a note a job or rebase
    # wrote and for one moved over from the old `note` column.
    author: str | None
    author_id: UUID | None
    # Absent only for a note that existed before the thread did («Nota precedente»).
    source: NoteSource | None
    created_at: datetime
    edited_at: datetime | None
    # The member's name now, not a snapshot: who last changed the text.
    edited_by: str | None = None
    # Whether the caller who asked may edit and delete it: their own, or any if they are
    # an admin, and never one `system` or `rebase` wrote.
    editable: bool = False


class NoteRevisionRead(BaseModel):
    """What a note said before one edit, who changed it and when."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    note_id: UUID
    text: str
    edited_at: datetime
    edited_by: str | None
    source: NoteSource | None
