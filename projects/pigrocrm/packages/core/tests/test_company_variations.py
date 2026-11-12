"""REB-1089: the variations that take an srl's accounting profit to its IRES base.

Three groups, each proved against numbers worked by hand: the IRAP deducted from IRES (art. 2 c. 1
D.L. 201/2011, art. 6 D.L. 185/2008), the costs the law limits by kind (art. 164, 102 c. 9, 109 c. 5
and 108 c. 2 TUIR) and depreciation instead of the full cost of an asset (art. 102 TUIR, D.M.
31/12/1988). The pure functions are tested on their own, then the whole estimate on a Milan srl in
2026 (Lombardia, 3.90%):

    revenue 100,000.00, purchases 20,000.00, employee 31,200.00, director 12,000.00
    IRAP: base 72,000.00 after the small-base deduction of 8,000.00, 3.90% = 2,808.00
    staff share: (31,200 + 12,000 - 8,000) / 72,000 = 35,200 / 72,000 of the IRAP
"""

import datetime as dt
from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal import tax_items
from pigrocrm.core.fiscal.advance_method import TaxAdvanceMethodService
from pigrocrm.core.fiscal.advance_schemas import TaxAdvanceMethodSet
from pigrocrm.core.fiscal.cost_variations import (
    PurchaseFact,
    compute_cost_variations,
    depreciation,
)
from pigrocrm.core.fiscal.income_variations import IrapPaid
from pigrocrm.core.fiscal.irap_deduction import irap_deduction
from pigrocrm.core.fiscal.ires_adjustment import ires_adjustment
from pigrocrm.core.fiscal.law_ires import IRES_VARIATIONS_2026 as LAW
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.pack import IT_COMPANY_PACK, IT_STANDARD_PACK
from pigrocrm.core.fiscal.tax_items import TaxDueItemService
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.purchases.deductibility import DEDUCTIBILITY_KINDS
from pigrocrm.core.purchases.models import Purchase
from pigrocrm.core.purchases.schemas import PurchaseCreate, PurchaseUpdate
from pigrocrm.core.purchases.service import PurchaseService
from pigrocrm.core.staff.schemas import DirectorCreate, EmployeeCreate
from pigrocrm.core.staff.service import StaffService
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.models import CostCategory
from pigrocrm.core.timetracking.schemas import CostCategoryCreate, CostCategoryUpdate

D = dt.date
M = Decimal
ADMIN = Actor(id=None, type="system", role="admin")
STAFF_ADMIN = Actor(id=uuid4(), type="user", role="admin")
WRITER = Actor(id=None, type="user", role="collaborator")
YEAR = 2026
_NUMBERS = count(1)


# --- pure: the law ---------------------------------------------------------------------------


def test_the_vocabulary_a_person_states_and_the_law_that_answers_it_agree() -> None:
    limited = {limit.kind for limit in LAW.limits}
    assert limited == set(DEDUCTIBILITY_KINDS) - {"full", "representation"}
    assert LAW.limit("full") is None and LAW.limit("representation") is None
    assert LAW.limit("vehicle").percent == M("20")  # type: ignore[union-attr]
    assert LAW.limit("vehicle_agent").percent == M("80")  # type: ignore[union-attr]
    assert LAW.limit("vehicle_employee").percent == M("70")  # type: ignore[union-attr]
    assert LAW.limit("phone").percent == M("80")  # type: ignore[union-attr]
    assert LAW.limit("hospitality").percent == M("75")  # type: ignore[union-attr]


def test_the_pack_reads_the_variations_by_year_and_only_for_the_company() -> None:
    assert IT_COMPANY_PACK.ires_variations_for(2025) is None
    assert IT_COMPANY_PACK.ires_variations_for(2026) is LAW
    # A statutory section: the 2026 entry holds until a later one replaces it.
    assert IT_COMPANY_PACK.ires_variations_for(2027) is LAW
    assert IT_STANDARD_PACK.ires_variations_for(2026) is None


def test_the_representation_cap_is_a_share_of_the_revenue_in_three_bands() -> None:
    cap = LAW.representation.cap
    assert cap(M("100000")) == M("1500.00")
    # 1.5% of 10 million, then 0.6% of the 2 million above it.
    assert cap(M("12000000")) == M("162000.00")
    # 150,000 + 0.6% of 40 million + 0.4% of the 10 million above 50.
    assert cap(M("60000000")) == M("430000.00")
    assert cap(M("0")) == 0 and cap(M("-5000")) == 0


def test_a_depreciation_halves_in_the_first_year_and_stops_at_the_cost() -> None:
    schedule = [depreciation(M("1000"), M("20"), M("50"), year) for year in range(7)]
    assert schedule == [M("100"), M("200"), M("200"), M("200"), M("200"), M("100"), M("0")]
    assert sum(schedule) == M("1000")


# --- pure: the costs -------------------------------------------------------------------------


def _fact(
    cost: str,
    kind: str | None = None,
    asset: str | None = None,
    years: int = 0,
    code: str | None = None,
    description: str = "Acquisto",
) -> PurchaseFact:
    return PurchaseFact(
        id=uuid4(),
        description=description,
        date=D(YEAR - years, 3, 1),
        cost=M(cost),
        kind=kind,
        asset_group=asset,
        category_code=code,
        category_name="Categoria",
        years_since=years,
    )


def _amounts(lines: tuple) -> list[tuple[str, Decimal]]:  # type: ignore[type-arg]
    return [(line.code, line.amount) for line in lines]


def test_each_limited_kind_adds_back_the_part_the_law_refuses() -> None:
    result = compute_cost_variations(
        LAW,
        [
            _fact("1000", "vehicle"),
            _fact("1000", "vehicle_agent"),
            _fact("1000", "vehicle_employee"),
            _fact("500", "phone"),
            _fact("400", "hospitality"),
        ],
        revenue=M("100000"),
    )
    assert _amounts(result.ires_lines) == [
        ("limit_vehicle", M("800.00")),
        ("limit_vehicle_agent", M("200.00")),
        ("limit_vehicle_employee", M("300.00")),
        ("limit_phone", M("100.00")),
        ("limit_hospitality", M("100.00")),
    ]
    # IRAP starts from the income statement: none of the TUIR limits reach it.
    assert result.irap_lines == () and result.missing is None
    assert "art. 164" in result.ires_lines[0].label and "20%" in result.ires_lines[0].label


def test_a_cost_stated_full_or_not_stated_is_deducted_in_full() -> None:
    result = compute_cost_variations(
        LAW,
        [_fact("1000", "full"), _fact("700", None), _fact("300", "vehicle_agent", asset=None)],
        revenue=M("100000"),
    )
    assert _amounts(result.ires_lines) == [("limit_vehicle_agent", M("60.00"))]
    assert result.ires_assumptions == ()


def test_representation_is_deducted_up_to_the_cap_on_the_revenue() -> None:
    facts = [_fact("1000", "representation"), _fact("1000", "representation")]
    over = compute_cost_variations(LAW, facts, revenue=M("100000"))
    # 2,000 spent against 1.5% of 100,000 = 1,500.
    assert _amounts(over.ires_lines) == [("limit_representation", M("500.00"))]
    within = compute_cost_variations(LAW, facts, revenue=M("200000"))
    assert within.ires_lines == ()
    nothing_sold = compute_cost_variations(LAW, facts, revenue=M("0"))
    assert _amounts(nothing_sold.ires_lines) == [("limit_representation", M("2000.00"))]


def test_the_assumption_counts_unstated_purchases_only_in_ambiguous_categories() -> None:
    ambiguous = [
        _fact("100", None, code="travel"),
        _fact("100", None, code="travel"),
        _fact("100", None, code="other"),
        _fact("100", None, code="materials"),
        _fact("100", None),
        _fact("100", "full", code="other"),
    ]
    result = compute_cost_variations(LAW, ambiguous, revenue=M("100000"))
    assert len(result.ires_assumptions) == 1
    assert result.ires_assumptions[0].startswith("3 acquisti")
    plain = compute_cost_variations(
        LAW,
        [_fact("100", None, code="materials"), _fact("50", "full", code="travel")],
        revenue=M("1"),
    )
    assert plain.ires_assumptions == ()


# --- pure: the assets ------------------------------------------------------------------------


def test_an_asset_leaves_the_costs_and_its_depreciation_enters_them() -> None:
    result = compute_cost_variations(
        LAW, [_fact("3000", asset="office_electronics")], revenue=M("100000")
    )
    # 20% halved in the first year: 300.
    expected = [("assets_cost", M("3000.00")), ("depreciation", M("-300.00"))]
    assert _amounts(result.ires_lines) == expected
    assert _amounts(result.irap_lines) == expected
    assert any("31/12/1988" in text for text in result.irap_assumptions)


def test_an_asset_of_an_earlier_year_keeps_depreciating() -> None:
    result = compute_cost_variations(
        LAW, [_fact("3000", asset="office_electronics", years=2)], revenue=M("100000")
    )
    # Two years after the purchase: the full 20% of 3,000 (the first year took 300, the second 600).
    assert _amounts(result.ires_lines) == [("depreciation", M("-600.00"))]
    fully = compute_cost_variations(
        LAW, [_fact("3000", asset="office_electronics", years=7)], revenue=M("100000")
    )
    assert fully.ires_lines == ()


def test_an_asset_of_at_most_516_46_is_deducted_in_full_with_no_coefficient() -> None:
    for group in ("office_furniture", "other"):
        result = compute_cost_variations(
            LAW, [_fact("516.46", asset=group, description="Sedia")], revenue=M("100000")
        )
        assert result.ires_lines == () and result.irap_lines == () and result.missing is None
    above = compute_cost_variations(
        LAW, [_fact("516.47", asset="office_furniture")], revenue=M("100000")
    )
    assert [code for code, _ in _amounts(above.ires_lines)] == ["assets_cost", "depreciation"]


def test_an_asset_of_a_group_the_table_does_not_carry_is_missing_not_guessed() -> None:
    result = compute_cost_variations(
        LAW,
        [
            _fact("2000", asset="other", description="Muletto"),
            _fact("9000", asset="office_electronics"),
        ],
        revenue=M("100000"),
    )
    assert result.missing is not None and "Muletto" in result.missing
    assert result.ires_lines == () and result.irap_lines == ()


def test_a_car_counts_up_to_the_cost_cap_and_in_the_percentage_of_its_kind() -> None:
    result = compute_cost_variations(
        LAW, [_fact("30000", "vehicle", asset="cars")], revenue=M("100000")
    )
    # IRES: the cost counts up to 18,075.99; 25% halved is 2,259.49875, deductible at 20%, the 80%
    # of it, 1,807.599, is added back. IRAP depreciates the whole 30,000: 12.5% = 3,750.
    assert _amounts(result.ires_lines) == [
        ("assets_cost", M("30000.00")),
        ("depreciation", M("-2259.50")),
        ("limit_vehicle", M("1807.60")),
    ]
    assert _amounts(result.irap_lines) == [
        ("assets_cost", M("30000.00")),
        ("depreciation", M("-3750.00")),
    ]


# --- pure: the IRAP deducted -----------------------------------------------------------------

_IRAP_LINES = [("revenue", M("100000")), ("purchases", M("-20000")), ("small_base", M("-8000"))]


def _deduction(**overrides):  # type: ignore[no-untyped-def]
    args = {
        "irap_lines": _IRAP_LINES,
        "irap_base": M("72000"),
        "irap_amount": M("2808"),
        "staff_cost": M("43200"),
        "paid": None,
    }
    args.update(overrides)
    return irap_deduction(LAW.irap_deduction, **args)


def test_with_no_payment_registered_the_estimated_irap_stands_in() -> None:
    result = _deduction()
    # 2,808 x 35,200 / 72,000 = 1,372.80 on the staff; the 10% lump sum is not taken.
    assert _amounts(result.lines) == [("irap_staff", M("-1372.80"))]
    assert "stimata" in result.assumptions[0]
    assert any(
        "non è dedotto" in text and "interessi passivi" in text for text in result.assumptions
    )


def test_the_irap_paid_in_the_year_is_deducted_when_it_was_registered() -> None:
    result = _deduction(paid=IrapPaid(total=M("4000"), advances=M("1500")))
    # 4,000 x 35,200 / 72,000 = 1,955.56; no lump sum.
    assert _amounts(result.lines) == [("irap_staff", M("-1955.56"))]
    assert "versata" in result.assumptions[0]


def test_advances_count_only_up_to_the_irap_due_for_the_year() -> None:
    result = _deduction(paid=IrapPaid(total=M("5000"), advances=M("4000")))
    # The balance of the year before (1,000) in full, the advances up to the 2,808 due: 3,808 in
    # all, of which 3,808 x 35,200 / 72,000 = 1,861.69 on the staff.
    assert _amounts(result.lines) == [("irap_staff", M("-1861.69"))]


def test_the_two_deductions_never_exceed_the_irap_paid() -> None:
    # Staff cost far above the base: the incidence is 100%, so the staff share takes it all.
    result = _deduction(
        irap_lines=[("revenue", M("60000")), ("purchases", M("-10000"))],
        irap_base=M("50000"),
        irap_amount=M("1950"),
        staff_cost=M("100000"),
    )
    assert _amounts(result.lines) == [("irap_staff", M("-1950.00"))]


def test_a_year_with_no_irap_and_no_payment_deducts_nothing_and_says_so_only_if_unknown() -> None:
    unknown = _deduction(irap_amount=None, irap_base=None)
    assert unknown.lines == () and "non è calcolata" in unknown.assumptions[0]
    zero = _deduction(irap_amount=M("0"), irap_base=M("-100"))
    assert zero.lines == () and zero.assumptions == ()


def test_a_pack_with_no_entry_for_the_year_leaves_the_profit_as_it_was() -> None:
    result = ires_adjustment(
        None, irap_lines=[], irap_base=None, irap_amount=None, staff_cost=M("0")
    )
    assert result.lines == () and "senza le variazioni" in result.assumptions[0]


# --- the whole estimate ----------------------------------------------------------------------


def _srl(session: Session) -> LegalEntity:
    entity = LegalEntity(
        name=f"srl {uuid4().hex[:6]}",
        legal_name="Esempio S.r.l.",
        country="IT",
        city="Milano",
        province="MI",
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
        number=next(_NUMBERS) + 300_000,
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


def _category(session: Session, kind: str | None = None, code: str | None = None) -> CostCategory:
    category = CostCategory(name=f"Categoria {uuid4().hex[:6]}", code=code, deductibility_kind=kind)
    session.add(category)
    session.flush()
    return category


def _purchase(
    session: Session,
    entity: LegalEntity,
    cost: str,
    *,
    category: CostCategory | None = None,
    kind: str | None = None,
    asset: str | None = None,
    date: dt.date | None = None,
    description: str = "Acquisto",
) -> Purchase:
    category = category or _category(session)
    purchase = Purchase(
        legal_entity_id=entity.id,
        category_id=category.id,
        kind="expense",
        status="registered",
        date=date or D(YEAR, 3, 1),
        amount=M(cost),
        cost_amount=M(cost),
        description=description,
        deductibility_kind=kind,
        asset_group=asset,
        custom_fields={},
    )
    session.add(purchase)
    session.flush()
    return purchase


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


def _base(session: Session) -> LegalEntity:
    """The worked example, with its 20,000 of purchases left for the test to add."""
    entity = _srl(session)
    _revenue(session, entity, "100000.00")
    _staff(session, entity)
    return entity


def _company(session: Session, entity: LegalEntity):  # type: ignore[no-untyped-def]
    estimate = AnalyticsService(session).get_fiscal_estimate(YEAR, ADMIN, legal_entity_id=entity.id)
    assert estimate.company is not None
    return estimate, estimate.company


def test_the_irap_is_deducted_from_the_ires_of_the_worked_example(db_session: Session) -> None:
    entity = _base(db_session)
    _purchase(db_session, entity, "20000.00")
    estimate, company = _company(db_session, entity)

    assert [(line.code, line.amount) for line in company.ires.lines] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("staff", M("-31200.00")),
        ("director", M("-12000.00")),
        ("irap_staff", M("-1372.80")),
    ]
    # 36,800 - 1,372.80 = 35,427.20; 24% = 8,502.528.
    assert (company.ires.base, company.ires.amount) == (M("35427.20"), M("8502.53"))
    assert company.irap.amount == M("2808.00")
    assert estimate.due_total == M("11310.53")
    assert any("stimata" in text for text in company.ires.assumptions)
    # What stays out is the part the data cannot support, said once.
    assert any("art. 96" in text for text in company.ires.assumptions)


def test_the_irap_paid_replaces_the_estimate_in_the_deduction(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tax_items, "today_local", lambda: D(2026, 5, 15))
    entity = _base(db_session)
    _purchase(db_session, entity, "20000.00")
    profile = db_session.scalars(
        select(FiscalProfile).where(FiscalProfile.legal_entity_id == entity.id)
    ).one()
    profile.isa = False
    db_session.flush()
    TaxAdvanceMethodService(db_session).set_tax_advance_method(
        TaxAdvanceMethodSet(year=YEAR, method="forecast"), ADMIN, legal_entity_id=entity.id
    )
    TaxDueItemService(db_session).refresh_tax_due_items(entity.id, today=D(2026, 5, 15))
    first = db_session.scalars(
        select(DueItem).where(
            DueItem.legal_entity_id == entity.id,
            DueItem.tax == "irap",
            DueItem.source_kind == "tax_advance",
            DueItem.due_date == D(2026, 7, 20),
            DueItem.state != "dropped",
        )
    ).one()
    assert first.amount == M("1123.20")
    DueItemService(db_session).settle(
        first.id, SettlementCreate(amount=first.amount, settled_on=D(2026, 7, 20)), ADMIN
    )
    db_session.expire_all()

    _, company = _company(db_session, entity)
    # The IRAP paid is the first advance, 1,123.20: 35,200/72,000 of it on the staff (549.12).
    # 36,800 - 549.12 = 36,250.88; 24% = 8,700.2112.
    assert [(line.code, line.amount) for line in company.ires.lines][-1:] == [
        ("irap_staff", M("-549.12")),
    ]
    assert company.ires.amount == M("8700.21")
    assert any("versata nell'esercizio" in text for text in company.ires.assumptions)
    assert not any("al posto dell'IRAP versata" in text for text in company.ires.assumptions)


def test_a_category_that_states_a_limited_kind_limits_every_purchase_of_it(
    db_session: Session,
) -> None:
    entity = _base(db_session)
    _purchase(db_session, entity, "19000.00")
    _purchase(db_session, entity, "1000.00", category=_category(db_session, "vehicle"))
    _, company = _company(db_session, entity)

    limit = next(line for line in company.ires.lines if line.code == "limit_vehicle")
    assert limit.amount == M("800.00")
    # The IRAP is the same as when the 1,000 was an ordinary cost.
    assert company.irap.amount == M("2808.00")
    assert not any(line.code.startswith("limit_") for line in company.irap.lines)
    # 100,000 - 20,000 - 43,200 + 800 = 37,600, less the IRAP deducted (1,372.80).
    assert company.ires.base == M("36227.20")


def test_a_purchase_overrides_the_kind_of_its_category(db_session: Session) -> None:
    entity = _base(db_session)
    vehicles = _category(db_session, "vehicle")
    _purchase(db_session, entity, "1000.00", category=vehicles, kind="full")
    _purchase(db_session, entity, "500.00", category=vehicles)
    _purchase(db_session, entity, "500.00", kind="phone")
    _, company = _company(db_session, entity)

    limits = {
        line.code: line.amount for line in company.ires.lines if line.code.startswith("limit_")
    }
    # The 1,000 stated `full` is untouched, the 500 left to its category is a vehicle (80% back)
    # and the 500 that states a phone gets the phone's 20% back.
    assert limits == {"limit_vehicle": M("400.00"), "limit_phone": M("100.00")}


def test_unstated_purchases_of_an_ambiguous_category_are_counted_in_the_assumption(
    db_session: Session,
) -> None:
    entity = _base(db_session)
    travel = _category(db_session, code="travel")
    _purchase(db_session, entity, "300.00", category=travel)
    _purchase(db_session, entity, "200.00", category=travel)
    _purchase(db_session, entity, "100.00", category=travel, kind="full")
    _, company = _company(db_session, entity)
    counted = [text for text in company.ires.assumptions if text.startswith("2 acquisti")]
    assert len(counted) == 1

    plain = _base(db_session)
    _purchase(db_session, plain, "300.00")
    _, plain_company = _company(db_session, plain)
    assert not any("acquisti in categorie" in text for text in plain_company.ires.assumptions)


def test_an_asset_depreciates_in_the_ires_and_in_the_irap(db_session: Session) -> None:
    entity = _base(db_session)
    _purchase(db_session, entity, "17000.00")
    _purchase(db_session, entity, "3000.00", asset="office_electronics", description="Portatili")
    # Bought a year before: 600 more of depreciation, no cost to take out.
    _purchase(
        db_session,
        entity,
        "3000.00",
        asset="office_electronics",
        date=D(YEAR - 1, 9, 1),
        description="Server",
    )
    _, company = _company(db_session, entity)

    assert [(line.code, line.amount) for line in company.irap.lines][:4] == [
        ("revenue", M("100000.00")),
        ("purchases", M("-20000.00")),
        ("assets_cost", M("3000.00")),
        ("depreciation", M("-900.00")),
    ]
    # IRAP value 100,000 - 20,000 + 3,000 - 900 = 82,100; less 8,000 = 74,100; 3.90% = 2,889.90.
    assert (company.irap.base, company.irap.amount) == (M("74100.00"), M("2889.90"))
    assert [(line.code, line.amount) for line in company.ires.lines][4:6] == [
        ("assets_cost", M("3000.00")),
        ("depreciation", M("-900.00")),
    ]


def test_an_asset_of_an_unknown_group_makes_both_figures_missing(db_session: Session) -> None:
    entity = _base(db_session)
    _purchase(db_session, entity, "20000.00")
    _purchase(db_session, entity, "5000.00", asset="other", description="Muletto elettrico")
    estimate, company = _company(db_session, entity)

    for tax in (company.ires, company.irap):
        assert tax.amount is None
        assert tax.missing is not None and "Muletto elettrico" in tax.missing
    assert estimate.due_total is None


def test_a_cancelled_or_other_entitys_asset_is_not_depreciated(db_session: Session) -> None:
    entity = _base(db_session)
    other = _base(db_session)
    cancelled = _purchase(db_session, entity, "3000.00", asset="office_electronics")
    cancelled.status = "cancelled"
    _purchase(db_session, other, "3000.00", asset="office_electronics")
    db_session.flush()
    _, company = _company(db_session, entity)
    assert not any(line.code == "depreciation" for line in company.ires.lines)


# --- what a person states ---------------------------------------------------------------------


def test_a_purchase_states_its_kind_and_an_asset_group_through_the_service(
    db_session: Session, seeded_category_id
) -> None:  # type: ignore[no-untyped-def]
    service = PurchaseService(db_session)
    created = service.create(
        PurchaseCreate(
            category_id=seeded_category_id,
            date=D(YEAR, 3, 5),
            amount=M("1200.00"),
            description="Portatile",
            deductibility_kind="phone",
            asset_group="office_electronics",
        ),
        WRITER,
    )
    assert (created.deductibility_kind, created.asset_group) == ("phone", "office_electronics")

    cleared = service.update(
        created.id, PurchaseUpdate(deductibility_kind=None, asset_group=None), WRITER
    )
    assert (cleared.deductibility_kind, cleared.asset_group) == (None, None)
    kept = service.update(created.id, PurchaseUpdate(description="Portatile 14"), WRITER)
    assert kept.asset_group is None


def test_a_refund_is_no_asset(db_session: Session, seeded_category_id) -> None:  # type: ignore[no-untyped-def]
    service = PurchaseService(db_session)
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(
            PurchaseCreate(
                category_id=seeded_category_id,
                date=D(YEAR, 3, 5),
                amount=M("-300.00"),
                description="Rimborso",
                asset_group="cars",
            ),
            WRITER,
        )
    assert excinfo.value.details["field"] == "asset_group"


def test_a_category_states_its_kind_and_an_unknown_kind_is_refused(db_session: Session) -> None:
    service = CostCategoryService(db_session)
    created = service.create_cost_category(
        CostCategoryCreate(name="Auto aziendali", deductibility_kind="vehicle"), ADMIN
    )
    assert created.deductibility_kind == "vehicle"
    updated = service.update_cost_category(
        created.id, CostCategoryUpdate(deductibility_kind="full"), ADMIN
    )
    assert updated.deductibility_kind == "full" and updated.name == "Auto aziendali"
    assert (
        service.update_cost_category(
            created.id, CostCategoryUpdate(deductibility_kind=None), ADMIN
        ).deductibility_kind
        is None
    )
    with pytest.raises(ValueError):
        CostCategoryCreate(name="X", deductibility_kind="luxury")  # type: ignore[arg-type]


def test_a_buyer_whose_pack_does_not_read_the_facts_is_refused_them(
    db_session: Session, seeded_category_id
) -> None:  # type: ignore[no-untyped-def]
    flat = LegalEntity(name="forfettario", legal_name="Ditta", country="IT")
    db_session.add(flat)
    db_session.flush()
    db_session.add(
        FiscalProfile(legal_entity_id=flat.id, pack_id="it-flat-rate", regime_code="RF19")
    )
    db_session.flush()
    service = PurchaseService(db_session)
    with pytest.raises(ValidationFailed) as excinfo:
        service.create(
            PurchaseCreate(
                category_id=seeded_category_id,
                legal_entity_id=flat.id,
                date=D(YEAR, 3, 5),
                amount=M("300.00"),
                description="Telefono",
                deductibility_kind="phone",
            ),
            WRITER,
        )
    assert excinfo.value.details["field"] == "deductibility_kind"
