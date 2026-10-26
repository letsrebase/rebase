"""The daily automation pass: three rules that open an attivita, once each (REB-727, REB-728).

Slice 7 §10.4 and §10.5 are what this file is for: a rule that runs twice on the same subject
opens **one** attivita and the second run says why it did not open another; closing the
attivita lets the next pass open a new one. The concurrent case is proved on two real
connections with a barrier, as the spec asks, and not with two calls in a row.

The ordinary cases run inside `db_session`'s rolled-back transaction. The race cannot: it
needs rows another connection can see, so it commits its own and removes them afterwards.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.attivita.service import AttivitaService
from pigrocrm.core.auth.models import User
from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.daily import (
    OFFERTA_ATTESA_GIORNI,
    OFFERTA_FINESTRA_GIORNI,
    AutomationPass,
    PassReport,
)
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.schemas import (
    KIND_ATTIVITA_OPENED,
    AttivitaRule,
    AutomationConfigUpdate,
)
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage

ADMIN = Actor(id=None, type="user", role="admin")


# -- fixtures: made-up data ------------------------------------------------------------


def _customer(session: Session, nome: str = "Acme S.r.l.") -> Customer:
    customer = Customer(ragione_sociale=nome, nazione="IT", custom_fields={})
    session.add(customer)
    session.flush()
    return customer


def _stage(session: Session, tipo: str = "open") -> PipelineStage:
    stage = PipelineStage(
        nome=f"Fase {tipo} {datetime.now(UTC).timestamp()}",
        posizione=0,
        probabilita_default=10,
        tipo=tipo,
    )
    session.add(stage)
    session.flush()
    return stage


def _owner(session: Session, *, attivo: bool = True) -> User:
    user = User(
        email=f"titolare-{datetime.now(UTC).timestamp()}@example.test",
        password_hash="x",
        nome="Maria Rossi",
        ruolo="collaboratore",
        attivo=attivo,
    )
    session.add(user)
    session.flush()
    return user


def _deal(
    session: Session,
    customer: Customer,
    stage: PipelineStage,
    *,
    nome: str = "Sito Acme",
    owner: User | None = None,
    created_days_ago: int = 0,
) -> Deal:
    deal = Deal(
        nome=nome,
        customer_id=customer.id,
        pipeline_stage_id=stage.id,
        probabilita=10,
        owner_id=owner.id if owner else None,
        custom_fields={},
    )
    session.add(deal)
    session.flush()
    if created_days_ago:
        deal.created_at = datetime.now(UTC) - timedelta(days=created_days_ago)
        session.flush()
    return deal


def _offer(
    session: Session,
    *,
    deal: Deal | None = None,
    customer: Customer | None = None,
    sent_days_ago: int,
    titolo: str = "Offerta sito",
    stato: str = "inviata",
) -> Document:
    document = Document(
        deal_id=deal.id if deal else None,
        customer_id=customer.id if customer else None,
        tipo="offerta",
        titolo=titolo,
        stato=stato,
        stato_dal=today_local() - timedelta(days=sent_days_ago),
        versione_corrente=1,
        custom_fields={},
    )
    session.add(document)
    session.flush()
    return document


def _invoice(
    session: Session,
    customer: Customer,
    *,
    deal: Deal | None = None,
    overdue_days: int = 3,
    pagamento: str = "da_incassare",
    numero: int | None = 12,
    stato: str = "emessa",
) -> Invoice:
    invoice = Invoice(
        customer_id=customer.id,
        deal_id=deal.id if deal else None,
        tipo="fattura",
        stato=stato,
        stato_pagamento=pagamento,
        anno=2026 if numero is not None else None,
        numero=numero,
        imponibile=Decimal("100.00"),
        imposta=Decimal("22.00"),
        bollo=Decimal("0.00"),
        totale=Decimal("122.00"),
        data_emissione=today_local() - timedelta(days=overdue_days + 30),
        data_scadenza=today_local() - timedelta(days=overdue_days),
    )
    session.add(invoice)
    session.flush()
    return invoice


def _run(session: Session, **kwargs: object) -> PassReport:
    return AutomationPass(session).run(**kwargs)  # type: ignore[arg-type]


def _report(report: PassReport, rule: AttivitaRule) -> tuple[bool, int, int]:
    (one,) = [r for r in report.rules if r.regola == rule]
    return one.disattivata, one.aperte, one.gia_aperte


def _rows(session: Session, rule: AttivitaRule) -> list[Attivita]:
    return list(
        session.scalars(
            select(Attivita).where(Attivita.regola == rule).order_by(Attivita.created_at)
        )
    )


@pytest.fixture(autouse=True)
def _clean_slate(db_session: Session) -> None:
    """The shared engine may hold rows another module committed; these tests count."""
    db_session.execute(delete(Attivita))


# -- offerta_inviata_richiama ----------------------------------------------------------


def test_an_offer_waiting_seven_days_opens_one_attivita_for_the_deals_owner(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    owner = _owner(db_session)
    deal = _deal(db_session, customer, _stage(db_session), owner=owner)
    _offer(db_session, deal=deal, sent_days_ago=7)

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "offerta_inviata_richiama") == (False, 1, 0)
    # The second run names why it opened nothing: the subject already has its attivita.
    assert _report(second, "offerta_inviata_richiama") == (False, 0, 1)
    (row,) = _rows(db_session, "offerta_inviata_richiama")
    assert row.titolo == "Richiamare Acme S.r.l. sull'offerta Offerta sito"
    assert row.stato == "aperta"
    assert row.origine == "automazione"
    assert row.deal_id == deal.id
    assert row.assegnata_a == owner.id
    # Due seven days after the sending, which is today when the pass runs on day seven.
    assert row.scadenza == today_local()


def test_an_offer_sent_six_days_ago_is_not_yet_called_back(db_session: Session) -> None:
    deal = _deal(db_session, _customer(db_session), _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=OFFERTA_ATTESA_GIORNI - 1)

    assert _report(_run(db_session), "offerta_inviata_richiama") == (False, 0, 0)


def test_an_offer_older_than_the_window_is_not_the_follow_up_of_its_sending(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    deal = _deal(db_session, customer, _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=OFFERTA_ATTESA_GIORNI + OFFERTA_FINESTRA_GIORNI + 1)
    _offer(
        db_session,
        customer=customer,
        sent_days_ago=OFFERTA_ATTESA_GIORNI + OFFERTA_FINESTRA_GIORNI,
        titolo="Offerta limite",
    )

    assert _report(_run(db_session), "offerta_inviata_richiama") == (False, 1, 0)
    (row,) = _rows(db_session, "offerta_inviata_richiama")
    # Born late when the pass is late: due seven days after the sending, in the past.
    assert row.scadenza == today_local() - timedelta(days=OFFERTA_FINESTRA_GIORNI)
    assert row.customer_id == customer.id
    assert row.deal_id is None


def test_an_answered_or_draft_offer_is_left_alone(db_session: Session) -> None:
    deal = _deal(db_session, _customer(db_session), _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=10, stato="accettata")
    _offer(db_session, deal=deal, sent_days_ago=10, stato="rifiutata", titolo="Altra")
    _offer(db_session, deal=deal, sent_days_ago=10, stato="bozza", titolo="Bozza")

    assert _report(_run(db_session), "offerta_inviata_richiama") == (False, 0, 0)


# -- deal_fermo ------------------------------------------------------------------------


def test_a_deal_in_the_same_stage_past_the_threshold_opens_one_attivita_due_today(
    db_session: Session,
) -> None:
    owner = _owner(db_session)
    deal = _deal(
        db_session, _customer(db_session), _stage(db_session), owner=owner, created_days_ago=31
    )

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "deal_fermo") == (False, 1, 0)
    assert _report(second, "deal_fermo") == (False, 0, 1)
    (row,) = _rows(db_session, "deal_fermo")
    assert row.titolo == "Il deal Sito Acme è fermo da 31 giorni"
    assert row.deal_id == deal.id
    assert row.assegnata_a == owner.id
    assert row.scadenza == today_local()
    assert row.origine == "automazione"


def test_a_recent_stage_change_resets_the_clock_and_a_closed_deal_is_never_stalled(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    moved = _deal(db_session, customer, _stage(db_session), nome="Spostato", created_days_ago=90)
    db_session.add(
        Activity(
            entity_type="deal",
            entity_id=moved.id,
            kind="stage_changed",
            actor_type="user",
            payload={},
            occurred_at=datetime.now(UTC) - timedelta(days=2),
        )
    )
    _deal(db_session, customer, _stage(db_session, "won"), nome="Vinto", created_days_ago=90)
    _deal(db_session, customer, _stage(db_session, "lost"), nome="Perso", created_days_ago=90)
    db_session.flush()

    assert _report(_run(db_session), "deal_fermo") == (False, 0, 0)


def test_the_threshold_is_the_configured_one(db_session: Session) -> None:
    _deal(db_session, _customer(db_session), _stage(db_session), created_days_ago=10)
    assert _report(_run(db_session), "deal_fermo") == (False, 0, 0)

    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a4_deal_fermo_giorni=10), ADMIN
    )

    assert _report(_run(db_session), "deal_fermo") == (False, 1, 0)


# -- fattura_scaduta -------------------------------------------------------------------


def test_an_overdue_unpaid_invoice_opens_one_attivita_for_its_deals_owner(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    owner = _owner(db_session)
    deal = _deal(db_session, customer, _stage(db_session), owner=owner)
    invoice = _invoice(db_session, customer, deal=deal)

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "fattura_scaduta") == (False, 1, 0)
    assert _report(second, "fattura_scaduta") == (False, 0, 1)
    (row,) = _rows(db_session, "fattura_scaduta")
    assert row.titolo == "Sollecitare Acme S.r.l. per 2026/12"
    assert row.invoice_id == invoice.id
    assert row.assegnata_a == owner.id
    assert row.scadenza == today_local()


def test_due_today_paid_draft_and_annulled_invoices_are_not_overdue(db_session: Session) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer, overdue_days=0, numero=1)  # due today is not late
    _invoice(db_session, customer, pagamento="incassata", numero=2)
    _invoice(db_session, customer, numero=None, stato="bozza")
    annullata = _invoice(db_session, customer, numero=4)
    annullata.stato = "annullata"
    annullata.annullata_il = today_local()
    annullata.motivo_annullamento = "emessa per errore"
    db_session.flush()

    assert _report(_run(db_session), "fattura_scaduta") == (False, 0, 0)


def test_closing_the_attivita_lets_the_next_pass_open_a_new_one(db_session: Session) -> None:
    """Slice 7 §10.5. «Chase Rossi» closed without Rossi paying means «I spoke to him»: the
    next pass opens the next one, and a cancelled one frees the subject the same way."""
    customer = _customer(db_session)
    _invoice(db_session, customer)
    _run(db_session)
    (first,) = _rows(db_session, "fattura_scaduta")

    AttivitaService(db_session).complete(first.id, ADMIN)
    assert _report(_run(db_session), "fattura_scaduta") == (False, 1, 0)
    second = [r for r in _rows(db_session, "fattura_scaduta") if r.stato == "aperta"]
    assert len(second) == 1 and second[0].id != first.id

    AttivitaService(db_session).cancel(second[0].id, ADMIN)
    assert _report(_run(db_session), "fattura_scaduta") == (False, 1, 0)
    assert len(_rows(db_session, "fattura_scaduta")) == 3


def test_a_deal_without_an_owner_falls_back_to_the_customers_owner(db_session: Session) -> None:
    owner = _owner(db_session)
    customer = _customer(db_session)
    customer.owner_id = owner.id
    deal = _deal(db_session, customer, _stage(db_session))
    _invoice(db_session, customer, deal=deal, numero=5)
    _invoice(db_session, customer, numero=6)  # no deal at all

    _run(db_session)

    rows = _rows(db_session, "fattura_scaduta")
    assert [row.assegnata_a for row in rows] == [owner.id, owner.id]


def test_an_inactive_owner_leaves_the_attivita_for_whoever_opens_the_list(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    gone = _owner(db_session, attivo=False)
    deal = _deal(db_session, customer, _stage(db_session), owner=gone)
    _invoice(db_session, customer, deal=deal)

    _run(db_session)

    (row,) = _rows(db_session, "fattura_scaduta")
    assert row.assegnata_a is None


# -- switches, rehearsal, timeline -----------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "field"),
    [
        ("offerta_inviata_richiama", "a3_offerta_inviata_richiama"),
        ("deal_fermo", "a4_deal_fermo"),
        ("fattura_scaduta", "a5_fattura_scaduta"),
    ],
)
def test_switching_a_rule_off_stops_it_and_the_report_says_so(
    db_session: Session, rule: AttivitaRule, field: str
) -> None:
    customer = _customer(db_session)
    deal = _deal(db_session, customer, _stage(db_session), created_days_ago=40)
    _offer(db_session, deal=deal, sent_days_ago=9)
    _invoice(db_session, customer)
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(**{field: False}), ADMIN
    )

    report = _run(db_session)

    assert _report(report, rule) == (True, 0, 0)
    assert _rows(db_session, rule) == []
    # The other two are untouched by one rule's switch.
    others = [r for r in report.rules if r.regola != rule]
    assert [(r.disattivata, r.aperte) for r in others] == [(False, 1), (False, 1)]


def test_every_opened_attivita_leaves_a_system_entry_on_its_own_timeline(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer)

    _run(db_session)

    (row,) = _rows(db_session, "fattura_scaduta")
    (entry,) = db_session.scalars(
        select(Activity).where(Activity.entity_type == "attivita", Activity.entity_id == row.id)
    )
    assert entry.kind == KIND_ATTIVITA_OPENED
    assert entry.actor_type == "system" and entry.actor_id is None
    assert entry.payload["regola"] == "fattura_scaduta"
    assert entry.payload["invoice_id"] == str(row.invoice_id)


def test_a_rehearsal_reports_what_would_open_and_writes_nothing(db_session: Session) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer)

    rehearsal = _run(db_session, dry_run=True)
    assert _report(rehearsal, "fattura_scaduta") == (False, 1, 0)
    assert _rows(db_session, "fattura_scaduta") == []

    assert _report(_run(db_session), "fattura_scaduta") == (False, 1, 0)


def test_the_index_refuses_a_second_open_attivita_of_one_rule_and_subject(
    db_session: Session,
) -> None:
    """The constraint itself, below the pass: a writer that skipped the read still cannot
    open two. Different rules, or the same rule after a close, are not refused."""
    customer = _customer(db_session)
    invoice = _invoice(db_session, customer)

    def add(regola: str, stato: str = "aperta") -> None:
        with db_session.begin_nested():
            db_session.add(
                Attivita(
                    titolo="x",
                    stato=stato,
                    completata_il=today_local() if stato == "completata" else None,
                    origine="automazione",
                    regola=regola,
                    invoice_id=invoice.id,
                    custom_fields={},
                )
            )

    add("fattura_scaduta")
    add("fattura_scaduta", "completata")
    add("altra_regola")
    with pytest.raises(IntegrityError, match="uq_attivita_regola_aperta"):
        add("fattura_scaduta")


# -- the race --------------------------------------------------------------------------


@pytest.fixture
def committed_overdue_invoice(db_engine: Engine) -> Iterator[UUID]:
    factory = session_factory(db_engine)
    with factory() as session:
        customer = _customer(session, "RACE Cliente")
        invoice = _invoice(session, customer, numero=99)
        # The configuration row exists before the passes start: two first reads racing to
        # create it is a different race, and not the one this test is about.
        session.add(AutomationConfig())
        session.commit()
        invoice_id, customer_id = invoice.id, customer.id
    try:
        yield invoice_id
    finally:
        with factory() as session:
            ids = [invoice_id]
            session.execute(delete(Activity).where(Activity.entity_type == "attivita"))
            session.execute(delete(Attivita).where(Attivita.invoice_id.in_(ids)))
            session.execute(delete(Invoice).where(Invoice.id == invoice_id))
            session.execute(delete(Customer).where(Customer.id == customer_id))
            session.execute(delete(AutomationConfig))
            session.commit()


def test_two_passes_at_the_same_instant_open_one_attivita(
    db_engine: Engine,
    committed_overdue_invoice: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 7 §10.4, on two real connections. Both passes read «no open attivita» before
    either has written (the barrier holds each after its read), both try to insert, and the
    index lets exactly one through: the loser's insert fails on `uq_attivita_regola_aperta`,
    is absorbed under its savepoint and counted as «già aperta», not raised."""
    factory = session_factory(db_engine)
    after_read = Barrier(2)
    original = AutomationPass._already_open

    def read_then_wait(self: AutomationPass, rule: AttivitaRule) -> set[UUID]:
        seen = original(self, rule)
        if rule == "fattura_scaduta":
            after_read.wait(timeout=30)
        return seen

    monkeypatch.setattr(AutomationPass, "_already_open", read_then_wait)

    def one_pass() -> tuple[bool, int, int]:
        with factory() as session:
            return _report(AutomationPass(session).run(), "fattura_scaduta")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(
            f.result(timeout=60) for f in [pool.submit(one_pass), pool.submit(one_pass)]
        )

    # One opened it and one found it already there: never two, never an error.
    assert results == [(False, 0, 1), (False, 1, 0)]
    with factory() as session:
        rows = session.scalars(
            select(Attivita).where(Attivita.invoice_id == committed_overdue_invoice)
        ).all()
    assert len(rows) == 1
