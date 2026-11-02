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


def _member(session: Session, nome: str, ruolo: str = "collaboratore") -> User:
    user = User(email=f"{uuid4()}@example.test", password_hash="x", nome=nome, ruolo=ruolo)
    session.add(user)
    session.flush()
    return user


def _actor(user: User, type_: str = "user") -> Actor:
    return Actor(id=user.id, type=type_, role=user.ruolo)  # type: ignore[arg-type]


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    return CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME"), SYSTEM).id


@pytest.fixture
def records(db_session: Session, customer_id: UUID, seeded_open_stage_id: UUID) -> dict[str, UUID]:
    """One record of each kind a thread hangs on."""
    deal = DealService(db_session).create(
        DealCreate(
            nome="Progetto", customer_id=customer_id, pipeline_stage_id=seeded_open_stage_id
        ),
        SYSTEM,
    )
    person = PersonService(db_session).create(PersonCreate(nome="Anna"), SYSTEM)
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
        NoteCreate(testo="  Richiamare martedì.\nPortare il preventivo.  "),
        _actor(giulia),
    )

    assert note.testo == "Richiamare martedì.\nPortare il preventivo."
    assert (note.autore, note.autore_id, note.fonte) == ("Giulia Bianchi", giulia.id, "web")
    assert before <= note.created_at <= datetime.now(UTC)
    assert note.modificata_at is None and note.modificata_da is None
    assert note.modificabile is True
    assert (note.entity_type, note.entity_id) == (kind, records[kind])


def test_the_source_follows_the_actor_type(db_session: Session, records: dict[str, UUID]) -> None:
    giulia = _member(db_session, "Giulia Bianchi", "admin")
    service = NoteService(db_session)
    by = {
        "web": _actor(giulia),
        "mcp": _actor(giulia, "mcp"),
        "sistema": SYSTEM,
        "rebase": REBASE,
    }
    for fonte, actor in by.items():
        note = service.add("customer", records["customer"], NoteCreate(testo=fonte), actor)
        assert note.fonte == fonte
        # Only a member has a name and an id to sign with.
        assert (note.autore_id is not None) == (fonte in {"web", "mcp"})


def test_a_caller_cannot_sign_a_note_with_another_name_or_date() -> None:
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"testo": "x", "autore": "Marco"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"testo": "x", "created_at": "2020-01-01T00:00:00Z"})
    with pytest.raises(ValidationError):
        NoteCreate.model_validate({"testo": "x", "fonte": "web"})


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_note_is_refused(blank: str) -> None:
    with pytest.raises(ValidationError):
        NoteCreate(testo=blank)


def test_a_note_cannot_hang_on_nothing_or_on_an_archived_record(
    db_session: Session, records: dict[str, UUID]
) -> None:
    service = NoteService(db_session)
    with pytest.raises(NotFound):
        service.add("deal", uuid4(), NoteCreate(testo="x"), SYSTEM)
    DealService(db_session).soft_delete(records["deal"], SYSTEM)
    with pytest.raises(NotFound):
        service.add("deal", records["deal"], NoteCreate(testo="x"), SYSTEM)
    with pytest.raises(NotFound):
        service.list_for_entity("deal", records["deal"], SYSTEM)


def test_a_readonly_member_reads_the_thread_and_writes_nothing(
    db_session: Session, records: dict[str, UUID]
) -> None:
    reader = _actor(_member(db_session, "Lettore", "readonly"))
    service = NoteService(db_session)
    written = service.add("deal", records["deal"], NoteCreate(testo="Dal sistema"), SYSTEM)

    with pytest.raises(PermissionDenied):
        service.add("deal", records["deal"], NoteCreate(testo="no"), reader)
    with pytest.raises(PermissionDenied):
        service.update(written.id, NoteUpdate(testo="no"), reader)
    with pytest.raises(PermissionDenied):
        service.delete(written.id, reader)
    [seen] = service.list_for_entity("deal", records["deal"], reader)
    assert seen.modificabile is False


def test_the_thread_is_newest_first_without_the_deleted_and_can_be_capped(
    db_session: Session, records: dict[str, UUID]
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    first = service.add("person", records["person"], NoteCreate(testo="uno"), capo)
    service.add("person", records["person"], NoteCreate(testo="due"), capo)
    service.add("person", records["person"], NoteCreate(testo="tre"), capo)
    service.add("customer", records["customer"], NoteCreate(testo="di un altro"), capo)

    assert [n.testo for n in service.list_for_entity("person", records["person"], capo)] == [
        "tre",
        "due",
        "uno",
    ]
    assert [
        n.testo for n in service.list_for_entity("person", records["person"], capo, limit=2)
    ] == ["tre", "due"]
    service.delete(first.id, capo)
    assert [n.testo for n in service.list_for_entity("person", records["person"], capo)] == [
        "tre",
        "due",
    ]


def test_an_author_edits_their_own_note_and_the_edit_says_who_and_when(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(testo="Prima"), _actor(giulia))
    before = datetime.now(UTC)

    edited = service.update(note.id, NoteUpdate(testo="Dopo"), _actor(giulia, "mcp"))

    assert edited.testo == "Dopo"
    assert edited.modificata_da == "Giulia Bianchi"
    assert edited.modificata_at is not None and edited.modificata_at >= before
    # Who wrote it and through which door does not change with an edit.
    assert (edited.autore, edited.fonte, edited.created_at) == (
        "Giulia Bianchi",
        "web",
        note.created_at,
    )


def test_the_same_text_again_is_no_edit_and_leaves_no_trace(
    db_session: Session, records: dict[str, UUID]
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(testo="Uguale"), capo)

    again = service.update(note.id, NoteUpdate(testo="Uguale"), capo)

    assert again.modificata_at is None
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
        "customer", records["customer"], NoteCreate(testo="Di Giulia"), _actor(giulia)
    )

    # The flag says it before the click.
    [as_marco] = service.list_for_entity("customer", records["customer"], _actor(marco))
    [as_capo] = service.list_for_entity("customer", records["customer"], _actor(capo))
    assert (as_marco.modificabile, as_capo.modificabile) == (False, True)
    with pytest.raises(NoteNotChangeable):
        service.update(note.id, NoteUpdate(testo="Di Marco"), _actor(marco))
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, _actor(marco))

    assert service.update(note.id, NoteUpdate(testo="Corretta dal capo"), _actor(capo)).testo == (
        "Corretta dal capo"
    )
    service.delete(note.id, _actor(capo))
    assert service.list_for_entity("customer", records["customer"], _actor(capo)) == []


@pytest.mark.parametrize("writer", [SYSTEM, REBASE], ids=["sistema", "rebase"])
def test_a_note_written_by_the_system_or_rebase_is_read_only_for_everybody(
    db_session: Session, records: dict[str, UUID], writer: Actor
) -> None:
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)
    note = service.add("deal", records["deal"], NoteCreate(testo="Un evento"), writer)

    assert note.modificabile is False
    with pytest.raises(NoteNotChangeable):
        service.update(note.id, NoteUpdate(testo="Altro"), capo)
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, capo)
    # Not even the actor that wrote it: it is an event of the record.
    with pytest.raises(NoteNotChangeable):
        service.delete(note.id, writer)
    [seen] = service.list_for_entity("deal", records["deal"], capo)
    assert (seen.testo, seen.modificabile) == ("Un evento", False)


def test_a_note_moved_over_from_the_old_field_has_no_source_and_is_anyone_s_who_may_write(
    db_session: Session, records: dict[str, UUID]
) -> None:
    """What migration 0064 leaves: no author, no source («Nota precedente»)."""
    legacy = Note(entity_type="deal", entity_id=records["deal"], testo="Da prima")
    db_session.add(legacy)
    db_session.flush()
    collaboratore = _actor(_member(db_session, "Giulia"))
    capo = _actor(_member(db_session, "Capo", "admin"))
    service = NoteService(db_session)

    [seen] = service.list_for_entity("deal", records["deal"], collaboratore)
    assert (seen.fonte, seen.autore, seen.autore_id, seen.modificabile) == (None, None, None, True)
    [seen_by_admin] = service.list_for_entity("deal", records["deal"], capo)
    assert seen_by_admin.modificabile is True
    # Nobody wrote it any more than anybody else: whoever may write the record may change it.
    assert service.update(legacy.id, NoteUpdate(testo="Rivista"), collaboratore).fonte is None
    service.delete(legacy.id, collaboratore)
    assert service.list_for_entity("deal", records["deal"], capo) == []

    reader = _actor(_member(db_session, "Lettore", "readonly"))
    other = Note(entity_type="deal", entity_id=records["deal"], testo="Un'altra da prima")
    db_session.add(other)
    db_session.flush()
    [seen_by_reader] = service.list_for_entity("deal", records["deal"], reader)
    assert seen_by_reader.modificabile is False


def test_an_agent_token_writes_notes_as_its_owner_through_the_mcp_door(
    db_session: Session, records: dict[str, UUID]
) -> None:
    marco = _member(db_session, "Marco Verdi")
    note = NoteService(db_session).add(
        "person", records["person"], NoteCreate(testo="Scritta dall'agente"), _actor(marco, "mcp")
    )
    assert (note.autore, note.autore_id, note.fonte) == ("Marco Verdi", marco.id, "mcp")


def test_every_add_edit_and_delete_is_an_entry_on_the_parent_s_timeline(
    db_session: Session, records: dict[str, UUID]
) -> None:
    giulia = _member(db_session, "Giulia Bianchi")
    actor = _actor(giulia, "mcp")
    service = NoteService(db_session)

    note = service.add("deal", records["deal"], NoteCreate(testo="Prima"), actor)
    service.update(note.id, NoteUpdate(testo="Dopo"), actor)
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
        assert entry.payload["nota_id"] == str(note.id)
        assert entry.payload["fonte"] == "mcp"
    edited = next(e for e in entries if e.kind == "note_edited")
    # The replaced text is a revision of the note, not a payload that would be cut.
    assert "prima" not in edited.payload
    [revision] = service.revisions(note.id, actor)
    assert (revision.testo, revision.edited_by, revision.fonte) == (
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
    note = service.add("deal", records["deal"], NoteCreate(testo=long_text), giulia)

    service.update(note.id, NoteUpdate(testo="Corta."), giulia)
    service.update(note.id, NoteUpdate(testo="Corta, ancora."), marco)

    revisions = service.revisions(note.id, giulia)
    assert [(r.testo, r.edited_by, r.fonte) for r in revisions] == [
        ("Corta.", "Marco Verdi", "mcp"),
        (long_text.strip(), "Giulia Bianchi", "web"),
    ]
    assert len(revisions[1].testo) > 4000
    # A revision of a deleted note is still readable, and an unknown note is not there.
    service.delete(note.id, giulia)
    assert len(service.revisions(note.id, giulia)) == 2
    with pytest.raises(NotFound):
        service.revisions(uuid4(), giulia)


def test_the_first_note_on_creation_is_bounded_like_any_note() -> None:
    from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH

    for create in (
        lambda note: CustomerCreate(ragione_sociale="A", note=note),
        lambda note: PersonCreate(nome="A", note=note),
        lambda note: DealCreate(nome="A", customer_id=uuid4(), note=note),
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
        service.add("customer", records["customer"], NoteCreate(testo=str(n)), capo)
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
        CustomerCreate(ragione_sociale="Beta", note="Cliente da una mail."), actor
    )
    deal = DealService(db_session).create(
        DealCreate(
            nome="Progetto",
            customer_id=customer.id,
            pipeline_stage_id=seeded_open_stage_id,
            note="Primo contatto.",
        ),
        actor,
    )
    person = PersonService(db_session).create(PersonCreate(nome="Anna", note="Referente."), actor)
    service = NoteService(db_session)

    for kind, record, text in (
        ("customer", customer.id, "Cliente da una mail."),
        ("deal", deal.id, "Primo contatto."),
        ("person", person.id, "Referente."),
    ):
        [note] = service.list_for_entity(kind, record, actor)
        assert (note.testo, note.autore, note.fonte) == (text, "Giulia Bianchi", "mcp")
        kinds = [e.kind for e in ActivityService(db_session).timeline(kind, record)]
        assert sorted(kinds) == ["created", "note_added"]


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_absent_or_blank_note_on_creation_makes_no_note(
    db_session: Session, empty: str | None
) -> None:
    customer = CustomerService(db_session).create(
        CustomerCreate(ragione_sociale="Gamma", note=empty), SYSTEM
    )
    assert NoteService(db_session).list_for_entity("customer", customer.id, SYSTEM) == []


def test_a_creation_that_fails_leaves_no_note(db_session: Session) -> None:
    with pytest.raises(NotFound):
        DealService(db_session).create(
            DealCreate(nome="Senza cliente", customer_id=uuid4(), note="Non deve restare"), SYSTEM
        )
    assert db_session.query(Note).filter(Note.testo == "Non deve restare").count() == 0


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
    assert "note" in native_fields("attivita")
