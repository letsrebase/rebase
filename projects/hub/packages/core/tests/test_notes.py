"""Notes on a freelancer or a company (REB-856): signed by the user and the door they came
through, edited and soft-deleted by an admin, each change in the parent's trail; the
person's and the hub's own events refused."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate, CompanyRead
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerRead
from rebase_core.freelancers import FreelancerService
from rebase_core.models import NOTE_MAX_LENGTH, Note, User
from rebase_core.notes import NoteService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    for table in ("admin_actions", "notes", "freelancers", "companies", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _freelancer(session: Session) -> FreelancerRead:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            last_name="Lovelace",
            email="ada@studio.it",
            daily_rate=Decimal("450"),
            posizione="Backend developer",
            remote="remote",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return read


def _company(session: Session) -> CompanyRead:
    read, _ = CompanyService(session).request(
        CompanyCreate(
            company_name="ACME Srl",
            contact_first_name="Wile",
            contact_last_name="E.",
            email="wile@acme.it",
            phone="+39 345 1234567",
            requested_role="Backend developer",
            project="Un backend developer per tre mesi.",
            period_from=date(2026, 10, 1),
            duration="3 mesi",
            daily_budget=Decimal("500"),
            remote="remote",
            headcount=1,
        )
    )
    return read


def _admin(session: Session, email: str = "ivan@rebase.it", nome: str = "Ivan") -> User:
    admin = User(email=email, nome=nome, last_name="Rossi", role="admin")
    session.add(admin)
    session.commit()
    return admin


def _trail(session: Session, entity: str, entity_id: UUID) -> list[tuple[str, dict]]:
    return [
        (entry.kind, entry.payload)
        for entry in reversed(AdminActionService(session).timeline(entity, entity_id))
    ]


def test_a_note_is_signed_by_the_user_and_the_thread_reads_newest_first(clean: Session) -> None:
    freelancer = _freelancer(clean)
    ivan, other = _admin(clean), _admin(clean, "lorenzo@rebase.it", "Lorenzo")
    notes = NoteService(clean)
    first = notes.add(
        "freelancer",
        freelancer.id,
        "  Sentito al telefono.\nRichiamare lunedì.  ",
        author_id=ivan.id,
        source="web",
    )
    second = notes.add(
        "freelancer", freelancer.id, "Ha mandato il portfolio.", author_id=other.id, source="mcp"
    )
    assert first.text == "Sentito al telefono.\nRichiamare lunedì."
    assert (first.author, first.author_id, first.source) == ("Ivan Rossi", ivan.id, "web")
    assert (second.author, second.source, second.editable) == ("Lorenzo Rossi", "mcp", True)
    assert [n.id for n in notes.list("freelancer", freelancer.id)] == [second.id, first.id]
    # The detail carries the thread, the list does not load it.
    read = FreelancerService(clean).get(freelancer.id)
    assert [n.text for n in read.note] == ["Ha mandato il portfolio.", first.text]
    assert FreelancerService(clean).list_recent().items[0].note == []


def test_a_company_thread_is_its_own_and_shows_on_its_detail(clean: Session) -> None:
    company, freelancer, ivan = _company(clean), _freelancer(clean), _admin(clean)
    notes = NoteService(clean)
    notes.add("company", company.id, "Budget confermato.", author_id=ivan.id, source="web")
    notes.add("freelancer", freelancer.id, "Altro thread.", author_id=ivan.id, source="web")
    assert [n.text for n in notes.list("company", company.id)] == ["Budget confermato."]
    assert [n.text for n in CompanyService(clean).get(company.id).note] == ["Budget confermato."]
    assert CompanyService(clean).list_recent().items[0].note == []


def test_a_note_needs_an_existing_row_a_text_a_known_author_and_source(clean: Session) -> None:
    freelancer, ivan = _freelancer(clean), _admin(clean)
    notes = NoteService(clean)

    def add(entity: str, row: UUID, text_: str, **extra: object) -> None:
        notes.add(entity, row, text_, **{"author_id": ivan.id, "source": "web", **extra})  # type: ignore[arg-type]

    with pytest.raises(NotFound):
        add("freelancer", uuid4(), "Nessuno qui.")
    with pytest.raises(NotFound):
        add("company", freelancer.id, "Un freelance non è un'azienda.")
    with pytest.raises(ValidationFailed) as kind:
        add("signup", freelancer.id, "Niente note sulle iscrizioni.")
    assert kind.value.details["field"] == "entity_type"
    with pytest.raises(ValidationFailed) as empty:
        add("freelancer", freelancer.id, "   \n ")
    assert empty.value.details["field"] == "text"
    with pytest.raises(ValidationFailed) as long:
        add("freelancer", freelancer.id, "x" * (NOTE_MAX_LENGTH + 1))
    assert "4000" in long.value.details["reason"]
    with pytest.raises(ValidationFailed) as control:
        add("freelancer", freelancer.id, "ok\x07")
    assert control.value.details["field"] == "text"
    with pytest.raises(ValidationFailed) as source:
        add("freelancer", freelancer.id, "Va bene.", source="altro")
    assert source.value.details["field"] == "source"
    with pytest.raises(NotFound):
        add("freelancer", freelancer.id, "Va bene.", author_id=uuid4())
    assert notes.list("freelancer", freelancer.id) == []


def test_add_edit_and_delete_each_write_the_trail_with_the_door(clean: Session) -> None:
    freelancer, ivan, lorenzo = _freelancer(clean), _admin(clean), _admin(clean, "l@rebase.it", "L")
    notes = NoteService(clean)
    note = notes.add("freelancer", freelancer.id, "Prima.", author_id=ivan.id, source="web")

    edited = notes.update(note.id, "Seconda.", admin_id=lorenzo.id, source="mcp")
    assert (edited.text, edited.edited_by, edited.author) == (
        "Seconda.",
        "L Rossi",
        "Ivan Rossi",
    )
    assert edited.edited_at is not None
    # The same text again is no change and writes nothing.
    notes.update(note.id, "Seconda.", admin_id=lorenzo.id, source="mcp")

    notes.delete(note.id, admin_id=ivan.id, source="web")
    assert notes.list("freelancer", freelancer.id) == []
    assert FreelancerService(clean).get(freelancer.id).note == []
    # Soft: the row is still there, and the trail names who deleted what.
    row = clean.get(Note, note.id)
    assert row is not None and row.deleted_by_id == ivan.id and row.text == "Seconda."

    trail = _trail(clean, "freelancer", freelancer.id)
    assert [kind for kind, _ in trail] == ["note_added", "note_edited", "note_deleted"]
    nid = str(note.id)
    assert trail[0][1] == {"note_id": nid, "source": "web", "text": "Prima."}
    assert trail[1][1] == {"note_id": nid, "source": "mcp", "before": "Prima.", "after": "Seconda."}
    assert trail[2][1] == {"note_id": nid, "source": "web", "text": "Seconda."}
    actors = {
        entry.kind: entry.admin_id
        for entry in AdminActionService(clean).timeline("freelancer", freelancer.id)
    }
    assert actors == {"note_added": ivan.id, "note_edited": lorenzo.id, "note_deleted": ivan.id}
    # A deleted note is gone for the editing calls too.
    with pytest.raises(NotFound):
        notes.update(note.id, "Terza.", admin_id=ivan.id, source="web")
    with pytest.raises(NotFound):
        notes.delete(note.id, admin_id=ivan.id, source="web")


def test_a_company_note_is_recorded_on_the_company(clean: Session) -> None:
    company, ivan = _company(clean), _admin(clean)
    note = NoteService(clean).add(
        "company", company.id, "Richiamare.", author_id=ivan.id, source="mcp"
    )
    NoteService(clean).delete(note.id, admin_id=ivan.id, source="web")
    assert [kind for kind, _ in _trail(clean, "company", company.id)] == [
        "note_added",
        "note_deleted",
    ]


def test_the_persons_and_the_systems_notes_are_events_an_admin_cannot_touch(
    clean: Session,
) -> None:
    freelancer, ivan = _freelancer(clean), _admin(clean)
    owner = clean.scalars(select(User).where(User.email == "ada@studio.it")).one()
    notes = NoteService(clean)
    mine = notes.add(
        "freelancer",
        freelancer.id,
        "CV caricato dalla persona",
        author_id=owner.id,
        source="person",
    )
    merged = notes.add(
        "freelancer", freelancer.id, "Scheda unita.", author_id=ivan.id, source="system"
    )
    assert (mine.editable, merged.editable) == (False, False)
    for note in (mine, merged):
        with pytest.raises(ValidationFailed) as edit:
            notes.update(note.id, "Riscritta.", admin_id=ivan.id, source="web")
        assert "non si modifica" in edit.value.details["reason"]
        with pytest.raises(ValidationFailed):
            notes.delete(note.id, admin_id=ivan.id, source="web")
    assert [n.text for n in notes.list("freelancer", freelancer.id)] == [
        "Scheda unita.",
        "CV caricato dalla persona",
    ]
    # The person's own events are the record themselves: nothing is added to the trail.
    assert _trail(clean, "freelancer", freelancer.id) == []


def test_a_note_from_before_the_source_was_tracked_may_be_edited(clean: Session) -> None:
    freelancer, ivan = _freelancer(clean), _admin(clean)
    old = Note(entity_type="freelancer", entity_id=freelancer.id, text="Vecchia.", author="Ivan")
    clean.add(old)
    clean.commit()
    read = NoteService(clean).list("freelancer", freelancer.id)[0]
    assert (read.source, read.editable) == (None, True)
    edited = NoteService(clean).update(old.id, "Nuova.", admin_id=ivan.id, source="web")
    assert edited.text == "Nuova."


def test_only_an_admin_door_may_edit(clean: Session) -> None:
    freelancer, ivan = _freelancer(clean), _admin(clean)
    note = NoteService(clean).add(
        "freelancer", freelancer.id, "Una.", author_id=ivan.id, source="web"
    )
    with pytest.raises(ValidationFailed) as door:
        NoteService(clean).update(note.id, "Due.", admin_id=ivan.id, source="person")
    assert door.value.details["field"] == "source"
