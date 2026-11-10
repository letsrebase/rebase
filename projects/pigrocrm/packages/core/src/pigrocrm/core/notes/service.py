from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.db import SoftDeleteMixin
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import NoteNotChangeable, NotFound, ValidationFailed
from pigrocrm.core.notes.models import (
    NOTE_ENTITY_TYPES,
    NOTE_READ_ONLY_SOURCES,
    Note,
    NoteRevision,
)
from pigrocrm.core.notes.schemas import NoteCreate, NoteRead, NoteRevisionRead, NoteUpdate
from pigrocrm.core.notes.writer import (
    KIND_DELETED,
    KIND_EDITED,
    SOURCES,
    append_note,
    display_name,
    may_change,
)
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.people.models import Person

ENTITY = "note"
# The parent table per `entity_type`: what a write looks up first, so a note can never
# point at a row that is not there, is archived, or is outside the caller's legal entities (the
# row-level policy of the parent answers «not there» for it).
_PARENTS: dict[str, type[Deal | Organization | Person]] = {
    "deal": Deal,
    "organization": Organization,
    "person": Person,
}


def _parent(session: Session, entity_type: str, entity_id: UUID) -> None:
    model = _PARENTS.get(entity_type)
    if model is None:
        raise ValidationFailed(ENTITY, "entity_type", f"uno fra {', '.join(NOTE_ENTITY_TYPES)}")
    # Every parent table soft-deletes through `SoftDeleteMixin`.
    parent = cast(SoftDeleteMixin | None, session.get(model, entity_id))
    if parent is None or parent.deleted_at is not None:
        raise NotFound(entity_type, entity_id)


class NoteService:
    """The notes thread of a deal, a customer or a person.

    Every add, edit and delete is an entry on the parent's timeline with the actor. The
    edit keeps the text it replaced there, since the row holds only the latest.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.activities = ActivityService(session)

    def add(self, entity_type: str, entity_id: UUID, data: NoteCreate, actor: Actor) -> NoteRead:
        actor.require_write("add_note")
        _parent(self.session, entity_type, entity_id)
        note = append_note(self.session, entity_type, entity_id, data.text, actor)
        self.session.commit()
        return self._reads([note], actor)[0]

    def update(self, note_id: UUID, data: NoteUpdate, actor: Actor) -> NoteRead:
        """Replaces the text of a note the actor may change. The same text again is no
        change and writes nothing."""
        actor.require_write("update_note")
        note = self._changeable(note_id, actor)
        if data.text == note.text:
            return self._reads([note], actor)[0]
        editor = self.session.get(User, actor.id) if actor.id is not None else None
        # The text the edit replaces, whole, in the transaction of the edit: the timeline
        # entry cannot keep it (its payload is cut at 2000 characters).
        self.session.add(
            NoteRevision(
                note_id=note.id,
                text=note.text,
                edited_by_id=editor.id if editor is not None else None,
                edited_by=display_name(editor) if editor is not None else None,
                source=SOURCES[actor.type],
            )
        )
        note.text = data.text
        note.edited_at = datetime.now(UTC)
        note.edited_by_id = actor.id
        self.session.flush()
        self.activities.record(
            note.entity_type,
            note.entity_id,
            KIND_EDITED,
            actor,
            {"note_id": note.id, "source": SOURCES[actor.type]},
        )
        self.session.commit()
        return self._reads([note], actor)[0]

    def delete(self, note_id: UUID, actor: Actor) -> None:
        """Soft-deletes a note the actor may change: it leaves every thread, the row
        stays."""
        actor.require_write("delete_note")
        note = self._changeable(note_id, actor)
        note.deleted_at = datetime.now(UTC)
        self.session.flush()
        self.activities.record(
            note.entity_type,
            note.entity_id,
            KIND_DELETED,
            actor,
            {"note_id": note.id, "source": SOURCES[actor.type]},
        )
        self.session.commit()

    def list_for_entity(
        self, entity_type: str, entity_id: UUID, actor: Actor, *, limit: int | None = None
    ) -> list[NoteRead]:
        """The thread, newest first, without the deleted notes. No page: a thread is a
        handful of remarks, and half of one is worse than none; `limit` is for the reader
        that states its own cap (the MCP's `get_deal` and its two siblings)."""
        _parent(self.session, entity_type, entity_id)
        query = (
            select(Note)
            .where(
                Note.entity_type == entity_type,
                Note.entity_id == entity_id,
                Note.deleted_at.is_(None),
            )
            .order_by(Note.created_at.desc(), Note.id.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return self._reads(self.session.scalars(query).all(), actor)

    def count_for_entity(self, entity_type: str, entity_id: UUID, actor: Actor) -> int:
        """How many live notes the record has: what a capped reader says it left out."""
        _parent(self.session, entity_type, entity_id)
        return int(
            self.session.scalar(
                select(func.count(Note.id)).where(
                    Note.entity_type == entity_type,
                    Note.entity_id == entity_id,
                    Note.deleted_at.is_(None),
                )
            )
            or 0
        )

    def revisions(self, note_id: UUID, actor: Actor) -> list[NoteRevisionRead]:
        """What the note said before each edit, newest first. A deleted note's too: the
        history is the point. Not there for a caller who cannot see the record."""
        note = self.session.get(Note, note_id)
        if note is None:
            raise NotFound(ENTITY, note_id)
        _parent(self.session, note.entity_type, note.entity_id)
        rows = self.session.scalars(
            select(NoteRevision)
            .where(NoteRevision.note_id == note_id)
            .order_by(NoteRevision.edited_at.desc(), NoteRevision.id.desc())
        )
        return [NoteRevisionRead.model_validate(row) for row in rows]

    def _changeable(self, note_id: UUID, actor: Actor) -> Note:
        note = self.session.get(Note, note_id)
        if note is None or note.deleted_at is not None:
            raise NotFound(ENTITY, note_id)
        # The parent's policy hides a note of a record outside the caller's legal entities: it
        # is not there for them, and reads as so rather than as a refusal.
        _parent(self.session, note.entity_type, note.entity_id)
        if not may_change(note, actor):
            raise NoteNotChangeable(note.id, recorded=note.source in NOTE_READ_ONLY_SOURCES)
        return note

    def _reads(self, notes: Sequence[Note], actor: Actor) -> list[NoteRead]:
        editors = {note.edited_by_id for note in notes if note.edited_by_id is not None}
        names: dict[UUID, str] = {}
        if editors:
            names = {
                user.id: display_name(user)
                for user in self.session.scalars(select(User).where(User.id.in_(editors)))
            }
        return [
            NoteRead.model_validate(note).model_copy(
                update={
                    "edited_by": names.get(note.edited_by_id)
                    if note.edited_by_id is not None
                    else None,
                    "editable": may_change(note, actor),
                }
            )
            for note in notes
        ]
