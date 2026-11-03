"""The notes thread of a deal, a customer and a person (REB-879): who wrote each note, when
and through which door, who may change it, and what the timeline of the record says."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.deals.service import DealService
from pigrocrm.core.errors import NoteNotChangeable, NotFound, PermissionDenied
from pigrocrm.core.notes.models import Note
from pigrocrm.core.notes.schemas import NoteCreate, NoteUpdate
from pigrocrm.core.notes.service import NoteService
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.people.service import PersonService

SYSTEM = Actor.system()
REBASE = Actor.rebase()


def _member(session: Session, name: str, role: str = "collaborator") -> User:
    user = User(email=f"{uuid4()}@example.test", password_hash="x", name=name, role=role)
    session.add(user)
    session.flush()
    return user


def _actor(user: User, type_: str = "user") -> Actor:
    return Actor(id=user.id, type=type_, role=user.role)  # type: ignore[arg-type]


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    return CustomerService(db_session).create(CustomerCreate(legal_name="ACME"), SYSTEM).id


@pytest.fixture
def records(db_session: Session, customer_id: UUID, seeded_open_stage_id: UUID) -> dict[str, UUID]:
    """One record of each kind a thread hangs on."""
    deal = DealService(db_session).create(
        DealCreate(
            name="Progetto", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
        ),
        SYSTEM,
    )
    person = PersonService(db_session).create(PersonCreate(first_name="Anna"), SYSTEM)
    return {"customer": customer_id, "deal": deal.id, "person": person.id}


@pytest.mark.parametrize("kind", ["customer", "deal", "person"])
def test_a_note_is_signed_dated_and_sourced_by_the_actor_not_the_caller(
    db_session: Session, records: dict[str, UUID], kind: str
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    before = datetime.now(UTC)

    note = NoteService(db_session).add(
        kind,
        records[kind],
        NoteCreate(text="  Richiamare martedì.\nPortare il preventivo.  "),
        _actor(giulia),
    )

    assert note.text == "Richiamare martedì.\nPortare il preventivo."
    assert (note.author, note.author_id, note.source) == ("Giulia Bianchi", giulia.id, "web")
    assert before <= note.created_at <= datetime.now(UTC)
    assert note.edited_at is None and note.edited_by is None
    assert note.editable is True
    assert (note.entity_type, note.entity_id) == (kind, records[kind])


def test_the_source_follows_the_actor_type(db_session: Session, records: dict[str, UUID]) -> None:
    giulia = _member(db_session, "Giulia Bianchi", "admin")
    service = NoteService(db_session)
    by = {
        "web": _actor(giulia),
        "mcp": _actor(giulia, "mcp"),
        "system": SYSTEM,
        "rebase": REBASE,
    }
    for source, actor in by.items():
        note = service.add("customer", records["customer"], NoteCreate(text=source), actor)
        assert note.source == source
        # Only a member has a name and an id to sign with.
        assert (note.author_id is not None) == (source in {"web", "mcp"})


def test_a_caller_cannot_sign_a_note_with_another_name_or_date() -> None:
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"text": "x", "author": "Marco"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"text": "x", "created_at": "2020-01-01T00:00:00Z"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"text": "x", "source": "web"})


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_note_is_refused(blank: str) -> None:
    with pytest.raises(ValidationError):
        NoteCreate(text=blank)


def test_a_note_cannot_hang_on_nothing_or_on_an_archived_record(
    db_session: Session, records: dict[str, UUID]
) -> None:
    service = NoteService(db_session)
    with pytest.raises(NotFound):
        service.add("deal", uuid4(), NoteCreate(text="x"), SYSTEM)
    DealService(db_session).soft_delete(records["deal"], SYSTEM)
    with pytest.raises(NotFound):
        service.add("deal", records["deal"], NoteCreate(text="x"), SYSTEM)
    with pytest.raises(NotFound):
        service.list_for_entity("deal", records["deal"], SYSTEM)


def test_a_readonly_member_reads_the_thread_and_writes_nothing(
    db_session: Session, records: dict[str, UUID]
) -> None:
    reader = _actor(_member(db_session, "Lettore", "readonly"))
    service = NoteService(db_session)
    written = service.add("deal", records["deal"], NoteCreate(text="Dal sistema"), SYSTEM)

    with pytest.raises(PermissionDenied):
        service.add("deal", records["deal"], NoteCreate(text="no"), reader)
    with pytest.raises(PermissionDenied):
        service.update(written.id, NoteUpdate(text="no"), reader)
    with pytest.raises(PermissionDenied):
        service.delete(written.id, reader)
    [seen] = service.list_for_entity("deal", records["deal"], reader)
    assert seen.editable is False


def test_the_thread_is_newest_first_without_the_deleted_and_can_be_capped(
    db_session: Session, records: dict[str, UUID]
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    first = service.add("person", records["person"], NoteCreate(text="uno"), capo)
    service.add("person", records["person"], NoteCreate(text="due"), capo)
    service.add("person", records["person"], NoteCreate(text="tre"), capo)
    service.add("customer", records["customer"], NoteCreate(text="di un altro"), capo)

    assert [n.text for n in service.list_for_entity("person", records["person"], capo)] == [
        "tre",
        "due",
        "uno",
    ]
    assert [
        n.text for n in service.list_for_entity("person", records["person"], capo, limit=2)
    ] == ["tre", "due"]
    service.delete(first.id, capo)
    assert [n.text for n in service.list_for_entity("person", records["person"], capo)] == [
        "tre",
        "due",
    ]


def test_an_author_edits_their_own_note_and_the_edit_says_who_and_when(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(text="Prima"), _actor(giulia))
    before = datetime.now(UTC)

    edited = service.update(note.id, NoteUpdate(text="Dopo"), _actor(giulia, "mcp"))

    assert edited.text == "Dopo"
    assert edited.edited_by == "Giulia Bianchi"
    assert edited.edited_at is not None and edited.edited_at >= before
    # Who wrote it and through which door does not change with an edit.
    assert (edited.author, edited.source, edited.created_at) == (
        "Giulia Bianchi",
        "web",
        note.created_at,
    )


def test_the_same_text_again_is_no_edit_and_leaves_no_trace(
    db_session: Session, records: dict[str, UUID]
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(text="Uguale"), capo)

    again = service.update(note.id, NoteUpdate(text="Uguale"), capo)

    assert again.edited_at is None
    kinds = [e.kind for e in ActivityService(db_session).timeline("deal", records["deal"])]
    assert "note_edited" not in kinds


def test_somebody_else_cannot_change_a_note_unless_they_are_an_admin(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    marco = _member(db_session, "Marco Verdi")
    capo = _member(db_session, "Capo", "admin")
    service = NoteService(db_session)
    note = service.add(
        "customer", records["customer"], NoteCreate(text="Di Giulia"), _actor(giulia)
    )

    # The flag says it before the click.
    [as_marco] = service.list_for_entity("customer", records["customer"], _actor(marco))
    [as_capo] = service.list_for_entity("customer", records["customer"], _actor(capo))
    assert (as_marco.editable, as_capo.editable) == (False, True)
    with pytest.raises(NoteNotChangeable):
        service.update(note.id, NoteUpdate(text="Di Marco"), _actor(marco))
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, _actor(marco))

    assert service.update(note.id, NoteUpdate(text="Corretta dal capo"), _actor(capo)).text == (
        "Corretta dal capo"
    )
    service.delete(note.id, _actor(capo))
    assert service.list_for_entity("customer", records["customer"], _actor(capo)) == []


@pytest.mark.parametrize("writer", [SYSTEM, REBASE], ids=["system", "rebase"])
def test_a_note_written_by_the_system_or_rebase_is_read_only_for_everybody(
    db_session: Session, records: dict[str, UUID], writer: Actor
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(text="Un evento"), writer)

    assert note.editable is False
    with pytest.raises(NoteNotChangeable):
        service.update(note.id, NoteUpdate(text="Altro"), capo)
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, capo)
    # Not even the actor that wrote it: it is an event of the record.
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, writer)
    [seen] = service.list_for_entity("deal", records["deal"], capo)
    assert (seen.text, seen.editable) == ("Un evento", False)


def test_a_note_moved_over_from_the_old_field_has_no_source_and_is_anyone_s_who_may_write(
    db_session: Session, records: dict[str, UUID]
) -> None:
    """What migration 0064 leaves: no author, no source («Nota precedente»)."""
    legacy = Note(entity_type="deal", entity_id=records["deal"], text="Da prima")
    db_session.add(legacy)
    db_session.flush()
    collaborator = _actor(_member(db_session, "Giulia"))
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)

    [seen] = service.list_for_entity("deal", records["deal"], collaborator)
    assert (seen.source, seen.author, seen.author_id, seen.editable) == (None, None, None, True)
    [seen_by_admin] = service.list_for_entity("deal", records["deal"], capo)
    assert seen_by_admin.editable is True
    # Nobody wrote it any more than anybody else: whoever may write the record may change it.
    assert service.update(legacy.id, NoteUpdate(text="Rivista"), collaborator).source is None
    service.delete(legacy.id, collaborator)
    assert service.list_for_entity("deal", records["deal"], capo) == []

    reader = _actor(_member(db_session, "Lettore", "readonly"))
    other = Note(entity_type="deal", entity_id=records["deal"], text="Un'altra da prima")
    db_session.add(other)
    db_session.flush()
    [seen_by_reader] = service.list_for_entity("deal", records["deal"], reader)
    assert seen_by_reader.editable is False


def test_an_agent_token_writes_notes_as_its_owner_through_the_mcp_door(
    db_session: Session, records: dict[str, UUID]
) -> None:
    marco = _member(db_session, "Marco Verdi")
    note = NoteService(db_session).add(
        "person", records["person"], NoteCreate(text="Scritta dall'agente"), _actor(marco, "mcp")
    )
    assert (note.author, note.author_id, note.source) == ("Marco Verdi", marco.id, "mcp")


def test_every_add_edit_and_delete_is_an_entry_on_the_parent_s_timeline(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    actor = _actor(giulia, "mcp")
    service = NoteService(db_session)

    note = service.add("deal", records["deal"], NoteCreate(text="Prima"), actor)
    service.update(note.id, NoteUpdate(text="Dopo"), actor)
    service.delete(note.id, actor)

    entries = [
        e
        for e in ActivityService(db_session).timeline("deal", records["deal"])
        if e.kind.startswith("note_")
    ]
    assert sorted(e.kind for e in entries) == ["note_added", "note_deleted", "note_edited"]
    for entry in entries:
        assert (entry.actor_id, entry.actor_type, entry.actor_name) == (
            giulia.id,
            "mcp",
            "Giulia Bianchi",
        )
        assert entry.payload["note_id"] == str(note.id)
        assert entry.payload["source"] == "mcp"
    edited = next(e for e in entries if e.kind == "note_edited")
    # The replaced text is a revision of the note, not a payload that would be cut.
    assert "before" not in edited.payload
    [revision] = service.revisions(note.id, actor)
    assert (revision.text, revision.edited_by, revision.source) == (
        "Prima",
        "Giulia Bianchi",
        "mcp",
    )


def test_an_edit_keeps_the_whole_text_it_replaced_even_past_the_timeline_cut(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _actor(_member(db_session, "Giulia Bianchi", "admin"))
    marco = _actor(_member(db_session, "Marco Verdi", "admin"), "mcp")
    service = NoteService(db_session)
    long_text = "Una riga di appunti sulla trattativa. " * 120  # about 4,500 characters
    note = service.add("deal", records["deal"], NoteCreate(text=long_text), giulia)

    service.update(note.id, NoteUpdate(text="Corta."), giulia)
    service.update(note.id, NoteUpdate(text="Corta, ancora."), marco)

    revisions = service.revisions(note.id, giulia)
    assert [(r.text, r.edited_by, r.source) for r in revisions] == [
        ("Corta.", "Marco Verdi", "mcp"),
        (long_text.strip(), "Giulia Bianchi", "web"),
    ]
    assert len(revisions[1].text) > 4000
    # A revision of a deleted note is still readable, and an unknown note is not there.
    service.delete(note.id, giulia)
    assert len(service.revisions(note.id, giulia)) == 2
    with pytest.raises(NotFound):
        service.revisions(uuid4(), giulia)


def test_the_first_note_on_creation_is_bounded_like_any_note() -> None:
    from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH

    for create in (
        lambda note: CustomerCreate(legal_name="A", note=note),
        lambda note: PersonCreate(first_name="A", note=note),
        lambda note: DealCreate(name="A", customer_id=uuid4(), note=note),
    ):
        create("x" * NOTE_MAX_LENGTH)
        with pytest.raises(ValidationError):
            create("x" * (NOTE_MAX_LENGTH + 1))


def test_the_count_of_a_thread_ignores_deleted_notes_and_the_cap(
    db_session: Session, records: dict[str, UUID]
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    notes = [
        service.add("customer", records["customer"], NoteCreate(text=str(n)), capo)
        for n in range(4)
    ]
    service.delete(notes[0].id, capo)
    assert service.count_for_entity("customer", records["customer"], capo) == 3
    assert len(service.list_for_entity("customer", records["customer"], capo, limit=2)) == 2


def test_a_note_on_creation_is_the_first_note_written_by_the_creating_actor(
    db_session: Session, seeded_open_stage_id: UUID
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    actor = _actor(giulia, "mcp")
    customer = CustomerService(db_session).create(
        CustomerCreate(legal_name="Beta", note="Cliente da una mail."), actor
    )
    deal = DealService(db_session).create(
        DealCreate(
            name="Progetto",
            customer_id=customer.id,
            pipeline_stage_id=seeded_open_stage_id,
            note="Primo contatto.",
        ),
        actor,
    )
    person = PersonService(db_session).create(
        PersonCreate(first_name="Anna", note="Referente."), actor
    )
    service = NoteService(db_session)

    for kind, record, text in (
        ("customer", customer.id, "Cliente da una mail."),
        ("deal", deal.id, "Primo contatto."),
        ("person", person.id, "Referente."),
    ):
        [note] = service.list_for_entity(kind, record, actor)
        assert (note.text, note.author, note.source) == (text, "Giulia Bianchi", "mcp")
        kinds = [e.kind for e in ActivityService(db_session).timeline(kind, record)]
        assert sorted(kinds) == ["created", "note_added"]


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_absent_or_blank_note_on_creation_makes_no_note(
    db_session: Session, empty: str | None
) -> None:
    customer = CustomerService(db_session).create(
        CustomerCreate(legal_name="Gamma", note=empty), SYSTEM
    )
    assert NoteService(db_session).list_for_entity("customer", customer.id, SYSTEM) == []


def test_a_creation_that_fails_leaves_no_note(db_session: Session) -> None:
    with pytest.raises(NotFound):
        DealService(db_session).create(
            DealCreate(name="Senza cliente", customer_id=uuid4(), note="Non deve restare"), SYSTEM
        )
    assert db_session.query(Note).filter(Note.text == "Non deve restare").count() == 0


def test_the_read_schemas_of_the_three_records_carry_no_note_and_updates_refuse_one(
    db_session: Session, records: dict[str, UUID]
) -> None:
    from pigrocrm.core.customers.schemas import CustomerRead, CustomerUpdate
    from pigrocrm.core.deals.schemas import DealRead, DealUpdate
    from pigrocrm.core.people.schemas import PersonRead, PersonUpdate

    for read in (CustomerRead, DealRead, PersonRead):
        assert "note" not in read.model_fields
    for update in (CustomerUpdate, DealUpdate, PersonUpdate):
        with pytest.raises(ValidationError):
            update.model_validate({"note": "x"})


def test_the_note_is_not_a_native_field_any_more() -> None:
    from pigrocrm.core.schema_registry import native_fields

    for kind in ("customer", "person", "deal"):
        assert "note" not in native_fields(kind)
    assert "note" in native_fields("task")
