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
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import CeilingSimulationQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice, InvoiceLine

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")

RICAVI_CEILING = IT_FLAT_RATE_PACK.ceilings[0]
FUORIUSCITA_CEILING = IT_FLAT_RATE_PACK.ceilings[1]
assert RICAVI_CEILING.id == "soglia_ricavi"
assert FUORIUSCITA_CEILING.id == "soglia_fuoriuscita_immediata"

ANNO = 2024


def _customer(session: Session) -> Customer:
    customer = Customer(ragione_sociale=f"Cliente {uuid4().hex[:8]}")
    session.add(customer)
    session.flush()
    return customer


def _paid_invoice(session: Session, *, numero: int, anno: int, imponibile: Decimal) -> Invoice:
    """A paid `fattura` of exactly `imponibile`, on one zero-rate line -- enough to
    move `paid_revenue_for_calendar_year`, the same basis `evaluate_pack` reads."""
    invoice = Invoice(
        customer_id=_customer(session).id,
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        data_emissione=date(anno, 6, 15),
        imponibile=imponibile,
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=imponibile,
        stato_pagamento="incassato",
        data_incasso=date(anno, 6, 20),
    )
    session.add(invoice)
    session.flush()
    session.add(
        InvoiceLine(
            invoice_id=invoice.id,
            numero_linea=1,
            descrizione="Consulenza",
            quantita=Decimal("1.000000"),
            prezzo_unitario=imponibile,
            prezzo_totale=imponibile,
            aliquota_iva=Decimal("0.00"),
            natura="N2.2",
        )
    )
    session.flush()
    return invoice


def _configure_profile(session: Session) -> None:
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)


# --- ceiling_headroom ----------------------------------------------------------------


def test_headroom_reports_room_remaining_before_each_active_ceiling(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, numero=1, anno=ANNO, imponibile=Decimal("60000.00"))

    headroom = AnalyticsService(db_session).ceiling_headroom(ANNO, READER)

    assert headroom.anno == ANNO
    assert headroom.pack_id == IT_FLAT_RATE_PACK.id
    assert headroom.pack_version == IT_FLAT_RATE_PACK.version
    ricavi_soglia = next(s for s in headroom.soglie if s.id == "soglia_ricavi")
    assert ricavi_soglia.ricavi == Decimal("60000.00")
    assert ricavi_soglia.residuo == Decimal("25000.00")
    assert ricavi_soglia.superata is False
    fuoriuscita = next(s for s in headroom.soglie if s.id == "soglia_fuoriuscita_immediata")
    assert fuoriuscita.residuo == Decimal("40000.00")


def test_headroom_marks_a_crossed_ceiling(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, numero=1, anno=ANNO, imponibile=Decimal("120000.00"))

    headroom = AnalyticsService(db_session).ceiling_headroom(ANNO, READER)

    ricavi_soglia = next(s for s in headroom.soglie if s.id == "soglia_ricavi")
    assert ricavi_soglia.superata is True
    assert ricavi_soglia.residuo == Decimal("-35000.00")


def test_headroom_without_a_fiscal_profile_says_so(db_session: Session) -> None:
    """`NotFound`, naming the screen to go to -- the same signal `get_fiscal_estimate`
    gives when nobody has configured a profile yet: there is no pack to resolve."""
    with pytest.raises(NotFound) as excinfo:
        AnalyticsService(db_session).ceiling_headroom(ANNO, READER)
    assert excinfo.value.details["entity"] == "fiscal_profile"


def test_headroom_is_readable_by_a_readonly_actor(db_session: Session) -> None:
    """Not admin-only, unlike `get_fiscal_estimate`: a revenue-versus-threshold
    figure, not the taxable-income computation that method alone gates."""
    _configure_profile(db_session)
    AnalyticsService(db_session).ceiling_headroom(ANNO, READER)  # does not raise


# --- simulate_ceiling ----------------------------------------------------------------


def test_simulate_adds_the_deals_own_value_estimate_directly(db_session: Session) -> None:
    _configure_profile(db_session)
    _paid_invoice(db_session, numero=1, anno=ANNO, imponibile=Decimal("60000.00"))
    query = CeilingSimulationQuery(valore_preventivato=Decimal("20000.00"))

    simulation = AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)

    assert simulation.aggiunta_sintetica == Decimal("20000.00")
    ricavi_soglia = next(s for s in simulation.soglie if s.id == "soglia_ricavi")
    assert ricavi_soglia.ricavi_attuali == Decimal("60000.00")
    assert ricavi_soglia.residuo_attuale == Decimal("25000.00")
    assert ricavi_soglia.ricavi_simulati == Decimal("80000.00")
    assert ricavi_soglia.residuo_simulato == Decimal("5000.00")
    assert ricavi_soglia.rientra is True


def test_simulate_derives_the_addition_from_hours_times_rate_when_no_value_was_typed(
    db_session: Session,
) -> None:
    """The other half of an "hours-and-value estimate": a not-yet-won deal that has
    priced a rate but not yet a total value still produces a synthetic addition."""
    _configure_profile(db_session)
    query = CeilingSimulationQuery(
        ore_preventivate=Decimal("100.00"), tariffa_oraria=Decimal("250.000000")
    )

    simulation = AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)

    assert simulation.aggiunta_sintetica == Decimal("25000.00")


def test_simulate_prefers_the_typed_value_over_hours_times_rate_when_both_are_present(
    db_session: Session,
) -> None:
    """`valore_preventivato` is the direct estimate; hours-times-rate is only the
    fallback for a deal that has not priced a value yet."""
    _configure_profile(db_session)
    query = CeilingSimulationQuery(
        ore_preventivate=Decimal("100.00"),
        tariffa_oraria=Decimal("250.000000"),
        valore_preventivato=Decimal("30000.00"),
    )

    simulation = AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)

    assert simulation.aggiunta_sintetica == Decimal("30000.00")


def test_simulate_shows_a_ceiling_that_would_not_fit(db_session: Session) -> None:
    """ "Would this fit?", answered no: the addition pushes this ceiling's own
    revenue to and past its threshold."""
    _configure_profile(db_session)
    _paid_invoice(db_session, numero=1, anno=ANNO, imponibile=Decimal("80000.00"))
    query = CeilingSimulationQuery(valore_preventivato=Decimal("10000.00"))

    simulation = AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)

    ricavi_soglia = next(s for s in simulation.soglie if s.id == "soglia_ricavi")
    assert ricavi_soglia.ricavi_simulati == Decimal("90000.00")
    assert ricavi_soglia.rientra is False
    assert ricavi_soglia.livello_allerta_simulato == "Soglia di ricavi raggiunta"


def test_simulate_without_any_estimate_is_a_validation_error_naming_the_field(
    db_session: Session,
) -> None:
    """Neither a direct value nor hours-and-rate: there is nothing to add, and the
    refusal names the field rather than silently simulating a zero addition."""
    _configure_profile(db_session)

    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).simulate_ceiling(ANNO, CeilingSimulationQuery(), READER)
    assert excinfo.value.details["field"] == "valore_preventivato"


def test_simulate_never_persists_anything(db_session: Session) -> None:
    """The whole point of reading the estimate raw rather than by `deal_id`: nothing
    about the simulated deal is written anywhere, and a later real read of the same
    year still sees only the paid revenue that was actually there."""
    _configure_profile(db_session)
    _paid_invoice(db_session, numero=1, anno=ANNO, imponibile=Decimal("60000.00"))
    query = CeilingSimulationQuery(valore_preventivato=Decimal("5000.00"))

    AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)

    again = AnalyticsService(db_session).ceiling_headroom(ANNO, READER)
    ricavi_soglia = next(s for s in again.soglie if s.id == "soglia_ricavi")
    assert ricavi_soglia.ricavi == Decimal("60000.00")


def test_simulate_is_readable_by_a_readonly_actor(db_session: Session) -> None:
    _configure_profile(db_session)
    query = CeilingSimulationQuery(valore_preventivato=Decimal("1000.00"))
    AnalyticsService(db_session).simulate_ceiling(ANNO, query, READER)  # does not raise
