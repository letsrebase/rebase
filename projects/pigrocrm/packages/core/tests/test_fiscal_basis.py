"""One revenue and cost basis per regime (REB-1071, taxes design § 2.1).

`year_basis` is the only function that decides what belongs to a legal entity's year, reading the
basis the pack's law names: a person's and the flat-rate regime's income is what was collected and
paid in the year (cash), an srl's is what accrued in it. The same invoices, collected in a
different year from the one they were issued in, land in different years under the two, and every
path that computes an estimate answers the figure the basis gives.

Rows are built by hand, with fixed years (2026 is the first year the ordinary and company packs
have a law for), so nothing here depends on the clock.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import UUID, uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.fiscal.basis import financial_year, year_basis
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import (
    IT_COMPANY_PACK,
    IT_FLAT_RATE_PACK,
    IT_STANDARD_PACK,
    NON_IT_PACK,
    SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.models import PayrollMonthClosure
from pigrocrm.core.staff.schemas import DirectorCreate, EmployeeCreate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.timetracking.models import CostCategory

D = dt.date
M = Decimal
ADMIN = Actor(id=None, type="system", role="admin")
STAFF_ADMIN = Actor(id=uuid4(), type="user", role="admin")
_NUMBERS = count(1)

# Issued in one year, collected in the next: the case the two bases put in different years.
LATE = D(2026, 12, 10)  # 1000.00, collected on 2027-01-15
EARLY = D(2027, 3, 1)  # 500.00, collected on 2027-04-01
OPEN = D(2027, 11, 1)  # 200.00, never collected


def _entity(
    session: Session,
    pack_id: str,
    *,
    year_end: tuple[int, int] = (12, 31),
    **profile: object,
) -> LegalEntity:
    entity = LegalEntity(
        name=f"basis {uuid4().hex[:6]}",
        legal_name="Basis S.r.l.",
        country="IT",
        fiscal_year_end_month=year_end[0],
        fiscal_year_end_day=year_end[1],
    )
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code="RF01", **profile)
    )
    session.flush()
    return entity


def _invoice(
    session: Session,
    entity: LegalEntity,
    issued: dt.date,
    taxable: str,
    *,
    collected: dt.date | None = None,
    accrual_from: dt.date | None = None,
    accrual_to: dt.date | None = None,
    lines: list[tuple[str, str]] | None = None,
) -> Invoice:
    """An issued invoice with its due item (the row hook writes it), paid in full on `collected`
    when given. `lines` are `(description, price)`; one line carries the whole amount otherwise."""
    amount = M(taxable)
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=issued.year,
        number=next(_NUMBERS) + 100_000,
        issue_date=issued,
        due_date=issued + dt.timedelta(days=30),
        accrual_from=accrual_from,
        accrual_to=accrual_to,
        taxable_amount=amount,
        vat_amount=M("0.00"),
        stamp_duty=M("0.00"),
        total=amount,
        payment_status="unpaid" if collected is None else "paid",
        payment_date=collected,
    )
    session.add(invoice)
    session.flush()
    for position, (description, price) in enumerate(lines or [("Consulenza", taxable)], start=1):
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=position,
                description=description,
                quantity=M("1.000000"),
                unit_price=M(price),
                total_price=M(price),
                vat_rate=M("0.00"),
                vat_nature="N2.2",
            )
        )
    session.flush()
    return invoice


def _invoicing(session: Session, entity: LegalEntity) -> None:
    """The three invoices of the cases above, the same on every entity that is given them."""
    _invoice(session, entity, LATE, "1000.00", collected=D(2027, 1, 15))
    _invoice(session, entity, EARLY, "500.00", collected=D(2027, 4, 1))
    _invoice(session, entity, OPEN, "200.00")


def _category(session: Session) -> CostCategory:
    category = session.scalars(select(CostCategory)).first()
    if category is None:
        category = CostCategory(name="Servizi")
        session.add(category)
        session.flush()
    return category


def _purchase(
    session: Session,
    entity: LegalEntity | None,
    on: dt.date,
    cost: str,
    *,
    status: str = "registered",
) -> Purchase:
    purchase = Purchase(
        legal_entity_id=None if entity is None else entity.id,
        category_id=_category(session).id,
        kind="expense",
        status=status,
        date=on,
        amount=M(cost),
        cost_amount=M(cost),
        description="Acquisto",
        custom_fields={},
    )
    session.add(purchase)
    session.flush()
    return purchase


def _due_item(
    session: Session, entity: LegalEntity, *, source_kind: str, source_id: UUID | None, amount: str
) -> DueItem:
    """An outgoing due item: the purchase's own, or a payroll item owed to the tax office."""
    if source_kind == "payroll":
        owed: dict[str, object] = {"counterparty_type": "tax_office", "tax": "payroll_inps"}
    else:
        supplier = Organization(legal_name="Fornitore", country="IT", roles=["supplier"])
        session.add(supplier)
        session.flush()
        owed = {"counterparty_type": "organization", "organization_id": supplier.id}
    item = DueItem(
        legal_entity_id=entity.id,
        direction="out",
        state="expected",
        amount=M(amount),
        source_kind=source_kind,
        source_id=source_id,
        own_date=D(2026, 1, 1),
        due_date=D(2026, 1, 1),
        **owed,
    )
    session.add(item)
    session.flush()
    return item


def _pay(session: Session, item: DueItem, amount: str, on: dt.date) -> DueItemSettlement:
    settlement = DueItemSettlement(
        due_item_id=item.id,
        legal_entity_id=item.legal_entity_id,
        amount=M(amount),
        settled_on=on,
        recorded_by_type="system",
    )
    session.add(settlement)
    session.flush()
    return settlement


def _basis(session: Session, entity: LegalEntity, year: int, pack_id: str):  # type: ignore[no-untyped-def]
    pack = {
        "it-flat-rate": IT_FLAT_RATE_PACK,
        "it-standard": IT_STANDARD_PACK,
        "it-company": IT_COMPANY_PACK,
        "non-it": NON_IT_PACK,
    }[pack_id]
    return year_basis(session, entity, year, pack)


# --- the same invoices, in different years --------------------------------------------------


def test_the_same_invoices_land_in_different_years_under_cash_and_accrual(
    db_session: Session,
) -> None:
    flat = _entity(db_session, "it-flat-rate")
    company = _entity(db_session, "it-company")
    for entity in (flat, company):
        _invoicing(db_session, entity)

    # Cash: the December invoice was collected in January, so it is 2027's.
    cash_2026 = _basis(db_session, flat, 2026, "it-flat-rate")
    cash_2027 = _basis(db_session, flat, 2027, "it-flat-rate")
    assert cash_2026 is not None and cash_2027 is not None
    assert cash_2026.basis == cash_2027.basis == "cash"
    assert cash_2026.revenue == M("0.00")
    assert cash_2027.revenue == M("1500.00")
    # What the year invoiced and did not collect by its end is said, in each year.
    assert [(line.code, line.amount) for line in cash_2026.left_out] == [
        ("not_collected", M("1000.00"))
    ]
    assert [(line.code, line.amount) for line in cash_2027.left_out] == [
        ("not_collected", M("200.00"))
    ]

    # Accrual: the same invoices by their competence, whatever was paid.
    accrual_2026 = _basis(db_session, company, 2026, "it-company")
    accrual_2027 = _basis(db_session, company, 2027, "it-company")
    assert accrual_2026 is not None and accrual_2027 is not None
    assert accrual_2026.basis == accrual_2027.basis == "accrual"
    assert accrual_2026.revenue == M("1000.00")
    assert accrual_2027.revenue == M("700.00")


def test_the_basis_carries_the_laws_own_words_and_the_period(db_session: Session) -> None:
    flat = _entity(db_session, "it-flat-rate")
    company = _entity(db_session, "it-company")
    cash = _basis(db_session, flat, 2026, "it-flat-rate")
    accrual = _basis(db_session, company, 2026, "it-company")
    assert cash is not None and accrual is not None
    assert (cash.period_from, cash.period_to) == (D(2026, 1, 1), D(2026, 12, 31))
    assert (accrual.period_from, accrual.period_to) == (D(2026, 1, 1), D(2026, 12, 31))
    assert "principio di cassa" in cash.wording and "L. 23 dicembre 2014 n. 190" in cash.source
    assert "esercizio di competenza" in accrual.wording and "art. 109" in accrual.source
    assert cash.label == "Incassato nel 2026, secondo il principio di cassa"
    assert accrual.label == "Ricavi e costi di competenza del 2026"
    assert cash.legal_entity_id == flat.id and accrual.legal_entity_id == company.id


def test_an_accrual_invoice_belongs_to_the_period_it_declares(db_session: Session) -> None:
    """Issued in January 2027 for December 2026's work: 2026's revenue under accrual, and the
    issue date only decides when no period is declared."""
    company = _entity(db_session, "it-company")
    _invoice(
        db_session,
        company,
        D(2027, 1, 10),
        "800.00",
        accrual_from=D(2026, 12, 1),
        accrual_to=D(2026, 12, 31),
    )
    _invoice(db_session, company, D(2027, 1, 20), "100.00")
    assert _basis(db_session, company, 2026, "it-company").revenue == M("800.00")  # type: ignore[union-attr]
    assert _basis(db_session, company, 2027, "it-company").revenue == M("100.00")  # type: ignore[union-attr]


def test_an_accrual_year_follows_the_legal_entitys_year_end(db_session: Session) -> None:
    """A financial year ending on 30 June 2027 is the tax year 2027, from 1 July 2026."""
    company = _entity(db_session, "it-company", year_end=(6, 30))
    assert financial_year(company, 2027) == (D(2026, 7, 1), D(2027, 6, 30))
    _invoice(db_session, company, D(2026, 9, 1), "300.00")
    _invoice(db_session, company, D(2027, 5, 1), "400.00")
    _invoice(db_session, company, D(2027, 8, 1), "900.00")
    basis = _basis(db_session, company, 2027, "it-company")
    assert basis is not None
    assert basis.revenue == M("700.00")
    assert (basis.period_from, basis.period_to) == (D(2026, 7, 1), D(2027, 6, 30))
    assert basis.label == "Ricavi e costi di competenza dell'esercizio dal 01/07/2026 al 30/06/2027"


def test_a_leap_day_year_end_falls_on_the_28th_when_there_is_no_29th(db_session: Session) -> None:
    company = _entity(db_session, "it-company", year_end=(2, 29))
    assert financial_year(company, 2027) == (D(2026, 3, 1), D(2027, 2, 28))
    assert financial_year(company, 2028) == (D(2027, 3, 1), D(2028, 2, 29))


# --- cash revenue ----------------------------------------------------------------------------


def test_a_payment_counts_in_the_year_it_arrived_for_its_share(db_session: Session) -> None:
    flat = _entity(db_session, "it-flat-rate")
    invoice = _invoice(db_session, flat, D(2026, 12, 1), "1000.00")
    item = db_session.scalars(select(DueItem).where(DueItem.source_id == invoice.id)).one()
    _pay(db_session, item, "400.00", D(2026, 12, 30))
    reversed_payment = _pay(db_session, item, "100.00", D(2026, 12, 31))
    reversed_payment.reversed_at = dt.datetime(2027, 1, 2, tzinfo=dt.UTC)
    reversed_payment.reversed_by_type = "system"
    _pay(db_session, item, "600.00", D(2027, 1, 2))
    db_session.flush()
    assert _basis(db_session, flat, 2026, "it-flat-rate").revenue == M("400.00")  # type: ignore[union-attr]
    assert _basis(db_session, flat, 2027, "it-flat-rate").revenue == M("600.00")  # type: ignore[union-attr]


def test_the_flat_rate_surcharge_is_taken_off_what_was_collected_and_said(
    db_session: Session,
) -> None:
    flat = _entity(db_session, "it-flat-rate")
    charge = IT_FLAT_RATE_PACK.charge(SOCIAL_SECURITY_SURCHARGE_INPS_CHARGE_ID, 2026)
    _invoice(
        db_session,
        flat,
        D(2026, 3, 1),
        "1040.00",
        collected=D(2026, 4, 1),
        lines=[("Consulenza", "1000.00"), (charge.description_line, "40.00")],
    )
    basis = _basis(db_session, flat, 2026, "it-flat-rate")
    assert basis is not None
    assert basis.revenue == M("1000.00")
    (surcharge,) = basis.left_out
    assert (surcharge.code, surcharge.amount) == ("inps_surcharge", M("40.00"))
    assert "commercialista" in surcharge.reason


def test_the_basis_reads_one_legal_entitys_rows_alone(db_session: Session) -> None:
    mine = _entity(db_session, "it-flat-rate")
    other = _entity(db_session, "it-flat-rate")
    _invoice(db_session, mine, D(2026, 3, 1), "300.00", collected=D(2026, 3, 5))
    _invoice(db_session, other, D(2026, 3, 1), "9000.00", collected=D(2026, 3, 5))
    assert _basis(db_session, mine, 2026, "it-flat-rate").revenue == M("300.00")  # type: ignore[union-attr]


def test_the_flat_rate_regime_has_no_costs_to_deduct(db_session: Session) -> None:
    flat = _entity(db_session, "it-flat-rate")
    _purchase(db_session, flat, D(2026, 5, 5), "250.00")
    assert _basis(db_session, flat, 2026, "it-flat-rate").costs is None  # type: ignore[union-attr]


# --- cash costs ------------------------------------------------------------------------------


def test_cash_costs_follow_the_payment_and_a_purchase_without_due_items_its_document_date(
    db_session: Session,
) -> None:
    person = _entity(db_session, "it-standard")
    # Paid in two parts across the new year: each part is its share of the cost, in its year.
    tracked = _purchase(db_session, person, D(2026, 12, 20), "1000.00")
    item = _due_item(
        db_session, person, source_kind="purchase", source_id=tracked.id, amount="1000.00"
    )
    _pay(db_session, item, "250.00", D(2026, 12, 28))
    _pay(db_session, item, "750.00", D(2027, 1, 9))
    # A tracked purchase nobody has paid yet is no cost of the year it is dated in.
    unpaid = _purchase(db_session, person, D(2026, 6, 1), "400.00")
    _due_item(db_session, person, source_kind="purchase", source_id=unpaid.id, amount="400.00")
    # No due item at all: the product cannot say it was paid, so the document date places it,
    # and the basis says it is a stand-in. A cancelled one counts nowhere.
    _purchase(db_session, person, D(2026, 3, 3), "60.00")
    _purchase(db_session, person, D(2026, 3, 4), "99.00", status="cancelled")
    payroll = _due_item(db_session, person, source_kind="payroll", source_id=None, amount="300.00")
    _pay(db_session, payroll, "300.00", D(2026, 7, 10))

    basis_2026 = _basis(db_session, person, 2026, "it-standard")
    basis_2027 = _basis(db_session, person, 2027, "it-standard")
    assert basis_2026 is not None and basis_2027 is not None
    assert basis_2026.basis == "cash"
    assert basis_2026.costs is not None and basis_2027.costs is not None
    assert (basis_2026.costs.purchases, basis_2026.costs.staff) == (M("310.00"), M("300.00"))
    assert (basis_2026.costs.director, basis_2026.costs.total) == (M("0.00"), M("610.00"))
    assert (basis_2027.costs.purchases, basis_2027.costs.staff) == (M("750.00"), M("0.00"))
    (stand_in,) = basis_2026.assumptions
    assert (stand_in.code, stand_in.amount, stand_in.count) == (
        "purchases_by_document_date",
        M("60.00"),
        1,
    )
    assert basis_2027.assumptions == []


def test_a_shared_purchase_is_in_no_legal_entitys_costs_and_the_basis_says_so(
    db_session: Session,
) -> None:
    person = _entity(db_session, "it-standard")
    _purchase(db_session, None, D(2026, 2, 2), "35.00")
    basis = _basis(db_session, person, 2026, "it-standard")
    assert basis is not None and basis.costs is not None
    assert basis.costs.purchases == M("0.00")
    (shared,) = basis.left_out
    assert (shared.code, shared.amount, shared.count) == ("shared_purchases", M("35.00"), 1)


# --- accrual costs ---------------------------------------------------------------------------


def _employee(entity: LegalEntity, **overrides: object) -> EmployeeCreate:
    values: dict[str, object] = {
        "legal_entity_id": entity.id,
        "name": "Giulia Bianchi",
        "start_date": D(2026, 1, 1),
        "gross_annual_pay": M("24000.00"),
        "monthly_payments": 13,
        "employer_inps_rate": M("30.00"),
    }
    values.update(overrides)
    return EmployeeCreate(**values)  # type: ignore[arg-type]


def _director(entity: LegalEntity) -> DirectorCreate:
    return DirectorCreate(
        legal_entity_id=entity.id,
        name="Marco Rossi",
        start_date=D(2026, 1, 1),
        annual_fee=M("12000.00"),
        accrues_tfm=False,
        inps_company_share=M("66.67"),
    )


def test_accrual_costs_tell_purchases_staff_and_directors_apart(db_session: Session) -> None:
    company = _entity(db_session, "it-company")
    # By document date, paid or not, deal or none; the previous year's is not this year's.
    _purchase(db_session, company, D(2026, 2, 1), "700.00")
    _purchase(db_session, company, D(2026, 12, 31), "300.00")
    _purchase(db_session, company, D(2025, 12, 31), "5000.00")
    staff = StaffService(db_session)
    staff.create_staff_member(_employee(company), STAFF_ADMIN)
    staff.create_staff_member(_director(company), STAFF_ADMIN)

    basis = _basis(db_session, company, 2026, "it-company")
    assert basis is not None and basis.costs is not None
    assert basis.costs.purchases == M("1000.00")
    # 24000 / 12 = 2000 a month, 30% employer INPS on top: 2600 a month for the twelve.
    assert basis.costs.staff == M("31200.00")
    # A director's fee of 12000 a year accrues at 1000 a month.
    assert basis.costs.director == M("12000.00")
    assert basis.costs.total == M("44200.00")
    assert basis.left_out == [] and basis.assumptions == []


def test_staff_cost_covers_only_the_months_of_the_financial_year_worked(
    db_session: Session,
) -> None:
    company = _entity(db_session, "it-company")
    StaffService(db_session).create_staff_member(
        _employee(company, start_date=D(2026, 10, 1), employer_inps_rate=M("0.00")), STAFF_ADMIN
    )
    basis = _basis(db_session, company, 2026, "it-company")
    assert basis is not None and basis.costs is not None
    # October to December, 2000 a month.
    assert basis.costs.staff == M("6000.00")


def test_a_person_in_service_with_no_cost_to_speak_of_is_counted_as_left_out(
    db_session: Session,
) -> None:
    """A month somebody closed is read as it was closed: a member it holds no row for has no
    cost there, which a total must say and not silently read as zero."""
    company = _entity(db_session, "it-company")
    StaffService(db_session).create_staff_member(_employee(company), STAFF_ADMIN)
    db_session.add(
        PayrollMonthClosure(
            legal_entity_id=company.id,
            month=D(2026, 3, 1),
            closed_at=dt.datetime(2026, 4, 5, tzinfo=dt.UTC),
            snapshot={"members": []},
        )
    )
    db_session.flush()
    basis = _basis(db_session, company, 2026, "it-company")
    assert basis is not None and basis.costs is not None
    # Eleven months of 2600: the closed month is out of the total, and said.
    assert basis.costs.staff == M("28600.00")
    (missing,) = basis.left_out
    assert (missing.code, missing.amount, missing.count) == ("staff_cost_not_estimated", None, 1)


# --- no basis, no figure ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pack_id", "year"), [("it-standard", 2025), ("it-company", 2025), ("non-it", 2026)]
)
def test_a_pack_that_states_no_basis_for_the_year_has_none(
    db_session: Session, pack_id: str, year: int
) -> None:
    """A basis is never chosen here: where the law says none (a year before the pack's law,
    a company abroad) there is nothing to count."""
    entity = _entity(db_session, pack_id)
    assert _basis(db_session, entity, year, pack_id) is None


def test_the_ordinary_person_has_a_cash_basis_and_still_no_estimate(db_session: Session) -> None:
    """`it-standard` states a cash basis from 2026 (art. 54 TUIR) but no income estimate yet: the
    estimate says what it was computed from and computes no line."""
    person = _entity(db_session, "it-standard")
    _invoice(db_session, person, D(2026, 3, 1), "800.00", collected=D(2026, 3, 9))
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=person.id
    )
    assert estimate.basis is not None and estimate.basis.basis == "cash"
    assert estimate.revenue == M("800.00")
    assert estimate.taxable_amount is None and estimate.due_total is None
    # A year the pack has no law for: no basis, no revenue, no figure.
    earlier = AnalyticsService(db_session).get_fiscal_estimate(
        2025, ADMIN, legal_entity_id=person.id
    )
    assert earlier.basis is None and earlier.revenue == M("0.00")


# --- every path reads it ---------------------------------------------------------------------


def _flat_rate_with_rates(session: Session) -> LegalEntity:
    return _entity(
        session,
        "it-flat-rate",
        profitability_coefficient=M("67.00"),
        substitute_tax_rate=M("5.00"),
        social_security_rate=M("26.07"),
    )


def test_the_estimate_and_the_economic_overview_answer_the_same_figure(
    db_session: Session,
) -> None:
    flat = _flat_rate_with_rates(db_session)
    _invoicing(db_session, flat)
    service = AnalyticsService(db_session)

    for year, collected in ((2026, "0.00"), (2027, "1500.00")):
        estimate = service.get_fiscal_estimate(year, ADMIN, legal_entity_id=flat.id)
        overview = service.economic_overview(year, ADMIN, "accrual", flat.id)
        assert estimate.revenue == M(collected)
        assert estimate.basis is not None
        assert overview.tax is not None
        # The figure the economic overview shows is the estimate itself, in every line and in
        # the basis it was computed from, whichever reading the charts are on.
        for reading in ("accrual", "collection"):
            again = service.economic_overview(year, ADMIN, reading, flat.id)  # type: ignore[arg-type]
            assert again.tax is not None
            assert again.tax.model_dump() == estimate.model_dump()
        assert overview.tax.revenue == estimate.revenue
        assert overview.tax.due_total == estimate.due_total
        assert overview.tax.basis == estimate.basis


def test_the_estimate_follows_the_collected_figure_to_the_cent(db_session: Session) -> None:
    flat = _flat_rate_with_rates(db_session)
    _invoicing(db_session, flat)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        2027, ADMIN, legal_entity_id=flat.id
    )
    assert estimate.revenue == M("1500.00")
    # 1500 * 0.67 = 1005.00; contributions 1005 * 0.2607 = 262.0035 -> 262.00; substitute tax
    # (1005.00 - 262.00) * 0.05 = 37.15.
    assert estimate.taxable_amount == M("1005.00")
    assert estimate.contributions == M("262.00")
    assert estimate.substitute_tax == M("37.15")


def test_a_what_if_revenue_names_no_basis(db_session: Session) -> None:
    flat = _flat_rate_with_rates(db_session)
    _invoicing(db_session, flat)
    what_if = AnalyticsService(db_session).get_fiscal_estimate(
        2027, ADMIN, revenue=M("2000.00"), legal_entity_id=flat.id
    )
    assert what_if.revenue == M("2000.00") and what_if.basis is None


def test_a_year_with_collections_and_no_invoice_of_its_own_is_a_revenue_year(
    db_session: Session,
) -> None:
    """The cash basis puts a payment in the year it arrives, so 2027 has revenue though its
    invoices were issued in 2026: the years an estimate is kept for are those with either."""
    flat = _entity(db_session, "it-flat-rate")
    _invoice(db_session, flat, LATE, "1000.00", collected=D(2027, 1, 15))
    assert AnalyticsRepository(db_session).revenue_years(flat.id) == [2026, 2027]
