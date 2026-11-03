"""Notes: what an admin, an agent or the person's own change says about a row (REB-856).

One thread per freelancer and per company, replacing both the row's own `note` column and
the append-only comments (ORB-59) at Lorenzo's request of 2026-10-29. A note says who wrote
it (`author`, derived from the user, never text a caller supplies), by which door (`source`)
and when, so its text carries none of that. An admin may edit or delete a note written by
an admin (`web`, `mcp`, or a row from before the source was tracked); a note the person or
the hub wrote is an event of the row's life and is refused. A delete is soft, and every
add, edit and delete by an admin is an `AdminAction` on the parent, with the door it came
through, so «who did what» is always in the trail.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService, utcnow
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.models import (
    AUTHOR_MAX_LENGTH,
    NOTE_ADMIN_SOURCES,
    NOTE_ENTITY_TYPES,
    NOTE_MAX_LENGTH,
    NOTE_SOURCES,
    Company,
    Freelancer,
    Note,
    User,
)
from rebase_core.note_schemas import NoteRead, note_is_editable
from rebase_core.schema_common import clean_multiline

ENTITY = "nota"
NOT_EDITABLE = (
    "Questa nota registra un evento della scheda, scritto dalla persona o dal sistema: "
    "non si modifica e non si cancella."
)
# The parent table per `entity_type`: what `add` looks up before it writes, so a note
# can never point at a row that is not there.
_PARENTS: dict[str, type[Freelancer] | type[Company]] = {
    "freelancer": Freelancer,
    "company": Company,
}


def display_name(user: User) -> str:
    return f"{user.first_name} {user.last_name}".strip()[:AUTHOR_MAX_LENGTH]


class NoteService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self, entity_type: str, entity_id: UUID, text: str, *, author_id: UUID, source: str
    ) -> NoteRead:
        """Appends one note. The text is trimmed, may span several lines and is at most
        `NOTE_MAX_LENGTH` characters; the author is the user `author_id` names, as their
        name reads now. The row must exist: a note on a freelancer that was never there is
        a `NotFound` naming it. An admin's note (`web`, `mcp`) is entered in the parent's
        trail; the person's and the hub's own are the record themselves."""
        if source not in NOTE_SOURCES:
            raise ValidationFailed(ENTITY, "source", f"una fra {', '.join(NOTE_SOURCES)}")
        parent = self._parent(entity_type)
        if self.session.get(parent, entity_id) is None:
            raise NotFound(entity_type, entity_id)
        author = self.session.get(User, author_id)
        if author is None:
            raise NotFound("utente", author_id)
        row = Note(
            entity_type=entity_type,
            entity_id=entity_id,
            text=self._text(text),
            author=display_name(author),
            author_id=author_id,
            source=source,
        )
        self.session.add(row)
        self.session.commit()
        if source in NOTE_ADMIN_SOURCES:
            AdminActionService(self.session).record(
                entity_type,
                entity_id,
                "note_added",
                author_id,
                {"note_id": str(row.id), "source": source, "text": row.text},
            )
        return self._reads([row])[0]

    def _reads(self, rows: Sequence[Note]) -> list[NoteRead]:
        editors = {row.edited_by_id for row in rows if row.edited_by_id is not None}
        names: dict[UUID, str] = {}
        if editors:
            names = {
                user.id: display_name(user)
                for user in self.session.scalars(select(User).where(User.id.in_(editors)))
            }
        return [
            NoteRead(
                id=row.id,
                text=row.text,
                author=row.author,
                author_id=row.author_id,
                source=row.source,
                created_at=row.created_at,
                edited_at=row.edited_at,
                edited_by=names.get(row.edited_by_id) if row.edited_by_id else None,
                editable=note_is_editable(row.source),
            )
            for row in rows
        ]

    def update(self, note_id: UUID, text: str, *, admin_id: UUID, source: str) -> NoteRead:
        """Replaces the text of a note an admin may edit, and records who and by which
        door. The same text again is no change and writes nothing."""
        row = self._editable(note_id, source)
        text = self._text(text)
        if text == row.text:
            return self._reads([row])[0]
        before = row.text
        row.text = text
        row.edited_at = utcnow()
        row.edited_by_id = admin_id
        self.session.commit()
        AdminActionService(self.session).record(
            row.entity_type,
            row.entity_id,
            "note_edited",
            admin_id,
            {"note_id": str(row.id), "source": source, "before": before, "after": text},
        )
        return self._reads([row])[0]

    def delete(self, note_id: UUID, *, admin_id: UUID, source: str) -> None:
        """Soft-deletes a note an admin may delete: it drops off every list and the trail
        keeps its text."""
        row = self._editable(note_id, source)
        row.deleted_at = utcnow()
        row.deleted_by_id = admin_id
        self.session.commit()
        AdminActionService(self.session).record(
            row.entity_type,
            row.entity_id,
            "note_deleted",
            admin_id,
            {"note_id": str(row.id), "source": source, "text": row.text},
        )

    def list(self, entity_type: str, entity_id: UUID) -> list[NoteRead]:
        """The whole thread, newest first, without the deleted notes. No page: a thread
        is a handful of remarks, and reading half of one is worse than reading none."""
        self._parent(entity_type)
        rows = self.session.scalars(
            select(Note)
            .where(
                Note.entity_type == entity_type,
                Note.entity_id == entity_id,
                Note.deleted_at.is_(None),
            )
            .order_by(Note.created_at.desc(), Note.id.desc())
        ).all()
        return self._reads(rows)

    def _editable(self, note_id: UUID, source: str) -> Note:
        if source not in NOTE_ADMIN_SOURCES:
            raise ValidationFailed(ENTITY, "source", f"una fra {', '.join(NOTE_ADMIN_SOURCES)}")
        row = self.session.get(Note, note_id)
        if row is None or row.deleted_at is not None:
            raise NotFound(ENTITY, note_id)
        if not note_is_editable(row.source):
            raise ValidationFailed(ENTITY, "source", NOT_EDITABLE)
        return row

    def _text(self, text: str) -> str:
        try:
            text = clean_multiline(text, what="un testo")
        except ValueError as exc:
            raise ValidationFailed(ENTITY, "text", str(exc)) from exc
        if len(text) > NOTE_MAX_LENGTH:
            raise ValidationFailed(
                ENTITY, "text", f"una nota può avere al massimo {NOTE_MAX_LENGTH} caratteri"
            )
        return text

    def _parent(self, entity_type: str) -> type[Freelancer] | type[Company]:
        parent = _PARENTS.get(entity_type)
        if parent is None:
            raise ValidationFailed(ENTITY, "entity_type", f"uno fra {', '.join(NOTE_ENTITY_TYPES)}")
        return parent
