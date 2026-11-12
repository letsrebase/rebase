"""REB-1098: the withholdings an srl suffers are credited to the fiscal period its income accrues
in (art. 79 c. 1 and art. 22 c. 1 lett. c) TUIR, art. 109 TUIR), not to the one the customer
paid in.

A srl whose year runs from 1 July to 30 June: an invoice of February 2026 belongs to the period
1 July 2025 to 30 June 2026 (tax year 2026 of the basis, which names a year by the one it closes in)
whenever the customer pays it, and an invoice of September 2026 belongs to the next one.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import uuid4

from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.fiscal.basis_schemas import BasisCosts, YearBasis
from pigrocrm.core.fiscal.company_estimate import estimate_company_taxes
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK
from pigrocrm.core.fiscal.payment_data import COMPANY_WITHHOLDING_CREDIT
from pigrocrm.core.fiscal.withholdings_suffered import company_withholdings_suffered
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.places.locate import locate

D = dt.date
M = Decimal
ADMIN = Actor(id=None, type="system", role="admin")
_NUMBERS = count(1)
FIRST, LAST = D(2025, 7, 1), D(2026, 6, 30)


def _srl(session: Session, *, year_end: tuple[int, int] = (6, 30)) -> LegalEntity:
    entity = LegalEntity(
        name=f"srl {uuid4().hex[:6]}",
        legal_name="Esempio S.r.l.",
        country="IT",
        city="Milano",
        province="MI",
        fiscal_year_end_month=year_end[0],
        fiscal_year_end_day=year_end[1],
    )
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-company", regime_code="RF01"))
    session.flush()
    return entity


def _invoice(
    session: Session,
    entity: LegalEntity,
    taxable: str,
    *,
    issued: dt.date,
    paid: dt.date | None,
    withholding: str,
    accrual: tuple[dt.date, dt.date] | None = None,
) -> Invoice:
    amount, kept = M(taxable), M(withholding)
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=issued.year,
        number=next(_NUMBERS) + 300_000,
        issue_date=issued,
        due_date=issued + dt.timedelta(days=30),
        taxable_amount=amount,
        vat_amount=M("0.00"),
        stamp_duty=M("0.00"),
        total=amount,
        withholding_amount=kept,
        withholding_rate=M("4.00"),
        net_receivable=amount - kept,
        payment_status="unpaid" if paid is None else "paid",
        payment_date=paid,
        accrual_from=accrual[0] if accrual else None,
        accrual_to=accrual[1] if accrual else None,
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Appalto di servizi",
            quantity=M("1.000000"),
            unit_price=amount,
            total_price=amount,
            vat_rate=M("0.00"),
            vat_nature="N2.2",
        )
    )
    session.flush()
    return invoice


def test_a_withholding_goes_to_the_period_the_invoice_accrued_in_whenever_it_was_paid(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    # Accrued in the period, paid after it closed: still this period's credit.
    _invoice(
        db_session,
        entity,
        "10000.00",
        issued=D(2026, 2, 1),
        paid=D(2026, 9, 10),
        withholding="400.00",
    )
    # Accrued and paid in the period.
    _invoice(
        db_session,
        entity,
        "5000.00",
        issued=D(2025, 10, 1),
        paid=D(2025, 11, 5),
        withholding="200.00",
    )
    # Issued in the next period: its withholding is not this period's, paid or not.
    _invoice(
        db_session,
        entity,
        "5000.00",
        issued=D(2026, 9, 1),
        paid=D(2026, 9, 15),
        withholding="200.00",
    )
    # Issued in the period and not paid yet: nothing was withheld, so nothing is credited.
    _invoice(db_session, entity, "3000.00", issued=D(2026, 3, 1), paid=None, withholding="120.00")
    # The accrual period an invoice declares places it, not the day it was issued.
    _invoice(
        db_session,
        entity,
        "2000.00",
        issued=D(2026, 7, 15),
        paid=D(2026, 8, 1),
        withholding="80.00",
        accrual=(D(2026, 5, 1), D(2026, 5, 31)),
    )
    # Another legal entity's.
    _invoice(
        db_session,
        _srl(db_session),
        "9000.00",
        issued=D(2026, 2, 1),
        paid=D(2026, 3, 1),
        withholding="360.00",
    )

    rule = COMPANY_WITHHOLDING_CREDIT
    assert company_withholdings_suffered(db_session, entity.id, FIRST, LAST, rule) == M("680.00")
    # The next period (1 July 2026 to 30 June 2027) holds the September invoice alone.
    assert company_withholdings_suffered(
        db_session, entity.id, D(2026, 7, 1), D(2027, 6, 30), rule
    ) == M("200.00")
    assert company_withholdings_suffered(
        db_session, entity.id, D(2024, 7, 1), D(2025, 6, 30), rule
    ) == M("0.00")


def test_a_reversed_collection_is_no_withholding(db_session: Session) -> None:
    entity = _srl(db_session)
    invoice = _invoice(
        db_session,
        entity,
        "10000.00",
        issued=D(2026, 2, 1),
        paid=D(2026, 3, 1),
        withholding="400.00",
    )
    item = db_session.query(DueItem).filter_by(source_id=invoice.id).one()
    db_session.query(DueItemSettlement).filter_by(due_item_id=item.id).update(
        {"reversed_at": dt.datetime(2026, 3, 2, tzinfo=dt.UTC), "reversed_by_type": "system"}
    )
    assert company_withholdings_suffered(
        db_session, entity.id, FIRST, LAST, COMPANY_WITHHOLDING_CREDIT
    ) == M("0.00")


def _basis(revenue: str, purchases: str = "0.00") -> YearBasis:
    costs = BasisCosts(
        purchases=M(purchases),
        staff=M("0.00"),
        director=M("0.00"),
        total=M(purchases),
        employees=[],
    )
    return YearBasis(
        year=2026,
        legal_entity_id=uuid4(),
        basis="accrual",
        period_from=FIRST,
        period_to=LAST,
        label="x",
        wording="x",
        source="x",
        revenue=M(revenue),
        costs=costs,
        left_out=[],
        assumptions=[],
    )


def _ires(withholdings: Decimal | None):  # type: ignore[no-untyped-def]
    law = IT_COMPANY_PACK.law_for(2026)
    assert law is not None and law.company_tax is not None
    estimate = estimate_company_taxes(
        _basis("100000.00", "20000.00"),
        law.company_tax,
        place=locate("Milano", "MI", "IT"),
        province="MI",
        withholdings=withholdings,
        withholdings_source=COMPANY_WITHHOLDING_CREDIT.source,
    )
    assert estimate is not None
    return estimate.ires, estimate.irap


def test_the_ires_is_net_of_the_withholdings_and_the_excess_is_a_credit() -> None:
    ires, irap = _ires(M("1500.00"))  # profit 80,000 x 24% = 19,200
    assert (ires.withholdings, ires.amount, ires.credit) == (M("1500.00"), M("17700.00"), M("0.00"))
    assert any("competenza" in sentence for sentence in ires.assumptions)
    # The IRAP takes no withholding off.
    assert irap.withholdings is None and irap.credit is None
    excess, _ = _ires(M("25000.00"))
    assert (excess.amount, excess.credit) == (M("0.00"), M("5800.00"))


def test_without_a_rule_the_ires_is_gross_and_says_so() -> None:
    ires, _ = _ires(None)
    assert (ires.amount, ires.withholdings, ires.credit) == (M("19200.00"), None, None)
    assert any("ritenute" in sentence for sentence in ires.assumptions)


def test_the_service_estimate_nets_the_srl_withholdings_of_the_period(db_session: Session) -> None:
    entity = _srl(db_session)
    _invoice(
        db_session,
        entity,
        "100000.00",
        issued=D(2026, 2, 1),
        paid=D(2026, 9, 10),
        withholding="4000.00",
    )
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        2026, ADMIN, legal_entity_id=entity.id
    )
    assert estimate.company is not None
    assert estimate.basis is not None and (
        estimate.basis.period_from,
        estimate.basis.period_to,
    ) == (
        FIRST,
        LAST,
    )
    ires = estimate.company.ires
    assert ires.withholdings == M("4000.00")
    # No staff, so no staff share of the IRAP is deducted, and the 10% lump sum is not taken:
    # 24% of 100,000 = 24,000, less 4,000.
    assert ires.amount == M("20000.00")
