"""Clause notes on a contract (REB-762): a person's reading of a clause, written beside
its words, and what an agent reads of it with the contract."""

from uuid import uuid4

import pydantic
import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.clause_note_service import ClauseNoteService
from pigrocrm.core.contracts.schemas import ClauseNoteCreate, ClauseNoteUpdate
from pigrocrm.core.contracts.service import ContractService
from pigrocrm.core.errors import NotFound, PermissionDenied

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")

RENEWAL = ClauseNoteCreate(
    clause_reference="Art. 8.3 Rinnovo",
    literal_text="Il contratto cessa alla scadenza salvo che il cliente non opti per il "
    "rinnovo; ciascuna parte può rifiutare il rinnovo con preavviso.",
    adopted_interpretation="Rinnovo a scelta del cliente; il rifiuto del fornitore vale "
    "solo con 60 giorni di preavviso.",
)


def _contract_id(db_session: Session):  # type: ignore[no-untyped-def]
    return (
        ContractService(db_session).create(_create_payload(make_customer(db_session).id), ADMIN).id
    )


def test_a_note_on_the_renewal_clause_is_read_with_the_contract(db_session: Session) -> None:
    """Done when: I add a note to the renewal clause and an agent reads it with
    `get_contract`, which is `ContractService.detail` over MCP."""
    contract_id = _contract_id(db_session)

    created = ClauseNoteService(db_session).create(contract_id, RENEWAL, ADMIN)

    detail = ContractService(db_session).detail(contract_id, ADMIN)
    assert [note.id for note in detail.note_clauses] == [created.id]
    [note] = detail.note_clauses
    assert note.clause_reference == "Art. 8.3 Rinnovo"
    assert note.literal_text.startswith("Il contratto cessa")
    assert note.adopted_interpretation.startswith("Rinnovo a scelta")
    assert note.note is None
    assert detail.title == "Consulenza CTO"


def test_a_contract_without_notes_has_an_empty_list_and_no_successor(db_session: Session) -> None:
    detail = ContractService(db_session).detail(_contract_id(db_session), ADMIN)
    assert detail.note_clauses == []
    assert detail.next_contract_id is None


def test_notes_are_listed_oldest_first_and_only_the_contracts_own(db_session: Session) -> None:
    first, other = _contract_id(db_session), _contract_id(db_session)
    service = ClauseNoteService(db_session)
    a = service.create(first, RENEWAL, ADMIN)
    b = service.create(
        first,
        RENEWAL.model_copy(update={"clause_reference": "Art. 12 Foro"}),
        ADMIN,
    )
    service.create(other, RENEWAL, ADMIN)

    assert [n.id for n in service.list_for_contract(first, ADMIN)] == [a.id, b.id]
    assert len(service.list_for_contract(other, ADMIN)) == 1


def test_a_note_is_corrected_and_its_optional_part_cleared(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    created = service.create(
        contract_id, RENEWAL.model_copy(update={"note": "Da chiedere al legale"}), ADMIN
    )

    corrected = service.update(
        contract_id,
        created.id,
        ClauseNoteUpdate(adopted_interpretation="Rinnovo tacito di anno in anno."),
        ADMIN,
    )
    assert corrected.adopted_interpretation == "Rinnovo tacito di anno in anno."
    assert corrected.note == "Da chiedere al legale"

    cleared = service.update(contract_id, created.id, ClauseNoteUpdate(note=None), ADMIN)
    assert cleared.note is None
    assert cleared.clause_reference == "Art. 8.3 Rinnovo"


def test_a_note_cannot_be_emptied_and_a_blank_one_is_not_written(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    created = ClauseNoteService(db_session).create(contract_id, RENEWAL, ADMIN)
    with pytest.raises(pydantic.ValidationError):
        ClauseNoteUpdate(literal_text="")
    with pytest.raises(pydantic.ValidationError):
        ClauseNoteCreate(clause_reference="Art. 1", literal_text="  ", adopted_interpretation="x")
    from pigrocrm.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed) as caught:
        ClauseNoteService(db_session).update(
            contract_id, created.id, ClauseNoteUpdate(adopted_interpretation=None), ADMIN
        )
    assert caught.value.details["field"] == "adopted_interpretation"


def test_a_note_of_another_contract_or_an_unknown_one_is_not_found(db_session: Session) -> None:
    mine, other = _contract_id(db_session), _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(mine, RENEWAL, ADMIN)

    with pytest.raises(NotFound):
        service.update(other, note.id, ClauseNoteUpdate(note="x"), ADMIN)
    with pytest.raises(NotFound):
        service.update(mine, uuid4(), ClauseNoteUpdate(note="x"), ADMIN)
    with pytest.raises(NotFound):
        service.create(uuid4(), RENEWAL, ADMIN)
    with pytest.raises(NotFound):
        service.list_for_contract(uuid4(), ADMIN)
    with pytest.raises(NotFound):
        ContractService(db_session).detail(uuid4(), ADMIN)


def test_reading_is_open_and_writing_is_not(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(contract_id, RENEWAL, ADMIN)

    assert len(service.list_for_contract(contract_id, READONLY)) == 1
    with pytest.raises(PermissionDenied):
        service.create(contract_id, RENEWAL, READONLY)
    with pytest.raises(PermissionDenied):
        service.update(contract_id, note.id, ClauseNoteUpdate(note="x"), READONLY)


def test_a_note_written_or_corrected_is_on_the_contracts_timeline(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(contract_id, RENEWAL, ADMIN)
    service.update(contract_id, note.id, ClauseNoteUpdate(note="riletto"), ADMIN)

    entries = ActivityService(db_session).timeline("contract", contract_id)

    assert [e.kind for e in entries][:2] == ["clause_note_updated", "clause_note_added"]
    assert entries[1].payload == {"clause_reference": "Art. 8.3 Rinnovo"}
    assert "literal_text" not in str(entries[1].payload)


def test_the_contract_carries_only_the_latest_notes_and_the_list_has_them_all(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pigrocrm.core.contracts import service as contracts_service

    monkeypatch.setattr(contracts_service, "DETAIL_NOTES_LIMIT", 2)
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    for number in (1, 2, 3):
        service.create(
            contract_id,
            RENEWAL.model_copy(update={"clause_reference": f"Art. {number}"}),
            ADMIN,
        )

    detail = ContractService(db_session).detail(contract_id, ADMIN)

    assert [n.clause_reference for n in detail.note_clauses] == ["Art. 2", "Art. 3"]
    assert detail.note_total_clauses == 3
    assert len(service.list_for_contract(contract_id, ADMIN)) == 3
