"""The notes thread on a freelancer or a company, as admins read and write it."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.models import NOTE_ADMIN_SOURCES, NOTE_MAX_LENGTH
from rebase_core.validation import SafeStr


class NoteRead(BaseModel):
    """One note of a row's thread (REB-856): who wrote it (`autore`, the name as it read
    then, and `autore_id`), by which door (`fonte`: `web`, `mcp`, `persona`, `sistema`, or
    `None` for a note that predates the tracking), when, and, once changed, who changed it
    last and when. `modificabile` is whether an admin may edit or delete it: not an event
    of the person's or the hub's own."""

    id: UUID
    testo: str
    autore: str
    autore_id: UUID | None
    fonte: str | None
    created_at: datetime
    modificata_at: datetime | None
    modificata_da: str | None
    modificabile: bool


class NoteCreate(BaseModel):
    """What the admin API takes for a new note: the text alone. The author is the
    logged-in admin and the source is the door, never fields the client fills in."""

    model_config = ConfigDict(extra="forbid")

    testo: SafeStr = Field(min_length=1, max_length=NOTE_MAX_LENGTH)


class NoteUpdate(NoteCreate):
    """The new text of a note an admin edits."""


def note_is_editable(fonte: str | None) -> bool:
    """Whether an admin may edit or delete a note of this source (`NOTE_ADMIN_SOURCES`,
    or `None` for a row written before the source was tracked)."""
    return fonte is None or fonte in NOTE_ADMIN_SOURCES
