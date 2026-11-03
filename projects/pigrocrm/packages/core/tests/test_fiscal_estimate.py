"""§8. The previous system computed this **per offer**, inside `App.jsx`:

    const taxableBase   = gross * FORFETTARIO_PROFITABILITY_RATE        // 0.67
    const substituteTax = taxableBase * FORFETTARIO_SUBSTITUTE_TAX_RATE // 0.05
    const inps          = (taxableBase - substituteTax) * FORFETTARIO_INPS_RATE // 0.2607
    const net           = gross - substituteTax - inps

Wrong in three independent ways, and none of them is cured by correcting the formula.
INPS is not proportional to one project's revenue -- it has a floor paid even at zero
income and a ceiling -- so a pro-rata share attributes to a job an amount that does not
depend on it. The profitability coefficient applies to the whole year, not to a slice, so
applying it to slices and adding them gives a different number. And the result changes
retroactively: a March project's "profit" depends on what is invoiced in November,
because both feed the same base -- the very property §7.4 refuses for general expenses,
here applied to the entire tax computation.

So the cure is not a corrected formula per deal. It is the same computation moved to the
level where it is meaningful: **a period report, never per deal**, in `packages/core`.

The three constants moved twice, and the second move is what this file is most careful
about. They left `App.jsx` for `fiscal_profile` (task 4B-2), not for a module-level
literal in `packages/core`: `FiscalPanel.tsx` already exposes all three as editable
fields, so a profile with a 78% coefficient is reachable today by any user who has an
ATECO code with one. The service-level tests below therefore run against a **non-default**
profile as well as the seeded one -- an implementation with 67/5/26.07 written into it
passes every arithmetic test in this file and fails those.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.fiscal import estimate_income
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.errors import NotFound, PermissionDenied
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")
# The pure arithmetic names its azienda like the service does (REB-630): any id will do.
LEGAL_ENTITY = UUID("00000000-0000-7000-8000-000000000001")
WRITER = Actor(id=None, type="user", role="collaborator")

# The year `deal_with_mixed_invoices` lands in. `issue()` refuses a `issue_date`
# outside the current year, so a fixture cannot choose one and the test must not hard-code
# a literal that stops being true on 1 January.
YEAR = today_local().year


def _set_rates(
    session: Session,
    *,
    coefficient: str | None,
    substitute: str | None,
    social_security: str | None,
) -> None:
    """Rewrites the three income columns on the one `fiscal_profile` row.

    Through `upsert`, not through SQL: the point being made is that a value reachable
    from the settings screen reaches the estimate, so the test has to travel the path the
    screen travels.
    """
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(
            regime_code="RF19",
            profitability_coefficient=None if coefficient is None else Decimal(coefficient),
            substitute_tax_rate=None if substitute is None else Decimal(substitute),
            social_security_rate=None if social_security is None else Decimal(social_security),
        ),
        ADMIN,
    )


# --- the arithmetic, with no session at all -----------------------------------------


def test_the_arithmetic_matches_the_previous_systems_at_the_annual_level() -> None:
    """The formula is carried over unchanged -- it was never the defect. Only the level
    is different, and the constants now live in a table."""
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("100000.00"),
        coefficient=Decimal("67.00"),
        rate_substitute=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    assert estimate.taxable_amount == Decimal("67000.00")
    assert estimate.substitute_tax == Decimal("3350.00")
    # (67000 - 3350) * 0.2607 = 16593.555, half-up to 16593.56.
    assert estimate.contributions == Decimal("16593.56")
    # Off `revenue`, not off `taxable_amount`: 100000 - 3350 - 16593.56.
    assert estimate.estimated_net_income == Decimal("80056.44")


def test_the_rates_are_arguments_and_not_constants() -> None:
    """The same revenue through a different ATECO coefficient and a different INPS rate.

    An implementation carrying 67/5/26.07 as literals reproduces the previous test
    exactly and every figure here wrongly, which is what makes this the test that the
    three constants really did move onto a row.
    """
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("100000.00"),
        coefficient=Decimal("78.00"),
        rate_substitute=Decimal("15.00"),
        social_security_rate=Decimal("24.00"),
    )
    assert estimate.taxable_amount == Decimal("78000.00")
    assert estimate.substitute_tax == Decimal("11700.00")
    # (78000 - 11700) * 0.24
    assert estimate.contributions == Decimal("15912.00")
    assert estimate.estimated_net_income == Decimal("72388.00")
    # Echoed back beside the figures they produced: a reader looking at a number they did
    # not expect needs to see which coefficient it came from.
    assert estimate.profitability_coefficient == Decimal("78.00")
    assert estimate.substitute_tax_rate == Decimal("15.00")
    assert estimate.social_security_rate == Decimal("24.00")


def test_it_says_it_is_an_estimate_in_its_own_payload() -> None:
    """A labelled estimate is useful; an estimate presented as an actual is the original
    defect in a new form. So the label is in the data, not only in the page copy."""
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("1000.00"),
        coefficient=Decimal("67.00"),
        rate_substitute=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    assert estimate.estimate is True
    # The three clauses that matter most, because each names a real reason the figure
    # will differ from the declaration -- the floor and the ceiling above all, which are
    # exactly why this computation is meaningless per deal.
    assert "minimale" in estimate.warning
    assert "massimale" in estimate.warning
    assert "acconti" in estimate.warning


def test_a_missing_coefficient_yields_null_not_a_guess() -> None:
    """A parameter nobody configured is not a parameter of zero: the report says it
    cannot compute that line rather than reporting a number derived from an assumption.

    `None` and not `0.00` for the same reason `line_value` returns `None` for an hour
    with no rate -- a silent zero here would read as "you owe nothing".
    """
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("1000.00"),
        coefficient=None,
        rate_substitute=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    assert estimate.taxable_amount is None
    assert estimate.substitute_tax is None
    assert estimate.contributions is None
    assert estimate.estimated_net_income is None
    # The revenue is a fact and survives: only the lines derived from the missing
    # parameter go null.
    assert estimate.revenue == Decimal("1000.00")


def test_only_the_lines_that_depend_on_the_missing_rate_go_null() -> None:
    """A missing INPS rate does not blank the tax the user *can* be told about.

    Nulling the whole payload whenever any one rate is absent would be the easy shape
    and the wrong one: two of the four lines are fully determined without it, and the
    net is not -- because the net is revenue less **both** outgoings.
    """
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("1000.00"),
        coefficient=Decimal("67.00"),
        rate_substitute=Decimal("5.00"),
        social_security_rate=None,
    )
    assert estimate.taxable_amount == Decimal("670.00")
    assert estimate.substitute_tax == Decimal("33.50")
    assert estimate.contributions is None
    assert estimate.estimated_net_income is None


def test_rounding_is_the_projects_own_half_up_at_every_step() -> None:
    """`ROUND_HALF_UP`, and at every step rather than once at the end, so the printed
    lines add up to the printed total -- §6.2's rule applied to a report."""
    estimate = estimate_income(
        year=2026,
        legal_entity_id=LEGAL_ENTITY,
        revenue=Decimal("1.05"),
        coefficient=Decimal("67.00"),
        rate_substitute=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    # 1.05 * 0.67 = 0.7035 -> 0.70 half-up. Banker's rounding would also give 0.70 here,
    # so the case below is the one that separates them.
    assert estimate.taxable_amount == Decimal("0.70")
    # 0.70 * 0.05 = 0.035 -> 0.04 half-up; ROUND_HALF_EVEN would give 0.03.
    assert estimate.substitute_tax == Decimal("0.04")
    # The contributions base is the *rounded* tax subtracted from the *rounded* base,
    # because those are the two figures the reader can see: (0.70 - 0.04) * 0.2607.
    assert estimate.contributions == Decimal("0.17")
    assert estimate.estimated_net_income == Decimal("0.84")


# --- the service, against a real year of invoices -----------------------------------


def test_the_service_reads_the_year_from_issued_invoices_only(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The fixture carries every invoice state that exists. Only the three issued
    `invoice` rows count: an annulled invoice keeps its number and loses its revenue, a
    confirmed proforma never touches the register, and a draft is a proposal."""
    expected = db_session.execute(
        text(
            "SELECT COALESCE(SUM(taxable_amount), 0) FROM invoices "
            "WHERE year = :year AND type = 'invoice' AND status = 'issued' "
            "  AND deleted_at IS NULL"
        ),
        {"year": YEAR},
    ).scalar_one()
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)
    assert estimate.revenue == Decimal(expected)
    # Spelled out as well as compared, so the query above cannot pass by being wrong in
    # the same direction as the service: 1000.00 + 250.50 + 333.33. The 500.00 annulled,
    # the 999.00 proforma and the 120.00 draft are all absent from it.
    assert estimate.revenue == Decimal("1583.83")
    assert estimate.year == YEAR


def test_a_year_with_no_invoices_is_zero_revenue_and_not_the_whole_register(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The year filter is applied, rather than the register summed and the year echoed
    back. Without this the previous test would pass on an implementation that ignored
    `year` entirely, since the fixture's invoices are all in one year."""
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR - 1, ADMIN)
    assert estimate.year == YEAR - 1
    assert estimate.revenue == Decimal("0.00")
    # Zero revenue still produces the lines, at zero: nothing was invoiced, which is a
    # figure, unlike a rate nobody configured.
    assert estimate.taxable_amount == Decimal("0.00")
    assert estimate.estimated_net_income == Decimal("0.00")


def test_the_service_reads_the_three_rates_from_the_profile_row(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The whole point of task 4B-2's three columns, and the reason this test exists
    beside the pure ones above.

    `FiscalPanel.tsx` exposes all three as editable fields, so this profile is reachable
    from the settings screen today. An implementation that read module-level literals
    instead of the row would return the seeded figures here and fail every line.
    """
    _set_rates(db_session, coefficient="78.00", substitute="15.00", social_security="24.00")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)

    assert estimate.profitability_coefficient == Decimal("78.00")
    # 1583.83 * 0.78 = 1235.3874 -> 1235.39
    assert estimate.taxable_amount == Decimal("1235.39")
    # 1235.39 * 0.15 = 185.3085 -> 185.31
    assert estimate.substitute_tax == Decimal("185.31")
    # (1235.39 - 185.31) * 0.24 = 252.0192 -> 252.02
    assert estimate.contributions == Decimal("252.02")
    assert estimate.estimated_net_income == Decimal("1146.50")
    # And none of them is what the seeded 67/5/26.07 profile would have produced, which
    # is what makes the four assertions above evidence rather than coincidence.
    assert estimate.taxable_amount != Decimal("1061.17")


def test_cleared_rates_reach_the_report_as_null(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The columns are nullable and clearing them is an explicit act, so the report has
    to survive it -- with the revenue it does know and nothing invented for the rest."""
    _set_rates(db_session, coefficient=None, substitute=None, social_security=None)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)

    assert estimate.revenue == Decimal("1583.83")
    assert estimate.profitability_coefficient is None
    assert estimate.taxable_amount is None
    assert estimate.substitute_tax is None
    assert estimate.contributions is None
    assert estimate.estimated_net_income is None


def test_with_no_fiscal_profile_at_all_it_says_so(db_session: Session) -> None:
    """`NotFound("fiscal_profile", ...)`, which names the screen to go to -- rather than
    an estimate of zero computed from three nulls, which reads as "you owe nothing"."""
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(db_session).upsert_default(LegalEntityUpsert(legal_name="Studio"), ADMIN)
    with pytest.raises(NotFound) as excinfo:
        AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)
    assert excinfo.value.details["entity"] == "fiscal_profile"


def test_it_is_admin_only(db_session: Session) -> None:
    """The only read on §11's exclusion list, and for a reason different from the rest:
    it is the most sensitive figure this product holds, and residual R10 leaves a PAT
    indistinguishable from full account access.

    The refusal comes before the profile is read -- this session has none -- so a
    collaborator gets `PermissionDenied` and not a `NotFound` that would tell them the
    settings screen is empty.
    """
    with pytest.raises(PermissionDenied):
        AnalyticsService(db_session).get_fiscal_estimate(YEAR, WRITER)


def test_there_is_no_per_deal_variant_anywhere() -> None:
    """Asserted so nobody helpfully adds one: the whole point of §8 is that this
    computation has no meaning per project, and the cheapest way to reintroduce the
    defect is a `deal_fiscal_estimate` written in good faith."""
    from pigrocrm.core.analytics.schemas import DealPnl

    assert not [name for name in dir(AnalyticsService) if "fiscal" in name and "deal" in name]
    assert not [
        field
        for field in DealPnl.model_fields
        if "vat_amount" in field or "inps" in field or "netto" in field
    ]


# --- REB-361: the social security surcharge never reaches the coefficient base ---------------


def test_the_social_security_surcharge_amount_is_excluded_from_the_coefficient_base(
    db_session: Session,
) -> None:
    """REB-352 §2's tension, resolved by §6: a surcharge line counts toward the
    ceiling in full (`AnalyticsRepository.annual_revenue` sums it, unmodified) but
    must never reach the coefficient base -- `estimate_income`'s `revenue` here must
    read 1000.00, the fee alone, not 1040.00, or the surcharge gets taxed at 67% like
    ordinary income."""
    from datetime import date

    from pigrocrm.core.customers.models import Customer
    from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK
    from pigrocrm.core.invoices.models import Invoice, InvoiceLine

    _set_rates(db_session, coefficient="67.00", substitute="5.00", social_security="26.07")
    customer = Customer(legal_name="Acme")
    db_session.add(customer)
    db_session.flush()
    invoice = Invoice(
        customer_id=customer.id,
        type="invoice",
        status="issued",
        year=YEAR,
        number=999001,
        issue_date=date(YEAR, 3, 1),
        taxable_amount=Decimal("1040.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("1040.00"),
        payment_status="unpaid",
    )
    db_session.add(invoice)
    db_session.flush()
    charge = IT_FLAT_RATE_PACK.charge("rivalsa_inps")
    db_session.add_all(
        [
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=1,
                description="Consulenza",
                quantity=Decimal("1.000000"),
                unit_price=Decimal("1000.00"),
                total_price=Decimal("1000.00"),
                vat_rate=Decimal("0.00"),
                vat_nature="N2.2",
            ),
            InvoiceLine(
                invoice_id=invoice.id,
                line_number=2,
                description=charge.description_line,
                quantity=Decimal("1.000000"),
                unit_price=Decimal("40.00"),
                total_price=Decimal("40.00"),
                vat_rate=Decimal("0.00"),
                vat_nature="N2.2",
            ),
        ]
    )
    db_session.flush()

    estimate = AnalyticsService(db_session).get_fiscal_estimate(YEAR, ADMIN)

    assert estimate.revenue == Decimal("1000.00")
    # 1000.00 * 0.67 = 670.00 -- and not 1040.00 * 0.67 = 696.80.
    assert estimate.taxable_amount == Decimal("670.00")


def test_an_explicit_revenue_override_is_never_reduced_by_the_pack(
    db_session: Session,
) -> None:
    """The "what if" override (the economic overview's collected/projected figures,
    per `get_fiscal_estimate`'s own docstring) is the caller's own already-decided
    number -- the pack's tag applies only to the default, `annual_revenue`-derived
    path, never to a figure a caller hands in explicitly."""
    _set_rates(db_session, coefficient="67.00", substitute="5.00", social_security="26.07")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        YEAR, ADMIN, revenue=Decimal("1040.00")
    )
    assert estimate.revenue == Decimal("1040.00")
