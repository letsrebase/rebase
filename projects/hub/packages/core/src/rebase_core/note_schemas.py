"""The notes thread on a freelancer or a company, as admins read and write it."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.models import NOTE_ADMIN_SOURCES, NOTE_MAX_LENGTH
from rebase_core.schema_common import ResponseModel
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import NoteSource


class NoteRead(ResponseModel):
    """One note of a row's thread (REB-856): who wrote it (`author`, the name as it read
    then, and `author_id`), by which door (`source`: `web`, `mcp`, `person`, `system`, or
    `None` for a note that predates the tracking), when, and, once changed, who changed it
    last and when. `editable` is whether an admin may edit or delete it: not an event
    of the person's or the hub's own."""

    id: UUID
    text: str
    author: str
    author_id: UUID | None
    source: NoteSource | None
    created_at: datetime
    edited_at: datetime | None
    edited_by: str | None
    editable: bool


class NoteCreate(BaseModel):
    """What the admin API takes for a new note: the text alone. The author is the
    logged-in admin and the source is the door, never fields the client fills in."""

    model_config = ConfigDict(extra="forbid")

    text: SafeStr = Field(min_length=1, max_length=NOTE_MAX_LENGTH)


class NoteUpdate(NoteCreate):
    """The new text of a note an admin edits."""


def note_is_editable(source: str | None) -> bool:
    """Whether an admin may edit or delete a note of this source (`NOTE_ADMIN_SOURCES`,
    or `None` for a row written before the source was tracked)."""
    return source is None or source in NOTE_ADMIN_SOURCES
