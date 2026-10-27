"""Clause notes on a contract (REB-762): a person's reading of a clause, written beside
its words, and what an agent reads of it with the contract."""

from uuid import uuid4

import pydantic
import pytest
from sqlalchemy.orm import Session
from test_contracts_service import _create_payload, _customer

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.schemas import ClauseNoteCreate, ClauseNoteUpdate
from pigrocrm.core.contracts.service import ClauseNoteService, ContractService
from pigrocrm.core.errors import NotFound, PermissionDenied

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")

RINNOVO = ClauseNoteCreate(
    riferimento_clausola="Art. 8.3 Rinnovo",
    testo_letterale="Il contratto cessa alla scadenza salvo che il cliente non opti per il "
    "rinnovo; ciascuna parte può rifiutare il rinnovo con preavviso.",
    interpretazione_adottata="Rinnovo a scelta del cliente; il rifiuto del fornitore vale "
    "solo con 60 giorni di preavviso.",
)


def _contract_id(db_session: Session):  # type: ignore[no-untyped-def]
    return ContractService(db_session).create(_create_payload(_customer(db_session).id), ADMIN).id


def test_a_note_on_the_renewal_clause_is_read_with_the_contract(db_session: Session) -> None:
    """Done when: I add a note to the renewal clause and an agent reads it with
    `get_contract`, which is `ContractService.detail` over MCP."""
    contract_id = _contract_id(db_session)

    created = ClauseNoteService(db_session).create(contract_id, RINNOVO, ADMIN)

    detail = ContractService(db_session).detail(contract_id, ADMIN)
    assert [note.id for note in detail.note_clausole] == [created.id]
    [note] = detail.note_clausole
    assert note.riferimento_clausola == "Art. 8.3 Rinnovo"
    assert note.testo_letterale.startswith("Il contratto cessa")
    assert note.interpretazione_adottata.startswith("Rinnovo a scelta")
    assert note.note is None
    assert detail.titolo == "Consulenza CTO"


def test_a_contract_without_notes_has_an_empty_list_and_no_successor(db_session: Session) -> None:
    detail = ContractService(db_session).detail(_contract_id(db_session), ADMIN)
    assert detail.note_clausole == []
    assert detail.contratto_successivo_id is None


def test_notes_are_listed_oldest_first_and_only_the_contracts_own(db_session: Session) -> None:
    first, other = _contract_id(db_session), _contract_id(db_session)
    service = ClauseNoteService(db_session)
    a = service.create(first, RINNOVO, ADMIN)
    b = service.create(
        first,
        RINNOVO.model_copy(update={"riferimento_clausola": "Art. 12 Foro"}),
        ADMIN,
    )
    service.create(other, RINNOVO, ADMIN)

    assert [n.id for n in service.list_for_contract(first, ADMIN)] == [a.id, b.id]
    assert len(service.list_for_contract(other, ADMIN)) == 1


def test_a_note_is_corrected_and_its_optional_part_cleared(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    created = service.create(
        contract_id, RINNOVO.model_copy(update={"note": "Da chiedere al legale"}), ADMIN
    )

    corrected = service.update(
        contract_id,
        created.id,
        ClauseNoteUpdate(interpretazione_adottata="Rinnovo tacito di anno in anno."),
        ADMIN,
    )
    assert corrected.interpretazione_adottata == "Rinnovo tacito di anno in anno."
    assert corrected.note == "Da chiedere al legale"

    cleared = service.update(contract_id, created.id, ClauseNoteUpdate(note=None), ADMIN)
    assert cleared.note is None
    assert cleared.riferimento_clausola == "Art. 8.3 Rinnovo"


def test_a_note_cannot_be_emptied_and_a_blank_one_is_not_written(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    created = ClauseNoteService(db_session).create(contract_id, RINNOVO, ADMIN)
    with pytest.raises(pydantic.ValidationError):
        ClauseNoteUpdate(testo_letterale="")
    with pytest.raises(pydantic.ValidationError):
        ClauseNoteCreate(
            riferimento_clausola="Art. 1", testo_letterale="  ", interpretazione_adottata="x"
        )
    from pigrocrm.core.errors import ValidationFailed

    with pytest.raises(ValidationFailed) as caught:
        ClauseNoteService(db_session).update(
            contract_id, created.id, ClauseNoteUpdate(interpretazione_adottata=None), ADMIN
        )
    assert caught.value.details["field"] == "interpretazione_adottata"


def test_a_note_of_another_contract_or_an_unknown_one_is_not_found(db_session: Session) -> None:
    mine, other = _contract_id(db_session), _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(mine, RINNOVO, ADMIN)

    with pytest.raises(NotFound):
        service.update(other, note.id, ClauseNoteUpdate(note="x"), ADMIN)
    with pytest.raises(NotFound):
        service.update(mine, uuid4(), ClauseNoteUpdate(note="x"), ADMIN)
    with pytest.raises(NotFound):
        service.create(uuid4(), RINNOVO, ADMIN)
    with pytest.raises(NotFound):
        service.list_for_contract(uuid4(), ADMIN)
    with pytest.raises(NotFound):
        ContractService(db_session).detail(uuid4(), ADMIN)


def test_reading_is_open_and_writing_is_not(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(contract_id, RINNOVO, ADMIN)

    assert len(service.list_for_contract(contract_id, READONLY)) == 1
    with pytest.raises(PermissionDenied):
        service.create(contract_id, RINNOVO, READONLY)
    with pytest.raises(PermissionDenied):
        service.update(contract_id, note.id, ClauseNoteUpdate(note="x"), READONLY)


def test_a_note_written_or_corrected_is_on_the_contracts_timeline(db_session: Session) -> None:
    contract_id = _contract_id(db_session)
    service = ClauseNoteService(db_session)
    note = service.create(contract_id, RINNOVO, ADMIN)
    service.update(contract_id, note.id, ClauseNoteUpdate(note="riletto"), ADMIN)

    entries = ActivityService(db_session).timeline("contract", contract_id)

    assert [e.kind for e in entries][:2] == ["clause_note_updated", "clause_note_added"]
    assert entries[1].payload == {"riferimento_clausola": "Art. 8.3 Rinnovo"}
    assert "testo_letterale" not in str(entries[1].payload)


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
            RINNOVO.model_copy(update={"riferimento_clausola": f"Art. {number}"}),
            ADMIN,
        )

    detail = ContractService(db_session).detail(contract_id, ADMIN)

    assert [n.riferimento_clausola for n in detail.note_clausole] == ["Art. 2", "Art. 3"]
    assert len(service.list_for_contract(contract_id, ADMIN)) == 3
