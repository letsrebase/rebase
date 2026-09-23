"""Spec §7's own Done-when, second half: the unbilled-`contract_expense`-to-
`InvoiceLine` assembly step. Proves it literally: a reimbursable expense becomes an
ordinary `InvoiceLine` through `InvoiceService.replace_lines`, with no schema
change to `Invoice`/`InvoiceLine`, and is never billed twice; a non-reimbursable
expense is never gathered at all.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contract_expenses.invoicing import (
    assemble_unbilled_contract_expenses_into_new_invoice,
    bind_contract_expenses,
    unbilled_contract_expense_lines,
)
from pigrocrm.core.contract_expenses.models import ContractExpense
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=None, type="system", role="admin")


def _install_fiscal_and_emitter_profiles(session: Session) -> None:
    """The same minimal setup `test_work_units_invoicing.py` installs, duplicated
    here for the identical reason that file's own helper gives: `conftest` is an
    ambiguous module name across this repository's three test roots."""
    from pigrocrm.core.emitter.repository import EmitterProfileRepository
    from pigrocrm.core.emitter.schemas import EmitterProfileUpsert
    from pigrocrm.core.emitter.service import EmitterProfileService
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    if FiscalProfileRepository(session).get() is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    if EmitterProfileRepository(session).get() is None:
        EmitterProfileService(session).upsert(
            EmitterProfileUpsert(
                ragione_sociale="Studio Rossi",
                partita_iva="01234567890",
                codice_fiscale="HMCRFT00A01H501K",
                indirizzo="Via Vittorio Veneto 12",
                cap="20124",
                comune="Milano",
                provincia="MI",
                nazione="IT",
                email="mario@example.com",
            ),
            ADMIN,
        )


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


def _category(db_session: Session) -> CostCategory:
    category = CostCategory(nome=f"Categoria {uuid4()}", posizione=0)
    db_session.add(category)
    db_session.flush()
    return category


def _expense(
    db_session: Session, contract: Contract, category: CostCategory, **overrides: object
) -> ContractExpense:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "category_id": category.id,
        "data": date(2026, 3, 5),
        "importo": Decimal("120.00"),
        "descrizione": "Biglietto treno",
        "pre_autorizzata": False,
    }
    payload.update(overrides)
    expense = ContractExpense(**payload)  # type: ignore[arg-type]
    db_session.add(expense)
    db_session.flush()
    db_session.refresh(expense)  # the trigger may rewrite `rimborsabile` in flight
    return expense


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    """A real `invoice_lines` row, built directly rather than through
    `InvoiceService` -- enough to satisfy `contract_expenses.invoice_line_id`'s own
    FK for a test that is not itself about invoice construction."""
    invoice = Invoice(customer_id=contract.customer_id, tipo="fattura", stato="bozza")
    db_session.add(invoice)
    db_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        numero_linea=1,
        descrizione="Riga esistente",
        quantita=Decimal("1.000000"),
        prezzo_unitario=Decimal("1.000000"),
        prezzo_totale=Decimal("1.00"),
        aliquota_iva=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    return line


# ---- unbilled_contract_expense_lines ---------------------------------------------------


def test_two_reimbursable_expenses_become_two_separate_lines(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category, data=date(2026, 3, 1), importo=Decimal("50.00"))
    _expense(db_session, contract, category, data=date(2026, 3, 20), importo=Decimal("75.00"))

    righe, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert len(righe) == 2
    assert len(expense_ids) == 2
    assert sorted(r.prezzo_unitario for r in righe) == [Decimal("50.00"), Decimal("75.00")]


def test_a_non_reimbursable_expense_is_never_gathered(db_session: Session) -> None:
    contract = _contract(db_session, politica_spese={"kind": "non_rimborsabile"})
    category = _category(db_session)
    _expense(db_session, contract, category)

    righe, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert righe == []
    assert expense_ids == []


def test_an_already_billed_expense_is_never_gathered_twice(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    line = _bare_invoice_line(db_session, contract)
    _expense(db_session, contract, category, invoice_line_id=line.id)

    righe, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert righe == []
    assert expense_ids == []


def test_no_unbilled_expenses_returns_empty_not_an_error(db_session: Session) -> None:
    contract = _contract(db_session)
    righe, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert righe == []
    assert expense_ids == []


def test_the_line_description_is_the_expenses_own_description(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category, descrizione="Hotel per trasferta Milano")

    righe, _ = unbilled_contract_expense_lines(db_session, contract.id)
    assert righe[0].descrizione == "Hotel per trasferta Milano"


def test_the_cap_is_never_applied_by_this_step(db_session: Session) -> None:
    """Enforcing `importo_tetto` is explicitly out of this issue's scope (models.py,
    invoicing.py's own docstrings) -- the line prices the expense at its own
    recorded amount, whatever the cap says."""
    contract = _contract(
        db_session,
        politica_spese={
            "kind": "rimborsabile_con_tetto",
            "importo_tetto": "10.00",
            "richiede_preautorizzazione": False,
        },
    )
    category = _category(db_session)
    _expense(db_session, contract, category, importo=Decimal("500.00"))

    righe, _ = unbilled_contract_expense_lines(db_session, contract.id)
    assert righe[0].prezzo_unitario == Decimal("500.00")


# ---- bind_contract_expenses --------------------------------------------------------


def test_bind_contract_expenses_sets_invoice_line_id_per_expense(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    first = _expense(db_session, contract, category, data=date(2026, 3, 1))
    second = _expense(db_session, contract, category, data=date(2026, 3, 2))
    line_a = _bare_invoice_line(db_session, contract)
    line_b = _bare_invoice_line(db_session, contract)

    bind_contract_expenses(db_session, [first.id, second.id], [line_a.id, line_b.id])
    db_session.flush()

    assert db_session.get(ContractExpense, first.id).invoice_line_id == line_a.id
    assert db_session.get(ContractExpense, second.id).invoice_line_id == line_b.id


# ---- assemble_unbilled_contract_expenses_into_new_invoice, end to end -------------------


def test_assemble_creates_an_invoice_line_through_replace_lines_and_binds_the_expense(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    category = _category(db_session)
    expense = _expense(db_session, contract, category, importo=Decimal("42.00"))

    invoice = assemble_unbilled_contract_expenses_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    lines = list(
        db_session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
        ).scalars()
    )
    assert len(lines) == 1
    assert lines[0].prezzo_unitario == Decimal("42.00")

    db_session.refresh(expense)
    assert expense.invoice_line_id == lines[0].id


def test_assemble_never_bills_the_same_expense_twice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category)

    assemble_unbilled_contract_expenses_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    with pytest.raises(ValidationFailed):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, contract.id, ADMIN
        )


def test_assemble_refuses_a_contract_with_nothing_to_bill(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, contract.id, ADMIN
        )


def test_assemble_raises_not_found_for_an_unknown_contract(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_emitter_profiles(db_session)
    with pytest.raises(NotFound):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, uuid4(), ADMIN
        )
