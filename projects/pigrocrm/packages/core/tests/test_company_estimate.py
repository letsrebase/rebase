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

    IRES: profit 100,000 - 20,000 - 31,200 - 12,000 = 36,800.00, less the IRAP deducted (REB-1089:
          the staff share 2,808 x 35,200 / 72,000 = 1,372.80, the estimate standing in for the
          IRAP paid; the 10% lump sum is not taken, it needs net interest expense)
          = 35,427.20; 24% = 8,502.528, 8,502.53.
    IRAP: value of production 100,000 - 20,000 = 80,000.00 (staff and director stay in the base);
          art. 11 c. 4-bis deduction 8,000.00 (base up to 180,759.91); base 72,000.00;
          3.90% = 2,808.00.
    Total to set aside: 8,502.53 + 2,808.00 = 11,310.53.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.fiscal.basis_schemas import BasisEmployee
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


def _employee(
    session: Session,
    entity: LegalEntity,
    name: str,
    contract_type: str | None,
    start: dt.date,
    *,
    disabled: bool | None = None,
    **extra: Any,
) -> None:
    StaffService(session).create_staff_member(
        EmployeeCreate(
            legal_entity_id=entity.id,
            name=name,
            start_date=start,
            gross_annual_pay=M("24000.00"),
            monthly_payments=13,
            employer_inps_rate=M("30.00"),
            contract_type=contract_type,  # type: ignore[arg-type]
            disabled_worker=disabled,
            **extra,
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
        ("irap_staff", M("-1372.80")),
    ]
    assert (company.ires.base, company.ires.rate, company.ires.amount) == (
        M("35427.20"),
        M("24.00"),
        M("8502.53"),
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
    assert not any(line.code == "irap_lump_sum" for line in company.ires.lines)
    assert any(
        "forfettario" in text and "non è dedotto" in text for text in company.ires.assumptions
    )

    assert estimate.due_total == M("11310.53")
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
    assert any("riportare" in text for text in company.ires.assumptions)
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
    # IRES does not depend on the place; the total waits for both. With no IRAP to deduct (the
    # region is unknown and nothing was paid) the profit is not reduced, and the payload says so.
    assert company.ires.amount == M("8832.00")
    assert any("non è calcolata" in text for text in company.ires.assumptions)
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
        M("8502.53"),
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
    assert estimate.company is not None and estimate.company.ires.amount == M("8502.53")


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
        ("ires", M("8502.53")),
        ("irap", M("2808.00")),
    ]
    assert estimate.readings.totals.estimate == M("11310.53") == estimate.due_total
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
    assert row.figures["company"]["ires"]["amount"] == "8502.53"


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
    # A permanent employee's cost is deducted from the IRAP base, so a missing cost moves it.
    assert any("Persone in servizio" in text for text in company.irap.assumptions)


def test_the_forecast_advances_of_an_srl_are_written_from_its_estimate(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-1074 reads `estimated_taxes`, which reads the readings' lines: IRES 8,502.53 (the IRAP
    deducted, REB-1089) and IRAP 2,808.00 give 40% on 30 June and 60% on 30 November, with no stub
    in between."""
    from sqlalchemy import select

    from pigrocrm.core.due_items.models import DueItem
    from pigrocrm.core.fiscal import tax_items
    from pigrocrm.core.fiscal.advance_method import TaxAdvanceMethodService
    from pigrocrm.core.fiscal.advance_schemas import TaxAdvanceMethodSet
    from pigrocrm.core.fiscal.tax_items import TaxDueItemService

    monkeypatch.setattr(tax_items, "today_local", lambda: D(2026, 5, 15))
    entity = _worked_example(db_session)
    profile = db_session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity.id)
    ).one()
    profile.isa = False
    db_session.flush()
    TaxAdvanceMethodService(db_session).set_tax_advance_method(
        TaxAdvanceMethodSet(year=YEAR, method="forecast"), ADMIN, legal_entity_id=entity.id
    )
    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=D(2026, 5, 15))
    db_session.expire_all()
    rows = db_session.scalars(
        select(DueItem)
        .where(
            DueItem.legal_entity_id == entity.id,
            DueItem.source_kind == "tax_advance",
            DueItem.state != "dropped",
        )
        .order_by(DueItem.due_date, DueItem.tax)
    )
    assert [(r.tax, r.due_date, r.amount, r.state) for r in rows] == [
        ("irap", D(2026, 7, 20), M("1123.20"), "forecast"),
        ("ires", D(2026, 7, 20), M("3401.01"), "forecast"),
        ("irap", D(2026, 11, 30), M("1684.80"), "forecast"),
        ("ires", D(2026, 11, 30), M("5101.52"), "forecast"),
    ]


def test_a_permanent_employees_cost_leaves_the_irap_base(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _purchase(db_session, entity, "20000.00")
    _employee(db_session, entity, "Giulia Bianchi", "permanent", D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None

    assert [(line.code, line.amount) for line in company.irap.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("permanent_staff", M("-31200.00")),
        ("small_base", M("-8000.00")),
    ]
    # 80,000 - 31,200 - 8,000 = 40,800; 3.90% = 1,591.20.
    assert (company.irap.base, company.irap.amount) == (M("40800.00"), M("1591.20"))
    assert not any("tipo di contratto non è indicato" in text for text in company.irap.assumptions)
    assert not any("sovrastimata" in text for text in company.irap.assumptions)


def test_mixed_contract_types_each_get_their_own_deduction(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _purchase(db_session, entity, "20000.00")
    _employee(db_session, entity, "Giulia Bianchi", "permanent", D(YEAR, 1, 1))
    _employee(db_session, entity, "Luca Verdi", "fixed_term", D(YEAR, 1, 1))
    _employee(db_session, entity, "Anna Neri", "seasonal", D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None

    assert [(line.code, line.amount) for line in company.irap.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("permanent_staff", M("-31200.00")),
        ("per_employee", M("-3700.00")),
        ("small_base", M("-8000.00")),
    ]
    # 80,000 - 31,200 - 2 x 1,850 - 8,000 = 37,100; 3.90% = 1,446.90.
    assert (company.irap.base, company.irap.amount) == (M("37100.00"), M("1446.90"))
    # The seasonal 70% needs the contract history, which the records do not hold: said, not
    # applied.
    assert any(
        "70%" in text and "Anna Neri" in text and "storico" in text
        for text in company.irap.assumptions
    )


def test_a_part_year_fixed_term_employee_is_deducted_pro_rata(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Luca Verdi", "fixed_term", D(YEAR, 7, 1))
    company = _estimate(db_session, entity).company
    assert company is not None
    line = next(line for line in company.irap.lines if line.code == "per_employee")
    # 184 days of 365 at 1,850 a year.
    assert line.amount == -M("932.60")


def test_the_per_employee_deduction_is_not_due_above_the_revenue_limit(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "400000.01")
    _employee(db_session, entity, "Luca Verdi", "fixed_term", D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None
    assert "per_employee" not in [line.code for line in company.irap.lines]
    assert any("c. 4-bis.1" in text and "non spetta" in text for text in company.irap.assumptions)


def test_an_employee_with_no_contract_type_is_named_and_not_deducted(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Giulia Bianchi", "permanent", D(YEAR, 1, 1))
    _employee(db_session, entity, "Piero Gialli", None, D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None

    codes = [line.code for line in company.irap.lines]
    assert "permanent_staff" in codes and "per_employee" not in codes
    # Only the permanent employee's 31,200 leaves the base, not the untyped one's.
    permanent = next(line for line in company.irap.lines if line.code == "permanent_staff")
    assert permanent.amount == -M("31200.00")
    named = [text for text in company.irap.assumptions if "Piero Gialli" in text]
    assert len(named) == 1 and "sovrastimata" in named[0]
    assert not any("Giulia Bianchi" in text and "non è indicato" in text for text in named)


def _excluded_codes(company) -> list[str]:  # type: ignore[no-untyped-def]
    return [line.code for line in company.irap.lines]


@pytest.mark.parametrize("contract_type", ["apprentice", "training"])
def test_an_apprentice_or_training_contract_is_deducted_in_full_and_not_counted(
    db_session: Session, contract_type: str
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    for index in range(5):
        _employee(db_session, entity, f"Tempo {index}", "fixed_term", D(YEAR, 1, 1), disabled=False)
    _employee(db_session, entity, "Mario Apprendista", contract_type, D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None

    lines = {line.code: line.amount for line in company.irap.lines}
    # The whole cost of the person (24,000 plus 30% charges), and a full 1,850 for each of the
    # five others: the apprentice does not take a place among the five.
    assert lines["excluded_staff"] == -M("31200.00")
    assert lines["per_employee"] == -M("9250.00")
    text = " ".join(company.irap.assumptions)
    assert "Mario Apprendista" in text and "art. 11 c. 1 lett. a n. 5" in text
    assert "non sono distinti" not in text


def test_a_disabled_worker_is_deducted_in_full_and_a_missing_answer_is_named(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Dina Disabile", "fixed_term", D(YEAR, 1, 1), disabled=True)
    _employee(db_session, entity, "Nino Non Disabile", "fixed_term", D(YEAR, 1, 1), disabled=False)
    _employee(db_session, entity, "Sara Senza Dato", "seasonal", D(YEAR, 1, 1))
    company = _estimate(db_session, entity).company
    assert company is not None

    lines = {line.code: line.amount for line in company.irap.lines}
    assert lines["excluded_staff"] == -M("31200.00")
    # Two people count for c. 4-bis.1: the one who says no and the one who does not say.
    assert lines["per_employee"] == -M("3700.00")
    named = [text for text in company.irap.assumptions if "è indicato se" in text]
    assert len(named) == 1 and "Sara Senza Dato" in named[0]
    assert "Dina" not in named[0] and "Nino" not in named[0]


def test_a_permanent_disabled_worker_is_not_deducted_twice(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Paolo Permanente", "permanent", D(YEAR, 1, 1), disabled=True)
    company = _estimate(db_session, entity).company
    assert company is not None
    codes = _excluded_codes(company)
    assert "permanent_staff" in codes and "excluded_staff" not in codes


def test_a_disabled_worker_with_no_contract_type_is_deducted_not_named_untyped(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Dina Disabile", None, D(YEAR, 1, 1), disabled=True)
    company = _estimate(db_session, entity).company
    assert company is not None
    assert "excluded_staff" in _excluded_codes(company)
    assert not any("tipo di contratto non è indicato" in t for t in company.irap.assumptions)


def _seasonal(first_days: int, second_days: int, **extra: Any) -> dict[str, Any]:
    """A seasonal worker in service all year with a contract in 2025 and one in 2026."""
    return {
        "seasonal_contracts": [
            {
                "start_date": "2025-06-01",
                "end_date": "2025-09-30",
                "worked_days": {2025: first_days},
            },
            {
                "start_date": "2026-06-01",
                "end_date": "2026-09-30",
                "worked_days": {2026: second_days},
            },
        ],
        **extra,
    }


def test_a_seasonal_worker_with_a_qualifying_history_gets_70_percent_and_not_the_1850(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(
        db_session, entity, "Sara Stagionale", "seasonal", D(YEAR, 1, 1), **_seasonal(90, 100)
    )
    company = _estimate(db_session, entity).company
    assert company is not None

    lines = {line.code: line.amount for line in company.irap.lines}
    # 31,200 of cost (24,000 plus 30%), 70%, for 100 of the 365 days in service.
    assert lines["seasonal_staff"] == -M("5983.56")
    assert "per_employee" not in lines
    text = " ".join(company.irap.assumptions)
    assert "Sara Stagionale" in text and "non si applicano anche i 1,850 euro" in text


def test_the_inail_premium_stays_out_of_the_70_percent(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(
        db_session,
        entity,
        "Sara Stagionale",
        "seasonal",
        D(YEAR, 1, 1),
        **_seasonal(90, 100, employer_inail_rate=M("1.000")),
    )
    company = _estimate(db_session, entity).company
    assert company is not None
    lines = {line.code: line.amount for line in company.irap.lines}
    # Cost 24,000 x 1.31 = 31,440 with 240 of INAIL: 70% of 31,200 for 100/365 days.
    assert lines["seasonal_staff"] == -M("5983.56")


def test_a_seasonal_worker_below_120_days_keeps_the_1850_and_the_reason_is_said(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    _employee(db_session, entity, "Sara Stagionale", "seasonal", D(YEAR, 1, 1), **_seasonal(60, 30))
    company = _estimate(db_session, entity).company
    assert company is not None
    codes = _excluded_codes(company)
    assert "seasonal_staff" not in codes and "per_employee" in codes
    said = [t for t in company.irap.assumptions if "Sara Stagionale" in t and "non spetta" in t]
    assert len(said) == 1 and "90 giorni" in said[0]


def test_a_seasonal_worker_with_a_year_not_stated_is_named_and_not_deducted(
    db_session: Session,
) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "100000.00")
    contracts = _seasonal(90, 100)
    contracts["seasonal_contracts"][1]["worked_days"] = {}
    _employee(db_session, entity, "Sara Stagionale", "seasonal", D(YEAR, 1, 1), **contracts)
    company = _estimate(db_session, entity).company
    assert company is not None
    assert "seasonal_staff" not in _excluded_codes(company)
    said = [
        t for t in company.irap.assumptions if "Sara Stagionale" in t and "non è applicato" in t
    ]
    assert len(said) == 1 and "2026" in said[0]


def test_a_director_has_no_contract_type(db_session: Session) -> None:
    entity = _srl(db_session)
    with pytest.raises(ValueError):
        DirectorCreate(  # type: ignore[call-arg]
            legal_entity_id=entity.id,
            name="Marco Rossi",
            start_date=D(YEAR, 1, 1),
            annual_fee=M("12000.00"),
            contract_type="permanent",
        )


# --- the activity the srl states (REB-1086) ---------------------------------------------------


def _state_ateco(session: Session, entity: LegalEntity, ateco_2007: str, ateco_2025: str) -> None:
    profile = session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity.id)
    ).one()
    profile.ateco_2007_code = ateco_2007
    profile.ateco_2025_code = ateco_2025
    session.flush()


def test_a_tuscan_srl_with_no_activity_stated_gets_no_irap_figure(db_session: Session) -> None:
    entity = _worked_example(db_session, city="Firenze", province="FI")
    estimate = _estimate(db_session, entity)
    company = estimate.company
    assert company is not None
    assert company.irap.amount is None and company.irap.rate is None
    assert company.irap.missing is not None and "ATECO 2007" in company.irap.missing
    assert company.ires.amount == M("8832.00")
    assert estimate.due_total is None


def test_the_activity_stated_picks_the_tuscan_row(db_session: Session) -> None:
    entity = _worked_example(db_session, city="Firenze", province="FI")
    _state_ateco(db_session, entity, "62.01.00", "62.10.00")
    company = _estimate(db_session, entity).company
    assert company is not None
    # 72,000.00 x 4.82% (code 007, software) = 3,470.40
    assert (company.irap.rate, company.irap.amount) == (M("4.82"), M("3470.40"))
    assert any("riga 007" in text for text in company.irap.assumptions)


def test_a_table_1_srl_pays_two_points_more_in_lombardia(db_session: Session) -> None:
    entity = _worked_example(db_session)
    _state_ateco(db_session, entity, "35.11.00", "35.11.00")
    company = _estimate(db_session, entity).company
    assert company is not None
    # 72,000.00 x 5.90% (row 016 of Lombardia) = 4,248.00
    assert (company.irap.rate, company.irap.amount) == (M("5.90"), M("4248.00"))


def test_an_srl_that_states_no_code_in_a_region_with_no_list_is_told_table_1_was_not_checked(
    db_session: Session,
) -> None:
    entity = _worked_example(db_session)
    company = _estimate(db_session, entity).company
    assert company is not None and company.irap.amount == M("2808.00")
    assert any("ATECO 2025 non è indicato" in text for text in company.irap.assumptions)


def test_a_profile_on_another_pack_refuses_an_activity_code(db_session: Session) -> None:
    from pigrocrm.core.errors import ValidationFailed
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService

    entity = _srl(db_session)
    body = FiscalProfileUpsert.model_validate(
        {"pack_id": "it-standard", "regime_code": "RF01", "ateco_2007_code": "62.01.00"}
    )
    with pytest.raises(ValidationFailed) as refused:
        FiscalProfileService(db_session).upsert(body, ADMIN, legal_entity_id=entity.id)
    assert "ateco_2007_code" in str(refused.value)


def test_an_ateco_code_must_have_the_shape_of_one() -> None:
    from pydantic import ValidationError

    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert

    with pytest.raises(ValidationError):
        FiscalProfileUpsert.model_validate(
            {"pack_id": "it-company", "regime_code": "RF01", "ateco_2025_code": "6201"}
        )


# --- a period that is not the calendar year (REB-1086, art. 11 c. 4-bis.2) ----------------------


def _basis(period_from: dt.date, period_to: dt.date, revenue: str, employee: BasisEmployee | None):  # type: ignore[no-untyped-def]
    from pigrocrm.core.fiscal.basis_schemas import BasisCosts, YearBasis

    return YearBasis(
        year=YEAR,
        legal_entity_id=uuid4(),
        basis="accrual",
        period_from=period_from,
        period_to=period_to,
        label="",
        wording="",
        source="",
        revenue=M(revenue),
        costs=BasisCosts(
            purchases=M("10000.00"),
            staff=M("8000.00"),
            director=M("0.00"),
            total=M("18000.00"),
            employees=[employee] if employee else [],
        ),
        left_out=[],
        assumptions=[],
    )


def _irap_of(basis):  # type: ignore[no-untyped-def]
    from pigrocrm.core.fiscal.company_estimate import estimate_company_taxes
    from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK

    law = IT_COMPANY_PACK.law_for(YEAR)
    assert law is not None and law.company_tax is not None
    estimate = estimate_company_taxes(
        basis, law.company_tax, place=locate("Milano", "MI", "IT"), province="MI"
    )
    assert estimate is not None
    return estimate.irap


def test_a_first_period_of_122_days_prorates_both_deductions_to_the_calendar_year() -> None:
    employee = BasisEmployee(
        staff_member_id=uuid4(),
        name="Luca Verdi",
        contract_type="fixed_term",
        cost=M("8000.00"),
        days_in_service=122,
        part_time_percent=M("50"),
    )
    irap = _irap_of(_basis(D(YEAR, 9, 1), D(YEAR, 12, 31), "30000.00", employee))
    lines = {line.code: line.amount for line in irap.lines}
    # 1,850 x 122 / 365 x 50% = 309.18 (a person for 122 days, half time).
    assert lines["per_employee"] == -M("309.18")
    # 20,000 - 309.18 = 19,690.82 is 58,909 a year, the 8,000 row: 8,000 x 122 / 365 = 2,673.97.
    assert lines["small_base"] == -M("2673.97")
    assert irap.base == M("17016.85") and irap.amount == M("663.66")
    assert any("122 giorni" in text and "ragguagliate" in text for text in irap.assumptions)


def test_the_revenue_limit_of_the_per_employee_deduction_is_read_on_the_calendar_year() -> None:
    employee = BasisEmployee(
        staff_member_id=uuid4(),
        name="Luca Verdi",
        contract_type="fixed_term",
        cost=M("8000.00"),
        days_in_service=122,
    )
    # 150,000 in 122 days is 448,770 a year: above the 400,000 of c. 4-bis.1, so no deduction,
    # although 150,000 itself is under it.
    irap = _irap_of(_basis(D(YEAR, 9, 1), D(YEAR, 12, 31), "150000.00", employee))
    assert "per_employee" not in [line.code for line in irap.lines]
    assert any("ragguagliati all'anno solare" in text for text in irap.assumptions)


def test_a_calendar_year_is_not_prorated() -> None:
    employee = BasisEmployee(
        staff_member_id=uuid4(),
        name="Luca Verdi",
        contract_type="fixed_term",
        cost=M("8000.00"),
        days_in_service=365,
    )
    irap = _irap_of(_basis(D(YEAR, 1, 1), D(YEAR, 12, 31), "30000.00", employee))
    lines = {line.code: line.amount for line in irap.lines}
    assert lines["per_employee"] == -M("1850.00") and lines["small_base"] == -M("8000.00")
    assert not any("ragguagliate" in text for text in irap.assumptions)
