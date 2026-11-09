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
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.models import CostCategory

ADMIN = Actor(id=None, type="system", role="admin")


def _install_fiscal_and_legal_entity_profiles(session: Session) -> None:
    """The same minimal setup `test_work_units_invoicing.py` installs, duplicated
    here for the identical reason that file's own helper gives: `conftest` is an
    ambiguous module name across this repository's three test roots."""
    from pigrocrm.core.fiscal.repository import FiscalProfileRepository
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    # The azienda first, always (REB-615: a bare default is already seeded and this
    # fills in its identity), then its fiscal profile when it has none.
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            tax_code="HMCRFT00A01H501K",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
            email="mario@example.com",
        ),
        ADMIN,
    )
    legal_entity = LegalEntityRepository(session).default()
    assert legal_entity is not None
    if FiscalProfileRepository(session).get(legal_entity.id) is None:
        FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)


def _contract(db_session: Session, *, expense_policy: dict[str, object] | None = None) -> Contract:
    customer = Organization(legal_name=f"ACME {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy=expense_policy or {"kind": "reimbursable"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _category(db_session: Session) -> CostCategory:
    category = CostCategory(name=f"Categoria {uuid4()}", position=0)
    db_session.add(category)
    db_session.flush()
    return category


def _expense(
    db_session: Session, contract: Contract, category: CostCategory, **overrides: object
) -> ContractExpense:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "category_id": category.id,
        "date": date(2026, 3, 5),
        "amount": Decimal("120.00"),
        "description": "Biglietto treno",
        "pre_authorized": False,
    }
    payload.update(overrides)
    expense = ContractExpense(**payload)  # type: ignore[arg-type]
    db_session.add(expense)
    db_session.flush()
    db_session.refresh(expense)  # the trigger may rewrite `reimbursable` in flight
    return expense


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    """A real `invoice_lines` row, built directly rather than through
    `InvoiceService` -- enough to satisfy `contract_expenses.invoice_line_id`'s own
    FK for a test that is not itself about invoice construction."""
    invoice = Invoice(customer_id=contract.customer_id, type="invoice", status="draft")
    db_session.add(invoice)
    db_session.flush()
    line = InvoiceLine(
        invoice_id=invoice.id,
        line_number=1,
        description="Riga esistente",
        quantity=Decimal("1.000000"),
        unit_price=Decimal("1.000000"),
        total_price=Decimal("1.00"),
        vat_rate=Decimal("22.00"),
    )
    db_session.add(line)
    db_session.flush()
    return line


# ---- unbilled_contract_expense_lines ---------------------------------------------------


def test_two_reimbursable_expenses_become_two_separate_lines(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category, date=date(2026, 3, 1), amount=Decimal("50.00"))
    _expense(db_session, contract, category, date=date(2026, 3, 20), amount=Decimal("75.00"))

    lines, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert len(lines) == 2
    assert len(expense_ids) == 2
    assert sorted(r.unit_price for r in lines) == [Decimal("50.00"), Decimal("75.00")]


def test_a_non_reimbursable_expense_is_never_gathered(db_session: Session) -> None:
    contract = _contract(db_session, expense_policy={"kind": "not_reimbursable"})
    category = _category(db_session)
    _expense(db_session, contract, category)

    lines, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert lines == []
    assert expense_ids == []


def test_an_already_billed_expense_is_never_gathered_twice(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    line = _bare_invoice_line(db_session, contract)
    _expense(db_session, contract, category, invoice_line_id=line.id)

    lines, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert lines == []
    assert expense_ids == []


def test_no_unbilled_expenses_returns_empty_not_an_error(db_session: Session) -> None:
    contract = _contract(db_session)
    lines, expense_ids = unbilled_contract_expense_lines(db_session, contract.id)
    assert lines == []
    assert expense_ids == []


def test_the_line_description_is_the_expenses_own_description(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category, description="Hotel per trasferta Milano")

    lines, _ = unbilled_contract_expense_lines(db_session, contract.id)
    assert lines[0].description == "Hotel per trasferta Milano"


def test_the_cap_is_never_applied_by_this_step(db_session: Session) -> None:
    """Enforcing `amount_cap` is explicitly out of this issue's scope (models.py,
    invoicing.py's own docstrings) -- the line prices the expense at its own
    recorded amount, whatever the cap says."""
    contract = _contract(
        db_session,
        expense_policy={
            "kind": "reimbursable_with_cap",
            "amount_cap": "10.00",
            "requires_pre_authorization": False,
        },
    )
    category = _category(db_session)
    _expense(db_session, contract, category, amount=Decimal("500.00"))

    lines, _ = unbilled_contract_expense_lines(db_session, contract.id)
    assert lines[0].unit_price == Decimal("500.00")


# ---- bind_contract_expenses --------------------------------------------------------


def test_bind_contract_expenses_sets_invoice_line_id_per_expense(db_session: Session) -> None:
    contract = _contract(db_session)
    category = _category(db_session)
    first = _expense(db_session, contract, category, date=date(2026, 3, 1))
    second = _expense(db_session, contract, category, date=date(2026, 3, 2))
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
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    category = _category(db_session)
    expense = _expense(db_session, contract, category, amount=Decimal("42.00"))

    invoice = assemble_unbilled_contract_expenses_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    lines = list(
        db_session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
        ).scalars()
    )
    assert len(lines) == 1
    assert lines[0].unit_price == Decimal("42.00")

    db_session.refresh(expense)
    assert expense.invoice_line_id == lines[0].id


def test_assemble_never_bills_the_same_expense_twice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
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
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, contract.id, ADMIN
        )


def test_assemble_raises_not_found_for_an_unknown_contract(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    with pytest.raises(NotFound):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, uuid4(), ADMIN
        )


def test_bind_refuses_an_expense_billed_in_the_meantime_instead_of_repointing_it(
    db_session: Session,
) -> None:
    """Another transaction billed the expense after this session loaded it: the locked
    re-read must overwrite the loaded copy and see the line, not repoint it."""
    from sqlalchemy import update

    from pigrocrm.core.errors import Conflict

    contract = _contract(db_session)
    category = _category(db_session)
    expense = _expense(db_session, contract, category)
    first = _bare_invoice_line(db_session, contract)
    second = _bare_invoice_line(db_session, contract)
    assert expense.invoice_line_id is None
    db_session.execute(
        update(ContractExpense)
        .where(ContractExpense.id == expense.id)
        .values(invoice_line_id=first.id)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(Conflict):
        bind_contract_expenses(db_session, [expense.id], [second.id])

    db_session.refresh(expense)
    assert expense.invoice_line_id == first.id


def test_the_draft_is_issued_under_the_contracts_legal_entity_not_the_organizations_current_one(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository
    from pigrocrm.core.legal_entities.schemas import LegalEntityCreate
    from pigrocrm.core.legal_entities.service import LegalEntityService

    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    category = _category(db_session)
    _expense(db_session, contract, category)
    default = LegalEntityRepository(db_session).default()
    assert default is not None
    second = LegalEntityService(db_session).create(
        LegalEntityCreate(
            name="rebase ltd",
            legal_name="Rebase Ltd",
            vat_number="GB123456789",
            country="GB",
            fiscal_profile=FiscalProfileUpsert(pack_id="non-it", default_vat_rate=Decimal("20.00")),
        ),
        ADMIN,
    )
    contract.legal_entity_id = default.id
    customer = db_session.get(Organization, contract.customer_id)
    assert customer is not None
    customer.legal_entity_id = second.id  # the customer moved after the contract was made
    db_session.flush()

    invoice = assemble_unbilled_contract_expenses_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    assert invoice.legal_entity_id == default.id


def test_a_failed_binding_leaves_no_draft_and_the_expense_ready(
    db_session: Session,
    local_storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pigrocrm.core.contract_expenses import invoicing

    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    category = _category(db_session)
    expense = _expense(db_session, contract, category)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("binding failed")

    monkeypatch.setattr(invoicing, "bind_contract_expenses", explode)
    with pytest.raises(RuntimeError):
        assemble_unbilled_contract_expenses_into_new_invoice(
            db_session, local_storage, contract.id, ADMIN
        )
    monkeypatch.undo()

    db_session.refresh(expense)
    assert expense.invoice_line_id is None
    drafts = db_session.scalars(
        select(Invoice).where(Invoice.customer_id == contract.customer_id)
    ).all()
    assert drafts == []
