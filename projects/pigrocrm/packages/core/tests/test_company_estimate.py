"""REB-1073: the IRES and IRAP an srl is estimated to owe for a year.

Both read the one year basis (`fiscal/basis.py`) and the pack's law (`CompanyTaxLaw`, `IrapLaw`);
the tests prove the figures against numbers worked by hand, the loss and the unknown-region cases,
that a declared figure outranks the estimate and that the service, the economic overview and the
readings of «Tasse» all answer the same estimate.

The worked example, a Milan srl (Lombardia, 3.90%) in 2026:

    revenue (one invoice accrued in 2026)        100,000.00
    purchases                                     20,000.00
    employee: 24,000 gross x 13 months, 30% INPS  31,200.00   (2,600 a month for twelve months)
    director: fee of 12,000 a year                12,000.00

    IRES: profit 100,000 - 20,000 - 31,200 - 12,000 = 36,800.00; 24% = 8,832.00.
    IRAP: value of production 100,000 - 20,000 = 80,000.00 (staff and director stay in the base);
          art. 11 c. 4-bis deduction 8,000.00 (base up to 180,759.91); base 72,000.00;
          3.90% = 2,808.00.
    Total to set aside: 8,832.00 + 2,808.00 = 11,640.00.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import uuid4

from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.fiscal.company_estimate import irap_region_key
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.models import FiscalEstimateReading, FiscalProfile
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.places.locate import locate
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
YEAR = 2026


def _srl(session: Session, city: str | None = "Milano", province: str | None = "MI") -> LegalEntity:
    entity = LegalEntity(
        name=f"srl {uuid4().hex[:6]}",
        legal_name="Esempio S.r.l.",
        country="IT",
        city=city,
        province=province,
    )
    session.add(entity)
    session.flush()
    session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-company", regime_code="RF01"))
    session.flush()
    return entity


def _revenue(session: Session, entity: LegalEntity, taxable: str) -> None:
    amount = M(taxable)
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=YEAR,
        number=next(_NUMBERS) + 200_000,
        issue_date=D(YEAR, 6, 1),
        due_date=D(YEAR, 7, 1),
        taxable_amount=amount,
        vat_amount=M("0.00"),
        stamp_duty=M("0.00"),
        total=amount,
        payment_status="unpaid",
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulenza",
            quantity=M("1.000000"),
            unit_price=amount,
            total_price=amount,
            vat_rate=M("0.00"),
            vat_nature="N2.2",
        )
    )
    session.flush()


def _purchase(session: Session, entity: LegalEntity, cost: str) -> None:
    category = CostCategory(name=f"Servizi {uuid4().hex[:4]}")
    session.add(category)
    session.flush()
    session.add(
        Purchase(
            legal_entity_id=entity.id,
            category_id=category.id,
            kind="expense",
            status="registered",
            date=D(YEAR, 3, 1),
            amount=M(cost),
            cost_amount=M(cost),
            description="Acquisto",
            custom_fields={},
        )
    )
    session.flush()


def _staff(session: Session, entity: LegalEntity) -> None:
    service = StaffService(session)
    service.create_staff_member(
        EmployeeCreate(
            legal_entity_id=entity.id,
            name="Giulia Bianchi",
            start_date=D(YEAR, 1, 1),
            gross_annual_pay=M("24000.00"),
            monthly_payments=13,
            employer_inps_rate=M("30.00"),
        ),
        STAFF_ADMIN,
    )
    service.create_staff_member(
        DirectorCreate(
            legal_entity_id=entity.id,
            name="Marco Rossi",
            start_date=D(YEAR, 1, 1),
            annual_fee=M("12000.00"),
            accrues_tfm=False,
            inps_company_share=M("66.67"),
        ),
        STAFF_ADMIN,
    )


def _worked_example(session: Session, **place: str | None) -> LegalEntity:
    entity = _srl(session, **place)  # type: ignore[arg-type]
    _revenue(session, entity, "100000.00")
    _purchase(session, entity, "20000.00")
    _staff(session, entity)
    return entity


def _estimate(session: Session, entity: LegalEntity):  # type: ignore[no-untyped-def]
    return AnalyticsService(session).get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity.id)


def test_the_worked_example_of_a_milan_srl(db_session: Session) -> None:
    entity = _worked_example(db_session)
    estimate = _estimate(db_session, entity)

    company = estimate.company
    assert company is not None
    assert estimate.basis is not None and estimate.basis.basis == "accrual"

    assert [(line.code, line.amount) for line in company.ires.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("staff", M("-31200.00")),
        ("director", M("-12000.00")),
    ]
    assert (company.ires.base, company.ires.rate, company.ires.amount) == (
        M("36800.00"),
        M("24.00"),
        M("8832.00"),
    )
    assert not company.ires.loss

    assert [(line.code, line.amount) for line in company.irap.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("small_base", M("-8000.00")),
    ]
    assert (company.irap.base, company.irap.rate, company.irap.amount) == (
        M("72000.00"),
        M("3.90"),
        M("2808.00"),
    )
    assert company.irap.region == "03" and company.irap.missing is None
    # What is not applied is said, not silent.
    assert any("tempo indeterminato" in text for text in company.irap.assumptions)
    assert any("variazioni della dichiarazione" in text for text in company.ires.assumptions)
    assert any("perdite" in text for text in company.ires.assumptions)

    assert estimate.due_total == M("11640.00")
    assert estimate.pack_id == "it-company" and estimate.law_from_year == 2026
    # The flat-rate lines are not the srl's.
    assert estimate.substitute_tax is None and estimate.contributions is None


def test_a_loss_gives_no_tax_and_says_so(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "10000.00")
    _purchase(db_session, entity, "30000.00")
    company = _estimate(db_session, entity).company
    assert company is not None

    assert (company.ires.base, company.ires.amount, company.ires.loss) == (
        M("-20000.00"),
        M("0.00"),
        True,
    )
    assert any("perdita" in text for text in company.ires.assumptions)
    assert any("riportate" in text for text in company.ires.assumptions)
    assert (company.irap.base, company.irap.amount, company.irap.loss) == (
        M("-20000.00"),
        M("0.00"),
        True,
    )
    assert any("negativo" in text for text in company.irap.assumptions)


def test_an_unknown_place_gives_no_irap_figure_and_names_the_missing_address(
    db_session: Session,
) -> None:
    entity = _worked_example(db_session, city=None, province=None)
    estimate = _estimate(db_session, entity)
    company = estimate.company
    assert company is not None

    assert company.irap.amount is None and company.irap.rate is None
    assert company.irap.region is None
    assert company.irap.missing is not None and "indirizzo" in company.irap.missing
    # IRES does not depend on the place; the total waits for both.
    assert company.ires.amount == M("8832.00")
    assert estimate.due_total is None
    assert estimate.readings is not None
    lines = {line.tax: line for line in estimate.readings.lines}
    assert lines["irap"].estimable and lines["irap"].estimate is None
    assert lines["ires"].estimate == M("8832.00")


def test_a_province_the_city_does_not_belong_to_is_not_guessed(db_session: Session) -> None:
    # A city that is no municipality of the province still names the province's region.
    place = locate("Nowhereville", "MI", "IT")
    assert place.status == "unknown" and irap_region_key(place, "MI") == "03"
    entity = _worked_example(db_session, city="Milano", province="XX")
    company = _estimate(db_session, entity).company
    assert company is not None and company.irap.amount is None


def test_trentino_alto_adige_is_read_by_province(db_session: Session) -> None:
    rates = {}
    for city, province in (("Bolzano", "BZ"), ("Trento", "TN")):
        entity = _worked_example(db_session, city=city, province=province)
        company = _estimate(db_session, entity).company
        assert company is not None
        rates[province] = (company.irap.region, company.irap.rate, company.irap.amount)
    assert rates == {
        "BZ": ("BZ", M("3.90"), M("2808.00")),
        # 72,000.00 x 2.68% = 1,929.60
        "TN": ("TN", M("2.68"), M("1929.60")),
    }


def test_a_declared_figure_outranks_the_estimate(db_session: Session) -> None:
    entity = _worked_example(db_session)
    DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax="ires", year=YEAR, tax_due=M("9000.00")),
        STAFF_ADMIN,
        legal_entity_id=entity.id,
    )
    estimate = _estimate(db_session, entity)
    assert estimate.readings is not None
    ires, irap = estimate.readings.lines
    assert (ires.tax, ires.estimate, ires.declared, ires.counts) == (
        "ires",
        M("8832.00"),
        M("9000.00"),
        "declared",
    )
    assert (irap.tax, irap.estimate, irap.declared, irap.counts) == (
        "irap",
        M("2808.00"),
        None,
        "estimate",
    )
    # The estimate itself is unchanged by the typed figure.
    assert estimate.company is not None and estimate.company.ires.amount == M("8832.00")


def test_the_service_the_economic_overview_and_the_readings_answer_one_estimate(
    db_session: Session,
) -> None:
    entity = _worked_example(db_session)
    service = AnalyticsService(db_session)
    estimate = _estimate(db_session, entity)
    overview = service.economic_overview(YEAR, ADMIN, "accrual", entity.id)

    assert overview.tax is not None
    assert overview.tax.model_dump() == estimate.model_dump()
    # Accrual taxes are not taken off the cash gross, and a projection needs the year's costs.
    assert (overview.projected_tax, overview.actual_net, overview.projected_net) == (
        None,
        None,
        None,
    )
    assert estimate.readings is not None
    assert [(line.tax, line.estimate) for line in estimate.readings.lines] == [
        ("ires", M("8832.00")),
        ("irap", M("2808.00")),
    ]
    assert estimate.readings.totals.estimate == M("11640.00") == estimate.due_total
    # A «what if» on a revenue the caller supplied has no costs, so no srl figure.
    what_if = service.get_fiscal_estimate(
        YEAR, ADMIN, revenue=M("1000.00"), legal_entity_id=entity.id
    )
    assert what_if.company is None and what_if.readings is None


def test_the_frozen_year_keeps_the_company_figures(db_session: Session) -> None:
    entity = _worked_example(db_session)
    service = AnalyticsService(db_session)
    service.freeze_fiscal_estimates(ADMIN, entity.id)
    row = db_session.query(FiscalEstimateReading).filter_by(legal_entity_id=entity.id).one()
    assert (row.pack_id, row.pack_version, row.law_from_year) == ("it-company", "1", 2026)
    assert row.parameters["irap_region"] == "03"
    assert row.figures["company"]["irap"]["amount"] == "2808.00"
    assert row.figures["company"]["ires"]["amount"] == "8832.00"


def test_what_the_basis_left_out_is_an_assumption_of_the_figure(db_session: Session) -> None:
    entity = _worked_example(db_session)
    # A month somebody closed with no row for the employee: no cost there, said in the figure.
    db_session.add(
        PayrollMonthClosure(
            legal_entity_id=entity.id,
            month=D(YEAR, 3, 1),
            closed_at=dt.datetime(2026, 4, 5, tzinfo=dt.UTC),
            snapshot={"members": []},
        )
    )
    db_session.flush()
    company = _estimate(db_session, entity).company
    assert company is not None
    assert any("Persone in servizio" in text for text in company.ires.assumptions)
    # The IRAP base keeps staff cost in, so a missing staff cost does not move it.
    assert not any("Persone in servizio" in text for text in company.irap.assumptions)
