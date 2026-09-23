"""`ContractExpenseService`: the FK validation every create/update runs (contract,
category, document), the actor permission check, the friendlier pre-database
together-check ahead of the trigger's own `IntegrityError`, and that a caller of
this service observes the trigger's own computed `rimborsabile` -- not merely
what it asked for."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.schemas import ContractExpenseCreate, ContractExpenseUpdate
from pigrocrm.core.contract_expenses.service import ContractExpenseService
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.timetracking.models import CostCategory

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
READER = Actor(id=None, type="user", role="readonly")


def _contract(db_session: Session, *, politica_spese: dict[str, object] | None = None) -> Contract:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese=politica_spese or {"kind": "rimborsabile"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _category(db_session: Session, *, archiviata: bool = False) -> CostCategory:
    category = CostCategory(nome=f"Categoria {uuid4()}", posizione=0, archiviata=archiviata)
    db_session.add(category)
    db_session.flush()
    return category


def _document(db_session: Session, contract: Contract) -> Document:
    document = Document(contract_id=contract.id, tipo="altro", titolo="Ricevuta")
    db_session.add(document)
    db_session.flush()
    return document


def _create_data(category_id, **overrides: object) -> ContractExpenseCreate:
    payload: dict[str, object] = {
        "category_id": category_id,
        "data": date(2026, 3, 5),
        "importo": Decimal("100.00"),
        "descrizione": "Biglietto treno",
    }
    payload.update(overrides)
    return ContractExpenseCreate(**payload)  # type: ignore[arg-type]


# ---- create ---------------------------------------------------------------------------


def test_create_persists_and_reports_the_trigger_computed_rimborsabile(
    db_session: Session,
) -> None:
    contract = _contract(db_session, politica_spese={"kind": "rimborsabile"})
    category = _category(db_session)
    expense = ContractExpenseService(db_session).create(
        contract.id, _create_data(category.id), WRITER
    )
    assert expense.rimborsabile is True
    assert expense.contract_id == contract.id


def test_create_reports_flagged_not_reimbursable_without_failing_the_write(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    expense = ContractExpenseService(db_session).create(
        contract.id, _create_data(category.id, pre_autorizzata=False), WRITER
    )
    assert expense.rimborsabile is False


def test_create_on_an_unknown_contract_is_not_found(db_session: Session) -> None:
    category = _category(db_session)
    with pytest.raises(NotFound):
        ContractExpenseService(db_session).create(uuid4(), _create_data(category.id), WRITER)


def test_create_with_an_unknown_category_is_not_found(db_session: Session) -> None:
    contract = _contract(db_session)
    with pytest.raises(NotFound):
        ContractExpenseService(db_session).create(contract.id, _create_data(uuid4()), WRITER)


def test_create_with_an_archived_category_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session, archiviata=True)
    with pytest.raises(ValidationFailed):
        ContractExpenseService(db_session).create(contract.id, _create_data(category.id), WRITER)


def test_create_with_an_unknown_document_is_not_found(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    with pytest.raises(NotFound):
        ContractExpenseService(db_session).create(
            contract.id, _create_data(category.id, document_id=uuid4()), WRITER
        )


def test_create_with_a_real_document_succeeds(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    document = _document(db_session, contract)
    expense = ContractExpenseService(db_session).create(
        contract.id, _create_data(category.id, document_id=document.id), WRITER
    )
    assert expense.document_id == document.id


def test_a_reader_cannot_create_a_contract_expense(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    with pytest.raises(PermissionDenied):
        ContractExpenseService(db_session).create(contract.id, _create_data(category.id), READER)


def test_pre_autorizzata_true_without_a_reference_is_refused_before_the_database(
    db_session: Session,
) -> None:
    """Pydantic's own cross-field check on `ContractExpenseCreate`, ahead of the
    trigger's table it never even reaches."""
    with pytest.raises(ValueError):
        _create_data(uuid4(), pre_autorizzata=True, riferimento_autorizzazione=None)


# ---- update ---------------------------------------------------------------------------


def test_update_recomputes_rimborsabile_when_pre_autorizzata_changes(
    db_session: Session,
) -> None:
    contract = _contract(
        db_session,
        politica_spese={"kind": "rimborsabile", "richiede_preautorizzazione": True},
    )
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id, pre_autorizzata=False), WRITER)
    assert expense.rimborsabile is False

    updated = service.update(
        contract.id,
        expense.id,
        ContractExpenseUpdate(
            pre_autorizzata=True, riferimento_autorizzazione="email del 2026-03-02"
        ),
        WRITER,
    )
    assert updated.rimborsabile is True


def test_update_touches_only_the_supplied_fields(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id), WRITER)

    updated = service.update(
        contract.id, expense.id, ContractExpenseUpdate(importo=Decimal("250.00")), WRITER
    )
    assert updated.importo == Decimal("250.00")
    assert updated.descrizione == "Biglietto treno"


def test_update_refuses_clearing_importo_to_null(db_session: Session) -> None:
    """`importo` backs a `NOT NULL` column -- `reject_cleared_columns` refuses the
    attempt with a named field before it ever reaches `flush()`."""
    contract = _contract(db_session)
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id), WRITER)

    with pytest.raises(ValidationFailed):
        service.update(
            contract.id,
            expense.id,
            ContractExpenseUpdate(importo=None),
            WRITER,
        )


def test_update_enforces_the_together_check_against_the_merged_row(
    db_session: Session,
) -> None:
    """Flipping `pre_autorizzata` to true without also supplying a reference is
    refused against the *merged* state (the existing row has no reference), the
    friendlier error ahead of the trigger's own `IntegrityError`."""
    contract = _contract(db_session)
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id), WRITER)

    with pytest.raises(ValidationFailed):
        service.update(contract.id, expense.id, ContractExpenseUpdate(pre_autorizzata=True), WRITER)


def test_update_with_an_archived_category_is_refused(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    archived = _category(db_session, archiviata=True)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id), WRITER)

    with pytest.raises(ValidationFailed):
        service.update(
            contract.id, expense.id, ContractExpenseUpdate(category_id=archived.id), WRITER
        )


def test_update_on_an_expense_of_a_different_contract_is_not_found(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    other_contract = _contract(db_session)
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    expense = service.create(contract.id, _create_data(category.id), WRITER)

    with pytest.raises(NotFound):
        service.update(other_contract.id, expense.id, ContractExpenseUpdate(), WRITER)


# ---- list_for_contract ------------------------------------------------------------------


def test_list_for_contract_returns_every_expense_oldest_first(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    service = ContractExpenseService(db_session)
    second = service.create(contract.id, _create_data(category.id, data=date(2026, 3, 10)), WRITER)
    first = service.create(contract.id, _create_data(category.id, data=date(2026, 3, 1)), WRITER)

    items = service.list_for_contract(contract.id, WRITER)
    assert [item.id for item in items] == [first.id, second.id]


def test_list_for_contract_on_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        ContractExpenseService(db_session).list_for_contract(uuid4(), WRITER)
