"""Spec §9's own call site: the unbilled-`work_unit`-to-`InvoiceLine` assembly step.

Proves the Done-when literally: an approved, worked day becomes an ordinary
`InvoiceLine` through `InvoiceService.replace_lines`, with no schema change to
`Invoice`/`InvoiceLine`, and is never billed twice.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import AgentForbidden, NotFound, ValidationFailed
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceAnnul, InvoiceIssue
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.work_units.invoicing import (
    assemble_unbilled_work_units_into_new_invoice,
    billable_days,
    bind_work_units,
    unbilled_work_unit_lines,
)
from pigrocrm.core.work_units.models import WorkUnit, WorkUnitTransition

ADMIN = Actor(id=None, type="system", role="admin")


def _install_fiscal_and_legal_entity_profiles(session: Session) -> None:
    """The same minimal setup `tests/conftest.py::_invoice_service` installs --
    duplicated here rather than imported, since `conftest` is an ambiguous module
    name across this repository's three test roots (that file's own
    `extract_pdf_text` docstring explains why)."""
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
            cap="20124",
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


def _contract(db_session: Session) -> Contract:
    # Enough identity to appear on an issued document: some tests below issue the invoice.
    customer = Customer(
        legal_name=f"ACME {uuid4()}",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
    )
    db_session.add(contract)
    db_session.flush()
    return contract


def _rate_card(db_session: Session, contract: Contract, **overrides: object) -> RateCard:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
        "type": "daily",
        "amount": Decimal("500.00"),
        "unit": "day",
    }
    payload.update(overrides)
    rate_card = RateCard(**payload)  # type: ignore[arg-type]
    db_session.add(rate_card)
    db_session.flush()
    return rate_card


def _work_unit(db_session: Session, contract: Contract, **overrides: object) -> WorkUnit:
    payload: dict[str, object] = {
        "contract_id": contract.id,
        "date": date(2026, 3, 5),
        "quantity": Decimal("1.00"),
        "description": "Giornata di consulenza",
        "status": "worked",
    }
    payload.update(overrides)
    work_unit = WorkUnit(**payload)  # type: ignore[arg-type]
    db_session.add(work_unit)
    db_session.flush()
    return work_unit


def _bare_invoice_line(db_session: Session, contract: Contract) -> InvoiceLine:
    """A real `invoice_lines` row, built directly rather than through
    `InvoiceService` -- enough to satisfy `work_units.invoice_line_id`'s own FK for a
    test that is not itself about invoice construction."""
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


# ---- unbilled_work_unit_lines ---------------------------------------------------------


def test_two_days_in_the_same_month_on_the_same_rate_card_become_one_line(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, date=date(2026, 3, 5))
    _work_unit(db_session, contract, date=date(2026, 3, 12))

    lines, groups = unbilled_work_unit_lines(db_session, contract.id)

    assert len(lines) == 1
    assert lines[0].quantity == Decimal("2.00")
    assert lines[0].unit_price == Decimal("500.00")
    assert lines[0].unit_of_measure == "day"
    assert "marzo" in lines[0].description.lower()
    assert len(groups[0]) == 2


def test_days_in_different_months_become_separate_lines(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, date=date(2026, 3, 5))
    _work_unit(db_session, contract, date=date(2026, 4, 2))

    lines, groups = unbilled_work_unit_lines(db_session, contract.id)

    assert len(lines) == 2
    assert sorted(r.quantity for r in lines) == [Decimal("1.00"), Decimal("1.00")]
    assert sorted(len(g) for g in groups) == [1, 1]


def test_days_under_different_rate_cards_in_the_same_month_stay_separate(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 15))
    _rate_card(
        db_session, contract, valid_from=date(2026, 3, 16), valid_to=None, amount=Decimal("600.00")
    )
    _work_unit(db_session, contract, date=date(2026, 3, 5))
    _work_unit(db_session, contract, date=date(2026, 3, 20))

    lines, _ = unbilled_work_unit_lines(db_session, contract.id)

    assert sorted(r.unit_price for r in lines) == [Decimal("500.00"), Decimal("600.00")]


def test_a_proposed_day_is_never_gathered(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, status="proposed")

    lines, groups = unbilled_work_unit_lines(db_session, contract.id)
    assert lines == []
    assert groups == []


def test_an_already_billed_day_is_never_gathered_twice(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    existing_line = _bare_invoice_line(db_session, contract)
    _work_unit(db_session, contract, invoice_line_id=existing_line.id)

    lines, groups = unbilled_work_unit_lines(db_session, contract.id)
    assert lines == []
    assert groups == []


def test_a_day_with_no_covering_rate_card_is_refused_not_invented(db_session: Session) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valid_from=date(2026, 1, 1), valid_to=date(2026, 2, 28))
    _work_unit(db_session, contract, date=date(2026, 3, 5))  # outside the card's validity

    with pytest.raises(ValidationFailed):
        unbilled_work_unit_lines(db_session, contract.id)


def test_no_unbilled_days_returns_empty_not_an_error(db_session: Session) -> None:
    contract = _contract(db_session)
    lines, groups = unbilled_work_unit_lines(db_session, contract.id)
    assert lines == []
    assert groups == []


# ---- bind_work_units --------------------------------------------------------------


def test_bind_work_units_sets_invoice_line_id_per_group(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _work_unit(db_session, contract, date=date(2026, 3, 1))
    second = _work_unit(db_session, contract, date=date(2026, 3, 2))
    line_a = _bare_invoice_line(db_session, contract)
    line_b = _bare_invoice_line(db_session, contract)

    bind_work_units(db_session, [[first.id], [second.id]], [line_a.id, line_b.id], ADMIN)
    db_session.flush()

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.invoice_line_id == line_a.id
    assert second.invoice_line_id == line_b.id
    # The day reads «fatturato», not just linked: a day's page offers its moves from the
    # state, and the one that keeps a billed day from being revoked is this.
    assert first.status == second.status == "invoiced"


# ---- assemble_unbilled_work_units_into_new_invoice, end to end ------------------------


def test_assemble_creates_an_invoice_line_through_replace_lines_and_binds_the_days(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    first = _work_unit(db_session, contract, date=date(2026, 3, 5))
    second = _work_unit(db_session, contract, date=date(2026, 3, 12))

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    assert invoice.customer_id == contract.customer_id
    assert invoice.taxable_amount == Decimal("1000.00")  # 2 giorni * 500.00

    lines = list(
        db_session.execute(
            select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)
        ).scalars()
    )
    assert len(lines) == 1
    assert lines[0].total_price == Decimal("1000.00")

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.invoice_line_id == lines[0].id
    assert second.invoice_line_id == lines[0].id
    assert first.status == second.status == "invoiced"


def test_assemble_never_bills_the_same_day_twice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, date=date(2026, 3, 5))

    assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)

    with pytest.raises(ValidationFailed):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)


def test_assemble_refuses_a_contract_with_nothing_to_bill(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    with pytest.raises(ValidationFailed):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)


def test_assemble_raises_not_found_for_an_unknown_contract(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    with pytest.raises(NotFound):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, uuid4(), ADMIN)


def test_assemble_takes_only_the_days_of_the_period_asked_for(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    october = _work_unit(db_session, contract, date=date(2026, 10, 7))
    november = _work_unit(db_session, contract, date=date(2026, 11, 4))

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN, date(2026, 10, 1), date(2026, 10, 31)
    )

    assert invoice.taxable_amount == Decimal("500.00")
    db_session.refresh(october)
    db_session.refresh(november)
    assert (october.status, november.status) == ("invoiced", "worked")
    assert november.invoice_line_id is None


def test_an_agent_never_bills_a_contracts_days(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    agent = Actor(id=None, type="mcp", role="admin")

    with pytest.raises(AgentForbidden):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, agent)

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)


# ---- billable_days: what the page shows before anybody presses the button ---------------


def test_the_preview_is_the_invoice_the_button_would_make(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, date=date(2026, 10, 7))
    _work_unit(db_session, contract, date=date(2026, 10, 8), quantity=Decimal("0.50"))
    _work_unit(db_session, contract, date=date(2026, 11, 3))
    _work_unit(db_session, contract, date=date(2026, 11, 4), status="proposed")

    preview = billable_days(db_session, contract.id)

    assert preview.billable is True
    assert preview.months == ["2026-10", "2026-11"]
    assert [(r.month, r.work_units, r.quantity, r.amount) for r in preview.lines] == [
        ("2026-10", 2, Decimal("1.50"), Decimal("750.00")),
        ("2026-11", 1, Decimal("1.00"), Decimal("500.00")),
    ]
    assert (preview.work_units, preview.quantity, preview.taxable_amount) == (
        3,
        Decimal("2.50"),
        Decimal("1250.00"),
    )

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    assert invoice.taxable_amount == preview.taxable_amount
    # Billed: nothing left to show.
    assert billable_days(db_session, contract.id).lines == []


def test_the_preview_names_the_days_no_rate_card_covers_instead_of_refusing(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _rate_card(db_session, contract, valid_from=date(2026, 1, 1), valid_to=date(2026, 2, 28))
    _work_unit(db_session, contract, date=date(2026, 2, 3))
    _work_unit(db_session, contract, date=date(2026, 3, 5))

    preview = billable_days(db_session, contract.id)

    assert preview.billable is False
    assert preview.without_rate == [date(2026, 3, 5)]
    assert [r.month for r in preview.lines] == ["2026-02"]


def test_the_preview_counts_the_days_worked_without_the_approval_the_contract_requires(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    contract.requires_prior_approval = True
    db_session.flush()
    _rate_card(db_session, contract)
    _work_unit(db_session, contract, date=date(2026, 3, 5))  # no approval: redirected

    preview = billable_days(db_session, contract.id)

    assert preview.without_approval == 1
    assert preview.lines == []
    assert preview.billable is False


def test_the_preview_of_an_unknown_contract_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        billable_days(db_session, uuid4())


# ---- the days come back when the invoice they were on goes away -------------------------


def _states_of(db_session: Session, work_unit: WorkUnit) -> list[str]:
    return list(
        db_session.scalars(
            select(WorkUnitTransition.new_status)
            .where(WorkUnitTransition.work_unit_id == work_unit.id)
            .order_by(WorkUnitTransition.seq)
        )
    )


def test_deleting_the_draft_gives_its_days_back(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    InvoiceService(db_session, local_storage).soft_delete(invoice.id, ADMIN)

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)
    # The history says why it came back: the dispute in the middle is the graph's only way
    # home, not something anybody raised.
    assert _states_of(db_session, day) == ["worked", "invoiced", "disputed", "worked"]
    assert billable_days(db_session, contract.id).work_units == 1


def test_annulling_the_invoice_gives_its_days_back(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service = InvoiceService(db_session, local_storage)
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    db_session.refresh(day)
    assert day.status == "invoiced"

    service.annul(invoice.id, InvoiceAnnul(reason="importo errato"), ADMIN)

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)


def test_a_paid_day_stays_where_it_is_when_its_invoice_is_annulled(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    service = InvoiceService(db_session, local_storage)
    service.issue(invoice.id, InvoiceIssue(), ADMIN)
    db_session.refresh(day)
    day.status = "paid"
    db_session.flush()

    service.annul(invoice.id, InvoiceAnnul(reason="importo errato"), ADMIN)

    db_session.refresh(day)
    assert day.status == "paid"
    assert day.invoice_line_id is not None


# ---- a draft's days cannot be cut loose, and a failed binding leaves no draft -----------


def test_editing_the_lines_of_a_draft_that_bills_days_is_refused(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    from pigrocrm.core.errors import Conflict
    from pigrocrm.core.invoices.schemas import InvoiceLineIn

    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    with pytest.raises(Conflict):
        InvoiceService(db_session, local_storage).replace_lines(
            invoice.id,
            [InvoiceLineIn(description="Altro", quantity=Decimal("1"), unit_price=Decimal("1"))],
            ADMIN,
        )

    db_session.refresh(day)
    assert day.status == "invoiced" and day.invoice_line_id is not None


def test_a_day_billed_in_the_meantime_is_refused_not_repointed(
    db_session: Session,
) -> None:
    from pigrocrm.core.errors import Conflict

    contract = _contract(db_session)
    day = _work_unit(db_session, contract)
    first = _bare_invoice_line(db_session, contract)
    second = _bare_invoice_line(db_session, contract)
    bind_work_units(db_session, [[day.id]], [first.id], ADMIN)

    with pytest.raises(Conflict):
        bind_work_units(db_session, [[day.id]], [second.id], ADMIN)

    db_session.refresh(day)
    assert day.invoice_line_id == first.id


def test_a_day_billed_behind_the_sessions_back_is_refused_not_read_stale(
    db_session: Session,
) -> None:
    """Another transaction billed the day after this session loaded it: the locked re-read
    must overwrite the loaded copy (`populate_existing`), not hand back the stale one that
    still says `worked` with no line."""
    from sqlalchemy import update

    from pigrocrm.core.errors import Conflict

    contract = _contract(db_session)
    day = _work_unit(db_session, contract)
    first = _bare_invoice_line(db_session, contract)
    second = _bare_invoice_line(db_session, contract)
    assert (day.status, day.invoice_line_id) == ("worked", None)
    db_session.execute(
        update(WorkUnit)
        .where(WorkUnit.id == day.id)
        .values(status="invoiced", invoice_line_id=first.id)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(Conflict):
        bind_work_units(db_session, [[day.id]], [second.id], ADMIN)

    db_session.refresh(day)
    assert day.invoice_line_id == first.id


def test_a_failed_binding_takes_the_draft_back_out_and_leaves_the_days_ready(
    db_session: Session,
    local_storage: LocalFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pigrocrm.core.work_units import invoicing

    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("binding failed")

    monkeypatch.setattr(invoicing, "bind_work_units", explode)
    with pytest.raises(RuntimeError):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)
    monkeypatch.undo()

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)
    live = db_session.scalars(
        select(Invoice).where(
            Invoice.customer_id == contract.customer_id, Invoice.deleted_at.is_(None)
        )
    ).all()
    assert live == []
    assert billable_days(db_session, contract.id).work_units == 1


def test_a_usd_contract_is_refused_instead_of_billed_in_euros(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    contract.currency = "USD"
    db_session.flush()
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)

    preview = billable_days(db_session, contract.id)
    assert preview.billable is False and "USD" in (preview.block_reason or "")
    with pytest.raises(ValidationFailed):
        assemble_unbilled_work_units_into_new_invoice(db_session, local_storage, contract.id, ADMIN)

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)


def test_the_draft_is_issued_under_the_contracts_legal_entity_not_the_customers_current_one(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.legal_entities.repository import LegalEntityRepository
    from pigrocrm.core.legal_entities.schemas import LegalEntityCreate
    from pigrocrm.core.legal_entities.service import LegalEntityService

    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    _work_unit(db_session, contract)
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
    customer = db_session.get(Customer, contract.customer_id)
    assert customer is not None
    customer.legal_entity_id = second.id  # the customer moved after the contract was made
    db_session.flush()

    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )

    assert invoice.legal_entity_id == default.id


def test_deleting_the_draft_also_gives_back_a_day_resolved_to_worked_with_its_line_still_set(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _install_fiscal_and_legal_entity_profiles(db_session)
    contract = _contract(db_session)
    _rate_card(db_session, contract)
    day = _work_unit(db_session, contract)
    invoice = assemble_unbilled_work_units_into_new_invoice(
        db_session, local_storage, contract.id, ADMIN
    )
    # Disputed, then the dispute resolved: the graph's edge keeps the line.
    day.status = "disputed"
    db_session.flush()
    day.status = "worked"
    db_session.flush()
    assert day.invoice_line_id is not None

    InvoiceService(db_session, local_storage).soft_delete(invoice.id, ADMIN)

    db_session.refresh(day)
    assert (day.status, day.invoice_line_id) == ("worked", None)
    assert billable_days(db_session, contract.id).work_units == 1
