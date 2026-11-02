"""The notes notes_of of a record as a test reads it: the live notes, oldest first."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.notes.models import Note


def notes_of(session: Session, entity_type: str, entity_id: UUID) -> list[Note]:
    return list(
        session.scalars(
            select(Note)
            .where(
                Note.entity_type == entity_type,
                Note.entity_id == entity_id,
                Note.deleted_at.is_(None),
            )
            .order_by(Note.created_at, Note.id)
        )
    )


def texts(session: Session, entity_type: str, entity_id: UUID) -> list[str]:
    return [note.testo for note in notes_of(session, entity_type, entity_id)]
