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
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.errors import NotFound, PermissionDenied
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")
WRITER = Actor(id=None, type="user", role="collaboratore")

# The year `deal_with_mixed_invoices` lands in. `issue()` refuses a `data_emissione`
# outside the current year, so a fixture cannot choose one and the test must not hard-code
# a literal that stops being true on 1 January.
ANNO = oggi_in_italia().year


def _set_rates(
    session: Session,
    *,
    coefficiente: str | None,
    sostitutiva: str | None,
    inps: str | None,
) -> None:
    """Rewrites the three income columns on the one `fiscal_profile` row.

    Through `upsert`, not through SQL: the point being made is that a value reachable
    from the settings screen reaches the estimate, so the test has to travel the path the
    screen travels.
    """
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(
            codice_regime="RF19",
            coefficiente_redditivita=None if coefficiente is None else Decimal(coefficiente),
            aliquota_imposta_sostitutiva=None if sostitutiva is None else Decimal(sostitutiva),
            aliquota_inps=None if inps is None else Decimal(inps),
        ),
        ADMIN,
    )


# --- the arithmetic, with no session at all -----------------------------------------


def test_the_arithmetic_matches_the_previous_systems_at_the_annual_level() -> None:
    """The formula is carried over unchanged -- it was never the defect. Only the level
    is different, and the constants now live in a table."""
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("100000.00"),
        coefficiente=Decimal("67.00"),
        aliquota_sostitutiva=Decimal("5.00"),
        aliquota_inps=Decimal("26.07"),
    )
    assert estimate.imponibile == Decimal("67000.00")
    assert estimate.imposta_sostitutiva == Decimal("3350.00")
    # (67000 - 3350) * 0.2607 = 16593.555, half-up to 16593.56.
    assert estimate.contributi == Decimal("16593.56")
    # Off `ricavi`, not off `imponibile`: 100000 - 3350 - 16593.56.
    assert estimate.reddito_netto_stimato == Decimal("80056.44")


def test_the_rates_are_arguments_and_not_constants() -> None:
    """The same revenue through a different ATECO coefficient and a different INPS rate.

    An implementation carrying 67/5/26.07 as literals reproduces the previous test
    exactly and every figure here wrongly, which is what makes this the test that the
    three constants really did move onto a row.
    """
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("100000.00"),
        coefficiente=Decimal("78.00"),
        aliquota_sostitutiva=Decimal("15.00"),
        aliquota_inps=Decimal("24.00"),
    )
    assert estimate.imponibile == Decimal("78000.00")
    assert estimate.imposta_sostitutiva == Decimal("11700.00")
    # (78000 - 11700) * 0.24
    assert estimate.contributi == Decimal("15912.00")
    assert estimate.reddito_netto_stimato == Decimal("72388.00")
    # Echoed back beside the figures they produced: a reader looking at a number they did
    # not expect needs to see which coefficient it came from.
    assert estimate.coefficiente_redditivita == Decimal("78.00")
    assert estimate.aliquota_imposta_sostitutiva == Decimal("15.00")
    assert estimate.aliquota_inps == Decimal("24.00")


def test_it_says_it_is_an_estimate_in_its_own_payload() -> None:
    """A labelled estimate is useful; an estimate presented as an actual is the original
    defect in a new form. So the label is in the data, not only in the page copy."""
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("1000.00"),
        coefficiente=Decimal("67.00"),
        aliquota_sostitutiva=Decimal("5.00"),
        aliquota_inps=Decimal("26.07"),
    )
    assert estimate.stima is True
    # The three clauses that matter most, because each names a real reason the figure
    # will differ from the declaration -- the floor and the ceiling above all, which are
    # exactly why this computation is meaningless per deal.
    assert "minimale" in estimate.avvertenza
    assert "massimale" in estimate.avvertenza
    assert "acconti" in estimate.avvertenza


def test_a_missing_coefficient_yields_null_not_a_guess() -> None:
    """A parameter nobody configured is not a parameter of zero: the report says it
    cannot compute that line rather than reporting a number derived from an assumption.

    `None` and not `0.00` for the same reason `line_value` returns `None` for an hour
    with no rate -- a silent zero here would read as "you owe nothing".
    """
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("1000.00"),
        coefficiente=None,
        aliquota_sostitutiva=Decimal("5.00"),
        aliquota_inps=Decimal("26.07"),
    )
    assert estimate.imponibile is None
    assert estimate.imposta_sostitutiva is None
    assert estimate.contributi is None
    assert estimate.reddito_netto_stimato is None
    # The revenue is a fact and survives: only the lines derived from the missing
    # parameter go null.
    assert estimate.ricavi == Decimal("1000.00")


def test_only_the_lines_that_depend_on_the_missing_rate_go_null() -> None:
    """A missing INPS rate does not blank the tax the user *can* be told about.

    Nulling the whole payload whenever any one rate is absent would be the easy shape
    and the wrong one: two of the four lines are fully determined without it, and the
    net is not -- because the net is revenue less **both** outgoings.
    """
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("1000.00"),
        coefficiente=Decimal("67.00"),
        aliquota_sostitutiva=Decimal("5.00"),
        aliquota_inps=None,
    )
    assert estimate.imponibile == Decimal("670.00")
    assert estimate.imposta_sostitutiva == Decimal("33.50")
    assert estimate.contributi is None
    assert estimate.reddito_netto_stimato is None


def test_rounding_is_the_projects_own_half_up_at_every_step() -> None:
    """`ROUND_HALF_UP`, and at every step rather than once at the end, so the printed
    lines add up to the printed total -- §6.2's rule applied to a report."""
    estimate = estimate_income(
        anno=2026,
        ricavi=Decimal("1.05"),
        coefficiente=Decimal("67.00"),
        aliquota_sostitutiva=Decimal("5.00"),
        aliquota_inps=Decimal("26.07"),
    )
    # 1.05 * 0.67 = 0.7035 -> 0.70 half-up. Banker's rounding would also give 0.70 here,
    # so the case below is the one that separates them.
    assert estimate.imponibile == Decimal("0.70")
    # 0.70 * 0.05 = 0.035 -> 0.04 half-up; ROUND_HALF_EVEN would give 0.03.
    assert estimate.imposta_sostitutiva == Decimal("0.04")
    # The contributions base is the *rounded* tax subtracted from the *rounded* base,
    # because those are the two figures the reader can see: (0.70 - 0.04) * 0.2607.
    assert estimate.contributi == Decimal("0.17")
    assert estimate.reddito_netto_stimato == Decimal("0.84")


# --- the service, against a real year of invoices -----------------------------------


def test_the_service_reads_the_year_from_issued_invoices_only(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The fixture carries every invoice state that exists. Only the three issued
    `fattura` rows count: an annulled invoice keeps its number and loses its revenue, a
    confirmed proforma never touches the register, and a draft is a proposal."""
    expected = db_session.execute(
        text(
            "SELECT COALESCE(SUM(imponibile), 0) FROM invoices "
            "WHERE anno = :anno AND tipo = 'fattura' AND stato = 'emessa' "
            "  AND deleted_at IS NULL"
        ),
        {"anno": ANNO},
    ).scalar_one()
    estimate = AnalyticsService(db_session).get_fiscal_estimate(ANNO, ADMIN)
    assert estimate.ricavi == Decimal(expected)
    # Spelled out as well as compared, so the query above cannot pass by being wrong in
    # the same direction as the service: 1000.00 + 250.50 + 333.33. The 500.00 annulled,
    # the 999.00 proforma and the 120.00 draft are all absent from it.
    assert estimate.ricavi == Decimal("1583.83")
    assert estimate.anno == ANNO


def test_a_year_with_no_invoices_is_zero_revenue_and_not_the_whole_register(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The year filter is applied, rather than the register summed and the year echoed
    back. Without this the previous test would pass on an implementation that ignored
    `anno` entirely, since the fixture's invoices are all in one year."""
    estimate = AnalyticsService(db_session).get_fiscal_estimate(ANNO - 1, ADMIN)
    assert estimate.anno == ANNO - 1
    assert estimate.ricavi == Decimal("0.00")
    # Zero revenue still produces the lines, at zero: nothing was invoiced, which is a
    # figure, unlike a rate nobody configured.
    assert estimate.imponibile == Decimal("0.00")
    assert estimate.reddito_netto_stimato == Decimal("0.00")


def test_the_service_reads_the_three_rates_from_the_profile_row(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The whole point of task 4B-2's three columns, and the reason this test exists
    beside the pure ones above.

    `FiscalPanel.tsx` exposes all three as editable fields, so this profile is reachable
    from the settings screen today. An implementation that read module-level literals
    instead of the row would return the seeded figures here and fail every line.
    """
    _set_rates(db_session, coefficiente="78.00", sostitutiva="15.00", inps="24.00")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(ANNO, ADMIN)

    assert estimate.coefficiente_redditivita == Decimal("78.00")
    # 1583.83 * 0.78 = 1235.3874 -> 1235.39
    assert estimate.imponibile == Decimal("1235.39")
    # 1235.39 * 0.15 = 185.3085 -> 185.31
    assert estimate.imposta_sostitutiva == Decimal("185.31")
    # (1235.39 - 185.31) * 0.24 = 252.0192 -> 252.02
    assert estimate.contributi == Decimal("252.02")
    assert estimate.reddito_netto_stimato == Decimal("1146.50")
    # And none of them is what the seeded 67/5/26.07 profile would have produced, which
    # is what makes the four assertions above evidence rather than coincidence.
    assert estimate.imponibile != Decimal("1061.17")


def test_cleared_rates_reach_the_report_as_null(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """The columns are nullable and clearing them is an explicit act, so the report has
    to survive it -- with the revenue it does know and nothing invented for the rest."""
    _set_rates(db_session, coefficiente=None, sostitutiva=None, inps=None)
    estimate = AnalyticsService(db_session).get_fiscal_estimate(ANNO, ADMIN)

    assert estimate.ricavi == Decimal("1583.83")
    assert estimate.coefficiente_redditivita is None
    assert estimate.imponibile is None
    assert estimate.imposta_sostitutiva is None
    assert estimate.contributi is None
    assert estimate.reddito_netto_stimato is None


def test_with_no_fiscal_profile_at_all_it_says_so(db_session: Session) -> None:
    """`NotFound("fiscal_profile", ...)`, which names the screen to go to -- rather than
    an estimate of zero computed from three nulls, which reads as "you owe nothing"."""
    with pytest.raises(NotFound) as excinfo:
        AnalyticsService(db_session).get_fiscal_estimate(ANNO, ADMIN)
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
        AnalyticsService(db_session).get_fiscal_estimate(ANNO, WRITER)


def test_there_is_no_per_deal_variant_anywhere() -> None:
    """Asserted so nobody helpfully adds one: the whole point of §8 is that this
    computation has no meaning per project, and the cheapest way to reintroduce the
    defect is a `deal_fiscal_estimate` written in good faith."""
    from pigrocrm.core.analytics.schemas import DealPnl

    assert not [name for name in dir(AnalyticsService) if "fiscal" in name and "deal" in name]
    assert not [
        field
        for field in DealPnl.model_fields
        if "imposta" in field or "inps" in field or "netto" in field
    ]


# --- REB-361: the rivalsa amount never reaches the coefficiente base ---------------


def test_the_rivalsa_amount_is_excluded_from_the_coefficiente_base(db_session: Session) -> None:
    """REB-352 §2's tension, resolved per §6: a rivalsa line counts toward the
    ceiling in full (`AnalyticsRepository.annual_revenue` sums it, unmodified) but
    must never reach the coefficiente base -- `estimate_income`'s `ricavi` here must
    read 1000.00, the fee alone, not 1040.00, or the surcharge gets taxed at 67% like
    ordinary income."""
    from datetime import date

    from pigrocrm.core.customers.models import Customer
    from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK
    from pigrocrm.core.invoices.models import Invoice, InvoiceLine

    _set_rates(db_session, coefficiente="67.00", sostitutiva="5.00", inps="26.07")
    customer = Customer(ragione_sociale="Acme")
    db_session.add(customer)
    db_session.flush()
    invoice = Invoice(
        customer_id=customer.id,
        tipo="fattura",
        stato="emessa",
        anno=ANNO,
        numero=999001,
        data_emissione=date(ANNO, 3, 1),
        imponibile=Decimal("1040.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("1040.00"),
        stato_pagamento="da_incassare",
    )
    db_session.add(invoice)
    db_session.flush()
    charge = IT_FLAT_RATE_PACK.charge("rivalsa_inps")
    db_session.add_all(
        [
            InvoiceLine(
                invoice_id=invoice.id,
                numero_linea=1,
                descrizione="Consulenza",
                quantita=Decimal("1.000000"),
                prezzo_unitario=Decimal("1000.00"),
                prezzo_totale=Decimal("1000.00"),
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
            ),
            InvoiceLine(
                invoice_id=invoice.id,
                numero_linea=2,
                descrizione=charge.descrizione_riga,
                quantita=Decimal("1.000000"),
                prezzo_unitario=Decimal("40.00"),
                prezzo_totale=Decimal("40.00"),
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
            ),
        ]
    )
    db_session.flush()

    estimate = AnalyticsService(db_session).get_fiscal_estimate(ANNO, ADMIN)

    assert estimate.ricavi == Decimal("1000.00")
    # 1000.00 * 0.67 = 670.00 -- and not 1040.00 * 0.67 = 696.80.
    assert estimate.imponibile == Decimal("670.00")


def test_an_explicit_ricavi_override_is_never_reduced_by_the_pack(
    db_session: Session,
) -> None:
    """The "what if" override (the economic overview's collected/projected figures,
    per `get_fiscal_estimate`'s own docstring) is the caller's own already-decided
    number -- the pack's tag applies only to the default, `annual_revenue`-derived
    path, never to a figure a caller hands in explicitly."""
    _set_rates(db_session, coefficiente="67.00", sostitutiva="5.00", inps="26.07")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        ANNO, ADMIN, ricavi=Decimal("1040.00")
    )
    assert estimate.ricavi == Decimal("1040.00")
