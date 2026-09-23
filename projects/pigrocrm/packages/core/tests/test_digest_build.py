"""What one week of a space becomes, list by list, before any HTML exists.

Spec 2026-09-16 §3.1 and §3.2. `WeeklyDigest` is the contract the mail (§3.5) and the
command (§3.4) are both written against, so these tests read the *object* and never a
rendering of it: a section that is right here is right in every surface that shows it.

The corpus is committed, and it lives in this file rather than in `conftest.py` for the
reason `test_dashboard_operational.py` records: `DigestService.build` opens the
operational dashboard's `REPEATABLE READ` snapshot first, and that call refuses a session
with a transaction already in progress -- which is exactly what `db_session` holds open.
So the fixture commits on the engine and removes exactly what it wrote, and every read
here goes through a session of its own.

The week is derived from `today_local()` rather than written down as four literals.
`scadute` is «due on or before today», so an overdue invoice pinned to a fixed calendar
day stops being overdue the day somebody re-reads it. The two pure week helpers are the
one thing tested against literals, because they are the one thing that has no clock in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.schemas import WeeklyDigest
from pigrocrm.core.digest.service import (
    _MOVIMENTI_LETTI,
    IN_SCADENZA_GIORNI,
    DigestService,
    iso_week,
    previous_week,
    week_containing,
)
from pigrocrm.core.documents.models import Document
from pigrocrm.core.gmail.models import PaymentReminder
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry

SETTINGS = Settings(_env_file=None, timezone="Europe/Rome")  # type: ignore[call-arg]
# A `readonly` actor on purpose: building the report is a read from end to end, and a
# digest that needed write rights would be a digest the cron could not send for a space
# whose owner is a collaborator.
ATTORE = Actor(id=uuid7(), type="user", role="readonly")

_PREFIX = "DIGEST"


class Corpus(NamedTuple):
    engine: Engine
    da: date
    a: date
    # `(data_emissione, totale)` of every issued invoice this file writes, so the two
    # month totals can be checked against the corpus instead of against a second call to
    # the same sum.
    emissioni: tuple[tuple[date, Decimal], ...]


def _settimana_scorsa() -> tuple[date, date]:
    """The last complete week, computed here rather than through `previous_week`.

    Deriving the fixture's window from the function under test would make a shift of one
    day invisible: the corpus would move with the bug.
    """
    oggi = today_local(SETTINGS)
    lunedi = oggi - timedelta(days=oggi.isoweekday() - 1 + 7)
    return lunedi, lunedi + timedelta(days=6)


def _require_empty(session: Session) -> None:
    """`vuoto`, the pipeline and the three signals are reads over the whole space with no
    period at all, so the assertions below are only true if this file's rows are the only
    committed ones. A loud precondition beats an off-by-N nobody can read."""
    for model in (Invoice, TimeEntry, Deal, Document):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; "
            "a weekly report has no scope beyond the space, so leftovers change it."
        )


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[Corpus]:
    """One week with something in every section, plus the rows each section must exclude.

    Every date is an offset from the week itself, and the two that must not drift are
    written down here once:

      * the overdue invoice is due thirty days before today, comfortably past
        `solleciti_grace_days` (7), so it is a reminder candidate whatever day this runs;
      * the one due `a + 7` is the last day of the «in scadenza» window and, because the
        window opens the day after the closed week, can never also be overdue -- the two
        halves of «Da incassare» must not name the same invoice twice.
    """
    factory = session_factory(db_engine)
    da, a = _settimana_scorsa()
    oggi = today_local(SETTINGS)
    emissioni: list[tuple[date, Decimal]] = []
    with factory() as session:
        _require_empty(session)
        aperto = PipelineStage(
            nome=f"{_PREFIX} Aperto", posizione=0, probabilita_default=20, tipo="open"
        )
        vinto = PipelineStage(
            nome=f"{_PREFIX} Vinto", posizione=1, probabilita_default=100, tipo="won"
        )
        customer = Customer(ragione_sociale=f"{_PREFIX} Cliente", nazione="IT", custom_fields={})
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            nome="Digestore",
            ruolo="collaboratore",
        )
        session.add_all([aperto, vinto, customer, user])
        session.flush()

        def _deal(nome: str, stage: PipelineStage, valore: str) -> Deal:
            deal = Deal(
                nome=f"{_PREFIX} {nome}",
                customer_id=customer.id,
                pipeline_stage_id=stage.id,
                probabilita=stage.probabilita_default,
                valore_previsto=Decimal(valore),
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            return deal

        def _invoice(
            numero: int,
            *,
            totale: str,
            data_emissione: date,
            data_scadenza: date | None = None,
            data_incasso: date | None = None,
            deal: Deal | None = None,
        ) -> Invoice:
            importo = Decimal(totale)
            invoice = Invoice(
                customer_id=customer.id,
                deal_id=None if deal is None else deal.id,
                tipo="fattura",
                stato="emessa",
                stato_pagamento="incassato" if data_incasso is not None else "da_incassare",
                anno=oggi.year,
                numero=numero,
                imponibile=importo,
                imposta=Decimal("0.00"),
                bollo=Decimal("0.00"),
                totale=importo,
                data_emissione=data_emissione,
                data_scadenza=data_scadenza,
                data_incasso=data_incasso,
                tipo_documento="TD01",
                divisa="EUR",
                custom_fields={},
            )
            session.add(invoice)
            session.flush()
            emissioni.append((data_emissione, importo))
            return invoice

        deal_aperto = _deal("Deal aperto", aperto, "5000.00")
        deal_mosso = _deal("Deal mosso", vinto, "3000.00")

        # Emessa in the week. On an *open* deal, so it also lights the «fatturato ma non
        # vinto» signal and the report has a `segnali` list that is not empty.
        _invoice(
            1,
            totale="1000.00",
            data_emissione=da + timedelta(days=1),
            data_scadenza=a + timedelta(days=40),
            deal=deal_aperto,
        )
        # Issued long before, collected inside the week: the row that makes «Incassate»
        # a different section from «Emesse» rather than a second rendering of it.
        _invoice(
            2,
            totale="1500.00",
            data_emissione=da - timedelta(days=60),
            data_scadenza=da - timedelta(days=30),
            data_incasso=da + timedelta(days=2),
        )
        # Overdue by thirty days: the reminder candidate.
        _invoice(
            3,
            totale="800.00",
            data_emissione=oggi - timedelta(days=90),
            data_scadenza=oggi - timedelta(days=30),
        )
        # Due on the last day of the «in scadenza» window, which is never in the past.
        _invoice(
            4,
            totale="600.00",
            data_emissione=da - timedelta(days=10),
            data_scadenza=a + timedelta(days=7),
        )

        session.add(
            TimeEntry(
                deal_id=deal_aperto.id,
                user_id=user.id,
                data=da + timedelta(days=1),
                ore=Decimal("6.00"),
                descrizione=f"{_PREFIX} lavorazione",
                fatturabile=True,
                tariffa_applicata=Decimal("50.000000"),
                tariffa_origine="manuale",
                costo_applicato=None,
                costo_origine="assente",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        session.add(
            Document(
                deal_id=deal_mosso.id,
                tipo="offerta",
                titolo=f"{_PREFIX} Offerta",
                stato="inviata",
                stato_dal=da - timedelta(days=10),
                custom_fields={},
            )
        )

        def _stage_changed(giorno: date) -> Activity:
            return Activity(
                entity_type="deal",
                entity_id=deal_mosso.id,
                kind="stage_changed",
                actor_id=user.id,
                actor_type="user",
                payload={"from": aperto.nome, "to": vinto.nome},
                # Midday, so the instant lands on the same calendar day in Rome as in UTC
                # and the test is not asserting the timezone conversion by accident --
                # `test_the_week_lists_...` is about the week filter.
                occurred_at=datetime.combine(giorno, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=12),
            )

        session.add(_stage_changed(da + timedelta(days=2)))
        # The same movement three days before the week opened: the filter must drop it.
        session.add(_stage_changed(da - timedelta(days=3)))
        session.commit()
    try:
        yield Corpus(db_engine, da, a, tuple(emissioni))
    finally:
        with factory() as session:
            corpus_customers = select(Customer.id).where(
                Customer.ragione_sociale.like(f"{_PREFIX} %")
            )
            corpus_deals = select(Deal.id).where(Deal.nome.like(f"{_PREFIX} %"))
            session.execute(delete(Activity).where(Activity.entity_id.in_(corpus_deals)))
            session.execute(delete(Document).where(Document.titolo.like(f"{_PREFIX} %")))
            session.execute(delete(TimeEntry).where(TimeEntry.deal_id.in_(corpus_deals)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(corpus_customers)))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.ragione_sociale.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.nome == "Digestore"))
            session.commit()


def _fattura_in_piu(
    engine: Engine, *, numero: int, totale: str, data_emissione: date, data_scadenza: date
) -> UUID:
    """One more unpaid, issued invoice on the corpus's customer, committed.

    On that customer deliberately: the fixture's teardown deletes by `customer_id`, so a
    row added here leaves with the rest of the corpus -- and `payment_reminders` hangs off
    `invoices.id` with `ON DELETE CASCADE`, so the reminders of the test below go too.
    """
    with session_factory(engine)() as session:
        customer_id = session.execute(
            select(Customer.id).where(Customer.ragione_sociale == f"{_PREFIX} Cliente")
        ).scalar_one()
        importo = Decimal(totale)
        invoice = Invoice(
            customer_id=customer_id,
            tipo="fattura",
            stato="emessa",
            stato_pagamento="da_incassare",
            anno=today_local(SETTINGS).year,
            numero=numero,
            imponibile=importo,
            imposta=Decimal("0.00"),
            bollo=Decimal("0.00"),
            totale=importo,
            data_emissione=data_emissione,
            data_scadenza=data_scadenza,
            data_incasso=None,
            tipo_documento="TD01",
            divisa="EUR",
            custom_fields={},
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def _build(engine: Engine, settimana: tuple[date, date]) -> WeeklyDigest:
    with session_factory(engine)() as session:
        return DigestService(session, SETTINGS).build(ATTORE, settimana)


# --- the empty space ------------------------------------------------------------------


def test_an_empty_space_builds_an_empty_digest(db_engine: Engine) -> None:
    """§2: silence for an empty space. The command decides that by asking the report, so
    `vuoto` has to be true of a space with nothing in it and of nothing else."""
    with session_factory(db_engine)() as session:
        _require_empty(session)
    digest = _build(db_engine, (date(2026, 9, 7), date(2026, 9, 13)))
    assert digest.vuoto is True
    assert digest.settimana_ferma is True
    assert digest.settimana == "2026-W37"
    assert (digest.da, digest.a) == (date(2026, 9, 7), date(2026, 9, 13))


# --- the week that had something in it ------------------------------------------------


def test_the_week_lists_issued_collected_overdue_and_due(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.da, corpus.a))

    assert [i.importo for i in digest.emesse] == [Decimal("1000.00")]
    assert [i.data for i in digest.emesse] == [corpus.da + timedelta(days=1)]
    assert [i.numero for i in digest.emesse] == ["2026/1"]
    assert [i.cliente for i in digest.emesse] == [f"{_PREFIX} Cliente"]
    assert [i.stato for i in digest.emesse] == ["emessa"]
    assert [i.stato_pagamento for i in digest.emesse] == ["da_incassare"]

    assert [i.importo for i in digest.incassate] == [Decimal("1500.00")]
    # Attributed to the day the money arrived, not to the day the invoice was written.
    assert [i.data for i in digest.incassate] == [corpus.da + timedelta(days=2)]
    assert [i.stato_pagamento for i in digest.incassate] == ["incassato"]

    assert [i.giorni_di_ritardo for i in digest.scadute] == [30]
    assert [i.importo for i in digest.scadute] == [Decimal("800.00")]
    assert [i.numero for i in digest.scadute] == ["2026/3"]

    # The window opens the day after the closed week and runs seven days, so the invoice
    # due on `a + 7` is in it and the overdue one is not.
    assert [i.importo for i in digest.in_scadenza] == [Decimal("600.00")]
    assert [i.data for i in digest.in_scadenza] == [corpus.a + timedelta(days=7)]

    assert digest.ore is not None
    assert digest.ore.ore_totali == Decimal("6.00")
    assert (digest.ore.da, digest.ore.a) == (corpus.da, corpus.a)

    assert [m.stage_nome for m in digest.deal_mossi] == [f"{_PREFIX} Vinto"]
    assert [m.titolo for m in digest.deal_mossi] == [f"{_PREFIX} Deal mosso"]
    assert [m.quando for m in digest.deal_mossi] == [corpus.da + timedelta(days=2)]

    assert len(digest.offerte_in_attesa) == 1
    assert digest.offerte_in_attesa[0].titolo == f"{_PREFIX} Offerta"
    assert (
        digest.offerte_in_attesa[0].giorni
        == (today_local(SETTINGS) - (corpus.da - timedelta(days=10))).days
    )

    assert digest.settimana_ferma is False
    assert digest.vuoto is False


def test_the_months_are_the_register_sums_and_not_a_second_count(corpus: Corpus) -> None:
    """§3.1: the running month beside the one before it. Both come from
    `sum_emesse_in_periodo`, so they are checked against the corpus's own emission dates
    rather than against another call to the same sum."""
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    corrente_da, corrente_a = month_bounds(corpus.a.year, corpus.a.month)
    precedente = corrente_da - timedelta(days=1)
    precedente_da, precedente_a = month_bounds(precedente.year, precedente.month)

    def _somma(primo: date, ultimo: date) -> Decimal:
        return sum(
            (importo for giorno, importo in corpus.emissioni if primo <= giorno <= ultimo),
            Decimal("0.00"),
        )

    assert digest.totale_mese_corrente == _somma(corrente_da, corrente_a)
    assert digest.totale_mese_precedente == _somma(precedente_da, precedente_a)


def test_the_backlog_and_the_signals_come_from_the_operational_dashboard(
    corpus: Corpus,
) -> None:
    """§3.1's «Da emettere» and «Da sistemare». The hours and the accrued value are
    `AnalyticsService`'s backlog verbatim, and only the signals that fired are carried.

    `vinti_da_fatturare` is asserted twice, at zero and at one: a field checked only
    against zero is a field a `return 0` would satisfy, and this one is a count of the
    deals somebody is supposed to invoice this week.

    `concentrazione_sopra_soglia` (REB-371) is in the list too: the corpus's one
    customer holds the whole register, past the default 0.30 threshold, which is what
    makes this also a test of the `conteggio > 0` filter carrying that fourth signal
    exactly the way it already carries the first two.
    """
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    assert digest.ore_non_fatturate == Decimal("6.00")
    assert digest.valore_maturato == Decimal("300.00")
    # The won deal has no unbilled hours, so «vinto ma da fatturare» is zero -- which is
    # what makes this the test of the `conteggio > 0` filter and not only of the list.
    assert digest.vinti_da_fatturare == 0
    assert [s.codice for s in digest.segnali] == [
        "fatturato_non_vinto",
        "scaduto_non_incassato",
        "concentrazione_sopra_soglia",
    ]
    assert all(s.conteggio > 0 for s in digest.segnali)
    assert all(s.collegamento for s in digest.segnali)

    # One won deal with billable hours nobody has put on a document: exactly
    # `won_with_unbilled_hours_predicate`, which is where «da fatturare» is defined.
    with session_factory(corpus.engine)() as session:
        vinto_id = session.execute(
            select(PipelineStage.id).where(PipelineStage.nome == f"{_PREFIX} Vinto")
        ).scalar_one()
        customer_id = session.execute(
            select(Customer.id).where(Customer.ragione_sociale == f"{_PREFIX} Cliente")
        ).scalar_one()
        user_id = session.execute(select(User.id).where(User.nome == "Digestore")).scalar_one()
        deal = Deal(
            nome=f"{_PREFIX} Deal vinto da fatturare",
            customer_id=customer_id,
            pipeline_stage_id=vinto_id,
            probabilita=100,
            valore_previsto=Decimal("2000.00"),
            custom_fields={},
        )
        session.add(deal)
        session.flush()
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=user_id,
                data=corpus.da + timedelta(days=3),
                ore=Decimal("4.00"),
                descrizione=f"{_PREFIX} da fatturare",
                fatturabile=True,
                tariffa_applicata=Decimal("50.000000"),
                tariffa_origine="manuale",
                costo_applicato=None,
                costo_origine="assente",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        session.commit()

    aggiornato = _build(corpus.engine, (corpus.da, corpus.a))
    assert aggiornato.vinti_da_fatturare == 1
    assert "vinto_da_fatturare" in [s.codice for s in aggiornato.segnali]


def test_the_pipeline_carries_only_the_stages_that_hold_something(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    assert [(s.stage_nome, s.numero) for s in digest.pipeline] == [
        (f"{_PREFIX} Aperto", 1),
        (f"{_PREFIX} Vinto", 1),
    ]
    assert [s.valore_totale for s in digest.pipeline] == [
        Decimal("5000.00"),
        Decimal("3000.00"),
    ]


def test_a_movement_just_after_midnight_belongs_to_the_week_it_opens(corpus: Corpus) -> None:
    """`activities.occurred_at` is an instant; the week is seven days in the emitter's zone.

    23:30 UTC on the Sunday before the week is 00:30 or 01:30 on its Monday in Rome,
    whichever side of the DST change it falls. Read as a UTC day it lands in the week
    before -- the week the report already went out for -- and the movement is never
    mentioned at all. This is the one test that fails if the conversion is dropped.
    """
    with session_factory(corpus.engine)() as session:
        deal_id = session.execute(
            select(Deal.id).where(Deal.nome == f"{_PREFIX} Deal mosso")
        ).scalar_one()
        session.add(
            Activity(
                entity_type="deal",
                entity_id=deal_id,
                kind="stage_changed",
                actor_id=None,
                actor_type="system",
                payload={"from": f"{_PREFIX} Aperto", "to": f"{_PREFIX} Vinto"},
                occurred_at=datetime.combine(
                    corpus.da - timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                + timedelta(hours=23, minutes=30),
            )
        )
        session.commit()
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    assert sorted(m.quando for m in digest.deal_mossi) == [
        corpus.da,
        corpus.da + timedelta(days=2),
    ]


def test_a_week_keeps_its_movements_however_many_came_after_it(corpus: Corpus) -> None:
    """A resend of an old week (`pigrocrm digest --data`) must report *that* week.

    The read is bounded -- `_MOVIMENTI_LETTI` rows, newest first -- so a read by kind
    alone answers the latest movements of the whole space and this week's are filtered
    out of a list they were never in. Here every one of the two hundred and one rows
    below happened *after* the week, which is enough to push its own movement past the
    ceiling: with a window in the query the week still has its deal mosso, with a filter
    in Python it silently has none.
    """
    with session_factory(corpus.engine)() as session:
        deal_id = session.execute(
            select(Deal.id).where(Deal.nome == f"{_PREFIX} Deal mosso")
        ).scalar_one()
        dopo = datetime.combine(
            corpus.a + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        ) + timedelta(hours=12)
        for i in range(_MOVIMENTI_LETTI + 1):
            session.add(
                Activity(
                    entity_type="deal",
                    entity_id=deal_id,
                    kind="stage_changed",
                    actor_id=None,
                    actor_type="system",
                    payload={"from": f"{_PREFIX} Aperto", "to": f"{_PREFIX} Vinto"},
                    occurred_at=dopo + timedelta(seconds=i),
                )
            )
        session.commit()

    digest = _build(corpus.engine, (corpus.da, corpus.a))

    assert [m.quando for m in digest.deal_mossi] == [corpus.da + timedelta(days=2)]


def test_an_invoice_that_fell_due_inside_the_week_is_money_to_collect(corpus: Corpus) -> None:
    """The one «Da incassare» most needs to name, and the one it used to lose.

    `scadute` was `SollecitiService.candidates`, whose predicate is «overdue by more than
    `solleciti_grace_days`» -- seven days by default -- because chasing the day after the
    due date is aggressive. That is a property of a *reminder*. An invoice that fell due
    on the last day of the reported week is at most seven days late on the Monday the
    report goes out, so it was inside the grace period every single time and never
    appeared at all: the report said nothing about the money that had just come due.

    The due date is `corpus.a`, the last day of the week, precisely because that is the
    worst case: whatever day this test runs, `a` is between one and seven days back.
    """
    fattura = _fattura_in_piu(
        corpus.engine,
        numero=10,
        totale="900.00",
        data_emissione=corpus.da - timedelta(days=20),
        data_scadenza=corpus.a,
    )
    ritardo = (today_local(SETTINGS) - corpus.a).days
    # The grace period the old source applied, and the proof this row was inside it.
    assert 1 <= ritardo <= SETTINGS.solleciti_grace_days

    digest = _build(corpus.engine, (corpus.da, corpus.a))

    righe = {riga.invoice_id: riga for riga in digest.scadute}
    assert fattura in righe
    assert righe[fattura].giorni_di_ritardo == ritardo
    assert righe[fattura].importo == Decimal("900.00")
    assert righe[fattura].cliente == f"{_PREFIX} Cliente"
    assert righe[fattura].stato_pagamento == "da_incassare"
    # Worst first: the corpus's thirty-day invoice comes before this week's own.
    assert [riga.giorni_di_ritardo for riga in digest.scadute] == [30, ritardo]


def test_an_invoice_due_today_is_scaduta_with_no_days_of_delay(corpus: Corpus) -> None:
    """Due today is due today: `list_scadute` is `<=` and not `_overdue_predicate()`'s
    `<`, so the invoice appears with zero days of delay rather than being left out of the
    section until tomorrow.

    Today is also inside the «in scadenza» window on the Monday the cron runs -- that
    window opens on `a + 1`, which *is* today -- so this is the row that proves the two
    halves still name one invoice once.
    """
    oggi = today_local(SETTINGS)
    fattura = _fattura_in_piu(
        corpus.engine,
        numero=11,
        totale="700.00",
        data_emissione=oggi - timedelta(days=30),
        data_scadenza=oggi,
    )

    digest = _build(corpus.engine, (corpus.da, corpus.a))

    righe = {riga.invoice_id: riga for riga in digest.scadute}
    assert righe[fattura].giorni_di_ritardo == 0
    assert corpus.a + timedelta(days=1) <= oggi <= corpus.a + timedelta(days=IN_SCADENZA_GIORNI)
    assert fattura not in [riga.invoice_id for riga in digest.in_scadenza]


def test_an_invoice_chased_to_the_ceiling_is_still_owed(corpus: Corpus) -> None:
    """`SollecitiService.candidates` also drops an invoice with `solleciti_max_reminders`
    rows and one chased inside `solleciti_min_interval_days`: there is no further reminder
    to prepare, and nothing to prepare one from today. Neither says the money arrived.

    Three reminders, all sent today, is both conditions at once on the corpus's own
    thirty-day invoice -- and it still has to be the first line of «Da incassare».
    """
    with session_factory(corpus.engine)() as session:
        scaduta = session.execute(
            select(Invoice.id).where(Invoice.anno == 2026, Invoice.numero == 3)
        ).scalar_one()
        for sequence in range(1, SETTINGS.solleciti_max_reminders + 1):
            session.add(
                PaymentReminder(invoice_id=scaduta, sequence=sequence, sent_at=datetime.now(UTC))
            )
        session.commit()

    digest = _build(corpus.engine, (corpus.da, corpus.a))

    righe = {riga.invoice_id: riga for riga in digest.scadute}
    assert scaduta in righe
    assert righe[scaduta].giorni_di_ritardo == 30
    assert righe[scaduta].importo == Decimal("800.00")


def test_an_invoice_that_is_already_overdue_is_not_also_in_scadenza(corpus: Corpus) -> None:
    """The two halves of «Da incassare» never name the same invoice.

    `scadute` is «due on or before today» and `in_scadenza` is «due in the seven days
    after the week», and the two windows meet: on the Monday the cron runs they share
    exactly today, and for a week recomputed with `--data` the whole of the second one has
    since gone by and lies inside the first. Here it is the corpus's own overdue invoice,
    and the week is chosen so that its due date falls inside the «in scadenza» window that
    opens the day after.
    """
    oggi = today_local(SETTINGS)
    settimana = week_containing(oggi - timedelta(days=IN_SCADENZA_GIORNI + 30))
    with session_factory(corpus.engine)() as session:
        scaduta = session.execute(
            select(Invoice.id).where(Invoice.anno == 2026, Invoice.numero == 3)
        ).scalar_one()

    digest = _build(corpus.engine, settimana)

    # The invoice is due inside the window the week opens: without the dedupe it is read
    # twice, once as overdue and once as still coming.
    assert settimana[1] + timedelta(days=1) <= oggi - timedelta(days=30)
    assert oggi - timedelta(days=30) <= settimana[1] + timedelta(days=IN_SCADENZA_GIORNI)
    assert scaduta in [i.invoice_id for i in digest.scadute]
    assert scaduta not in [i.invoice_id for i in digest.in_scadenza]


def test_an_invoice_with_no_date_at_all_is_refused_by_its_id(db_engine: Engine) -> None:
    """`_riga`'s three predicates cannot produce a row with no date, so the row that
    arrives without one is a register this report has no honest line for: `date.min`
    would put «lun 1 gen» of the year 1 in a mail every Monday. The id is in the message
    because that is what the operator has to look up."""
    invoice = Invoice(
        id=uuid7(),
        customer_id=uuid7(),
        tipo="fattura",
        stato="bozza",
        anno=None,
        numero=None,
        imponibile=Decimal("10.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("10.00"),
        data_emissione=None,
        data_scadenza=None,
        data_incasso=None,
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )
    with (
        session_factory(db_engine)() as session,
        pytest.raises(ValueError, match=str(invoice.id)),
    ):
        DigestService(session, SETTINGS)._riga(invoice, {}, None)


def test_a_quiet_week_is_flagged_but_the_space_is_not_empty(corpus: Corpus) -> None:
    """§3.1's variable closing. Nothing was issued, collected, logged or moved in the week
    of 2020 -- but the space still owes money and still has a pipeline, so the report goes
    out with its «settimana ferma» closing rather than not going out at all."""
    digest = _build(corpus.engine, week_containing(date(2020, 1, 8)))
    assert (digest.emesse, digest.incassate, digest.deal_mossi) == ([], [], [])
    assert digest.ore is None
    assert digest.settimana_ferma is True
    assert digest.vuoto is False
    assert digest.scadute != []
    assert digest.pipeline != []


# --- the three week helpers -----------------------------------------------------------


def test_previous_week_and_iso_week() -> None:
    assert week_containing(date(2026, 9, 16)) == (date(2026, 9, 14), date(2026, 9, 20))
    # A Monday and a Sunday are both inside their own week, both ends included.
    assert week_containing(date(2026, 9, 14)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert week_containing(date(2026, 9, 20)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert iso_week(date(2026, 9, 14)) == "2026-W38"
    # Two digits, always: `2026-W7` sorts after `2026-W38` as a string.
    assert iso_week(date(2026, 2, 9)) == "2026-W07"
    # The last days of December belong to the first ISO week of the next year, which is
    # why the year comes from `isocalendar()` and never from `day.year`.
    assert iso_week(date(2026, 12, 31)) == "2026-W53"
    assert iso_week(date(2027, 1, 1)) == "2026-W53"


def test_previous_week_is_the_current_one_shifted_back_seven_days() -> None:
    da, a = previous_week(SETTINGS)
    assert (da, a) == _settimana_scorsa()
    assert a - da == timedelta(days=6)
    assert da.isoweekday() == 1


def test_the_week_the_digest_reports_is_the_one_it_was_given(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    assert (digest.da, digest.a) == (corpus.da, corpus.a)
    assert digest.settimana == iso_week(corpus.da)


def test_deal_ids_and_invoice_ids_are_the_real_rows(corpus: Corpus) -> None:
    """Every row carries the id of the thing it describes, because §3.1 requires every
    line of the mail to link to the record it came from."""
    digest = _build(corpus.engine, (corpus.da, corpus.a))
    with session_factory(corpus.engine)() as session:
        emessa = session.execute(
            select(Invoice.id).where(Invoice.anno == 2026, Invoice.numero == 1)
        ).scalar_one()
        mosso = session.execute(
            select(Deal.id).where(Deal.nome == f"{_PREFIX} Deal mosso")
        ).scalar_one()
    assert [i.invoice_id for i in digest.emesse] == [emessa]
    assert [m.deal_id for m in digest.deal_mossi] == [mosso]
    assert isinstance(digest.emesse[0].invoice_id, UUID)
