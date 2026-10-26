"""`CalendarService.month`: what was worked and what falls due, by day.

The service adds no data. What it can get wrong is the assembly, and the ways it can
get it wrong are the tests here: a day that should not be in the answer at all, a total
that disagrees with its own rows, an undated commitment drawn in a cell it cannot
belong to, and a month boundary computed in the browser's timezone instead of the
emitter's.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.schemas import AttivitaCreate
from pigrocrm.core.attivita.service import AttivitaService
from pigrocrm.core.calendario.service import CalendarService, month_bounds
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.timetracking.models import TimeEntry

ADMIN = Actor(id=None, type="system", role="admin")
MESE = "2026-09"


@pytest.fixture
def calendario(db_session: Session) -> CalendarService:
    return CalendarService(db_session)


@pytest.fixture
def attivita(db_session: Session) -> AttivitaService:
    return AttivitaService(db_session)


def _log(
    session: Session,
    deal_id: UUID,
    user_id: UUID,
    giorno: date,
    ore: str,
    *,
    descrizione: str = "x",
) -> None:
    """Straight into the table: what is under test is the assembly of a month, and
    `TimeEntryService.create` would drag a rate resolution and a period lock into every
    one of these tests. `TimeEntryService` has its own file."""
    session.add(
        TimeEntry(
            deal_id=deal_id,
            user_id=user_id,
            data=giorno,
            ore=Decimal(ore),
            descrizione=descrizione,
            tariffa_applicata=Decimal("80.000000"),
            tariffa_origine="manuale",
        )
    )
    session.flush()


# --- the month itself ------------------------------------------------------------------


def test_the_month_knows_its_own_first_and_last_day() -> None:
    """Server-side, so the client never has to agree about how long February is."""
    assert month_bounds("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))
    assert month_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    # A leap year, and December -- the month where `mese + 1` is 13 and the naive
    # arithmetic breaks.
    assert month_bounds("2024-02") == (date(2024, 2, 1), date(2024, 2, 29))
    assert month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))


@pytest.mark.parametrize("mese", ["2026-13", "2026-00", "2026-9", "settembre", "2026", ""])
def test_a_month_that_is_not_one_is_refused_with_a_sentence(mese: str) -> None:
    """Validated before it is parsed, so `2026-13` reads as «non è un mese» rather than
    arriving as a `ValueError` from the standard library."""
    with pytest.raises(ValidationFailed) as excinfo:
        month_bounds(mese)

    assert excinfo.value.details["field"] == "mese"


def test_an_empty_month_is_an_empty_answer_and_not_an_error(
    calendario: CalendarService,
) -> None:
    month = calendario.month(MESE, ADMIN)

    assert month.giorni == []
    assert month.ore_totali == Decimal("0.00")
    assert (month.da, month.a) == (date(2026, 9, 1), date(2026, 9, 30))


def test_only_the_days_that_have_something_are_returned(
    calendario: CalendarService, db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """Thirty-one empty objects per request would be noise, and the client knows how
    many days the month has."""
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 14), "8.00")

    month = calendario.month(MESE, ADMIN)

    assert [day.giorno for day in month.giorni] == [date(2026, 9, 14)]


def test_a_day_carries_its_total_and_its_breakdown_by_deal(
    calendario: CalendarService,
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    seeded_open_stage_id: UUID,
) -> None:
    """The total is the sum of the day's own rows: computing it separately in SQL would
    be two totals of the same hours, which is how they start to disagree."""
    customer = Customer(ragione_sociale="Secondo cliente")
    db_session.add(customer)
    db_session.flush()
    second = Deal(
        nome="Secondo progetto",
        customer_id=customer.id,
        pipeline_stage_id=seeded_open_stage_id,
        probabilita=10,
    )
    db_session.add(second)
    db_session.flush()

    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 14), "5.50")
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 14), "2.50")
    _log(db_session, second.id, seeded_user_id, date(2026, 9, 14), "1.00")

    day = calendario.month(MESE, ADMIN).giorni[0]

    assert day.ore == Decimal("9.00")
    assert {(riga.deal_nome, riga.ore) for riga in day.per_deal} == {
        ("Progetto di prova", Decimal("8.00")),
        ("Secondo progetto", Decimal("1.00")),
    }
    # The client's name travels with the deal, so a cell can read «Acme · Progetto di prova» and
    # not just the project. The seeded deal's own customer is named with a uuid by the
    # fixture, so what is asserted is that it is there at all.
    per_deal = {riga.deal_nome: riga.cliente for riga in day.per_deal}
    assert per_deal["Secondo progetto"] == "Secondo cliente"
    assert per_deal["Progetto di prova"]


def test_hours_outside_the_month_are_not_in_it(
    calendario: CalendarService, db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """The boundary in both directions, which is where an off-by-one lives."""
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 8, 31), "8.00")
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 1), "1.00")
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 30), "2.00")
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 10, 1), "4.00")

    month = calendario.month(MESE, ADMIN)

    assert [day.giorno for day in month.giorni] == [date(2026, 9, 1), date(2026, 9, 30)]
    assert month.ore_totali == Decimal("3.00")


def test_the_hours_can_be_scoped_to_one_person(
    calendario: CalendarService,
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
) -> None:
    """«My calendar» is the ordinary reading, and an admin's session would otherwise
    fill a personal grid with the whole team's hours."""
    from pigrocrm.core.auth.models import User

    altro = User(
        email=f"altro-{uuid4()}@example.test",
        password_hash="x",
        nome="Altro",
        ruolo="collaboratore",
    )
    db_session.add(altro)
    db_session.flush()
    _log(db_session, seeded_deal_id, seeded_user_id, date(2026, 9, 14), "8.00")
    _log(db_session, seeded_deal_id, altro.id, date(2026, 9, 15), "3.00")

    mine = calendario.month(MESE, ADMIN, user_id=seeded_user_id)
    everybody = calendario.month(MESE, ADMIN)

    assert [day.giorno for day in mine.giorni] == [date(2026, 9, 14)]
    assert everybody.ore_totali == Decimal("11.00")


# --- the deadlines ---------------------------------------------------------------------


def test_an_activity_with_a_date_lands_on_its_day(
    calendario: CalendarService, attivita: AttivitaService
) -> None:
    attivita.create(AttivitaCreate(titolo="Sollecitare Rossi", scadenza=date(2026, 9, 18)), ADMIN)

    month = calendario.month(MESE, ADMIN)

    assert [day.giorno for day in month.giorni] == [date(2026, 9, 18)]
    assert [item.titolo for item in month.giorni[0].attivita] == ["Sollecitare Rossi"]


def test_an_activity_without_a_date_is_outside_the_grid(
    calendario: CalendarService, attivita: AttivitaService
) -> None:
    """The decision of §3.2, seen from the calendar: it cannot be drawn in a cell, it is
    not late and it is not for today. A NULL is not zero days."""
    attivita.create(AttivitaCreate(titolo="Chiedere il codice SDI"), ADMIN)

    month = calendario.month(MESE, ADMIN)

    assert month.giorni == []
    assert [item.titolo for item in month.attivita_senza_scadenza] == ["Chiedere il codice SDI"]


def test_a_completed_undated_activity_is_not_in_the_section_either(
    calendario: CalendarService, attivita: AttivitaService
) -> None:
    """That section is a list of things still to do; a completed one is history."""
    created = attivita.create(AttivitaCreate(titolo="Fatto e senza data"), ADMIN)
    attivita.complete(created.id, ADMIN)

    assert calendario.month(MESE, ADMIN).attivita_senza_scadenza == []


def test_a_completed_activity_with_a_date_stays_on_its_day(
    calendario: CalendarService, attivita: AttivitaService
) -> None:
    """Its state travels with it, so the grid can draw «done» differently -- hiding it
    would make the calendar say the work was never done."""
    created = attivita.create(AttivitaCreate(titolo="Fatto", scadenza=date(2026, 9, 18)), ADMIN)
    attivita.complete(created.id, ADMIN)

    day = calendario.month(MESE, ADMIN).giorni[0]

    assert [(item.titolo, item.stato) for item in day.attivita] == [("Fatto", "completata")]


def test_an_archived_activity_is_in_no_month(
    calendario: CalendarService, attivita: AttivitaService
) -> None:
    created = attivita.create(
        AttivitaCreate(titolo="Creata per errore", scadenza=date(2026, 9, 18)), ADMIN
    )
    attivita.soft_delete(created.id, ADMIN)

    assert calendario.month(MESE, ADMIN).giorni == []


# --- the invoices ----------------------------------------------------------------------


def test_an_issued_unpaid_invoice_lands_on_its_due_date(
    calendario: CalendarService, db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """Read-only in the calendar, and read from the invoice: the number, the client and
    the amount, so the cell is worth looking at. The fixture's invoices carry no due
    date, so one is given here -- which is also the point: an invoice without a
    `data_scadenza` is on no day at all."""
    from sqlalchemy import select

    from pigrocrm.core.invoices.models import Invoice

    invoice = db_session.scalars(
        select(Invoice).where(Invoice.stato == "emessa").order_by(Invoice.numero).limit(1)
    ).first()
    assert invoice is not None
    invoice.data_scadenza = date(2026, 9, 25)
    db_session.flush()

    day = calendario.month(MESE, ADMIN).giorni[0]

    assert day.giorno == date(2026, 9, 25)
    assert len(day.fatture) == 1
    assert day.fatture[0].numero is not None
    assert day.fatture[0].totale > Decimal("0")


# --- the contract days (REB-748) ---------------------------------------------------------


def _contract_day(db_session: Session, giorno: date, stato: str = "lavorato") -> UUID:
    from pigrocrm.core.contracts.models import Contract
    from pigrocrm.core.work_units.models import WorkUnit

    customer = Customer(ragione_sociale=f"Cliente {uuid4()}")
    db_session.add(customer)
    db_session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=True,
    )
    db_session.add(contract)
    db_session.flush()
    day = WorkUnit(
        contract_id=contract.id,
        data=giorno,
        quantita=Decimal("0.50"),
        descrizione="Revisione",
        stato=stato,
    )
    db_session.add(day)
    db_session.flush()
    db_session.refresh(day)
    return day.id


def test_a_day_worked_without_the_required_approval_is_on_its_date_marked_as_such(
    calendario: CalendarService, db_session: Session
) -> None:
    day_id = _contract_day(db_session, date(2026, 9, 3))

    month = calendario.month(MESE, ADMIN)

    day = next(d for d in month.giorni if d.giorno == date(2026, 9, 3))
    assert [(g.id, g.stato, g.quantita) for g in day.giornate] == [
        (day_id, "lavorato_senza_approvazione", Decimal("0.50"))
    ]
    assert day.ore == Decimal("0.00")


def test_a_withdrawn_or_refused_day_does_not_occupy_a_cell(
    calendario: CalendarService, db_session: Session
) -> None:
    _contract_day(db_session, date(2026, 9, 4), "proposto")
    from pigrocrm.core.work_units.models import WorkUnit

    rows = db_session.query(WorkUnit).all()
    for row in rows:
        row.stato = "rifiutato"
    db_session.flush()

    month = calendario.month(MESE, ADMIN)

    assert month.giorni == []
