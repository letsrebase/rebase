"""Notes: what an admin, an agent or the person's own change says about a row (REB-856).

One thread per freelancer and per company, replacing both the row's own `note` column and
the append-only comments (ORB-59) at Lorenzo's request of 2026-10-29. A note says who wrote
it (`autore`, derived from the user, never text a caller supplies), by which door (`fonte`)
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
    AUTORE_MAX_LENGTH,
    NOTE_ADMIN_SOURCES,
    NOTE_ENTITY_TYPES,
    NOTE_MAX_LENGTH,
    NOTE_SOURCES,
    Company,
    Freelancer,
    Note,
    User,
)
from rebase_core.schemas import NoteRead, clean_multiline, note_is_editable

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
    return f"{user.nome} {user.cognome}".strip()[:AUTORE_MAX_LENGTH]


class NoteService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(
        self, entity_type: str, entity_id: UUID, testo: str, *, autore_id: UUID, fonte: str
    ) -> NoteRead:
        """Appends one note. The text is trimmed, may span several lines and is at most
        `NOTE_MAX_LENGTH` characters; the author is the user `autore_id` names, as their
        name reads now. The row must exist: a note on a freelancer that was never there is
        a `NotFound` naming it. An admin's note (`web`, `mcp`) is entered in the parent's
        trail; the person's and the hub's own are the record themselves."""
        if fonte not in NOTE_SOURCES:
            raise ValidationFailed(ENTITY, "fonte", f"una fra {', '.join(NOTE_SOURCES)}")
        parent = self._parent(entity_type)
        if self.session.get(parent, entity_id) is None:
            raise NotFound(entity_type, entity_id)
        author = self.session.get(User, autore_id)
        if author is None:
            raise NotFound("utente", autore_id)
        row = Note(
            entity_type=entity_type,
            entity_id=entity_id,
            testo=self._text(testo),
            autore=display_name(author),
            autore_id=autore_id,
            fonte=fonte,
        )
        self.session.add(row)
        self.session.commit()
        if fonte in NOTE_ADMIN_SOURCES:
            AdminActionService(self.session).record(
                entity_type,
                entity_id,
                "note_added",
                autore_id,
                {"nota_id": str(row.id), "fonte": fonte, "testo": row.testo},
            )
        return self._reads([row])[0]

    def _reads(self, rows: Sequence[Note]) -> list[NoteRead]:
        editors = {row.modificata_da_id for row in rows if row.modificata_da_id is not None}
        names: dict[UUID, str] = {}
        if editors:
            names = {
                user.id: display_name(user)
                for user in self.session.scalars(select(User).where(User.id.in_(editors)))
            }
        return [
            NoteRead(
                id=row.id,
                testo=row.testo,
                autore=row.autore,
                autore_id=row.autore_id,
                fonte=row.fonte,
                created_at=row.created_at,
                modificata_at=row.modificata_at,
                modificata_da=names.get(row.modificata_da_id) if row.modificata_da_id else None,
                modificabile=note_is_editable(row.fonte),
            )
            for row in rows
        ]

    def update(self, note_id: UUID, testo: str, *, admin_id: UUID, fonte: str) -> NoteRead:
        """Replaces the text of a note an admin may edit, and records who and by which
        door. The same text again is no change and writes nothing."""
        row = self._editable(note_id, fonte)
        text = self._text(testo)
        if text == row.testo:
            return self._reads([row])[0]
        before = row.testo
        row.testo = text
        row.modificata_at = utcnow()
        row.modificata_da_id = admin_id
        self.session.commit()
        AdminActionService(self.session).record(
            row.entity_type,
            row.entity_id,
            "note_edited",
            admin_id,
            {"nota_id": str(row.id), "fonte": fonte, "before": before, "after": text},
        )
        return self._reads([row])[0]

    def delete(self, note_id: UUID, *, admin_id: UUID, fonte: str) -> None:
        """Soft-deletes a note an admin may delete: it drops off every list and the trail
        keeps its text."""
        row = self._editable(note_id, fonte)
        row.eliminata_at = utcnow()
        row.eliminata_da_id = admin_id
        self.session.commit()
        AdminActionService(self.session).record(
            row.entity_type,
            row.entity_id,
            "note_deleted",
            admin_id,
            {"nota_id": str(row.id), "fonte": fonte, "testo": row.testo},
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
                Note.eliminata_at.is_(None),
            )
            .order_by(Note.created_at.desc(), Note.id.desc())
        ).all()
        return self._reads(rows)

    def _editable(self, note_id: UUID, fonte: str) -> Note:
        if fonte not in NOTE_ADMIN_SOURCES:
            raise ValidationFailed(ENTITY, "fonte", f"una fra {', '.join(NOTE_ADMIN_SOURCES)}")
        row = self.session.get(Note, note_id)
        if row is None or row.eliminata_at is not None:
            raise NotFound(ENTITY, note_id)
        if not note_is_editable(row.fonte):
            raise ValidationFailed(ENTITY, "fonte", NOT_EDITABLE)
        return row

    def _text(self, testo: str) -> str:
        try:
            text = clean_multiline(testo, what="un testo")
        except ValueError as exc:
            raise ValidationFailed(ENTITY, "testo", str(exc)) from exc
        if len(text) > NOTE_MAX_LENGTH:
            raise ValidationFailed(
                ENTITY, "testo", f"una nota può avere al massimo {NOTE_MAX_LENGTH} caratteri"
            )
        return text

    def _parent(self, entity_type: str) -> type[Freelancer] | type[Company]:
        parent = _PARENTS.get(entity_type)
        if parent is None:
            raise ValidationFailed(ENTITY, "entity_type", f"uno fra {', '.join(NOTE_ENTITY_TYPES)}")
        return parent
