"""What writes a note and says who may change it, apart from `NoteService` so the three
services that create a record with a first note can import it: `NoteService` reaches the
deal, customer and person models, and those packages' `__init__` load their services."""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.notes.models import AUTORE_MAX_LENGTH, NOTE_READ_ONLY_SOURCES, Note

# `Actor.type` to the source a note carries. Derived here and nowhere else: no caller
# passes a source.
SOURCES = {"user": "web", "mcp": "mcp", "system": "sistema", "rebase": "rebase"}

KIND_ADDED = "note_added"
KIND_EDITED = "note_edited"
KIND_DELETED = "note_deleted"


def display_name(user: User) -> str:
    return user.nome[:AUTORE_MAX_LENGTH]


def append_note(
    session: Session, entity_type: str, entity_id: UUID, testo: str, actor: Actor
) -> Note:
    """Writes one note and its timeline entry on `session`, and commits nothing.

    A function and not a `NoteService` method, like `serve_activities`: the three
    services that create a deal, a customer or a person with a first note write it in the
    transaction of the creation, so a creation that fails leaves no note, and a public
    method that neither gates nor commits would be a surface the MCP coverage sweep asks
    a tool for. It does not check the role: the caller has (`create_*` did, `NoteService.add`
    does). Must be the last thing before the caller's commit, for `ActivityService.record`'s
    reason.
    """
    author = session.get(User, actor.id) if actor.id is not None else None
    note = Note(
        entity_type=entity_type,
        entity_id=entity_id,
        testo=testo,
        autore_id=author.id if author is not None else None,
        autore=display_name(author) if author is not None else None,
        fonte=SOURCES[actor.type],
    )
    session.add(note)
    session.flush()
    ActivityService(session).record(
        entity_type, entity_id, KIND_ADDED, actor, {"nota_id": note.id, "fonte": note.fonte}
    )
    return note


def append_first_note(
    session: Session, entity_type: str, entity_id: UUID, note: str | None, actor: Actor
) -> None:
    """The `note` a Create carries, as the first note of the new record: nothing for an
    absent or blank one (the forms send `""` for a field left empty). Same transaction and
    same rule as `append_note`."""
    if note is not None and note.strip():
        append_note(session, entity_type, entity_id, note.strip(), actor)


def may_change(note: Note, actor: Actor) -> bool:
    """Whether `actor` may edit or delete `note`: they may write at all, the note is
    somebody's remark and not an event of the record, and it is theirs, or an admin's to
    change, or from before the thread (no author, no source: migration 0064 moved it in),
    which belongs to whoever may write the record."""
    if note.fonte in NOTE_READ_ONLY_SOURCES or not actor.can_write:
        return False
    if note.autore_id is None and note.fonte is None:
        return True
    return actor.can_administer or (note.autore_id is not None and note.autore_id == actor.id)
