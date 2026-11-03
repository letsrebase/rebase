"""AnalyticsService.ceiling_headroom / .simulate_ceiling -- REB-373, REB-352 §1.4.

The arithmetic underneath (`evaluate_pack`, `evaluate_ceiling`,
`paid_revenue_for_calendar_year`) already has its own tests in
`test_fiscal_ceiling.py`; these exercise the service wiring built on top of it:
resolving the configured jurisdiction pack, mapping `evaluate_pack`'s own
`CeilingStatus` onto the reader-facing schema, and the simulator's synthetic
addition, sourced raw from a not-yet-won deal's own three estimate columns rather
than a `deal_id`, per this issue's own "no persistence required".
"""

from datetime import date
from decimal import Decimal

import pytest
from fakes.customer_fixtures import make_customer
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice, InvoiceLine

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")

REVENUE_CEILING = IT_FLAT_RATE_PACK.ceilings[0]
IMMEDIATE_EXIT_CEILING = IT_FLAT_RATE_PACK.ceilings[1]
assert REVENUE_CEILING.id == "soglia_ricavi"
assert IMMEDIATE_EXIT_CEILING.id == "soglia_fuoriuscita_immediata"

YEAR = 2024


def _paid_invoice(session: Session, *, number: int, year: int, taxable_amount: Decimal) -> Invoice:
    """A paid `invoice` of exactly `taxable_amount`, on one zero-rate line -- enough to
    move `paid_revenue_for_calendar_year`, the same basis `evaluate_pack` reads."""
    invoice = Invoice(
        customer_id=make_customer(session).id,
        type="invoice",
        status="issued",
        year=year,
        number=number,
        issue_date=date(year, 6, 15),
        taxable_amount=taxable_amount,
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=taxable_amount,
        payment_status="paid",
        payment_date=date(year, 6, 20),
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            line_number=1,
            description="Consulenza",
            quantity=Decimal("1.000000"),
            unit_price=taxable_amount,
            total_price=taxable_amount,
            vat_rate=Decimal("0.00"),
            vat_nature="N2.2",
        )
    )
    session.flush()
    return invoice


def _configure_profile(session: Session) -> None:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)


# --- ceiling_headroom ----------------------------------------------------------------


def test_headroom_reports_room_remaining_before_each_active_ceiling(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("60000.00"))

    headroom = AnalyticsService(db_session).ceiling_headroom(YEAR, READER)

    assert headroom.year == YEAR
    assert headroom.pack_id == IT_FLAT_RATE_PACK.id
    assert headroom.pack_version == IT_FLAT_RATE_PACK.version
    revenue_threshold = next(s for s in headroom.thresholds if s.id == "soglia_ricavi")
    assert revenue_threshold.revenue == Decimal("60000.00")
    assert revenue_threshold.remaining == Decimal("25000.00")
    assert revenue_threshold.exceeded is False
    immediate_exit = next(s for s in headroom.thresholds if s.id == "soglia_fuoriuscita_immediata")
    assert immediate_exit.remaining == Decimal("40000.00")


def test_headroom_states_how_much_of_each_ceiling_is_used(db_session: Session) -> None:
    """`usage` is `revenue / threshold`, computed here so a progress bar is drawn from a
    figure the server produced: 60,000 of 85,000 is 0.7059, of 100,000 is 0.6."""
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("60000.00"))

    headroom = AnalyticsService(db_session).ceiling_headroom(YEAR, READER)

    by_id = {s.id: s.usage for s in headroom.thresholds}
    assert by_id == {"soglia_ricavi": 0.7059, "soglia_fuoriuscita_immediata": 0.6}


def test_an_ordinary_regime_legal_entity_has_no_flat_rate_ceiling(db_session: Session) -> None:
    """The 85,000 and 100,000 euro thresholds belong to the flat-rate regime. A legal entity on
    the ordinary regime points at `it-standard` (REB-760), which declares no ceiling:
    `thresholds` is empty, for the room and for the simulator alike, and nothing is raised."""
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("90000.00"))

    service = AnalyticsService(db_session)
    headroom = service.ceiling_headroom(YEAR, READER)
    simulation = service.simulate_ceiling(
        YEAR, CeilingSimulationQuery(budgeted_value=Decimal("20000.00")), READER
    )

    assert headroom.thresholds == []
    assert headroom.pack_id == "it-standard"
    assert simulation.thresholds == []
    assert simulation.brief_addition == Decimal("20000.00")


def test_headroom_marks_a_crossed_ceiling(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("120000.00"))

    headroom = AnalyticsService(db_session).ceiling_headroom(YEAR, READER)

    revenue_threshold = next(s for s in headroom.thresholds if s.id == "soglia_ricavi")
    assert revenue_threshold.exceeded is True
    assert revenue_threshold.remaining == Decimal("-35000.00")


def test_headroom_without_a_fiscal_profile_says_so(db_session: Session) -> None:
    """`NotFound`, naming the screen to go to -- the same signal `get_fiscal_estimate`
    gives when nobody has configured a profile yet: there is no pack to resolve."""
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService

    LegalEntityService(db_session).upsert_default(LegalEntityUpsert(legal_name="Studio"), ADMIN)
    with pytest.raises(NotFound) as excinfo:
        AnalyticsService(db_session).ceiling_headroom(YEAR, READER)
    assert excinfo.value.details["entity"] == "fiscal_profile"


def test_headroom_is_readable_by_a_readonly_actor(db_session: Session) -> None:
    """Not admin-only, unlike `get_fiscal_estimate`: a revenue-versus-threshold
    figure, not the taxable-income computation that method alone gates."""
    _configure_profile(db_session)
    AnalyticsService(db_session).ceiling_headroom(YEAR, READER)  # does not raise


# --- simulate_ceiling ----------------------------------------------------------------


def test_simulate_adds_the_deals_own_value_estimate_directly(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("60000.00"))
    query = CeilingSimulationQuery(budgeted_value=Decimal("20000.00"))

    simulation = AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)

    assert simulation.brief_addition == Decimal("20000.00")
    revenue_threshold = next(s for s in simulation.thresholds if s.id == "soglia_ricavi")
    assert revenue_threshold.current_revenue == Decimal("60000.00")
    assert revenue_threshold.current_remaining == Decimal("25000.00")
    assert revenue_threshold.simulated_revenue == Decimal("80000.00")
    assert revenue_threshold.simulated_remaining == Decimal("5000.00")
    assert (revenue_threshold.current_usage, revenue_threshold.simulated_usage) == (0.7059, 0.9412)
    assert revenue_threshold.falls_within is True


def test_simulate_derives_the_addition_from_hours_times_rate_when_no_value_was_typed(
    db_session: Session,
) -> None:
    """The other half of an "hours-and-value estimate": a not-yet-won deal that has
    priced a rate but not yet a total value still produces a synthetic addition."""
    _configure_profile(db_session)
    query = CeilingSimulationQuery(
        budgeted_hours=Decimal("100.00"), hourly_rate=Decimal("250.000000")
    )

    simulation = AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)

    assert simulation.brief_addition == Decimal("25000.00")


def test_simulate_prefers_the_typed_value_over_hours_times_rate_when_both_are_present(
    db_session: Session,
) -> None:
    """`budgeted_value` is the direct estimate; hours-times-rate is only the
    fallback for a deal that has not priced a value yet."""
    _configure_profile(db_session)
    query = CeilingSimulationQuery(
        budgeted_hours=Decimal("100.00"),
        hourly_rate=Decimal("250.000000"),
        budgeted_value=Decimal("30000.00"),
    )

    simulation = AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)

    assert simulation.brief_addition == Decimal("30000.00")


def test_simulate_shows_a_ceiling_that_would_not_fit(db_session: Session) -> None:
    """ "Would this fit?", answered no: the addition pushes this ceiling's own
    revenue to and past its threshold."""
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("80000.00"))
    query = CeilingSimulationQuery(budgeted_value=Decimal("10000.00"))

    simulation = AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)

    revenue_threshold = next(s for s in simulation.thresholds if s.id == "soglia_ricavi")
    assert revenue_threshold.simulated_revenue == Decimal("90000.00")
    assert revenue_threshold.falls_within is False
    assert revenue_threshold.alert_level_simulated == "Soglia di ricavi raggiunta"


def test_simulate_without_any_estimate_is_a_validation_error_naming_the_field(
    db_session: Session,
) -> None:
    """Neither a direct value nor hours-and-rate: there is nothing to add, and the
    refusal names the field rather than silently simulating a zero addition."""
    _configure_profile(db_session)

    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).simulate_ceiling(YEAR, CeilingSimulationQuery(), READER)
    assert excinfo.value.details["field"] == "budgeted_value"


def test_simulate_never_persists_anything(db_session: Session) -> None:
    """The whole point of reading the estimate raw rather than by `deal_id`: nothing
    about the simulated deal is written anywhere, and a later real read of the same
    year still sees only the paid revenue that was actually there."""
    _configure_profile(db_session)
    _paid_invoice(db_session, number=1, year=YEAR, taxable_amount=Decimal("60000.00"))
    query = CeilingSimulationQuery(budgeted_value=Decimal("5000.00"))

    AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)

    again = AnalyticsService(db_session).ceiling_headroom(YEAR, READER)
    revenue_threshold = next(s for s in again.thresholds if s.id == "soglia_ricavi")
    assert revenue_threshold.revenue == Decimal("60000.00")


def test_simulate_is_readable_by_a_readonly_actor(db_session: Session) -> None:
    _configure_profile(db_session)
    query = CeilingSimulationQuery(budgeted_value=Decimal("1000.00"))
    AnalyticsService(db_session).simulate_ceiling(YEAR, query, READER)  # does not raise
