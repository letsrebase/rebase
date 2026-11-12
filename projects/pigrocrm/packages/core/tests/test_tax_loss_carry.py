"""REB-1088: an earlier year's tax loss reduces the IRES of an srl (art. 84 TUIR) and the IRPEF of
an ordinary person on a business loss (art. 8 c. 3 TUIR).

The rules are proved on `apply_losses` with the incomes of the years spelled out, against numbers
worked by hand, and the whole chain on the estimate of a Milan srl: the 80% limit, the 100% of the
first three periods of a new activity, a loss of an art or a profession that is not carried, the
remainder that rolls from one year to the next, an earlier year's own estimated loss as a labelled
fallback that a typed loss replaces, and the case where what an intermediate year used cannot be
known.

The worked srl is the one of `test_company_estimate.py`: revenue 100,000.00, purchases 20,000.00,
staff 31,200.00 and a director's 12,000.00 give a 2026 profit of 36,800.00 before any loss.
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from fakes.customer_fixtures import make_customer
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import AgentForbidden, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fiscal.law_loss_carry import COMPANY_LOSS_RULE, PERSON_BUSINESS_LOSS_RULE
from pigrocrm.core.fiscal.loss_carry import LossStock, apply_losses, stock_from_typed
from pigrocrm.core.fiscal.loss_schemas import TaxLossRead, TaxLossSet
from pigrocrm.core.fiscal.losses import TaxLossService
from pigrocrm.core.fiscal.models import FiscalProfile, TaxLoss
from pigrocrm.core.fiscal.ordinary_estimate import InpsFacts, estimate_ordinary_taxes
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_FLAT_RATE_PACK, IT_STANDARD_PACK
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.places.locate import locate
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.staff.schemas import DirectorCreate, EmployeeCreate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.timetracking.models import CostCategory

D = Decimal
M = Decimal
ADMIN = Actor(id=None, type="system", role="admin")
STAFF_ADMIN = Actor(id=uuid4(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid4(), type="user", role="collaborator")
AGENT_ADMIN = Actor(id=uuid4(), type="mcp", role="admin")
AGENT_ADMIN_FULL = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
_NUMBERS = count(1)


# --- the rules, with the incomes of the years spelled out ------------------------------------


def _stock(origin: int, remaining: str, as_of: int | None = None, **kwargs: bool) -> LossStock:
    return LossStock(
        origin, D(remaining), origin if as_of is None else as_of, estimated=False, **kwargs
    )


def _incomes(known: dict[int, str]):  # type: ignore[no-untyped-def]
    return lambda year: D(known[year]) if year in known else None


def test_an_srl_carries_a_loss_against_eighty_percent_of_the_income() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("36800.00"),
        stocks=[_stock(2025, "50000.00")],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )

    # 80% of 36,800.00 is 29,440.00; the rest of the loss waits for the next year.
    assert (offset.used, offset.remaining, offset.estimated) == (
        D("29440.00"),
        D("20560.00"),
        False,
    )
    (origin,) = offset.origins
    assert (origin.origin_year, origin.limit_percent, origin.available) == (
        2025,
        D("80"),
        D("50000.00"),
    )
    assert (origin.used, origin.remaining) == (D("29440.00"), D("20560.00"))
    assert offset.missing == []
    assert any("senza limiti di tempo" in text for text in offset.assumptions)
    assert COMPANY_LOSS_RULE.source in offset.sources


def test_a_loss_smaller_than_the_limit_is_used_whole() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("36800.00"),
        stocks=[_stock(2025, "10000.00")],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    assert (offset.used, offset.remaining) == (D("10000.00"), D("0.00"))


def test_the_first_three_periods_of_a_new_activity_offset_the_whole_income() -> None:
    row = TaxLossRead(
        legal_entity_id=uuid4(),
        tax="ires",
        year=2025,
        amount=D("35000.00"),
        remaining=D("35000.00"),
        remaining_as_of_year=2025,
        new_activity=True,
        activity_start_year=2024,
        source="typed",
        updated_at=dt.datetime(2026, 11, 12, tzinfo=dt.UTC),
    )
    stock = stock_from_typed(row, COMPANY_LOSS_RULE)
    assert stock.full

    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("36800.00"),
        stocks=[stock],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    # Under the general limit only 29,440.00 would go; the first periods carry all 35,000.00.
    assert (offset.used, offset.remaining) == (D("35000.00"), D("0.00"))
    assert offset.origins[0].limit_percent == D("100")


@pytest.mark.parametrize(
    ("start", "new_activity", "expected"),
    [
        (2023, True, True),  # 2023, 2024 and 2025 are the first three periods
        (2022, True, False),  # 2025 is the fourth
        (2024, False, False),  # not a new productive activity
        (None, False, False),
        (2026, False, False),
    ],
)
def test_the_hundred_percent_needs_the_start_year_and_the_new_activity(
    start: int | None, new_activity: bool, expected: bool
) -> None:
    row = TaxLossRead(
        legal_entity_id=uuid4(),
        tax="ires",
        year=2025,
        amount=D("100.00"),
        remaining=D("100.00"),
        remaining_as_of_year=2025,
        new_activity=new_activity,
        activity_start_year=start,
        source="typed",
        updated_at=dt.datetime(2026, 11, 12, tzinfo=dt.UTC),
    )
    assert stock_from_typed(row, COMPANY_LOSS_RULE).full is expected


def test_a_loss_that_does_not_say_it_is_a_first_period_one_gets_the_general_limit_and_says_so() -> (
    None
):
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("1000.00"),
        stocks=[_stock(2025, "5000.00")],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    assert offset.used == D("800.00")
    assert any("art. 84 comma 2" in text for text in offset.assumptions)


def test_the_remainder_rolls_through_the_years_between() -> None:
    """A 2024 loss of 50,000 meets 2025's income of 20,000 (80% = 16,000 used) and 2026's of 36,800
    (80% = 29,440, of the 34,000 left): 4,560 is left for 2027, whose income of 10,000 takes it."""
    offset = apply_losses(
        tax="ires",
        year=2027,
        income=D("10000.00"),
        stocks=[_stock(2024, "50000.00")],
        income_of=_incomes({2025: "20000.00", 2026: "36800.00"}),
        rule=COMPANY_LOSS_RULE,
    )
    (origin,) = offset.origins
    assert origin.available == D("4560.00")
    assert (origin.used, origin.remaining) == (D("4560.00"), D("0.00"))
    assert any("2025, 2026" in text for text in offset.assumptions)


def test_a_typed_remainder_starts_the_roll_at_its_own_year() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("36800.00"),
        stocks=[_stock(2024, "30000.00", as_of=2025)],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    assert offset.origins[0].available == D("30000.00")
    assert (offset.used, offset.remaining) == (D("29440.00"), D("560.00"))


def test_the_oldest_loss_is_used_first_and_the_limit_is_shared() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("10000.00"),
        stocks=[_stock(2025, "6000.00"), _stock(2024, "5000.00")],
        income_of=_incomes({2025: "0.00"}),
        rule=COMPANY_LOSS_RULE,
    )
    by_year = {origin.origin_year: origin for origin in offset.origins}
    assert [origin.origin_year for origin in offset.origins] == [2024, 2025]
    assert by_year[2024].used == D("5000.00") and by_year[2025].used == D("3000.00")
    assert offset.used == D("8000.00") and offset.remaining == D("3000.00")
    assert any("più vecchia" in text for text in offset.assumptions)


def test_the_two_limits_share_the_income() -> None:
    """A loss of the first periods (100%) and a general one (80%): together never above the
    income."""
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("10000.00"),
        stocks=[_stock(2024, "9000.00", full=True), _stock(2025, "9000.00")],
        income_of=_incomes({2025: "0.00"}),
        rule=COMPANY_LOSS_RULE,
    )
    by_year = {origin.origin_year: origin.used for origin in offset.origins}
    assert by_year == {2024: D("9000.00"), 2025: D("1000.00")}


def test_an_income_that_is_not_known_between_stops_the_roll_and_says_which_year() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("36800.00"),
        stocks=[_stock(2024, "50000.00")],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    assert offset.used == D("0.00")
    (sentence,) = offset.missing
    assert "2025" in sentence and "residuo" in sentence


def test_a_year_with_no_income_uses_nothing() -> None:
    offset = apply_losses(
        tax="ires",
        year=2026,
        income=D("-100.00"),
        stocks=[_stock(2025, "50000.00")],
        income_of=_incomes({}),
        rule=COMPANY_LOSS_RULE,
    )
    assert (offset.used, offset.remaining) == (D("0.00"), D("50000.00"))


def test_a_professional_loss_is_not_carried_and_the_estimate_says_why() -> None:
    offset = apply_losses(
        tax="irpef",
        year=2027,
        income=D("40000.00"),
        stocks=[],
        income_of=_incomes({}),
        rule=PERSON_BUSINESS_LOSS_RULE,
        professional_loss_years=[2026],
        professional_source="TUIR art. 8 c. 1",
    )
    assert (offset.used, offset.remaining) == (D("0.00"), D("0.00"))
    assert any("lavoro autonomo non si riporta" in text for text in offset.assumptions)
    assert "TUIR art. 8 c. 1" in offset.sources


# --- an ordinary person's IRPEF -------------------------------------------------------------


def _person_basis(revenue: str, year: int = 2026):  # type: ignore[no-untyped-def]
    from pigrocrm.core.fiscal.basis_schemas import BasisCosts, YearBasis
    from pigrocrm.core.money import ZERO_MONEY

    return YearBasis(
        year=year,
        legal_entity_id=uuid4(),
        basis="cash",
        period_from=dt.date(year, 1, 1),
        period_to=dt.date(year, 12, 31),
        label="Incassato",
        wording="art. 54",
        source="TUIR art. 54 c. 1",
        revenue=D(revenue),
        costs=BasisCosts(
            purchases=ZERO_MONEY, staff=ZERO_MONEY, director=ZERO_MONEY, total=ZERO_MONEY
        ),
        left_out=[],
        assumptions=[],
    )


def _person(revenue: str, loss: str | None):  # type: ignore[no-untyped-def]
    basis = _person_basis(revenue)
    law = IT_STANDARD_PACK.law_for(2026)
    assert law is not None
    offset = (
        apply_losses(
            tax="irpef",
            year=2026,
            income=D(revenue),
            stocks=[_stock(2025, loss)],
            income_of=_incomes({}),
            rule=PERSON_BUSINESS_LOSS_RULE,
        )
        if loss is not None
        else None
    )
    return estimate_ordinary_taxes(
        basis,
        IT_STANDARD_PACK,
        law,
        inps_facts=InpsFacts("gestione_separata", None, True, None),
        place=locate("Albinea", "RE", "IT"),
        province="RE",
        withholdings=D("0.00"),
        losses=offset,
    )


def test_a_business_loss_reduces_the_irpef_base_and_not_the_inps_base() -> None:
    """65,789.47 collected, a 2025 business loss of 20,000 (under the 80% limit): the INPS (24%,
    insured elsewhere) is 15,789.47 on the whole income, the IRPEF base is
    65,789.47 - 20,000 - 15,789.47 = 30,000.00 and its gross tax 7,100.00 (23% of 28,000 and 33%
    of 2,000)."""
    estimate = _person("65789.47", "20000.00")
    assert estimate is not None

    assert estimate.inps.base == D("65789.47") and estimate.inps.amount == D("15789.47")
    assert [(line.code, line.amount) for line in estimate.income.lines] == [
        ("revenue", D("65789.47")),
        ("costs", D("0.00")),
        ("loss_carried", D("-20000.00")),
        ("inps_deducted", D("-15789.47")),
    ]
    assert estimate.income.taxable_income == D("30000.00")
    assert estimate.irpef.base == D("30000.00")
    assert [part.amount for part in estimate.irpef.parts if part.code == "gross_irpef"] == [
        D("7100.00")
    ]
    assert estimate.income.losses is not None and estimate.income.losses.used == D("20000.00")
    assert any("art. 8 c. 3" in text for text in estimate.income.assumptions)
    assert PERSON_BUSINESS_LOSS_RULE.source in estimate.income.sources


def test_the_detractions_read_the_income_after_the_loss() -> None:
    """Art. 8 c. 1 and 3 TUIR make the total income the incomes less the losses and art. 13 reads
    the total income, so the self-employment detraction is worked on the income after the loss: a
    loss that brings it down changes the detraction, and the contributions deducted never exceed
    what is left."""
    with_loss = _person("65789.47", "20000.00")
    without = _person("65789.47", None)
    assert with_loss is not None and without is not None

    def detractions(estimate) -> Decimal:  # type: ignore[no-untyped-def]
        return sum(
            (part.amount for part in estimate.irpef.parts if part.code.startswith("detraction")),
            D("0.00"),
        )

    # Parts are signed: the income of 65,789.47 is above the detraction's top bracket and gets
    # none, the income after the loss (45,789.47) gets one.
    assert detractions(without) == D("0.00")
    assert detractions(with_loss) < D("0.00")
    # A loss that leaves less income than the contributions caps the deduction at that income.
    capped = _person("20000.00", "15000.00")
    assert capped is not None
    deducted = [line for line in capped.income.lines if line.code == "inps_deducted"]
    assert deducted and deducted[0].amount is not None
    assert -deducted[0].amount <= D("5000.00")
    assert capped.income.taxable_income == D("5000.00") + deducted[0].amount


def test_the_person_limit_is_eighty_percent_of_the_income() -> None:
    estimate = _person("100000.00", "90000.00")
    assert estimate is not None
    losses = estimate.income.losses
    assert losses is not None
    assert (losses.used, losses.remaining) == (D("80000.00"), D("10000.00"))


def test_without_a_loss_the_person_estimate_is_what_it_was() -> None:
    estimate = _person("65789.47", None)
    assert estimate is not None
    assert estimate.income.losses is None
    assert estimate.income.taxable_income == D("50000.00")


# --- the whole chain on an srl ---------------------------------------------------------------


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


def _revenue(session: Session, entity: LegalEntity, taxable: str, year: int) -> None:
    amount = M(taxable)
    invoice = Invoice(
        legal_entity_id=entity.id,
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=year,
        number=next(_NUMBERS) + 300_000,
        issue_date=dt.date(year, 6, 1),
        due_date=dt.date(year, 7, 1),
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


def _purchase(session: Session, entity: LegalEntity, cost: str, year: int) -> None:
    category = CostCategory(name=f"Servizi {uuid4().hex[:4]}")
    session.add(category)
    session.flush()
    session.add(
        Purchase(
            legal_entity_id=entity.id,
            category_id=category.id,
            kind="expense",
            status="registered",
            date=dt.date(year, 3, 1),
            amount=M(cost),
            cost_amount=M(cost),
            description="Acquisto",
            custom_fields={},
        )
    )
    session.flush()


def _staff(session: Session, entity: LegalEntity, year: int) -> None:
    service = StaffService(session)
    service.create_staff_member(
        EmployeeCreate(
            legal_entity_id=entity.id,
            name="Giulia Bianchi",
            start_date=dt.date(year, 1, 1),
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
            start_date=dt.date(year, 1, 1),
            annual_fee=M("12000.00"),
            accrues_tfm=False,
            inps_company_share=M("66.67"),
        ),
        STAFF_ADMIN,
    )


def _worked_example(session: Session) -> LegalEntity:
    """The 2026 srl whose profit before any loss is 36,800.00 (IRES 8,832.00)."""
    entity = _srl(session)
    _revenue(session, entity, "100000.00", 2026)
    _purchase(session, entity, "20000.00", 2026)
    _staff(session, entity, 2026)
    return entity


def _estimate(session: Session, entity: LegalEntity, year: int = 2026):  # type: ignore[no-untyped-def]
    return AnalyticsService(session).get_fiscal_estimate(year, ADMIN, legal_entity_id=entity.id)


def _type(session: Session, entity: LegalEntity, **fields: object) -> TaxLossRead:
    values: dict[str, object] = {"tax": "ires", "year": 2025, "amount": D("50000.00")}
    values.update(fields)
    return TaxLossService(session).set_tax_loss(
        TaxLossSet(**values),  # type: ignore[arg-type]
        STAFF_ADMIN,
        legal_entity_id=entity.id,
    )


def test_a_typed_loss_takes_eighty_percent_off_the_ires_base(db_session: Session) -> None:
    entity = _worked_example(db_session)
    before = _estimate(db_session, entity).company
    assert before is not None and before.ires.amount == M("8502.53")
    assert before.ires.losses is None
    assert any("Non risultano perdite" in text for text in before.ires.assumptions)

    _type(db_session, entity)
    estimate = _estimate(db_session, entity)
    company = estimate.company
    assert company is not None

    ires = company.ires
    assert [(line.code, line.amount) for line in ires.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("staff", M("-31200.00")),
        ("director", M("-12000.00")),
        ("irap_staff", M("-1372.80")),
        ("loss_carried", M("-28341.76")),
    ]
    # 36,800.00 - 1,372.80 of IRAP deducted = 35,427.20, and the loss takes 80% of that,
    # 28,341.76: 35,427.20 - 28,341.76 = 7,085.44; 24% = 1,700.5056.
    assert (ires.base, ires.amount, ires.loss) == (M("7085.44"), M("1700.51"), False)
    assert ires.losses is not None
    assert (ires.losses.used, ires.losses.remaining, ires.losses.estimated) == (
        M("28341.76"),
        M("21658.24"),
        False,
    )
    assert [use.origin_year for use in ires.losses.origins] == [2025]
    # IRAP is not reduced by a loss, and the total follows IRES.
    assert company.irap.amount == M("2808.00")
    assert estimate.due_total == M("4508.51")
    assert estimate.readings is not None
    assert {line.tax: line.estimate for line in estimate.readings.lines}["ires"] == M("1700.51")
    # The typed losses are listed for the screen.
    assert [(loss.tax, loss.year, loss.amount) for loss in estimate.losses] == [
        ("ires", 2025, M("50000.00"))
    ]


def test_the_first_three_periods_of_a_new_activity_are_carried_against_the_whole_profit(
    db_session: Session,
) -> None:
    entity = _worked_example(db_session)
    _type(
        db_session,
        entity,
        amount=D("35000.00"),
        new_activity=True,
        activity_start_year=2025,
    )
    ires = _estimate(db_session, entity).company.ires  # type: ignore[union-attr]
    # 36,800.00 - 1,372.80 of IRAP deducted - 35,000.00 = 427.20; 24% = 102.528.
    assert (ires.base, ires.amount) == (M("427.20"), M("102.53"))
    assert ires.losses is not None and ires.losses.origins[0].limit_percent == M("100")


def test_a_remainder_typed_for_a_later_year_is_what_is_carried(db_session: Session) -> None:
    entity = _worked_example(db_session)
    _type(
        db_session,
        entity,
        year=2024,
        amount=D("50000.00"),
        remaining=D("30000.00"),
        remaining_as_of_year=2025,
    )
    ires = _estimate(db_session, entity).company.ires  # type: ignore[union-attr]
    assert ires.losses is not None
    assert ires.losses.origins[0].available == M("30000.00")
    assert (ires.losses.used, ires.losses.remaining) == (M("28341.76"), M("1658.24"))


def test_a_loss_of_a_year_with_no_law_between_has_no_figure_until_the_remainder_is_typed(
    db_session: Session,
) -> None:
    entity = _worked_example(db_session)
    _type(db_session, entity, year=2024, amount=D("50000.00"))
    estimate = _estimate(db_session, entity)
    ires = estimate.company.ires  # type: ignore[union-attr]
    assert ires.amount is None and ires.base is None
    assert ires.missing is not None and "2025" in ires.missing
    # The IRAP still stands and the total waits for both.
    assert estimate.company.irap.amount == M("2808.00")  # type: ignore[union-attr]
    assert estimate.due_total is None

    _type(
        db_session,
        entity,
        year=2024,
        amount=D("50000.00"),
        remaining=D("50000.00"),
        remaining_as_of_year=2025,
    )
    ires = _estimate(db_session, entity).company.ires  # type: ignore[union-attr]
    assert ires.missing is None and ires.amount == M("1700.51")


def test_an_earlier_years_own_loss_is_carried_as_an_estimate_and_labelled(
    db_session: Session,
) -> None:
    """2026 closes in a 20,000.00 loss (10,000 of revenue, 30,000 of purchases) and 2027 earns
    100,000 less 20,000 of purchases: the estimated loss takes 80% of 80,000 = 64,000, all of it."""
    entity = _srl(db_session)
    _revenue(db_session, entity, "10000.00", 2026)
    _purchase(db_session, entity, "30000.00", 2026)
    _revenue(db_session, entity, "100000.00", 2027)
    _purchase(db_session, entity, "20000.00", 2027)

    own = _estimate(db_session, entity, 2026).company
    assert own is not None and own.ires.loss and own.ires.amount == M("0.00")
    company = _estimate(db_session, entity, 2027).company
    assert company is not None

    ires = company.ires
    assert ires.losses is not None and ires.losses.estimated
    (origin,) = ires.losses.origins
    assert (origin.origin_year, origin.estimated, origin.used) == (2026, True, M("20000.00"))
    assert (ires.base, ires.amount) == (M("60000.00"), M("14400.00"))
    assert any("non una perdita dichiarata" in text for text in ires.assumptions)


def test_a_typed_loss_replaces_the_estimated_one_of_the_same_year(db_session: Session) -> None:
    entity = _srl(db_session)
    _revenue(db_session, entity, "10000.00", 2026)
    _purchase(db_session, entity, "30000.00", 2026)
    _revenue(db_session, entity, "100000.00", 2027)
    _purchase(db_session, entity, "20000.00", 2027)
    _type(db_session, entity, year=2026, amount=D("15000.00"))

    ires = _estimate(db_session, entity, 2027).company.ires  # type: ignore[union-attr]
    assert ires.losses is not None and not ires.losses.estimated
    assert [(use.origin_year, use.used) for use in ires.losses.origins] == [(2026, M("15000.00"))]
    assert not any("non una perdita dichiarata" in text for text in ires.assumptions)


def test_the_remainder_of_a_2025_loss_rolls_through_2026_into_2027(db_session: Session) -> None:
    """The 2025 loss of 50,000 meets the estimated 2026 profit of 36,800 (29,440 used) and what
    is left, 20,560, meets 2027's profit: 50,000 of revenue less the staff's 43,200 is 6,800, of
    which 80% = 5,440 goes."""
    entity = _worked_example(db_session)
    _revenue(db_session, entity, "50000.00", 2027)
    _type(db_session, entity)

    ires = _estimate(db_session, entity, 2027).company.ires  # type: ignore[union-attr]
    assert ires.losses is not None
    (origin,) = ires.losses.origins
    assert origin.available == M("20560.00")
    assert (origin.used, origin.remaining) == (M("5440.00"), M("15120.00"))
    assert any("2026" in text and "stimati" in text for text in ires.assumptions)


def test_a_professionals_loss_is_not_carried_into_the_next_year(db_session: Session) -> None:
    entity = LegalEntity(name="persona", legal_name="Persona", country="IT", city="Albinea")
    db_session.add(entity)
    db_session.flush()
    db_session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-standard"))
    db_session.flush()
    _purchase(db_session, entity, "5000.00", 2026)

    person = _estimate(db_session, entity, 2027).person
    assert person is not None
    assert any("lavoro autonomo non si riporta" in text for text in person.income.assumptions)
    assert person.income.losses is not None and person.income.losses.used == M("0.00")
    assert not any(line.code == "loss_carried" for line in person.income.lines)


# --- the writer ------------------------------------------------------------------------------


def test_only_an_admin_with_the_right_token_types_a_loss(db_session: Session) -> None:
    entity = _worked_example(db_session)
    service = TaxLossService(db_session)
    data = TaxLossSet(tax="ires", year=2025, amount=D("100.00"))
    with pytest.raises(PermissionDenied):
        service.set_tax_loss(data, COLLABORATOR, legal_entity_id=entity.id)
    with pytest.raises(AgentForbidden):
        service.set_tax_loss(data, AGENT_ADMIN, legal_entity_id=entity.id)
    assert db_session.query(TaxLoss).count() == 0
    read = service.set_tax_loss(data, AGENT_ADMIN_FULL, legal_entity_id=entity.id)
    assert (read.remaining, read.remaining_as_of_year, read.source) == (
        D("100.00"),
        2025,
        "typed",
    )


def test_typing_a_loss_again_replaces_it_and_it_can_be_removed(db_session: Session) -> None:
    entity = _worked_example(db_session)
    _type(db_session, entity)
    again = _type(db_session, entity, amount=D("10000.00"))
    assert again.amount == D("10000.00")
    assert db_session.query(TaxLoss).count() == 1

    TaxLossService(db_session).delete_tax_loss("ires", 2025, STAFF_ADMIN, legal_entity_id=entity.id)
    assert db_session.query(TaxLoss).count() == 0
    with pytest.raises(NotFound):
        TaxLossService(db_session).delete_tax_loss(
            "ires", 2025, STAFF_ADMIN, legal_entity_id=entity.id
        )
    ires = _estimate(db_session, entity).company.ires  # type: ignore[union-attr]
    assert ires.losses is None and ires.amount == M("8502.53")


def test_a_company_that_owes_no_ires_cannot_carry_a_loss_against_it(db_session: Session) -> None:
    entity = LegalEntity(name="forfettario", legal_name="Forfettario", country="IT")
    db_session.add(entity)
    db_session.flush()
    db_session.add(FiscalProfile(legal_entity_id=entity.id, pack_id="it-flat-rate"))
    db_session.flush()
    with pytest.raises(ValidationFailed):
        TaxLossService(db_session).set_tax_loss(
            TaxLossSet(tax="irpef", year=2025, amount=D("100.00")),
            STAFF_ADMIN,
            legal_entity_id=entity.id,
        )


def test_the_input_is_consistent() -> None:
    for bad in (
        {"amount": D("0.00")},
        {"remaining": D("10.00")},
        {"remaining": D("60000.00"), "remaining_as_of_year": 2025},
        {"remaining": D("10.00"), "remaining_as_of_year": 2024},
        {"new_activity": True},
        {"activity_start_year": 2026},
        {"tax": "irap"},
    ):
        with pytest.raises(ValidationError):
            TaxLossSet(**{"tax": "ires", "year": 2025, "amount": D("50000.00"), **bad})  # type: ignore[arg-type]
    ok = TaxLossSet(
        tax="ires", year=2025, amount=D("1.00"), remaining=D("0.00"), remaining_as_of_year=2025
    )
    assert ok.remaining == D("0.00")


# --- the law ---------------------------------------------------------------------------------


def test_the_pack_states_each_rule_with_its_source() -> None:
    company = IT_COMPANY_PACK.loss_carry_for(2026)
    person = IT_STANDARD_PACK.loss_carry_for(2026)
    assert company is not None and company.company is not None
    assert (company.company.limit_percent, company.company.first_periods_limit_percent) == (
        D("80"),
        D("100"),
    )
    assert "art. 84" in company.company.source and "2026-11-12" in company.company.source
    assert person is not None and person.person_business is not None
    assert "art. 8 c. 3" in person.person_business.source
    assert person.professional_not_carried_source is not None
    assert "art. 8 c. 1" in person.professional_not_carried_source
    # A year before the law is entered has none, and the flat-rate regime carries nothing.
    assert IT_COMPANY_PACK.loss_carry_for(2025) is None
    assert IT_FLAT_RATE_PACK.loss_carry == ()
