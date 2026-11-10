"""REB-1026: the year's taxes as three readings per line, from the pack's law and the typed
declared figures. The page of «Tasse» draws what this returns and adds nothing itself, so what
is proved here is which lines exist, which reading counts, and the totals of each column."""

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.fiscal.declared import DeclaredTaxFigureService
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import DeclaredTaxFigureSet, TaxReadings
from pigrocrm.core.legal_entities.models import LegalEntity

ADMIN = Actor(id=uuid4(), type="user", role="admin")
YEAR = 2026


def _entity(session: Session, pack_id: str, regime: str | None, **rates: Decimal) -> UUID:
    entity = LegalEntity(name=f"readings {pack_id}", legal_name=f"Readings {pack_id}", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(legal_entity_id=entity.id, pack_id=pack_id, regime_code=regime, **rates)
    )
    session.flush()
    return entity.id


def _declare(session: Session, entity: UUID, tax: str, **figures: Decimal) -> None:
    DeclaredTaxFigureService(session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax=tax, year=YEAR, **figures),  # type: ignore[arg-type]
        ADMIN,
        legal_entity_id=entity,
    )


def _readings(session: Session, entity: UUID, year: int = YEAR) -> TaxReadings:
    estimate = AnalyticsService(session).get_fiscal_estimate(year, ADMIN, legal_entity_id=entity)
    readings = estimate.readings
    assert readings is not None
    return readings


def test_a_flat_rate_year_has_its_two_lines_with_the_estimate_counting(db_session: Session) -> None:
    entity = _entity(
        db_session,
        "it-flat-rate",
        "RF19",
        profitability_coefficient=Decimal("78.00"),
        substitute_tax_rate=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    readings = _readings(db_session, entity)
    assert [(line.tax, line.estimable, line.paid) for line in readings.lines] == [
        ("substitute_tax", True, None),
        ("inps", True, None),
    ]
    # No revenue: the estimate is zero, which is a figure, and it counts as the estimate.
    assert [line.estimate for line in readings.lines] == [Decimal("0.00"), Decimal("0.00")]
    assert [line.counts for line in readings.lines] == ["estimate", "estimate"]
    assert readings.totals.estimate == Decimal("0.00")
    assert (readings.totals.declared, readings.totals.paid) == (None, None)
    assert readings.totals.declared_left_out == 2


def test_a_missing_parameter_leaves_the_estimate_out_and_a_declared_figure_counts(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-flat-rate", "RF19")  # no coefficient, no rates
    _declare(db_session, entity, "substitute_tax", tax_due=Decimal("800.00"))
    readings = _readings(db_session, entity)
    substitute, inps = readings.lines
    assert (substitute.estimate, substitute.declared, substitute.counts) == (
        None,
        Decimal("800.00"),
        "declared",
    )
    assert (inps.estimate, inps.declared, inps.counts) == (None, None, None)
    assert readings.totals.estimate is None
    assert (readings.totals.declared, readings.totals.declared_left_out) == (Decimal("800.00"), 1)


def test_a_declared_figure_without_a_tax_due_does_not_count(db_session: Session) -> None:
    entity = _entity(db_session, "it-flat-rate", "RF19")
    _declare(db_session, entity, "substitute_tax", balance=Decimal("-50.00"))
    first = _readings(db_session, entity).lines[0]
    assert (first.declared, first.counts) == (None, None)


def test_the_totals_add_one_kind_of_reading_and_say_how_many_lines_they_leave_out(
    db_session: Session,
) -> None:
    entity = _entity(
        db_session,
        "it-flat-rate",
        "RF19",
        profitability_coefficient=Decimal("78.00"),
        substitute_tax_rate=Decimal("5.00"),
        social_security_rate=Decimal("26.07"),
    )
    _declare(db_session, entity, "substitute_tax", tax_due=Decimal("800.00"))
    _declare(db_session, entity, "inps", tax_due=Decimal("1500.50"))
    totals = _readings(db_session, entity).totals
    assert (totals.declared, totals.declared_left_out) == (Decimal("2300.50"), 0)
    assert (totals.estimate, totals.estimate_left_out) == (Decimal("0.00"), 0)
    assert (totals.paid, totals.paid_left_out) == (None, 2)


def test_the_ordinary_and_company_packs_list_their_taxes_with_no_estimate(
    db_session: Session,
) -> None:
    standard = _entity(db_session, "it-standard", "RF01")
    company = _entity(db_session, "it-company", "RF01")
    _declare(db_session, company, "ires", tax_due=Decimal("12000.00"))
    lines = _readings(db_session, standard).lines
    assert [(line.tax, line.estimable, line.estimate, line.counts) for line in lines] == [
        ("irpef", False, None, None),
        ("inps", False, None, None),
    ]
    lines = _readings(db_session, company).lines
    assert [(line.tax, line.estimate, line.declared, line.counts) for line in lines] == [
        ("ires", None, Decimal("12000.00"), "declared"),
        ("irap", None, None, None),
    ]


def test_a_year_the_pack_has_no_law_for_has_no_lines_but_a_typed_figure_is_still_shown(
    db_session: Session,
) -> None:
    company = _entity(db_session, "it-company", "RF01")
    assert _readings(db_session, company, 2025).lines == []
    DeclaredTaxFigureService(db_session).set_declared_tax_figure(
        DeclaredTaxFigureSet(tax="ires", year=2025, tax_due=Decimal("10.00")),
        ADMIN,
        legal_entity_id=company,
    )
    assert [line.tax for line in _readings(db_session, company, 2025).lines] == ["ires"]


def test_a_regime_with_no_taxes_has_no_lines(db_session: Session) -> None:
    abroad = _entity(db_session, "non-it", None)
    readings = _readings(db_session, abroad)
    assert readings.lines == []
    assert readings.totals.estimate is None


def test_a_what_if_revenue_carries_no_readings(db_session: Session) -> None:
    entity = _entity(db_session, "it-flat-rate", "RF19")
    estimate = AnalyticsService(db_session).get_fiscal_estimate(
        YEAR, ADMIN, revenue=Decimal("1000.00"), legal_entity_id=entity
    )
    assert estimate.readings is None
