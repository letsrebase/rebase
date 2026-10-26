"""REB-767: the invoices and billing group of the catalogue (spec 2026-10-26 §3.3).

Every test evaluates on a day it chooses, which is the point of a pure detector. The card's
proof is the first test: an invoice 31 days overdue raises an urgent reminder, one 5 days
overdue a normal one, and a space that does not export FatturaPA gets no transmission
reminder.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.contracts.models import Contract, RateCard
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
from pigrocrm.core.reminders.preferences import ReminderSettings
from pigrocrm.core.timetracking.models import TimeEntry
from pigrocrm.core.work_units.models import WorkUnit

TODAY = date(2026, 10, 26)
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
_NUMBERS = count(1)


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


def _ago(days: int) -> date:
    return TODAY - timedelta(days=days)


@dataclass
class World:
    session: Session
    owner: User
    bob: User  # owns the deal
    carol: User  # owns the customer
    customer: Customer
    deal: Deal
    azienda: UUID

    def profile(self, pack: str = "it-flat-rate", azienda: UUID | None = None) -> FiscalProfile:
        """The fiscal profile of an azienda (the default one), set to `pack`."""
        target = azienda or self.azienda
        row = self.session.scalar(select(FiscalProfile).where(FiscalProfile.azienda_id == target))
        if row is not None:
            self.session.delete(row)
            self.session.flush()
        upsert = (
            FiscalProfileUpsert(pack_id="non-it", aliquota_iva_default=Decimal("20.00"))
            if pack == "non-it"
            else FiscalProfileUpsert(codice_regime="RF19")
        )
        row = FiscalProfile(**upsert.model_dump(), azienda_id=target)
        self.session.add(row)
        self.session.flush()
        return row

    def invoice(self, *, tipo: str = "fattura", stato: str = "emessa", **fields: Any) -> Invoice:
        numbered = tipo == "fattura" and stato != "bozza"
        payload: dict[str, Any] = {
            "customer_id": self.customer.id,
            "azienda_id": self.azienda,
            "tipo": tipo,
            "stato": stato,
            "anno": 2026 if numbered else None,
            "numero": next(_NUMBERS) if numbered else None,
            "data_emissione": _ago(40) if numbered else None,
            "totale": Decimal("1220.00"),
        }
        payload.update(fields)
        row = Invoice(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def overdue(self, days: int, **fields: Any) -> Invoice:
        return self.invoice(data_scadenza=_ago(days), **fields)

    def line(self, invoice: Invoice) -> InvoiceLine:
        row = InvoiceLine(
            invoice_id=invoice.id,
            numero_linea=1,
            descrizione="Riga",
            quantita=Decimal("1"),
            prezzo_unitario=Decimal("1"),
            prezzo_totale=Decimal("1.00"),
            aliquota_iva=Decimal("22.00"),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def hours(self, data: date, *, deal: Deal | None = None, **fields: Any) -> TimeEntry:
        payload: dict[str, Any] = {
            "deal_id": (deal or self.deal).id,
            "user_id": self.bob.id,
            "data": data,
            "ore": Decimal("2.00"),
            "descrizione": "Analisi",
            "tariffa_origine": "manuale",
        }
        payload.update(fields)
        row = TimeEntry(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def contract(
        self, cadenza: str = "mensile", *, fine: date | None = None, inizio: date | None = None
    ) -> Contract:
        row = Contract(
            customer_id=self.customer.id,
            azienda_id=self.azienda,
            titolo="Consulenza",
            inizio=inizio or date(2026, 1, 1),
            fine=fine,
            tipo_rinnovo="nessuno",
            preavviso_disdetta_giorni=30,
            cadenza_fatturazione=cadenza,
            politica_spese={"tipo": "non_rimborsabili"},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def day(self, contract: Contract, data: date, **fields: Any) -> WorkUnit:
        payload: dict[str, Any] = {
            "contract_id": contract.id,
            "data": data,
            "quantita": Decimal("1.00"),
            "descrizione": "Giornata",
            "stato": "lavorato",
        }
        payload.update(fields)
        row = WorkUnit(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def fee(self, contract: Contract, **fields: Any) -> RateCard:
        payload: dict[str, Any] = {
            "contract_id": contract.id,
            "valido_da": date(2026, 1, 1),
            "tipo": "ricorrente_fisso",
            "importo": Decimal("1000.00"),
            "unita": "forfait",
            "periodo_erogazione": "mensile",
        }
        payload.update(fields)
        row = RateCard(**payload)
        self.session.add(row)
        self.session.flush()
        return row

    def evaluate(self, today: date = TODAY) -> Evaluation:
        self.session.flush()
        return ReminderEngine(self.session, SETTINGS).evaluate(today=today, now=NOON)

    def raised(
        self, type_key: str, today: date = TODAY, evaluation: Evaluation | None = None
    ) -> dict[UUID, tuple[str, set[UUID]]]:
        """Subject to `(severity, people told)` for one type."""
        found: dict[UUID, tuple[str, set[UUID]]] = {}
        for reminder in (evaluation or self.evaluate(today)).reminders:
            if reminder.type != type_key:
                continue
            subject = UUID(reminder.key.partition(":")[2])
            severity, people = found.get(subject, (reminder.severity, set()))
            people.add(reminder.user_id)
            found[subject] = (severity, people)
        return found

    def severity(self, type_key: str, subject: UUID, today: date = TODAY) -> str | None:
        found = self.raised(type_key, today).get(subject)
        return found[0] if found else None


@pytest.fixture
def world(db_session: Session) -> World:
    azienda = db_session.scalars(
        select(LegalEntity.id).where(LegalEntity.predefinita.is_(True))
    ).one()
    users = {}
    for name, ruolo in (("owner", "admin"), ("bob", "collaboratore"), ("carol", "collaboratore")):
        user = User(
            email=f"{name}-{uuid4()}@example.test",
            password_hash="x",
            nome=name.title(),
            ruolo=ruolo,
        )
        db_session.add(user)
        users[name] = user
    stage = PipelineStage(
        nome=f"Aperto {uuid4()}", posizione=0, probabilita_default=10, tipo="open"
    )
    db_session.add(stage)
    db_session.flush()
    customer = Customer(ragione_sociale="ACME Srl", azienda_id=azienda, owner_id=users["carol"].id)
    db_session.add(customer)
    db_session.flush()
    deal = Deal(
        nome="Sito web",
        customer_id=customer.id,
        azienda_id=azienda,
        pipeline_stage_id=stage.id,
        probabilita=10,
        owner_id=users["bob"].id,
        created_at=_at(_ago(2)),
    )
    db_session.add(deal)
    db_session.flush()
    built = World(db_session, users["owner"], users["bob"], users["carol"], customer, deal, azienda)
    built.profile()
    return built


def _urgent(world: World, user: UUID, type_key: str, severity: str) -> bool:
    settings = ReminderSettings.load(world.session).for_user(user).type(REMINDER_TYPES[type_key])
    return settings.is_urgent(severity)  # type: ignore[arg-type]


# -- the card's proof ------------------------------------------------------------------------


def test_the_cards_proof_31_days_overdue_is_urgent_5_is_not_and_abroad_gets_no_transmission(
    world: World,
) -> None:
    late = world.overdue(31)
    recent = world.overdue(5)

    evaluation = world.evaluate()

    assert evaluation.ran >= {"invoice_overdue", "invoice_not_transmitted"}
    assert world.severity("invoice_overdue", late.id) == "critical"
    assert _urgent(world, world.carol.id, "invoice_overdue", "critical")
    assert world.severity("invoice_overdue", recent.id) == "warning"
    assert not _urgent(world, world.carol.id, "invoice_overdue", "warning")
    # Both were issued 40 days ago and never transmitted: a space exporting FatturaPA is told.
    assert world.severity("invoice_not_transmitted", late.id) == "critical"

    world.profile("non-it")
    abroad = world.evaluate()

    assert "invoice_not_transmitted" not in abroad.ran
    assert world.raised("invoice_not_transmitted", evaluation=abroad) == {}
    assert world.severity("invoice_overdue", late.id) == "critical", "overdue is always on"


# -- invoice_overdue ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [
        (0, None),
        (1, "warning"),
        (7, "warning"),
        (8, "serious"),
        (29, "serious"),
        (30, "critical"),
        (200, "critical"),
    ],
)
def test_overdue_climbs_the_ladder_on_both_sides_of_every_rung(
    world: World, days: int, severity: str | None
) -> None:
    invoice = world.overdue(days)

    assert world.severity("invoice_overdue", invoice.id) == severity


def test_an_invoice_due_in_the_future_or_with_no_due_date_is_not_overdue(world: World) -> None:
    world.invoice(data_scadenza=TODAY + timedelta(days=3))
    world.invoice(data_scadenza=None)

    assert world.raised("invoice_overdue") == {}


def test_only_an_issued_unpaid_live_fattura_is_overdue(world: World) -> None:
    world.overdue(40, stato_pagamento="incassato", data_incasso=_ago(1))
    world.overdue(40, stato="bozza")
    world.overdue(
        40, stato="annullata", annullata_il=_ago(2), motivo_annullamento="errore di battitura"
    )
    world.overdue(40, tipo="proforma", stato="confermata")
    world.overdue(40, stato="bozza", deleted_at=_at(_ago(1)))

    assert world.raised("invoice_overdue") == {}


def test_overdue_is_told_to_the_customers_owner_and_the_deals_owner(world: World) -> None:
    plain = world.overdue(10)
    with_deal = world.overdue(10, deal_id=world.deal.id)

    raised = world.raised("invoice_overdue")

    assert raised[plain.id][1] == {world.carol.id}
    assert raised[with_deal.id][1] == {world.carol.id, world.bob.id}


def test_an_overdue_reminder_disappears_when_the_invoice_is_paid(world: World) -> None:
    invoice = world.overdue(5)
    assert world.severity("invoice_overdue", invoice.id) == "warning"

    invoice.stato_pagamento = "incassato"
    invoice.data_incasso = TODAY

    assert world.raised("invoice_overdue") == {}


def test_the_overdue_text_names_the_invoice_the_amount_and_the_customer(world: World) -> None:
    invoice = world.overdue(3, numero=7, anno=2026)

    reminder = next(r for r in world.evaluate().reminders if r.type == "invoice_overdue")

    assert reminder.title == "Incasso in ritardo: fattura 2026/7"
    assert "scaduta il " in reminder.detail and "(3 giorni fa)" in reminder.detail
    assert "ACME Srl" in reminder.detail
    assert reminder.entity == "invoice" and reminder.entity_id == invoice.id
    assert reminder.customer_id == world.customer.id


# -- invoice_not_transmitted -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [(1, None), (2, "warning"), (3, "serious"), (6, "serious"), (7, "critical")],
)
def test_transmission_climbs_the_ladder_from_the_issue_date(
    world: World, days: int, severity: str | None
) -> None:
    invoice = world.invoice(data_emissione=_ago(days))

    assert world.severity("invoice_not_transmitted", invoice.id) == severity


def test_a_transmitted_imported_or_foreign_invoice_is_not_asked_to_be_transmitted(
    world: World,
) -> None:
    world.invoice(trasmessa_esternamente_il=_ago(5))
    world.invoice(importata_da="esterno")
    world.invoice(snapshot={"fiscale": {"pack_id": "non-it"}}, snapshot_versione=1)
    sent_in_italy = world.invoice(
        snapshot={"fiscale": {"pack_id": "it-flat-rate"}}, snapshot_versione=1
    )
    world.invoice(stato="bozza")
    world.invoice(tipo="proforma", stato="confermata")

    raised = world.raised("invoice_not_transmitted")

    assert set(raised) == {sent_in_italy.id}, "an Italian snapshot is still asked for"


def test_marking_an_invoice_transmitted_clears_its_reminder(world: World) -> None:
    invoice = world.invoice()
    assert world.severity("invoice_not_transmitted", invoice.id) == "critical"

    invoice.trasmessa_esternamente_il = TODAY

    assert world.raised("invoice_not_transmitted") == {}


def test_one_italian_azienda_is_enough_for_the_space_to_export_fatturapa(world: World) -> None:
    abroad = LegalEntity(nome="Ltd", ragione_sociale="Ltd", nazione="GB", predefinita=False)
    world.session.add(abroad)
    world.session.flush()
    world.profile("non-it")
    world.profile("non-it", azienda=abroad.id)
    invoice = world.invoice()
    assert "invoice_not_transmitted" not in world.evaluate().ran

    world.profile("it-flat-rate", azienda=abroad.id)

    assert "invoice_not_transmitted" in world.evaluate().ran
    assert world.severity("invoice_not_transmitted", invoice.id) == "critical"


def test_a_space_with_no_fiscal_profile_at_all_exports_nothing(world: World) -> None:
    row = world.session.scalar(
        select(FiscalProfile).where(FiscalProfile.azienda_id == world.azienda)
    )
    assert row is not None
    world.session.delete(row)
    world.invoice()

    assert "invoice_not_transmitted" not in world.evaluate().ran


# -- proforma_not_invoiced ---------------------------------------------------------------------


def _confirmed(world: World, day: date, **fields: Any) -> Invoice:
    proforma = world.invoice(
        tipo="proforma", stato="confermata", created_at=_at(_ago(100)), **fields
    )
    world.session.add(
        Activity(
            entity_type="invoice",
            entity_id=proforma.id,
            kind="confirmed",
            actor_type="user",
            occurred_at=_at(day),
        )
    )
    world.session.flush()
    return proforma


@pytest.mark.parametrize(
    ("days", "severity"),
    [(6, None), (7, "warning"), (20, "warning"), (21, "serious"), (90, "serious")],
)
def test_a_confirmed_proforma_is_counted_from_the_day_it_was_confirmed(
    world: World, days: int, severity: str | None
) -> None:
    proforma = _confirmed(world, _ago(days))

    assert world.severity("proforma_not_invoiced", proforma.id) == severity


def test_a_proforma_with_no_confirmation_on_record_is_counted_from_its_creation(
    world: World,
) -> None:
    old = world.invoice(tipo="proforma", stato="confermata", created_at=_at(_ago(8)))
    fresh = world.invoice(tipo="proforma", stato="confermata", created_at=_at(_ago(6)))

    raised = world.raised("proforma_not_invoiced")

    assert set(raised) == {old.id}
    assert fresh.id not in raised


def test_a_converted_draft_or_discarded_proforma_is_not_waiting_to_be_invoiced(
    world: World,
) -> None:
    world.invoice(tipo="proforma", stato="consumata", created_at=_at(_ago(50)))
    world.invoice(tipo="proforma", stato="bozza", created_at=_at(_ago(50)))
    world.invoice(
        tipo="proforma", stato="confermata", created_at=_at(_ago(50)), deleted_at=_at(_ago(1))
    )
    world.invoice(tipo="fattura", stato="emessa", created_at=_at(_ago(50)))

    assert world.raised("proforma_not_invoiced") == {}


def test_a_proforma_is_told_to_the_customers_and_the_deals_owner_and_opens_as_a_proforma(
    world: World,
) -> None:
    proforma = _confirmed(world, _ago(10), deal_id=world.deal.id, riferimento="PF-2026-3")

    evaluation = world.evaluate()

    assert world.raised("proforma_not_invoiced", evaluation=evaluation)[proforma.id][1] == {
        world.carol.id,
        world.bob.id,
    }
    reminder = next(r for r in evaluation.reminders if r.type == "proforma_not_invoiced")
    assert (
        reminder.entity == "proforma"
        and reminder.title == "Proforma da fatturare: proforma PF-2026-3"
    )


# -- hours_unbilled ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [(29, None), (30, "warning"), (59, "warning"), (60, "serious"), (400, "serious")],
)
def test_unbilled_hours_climb_with_the_age_of_the_oldest_entry(
    world: World, days: int, severity: str | None
) -> None:
    world.hours(_ago(days))

    assert world.severity("hours_unbilled", world.deal.id) == severity


def test_one_reminder_per_deal_reads_the_oldest_entry_and_goes_to_both_owners(
    world: World,
) -> None:
    world.hours(_ago(10))
    world.hours(_ago(35))
    world.hours(_ago(65), deleted_at=_at(_ago(1)))
    other = Deal(
        nome="App",
        customer_id=world.customer.id,
        azienda_id=world.azienda,
        pipeline_stage_id=world.deal.pipeline_stage_id,
        probabilita=10,
        owner_id=None,
    )
    world.session.add(other)
    world.session.flush()
    world.hours(_ago(70), deal=other)

    raised = world.raised("hours_unbilled")

    assert raised[world.deal.id] == ("warning", {world.bob.id, world.carol.id})
    assert raised[other.id] == ("serious", {world.carol.id}), "no deal owner: the customer's"


def test_hours_on_an_issued_invoice_or_not_billable_are_not_unbilled_but_a_draft_does_not_bill(
    world: World,
) -> None:
    issued = world.line(world.invoice())
    draft = world.line(world.invoice(stato="bozza"))
    world.hours(_ago(90), invoice_line_id=issued.id)
    world.hours(_ago(90), fatturabile=False)
    assert world.raised("hours_unbilled") == {}

    world.hours(_ago(45), invoice_line_id=draft.id)

    assert world.severity("hours_unbilled", world.deal.id) == "warning"


def test_unbilled_hours_of_an_archived_deal_are_not_raised(world: World) -> None:
    world.hours(_ago(90))
    world.deal.deleted_at = _at(_ago(1))

    assert world.raised("hours_unbilled") == {}


def test_hours_leave_the_list_when_an_issued_invoice_takes_them(world: World) -> None:
    entry = world.hours(_ago(90))
    assert world.severity("hours_unbilled", world.deal.id) == "serious"

    entry.invoice_line_id = world.line(world.invoice()).id

    assert world.raised("hours_unbilled") == {}


# -- billable_period_closed --------------------------------------------------------------------


def test_a_closed_period_with_unbilled_days_is_counted_from_the_end_of_the_oldest_days_period(
    world: World,
) -> None:
    september = world.contract()
    world.day(september, date(2026, 9, 15))
    world.day(september, date(2026, 10, 5))  # this month's: not late, not counted

    raised = world.raised("billable_period_closed")

    assert raised == {september.id: ("warning", {world.carol.id})}
    reminder = next(r for r in world.evaluate().reminders if r.type == "billable_period_closed")
    assert reminder.measure == 26.0, "30 September to 26 October"
    assert reminder.entity == "contract" and "1 giornata di lavoro" in reminder.detail


@pytest.mark.parametrize(
    ("today", "severity"),
    [
        (date(2026, 10, 1), "warning"),
        (date(2026, 10, 29), "warning"),
        (date(2026, 10, 30), "serious"),
        (date(2026, 11, 28), "serious"),
        (date(2026, 11, 29), "critical"),
    ],
)
def test_the_closed_period_ladder_steps_at_30_and_60_days_past_the_end(
    world: World, today: date, severity: str
) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 15))

    assert world.severity("billable_period_closed", contract.id, today) == severity


def test_the_current_period_is_not_closed_and_the_cadence_sets_the_period(world: World) -> None:
    monthly = world.contract("mensile")
    world.day(monthly, date(2026, 10, 1))
    quarterly = world.contract("Trimestrale")
    world.day(quarterly, date(2026, 8, 1))  # Q3 ended 30 September
    open_quarter = world.contract("trimestrale")
    world.day(open_quarter, date(2026, 10, 2))  # Q4 is running
    yearly = world.contract("annuale")
    world.day(yearly, date(2026, 3, 1))  # 2026 is running

    raised = world.raised("billable_period_closed")

    assert set(raised) == {quarterly.id}


def test_a_day_billed_proposed_or_on_a_deleted_contract_is_not_unbilled(world: World) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 1), invoice_line_id=world.line(world.invoice()).id)
    world.day(contract, date(2026, 9, 2), stato="proposto")
    gone = world.contract()
    gone.deleted_at = _at(_ago(1))
    world.day(gone, date(2026, 9, 3))

    assert world.raised("billable_period_closed") == {}
    assert "billable_period_closed" in world.evaluate().ran


def test_a_contract_billed_on_completion_never_closes_a_period(world: World) -> None:
    world.day(world.contract("a_consuntivo"), date(2026, 8, 1))

    assert world.raised("billable_period_closed") == {}


def test_billable_period_is_not_run_in_a_space_without_days(world: World) -> None:
    world.contract()

    assert "billable_period_closed" not in world.evaluate().ran


def test_billing_the_days_clears_the_closed_period(world: World) -> None:
    contract = world.contract()
    unit = world.day(contract, date(2026, 9, 15))
    assert world.severity("billable_period_closed", contract.id) == "warning"

    unit.invoice_line_id = world.line(world.invoice()).id

    assert world.raised("billable_period_closed") == {}


# -- fixed_fee_period_unbilled -----------------------------------------------------------------


def _fee_contract(world: World, **fields: Any) -> Contract:
    contract = world.contract(**fields)
    world.fee(contract)
    return contract


def test_a_fixed_fee_period_with_no_invoice_since_its_last_day_is_raised_for_that_period(
    world: World,
) -> None:
    contract = _fee_contract(world)
    world.invoice(data_emissione=date(2026, 8, 31))

    raised = world.raised("fixed_fee_period_unbilled")

    subject = uuid5(contract.id, "2026-09-01")
    assert raised == {subject: ("warning", {world.carol.id})}
    reminder = next(r for r in world.evaluate().reminders if r.type == "fixed_fee_period_unbilled")
    assert reminder.entity == "contract" and reminder.entity_id == contract.id
    assert reminder.measure == 26.0
    assert "chiuso da 26 giorni" in reminder.detail


def test_an_invoice_dated_on_the_periods_last_day_or_later_covers_it(world: World) -> None:
    _fee_contract(world)
    world.invoice(data_emissione=date(2026, 9, 30))

    assert world.raised("fixed_fee_period_unbilled") == {}


@pytest.mark.parametrize(
    ("last_invoice", "period", "severity"),
    [
        (date(2026, 8, 31), "2026-09-01", "warning"),  # September ended 26 days ago
        (date(2026, 7, 31), "2026-08-01", "serious"),  # August ended 56 days ago
        (date(2026, 6, 30), "2026-07-01", "critical"),  # July ended 87 days ago
    ],
)
def test_a_fee_left_unbilled_for_months_is_counted_from_the_oldest_uncovered_period(
    world: World, last_invoice: date, period: str, severity: str
) -> None:
    contract = _fee_contract(world)
    world.invoice(data_emissione=last_invoice)

    assert world.raised("fixed_fee_period_unbilled") == {
        uuid5(contract.id, period): (severity, {world.carol.id})
    }


@pytest.mark.parametrize(
    ("today", "severity"), [(date(2026, 10, 29), "warning"), (date(2026, 10, 30), "serious")]
)
def test_the_fixed_fee_ladder_counts_from_the_last_day_of_the_oldest_uncovered_period(
    world: World, today: date, severity: str
) -> None:
    contract = _fee_contract(world)
    world.invoice(data_emissione=date(2026, 8, 31))

    assert (
        world.severity("fixed_fee_period_unbilled", uuid5(contract.id, "2026-09-01"), today)
        == severity
    )


def test_the_subject_is_stable_while_the_period_stays_uncovered_and_moves_when_it_is_billed(
    world: World,
) -> None:
    contract = _fee_contract(world)
    last = world.invoice(data_emissione=date(2026, 8, 31))
    september = uuid5(contract.id, "2026-09-01")

    assert set(world.raised("fixed_fee_period_unbilled")) == {september}
    later = world.raised("fixed_fee_period_unbilled", date(2026, 11, 2))
    assert later == {september: ("serious", {world.carol.id})}, "same subject, higher rung"

    last.data_emissione = date(2026, 9, 30)
    assert world.raised("fixed_fee_period_unbilled") == {}
    assert set(world.raised("fixed_fee_period_unbilled", date(2026, 11, 2))) == {
        uuid5(contract.id, "2026-10-01")
    }


def test_a_contract_never_invoiced_is_counted_from_the_first_period_with_a_fee_in_force(
    world: World,
) -> None:
    contract = world.contract()
    world.fee(contract, valido_da=date(2026, 9, 15))

    raised = world.raised("fixed_fee_period_unbilled")

    assert raised == {uuid5(contract.id, "2026-09-01"): ("warning", {world.carol.id})}


def test_a_fee_not_in_force_or_a_contract_not_running_in_the_period_is_not_raised(
    world: World,
) -> None:
    world.invoice(data_emissione=date(2026, 8, 31))
    ended_card = world.contract()
    world.fee(ended_card, valido_a=date(2026, 8, 31))
    not_started = _fee_contract(world, inizio=date(2026, 10, 1))
    ended_early = _fee_contract(world, fine=date(2026, 8, 31))
    no_fee = world.contract()
    world.day(no_fee, date(2026, 10, 1))
    deleted = _fee_contract(world)
    deleted.deleted_at = _at(_ago(1))
    on_completion = world.contract("a_consuntivo")
    world.fee(on_completion)

    assert "fixed_fee_period_unbilled" in world.evaluate().ran
    assert world.raised("fixed_fee_period_unbilled") == {}
    assert len({ended_card.id, not_started.id, ended_early.id, no_fee.id}) == 4


def test_a_space_with_no_fixed_fee_does_not_run_the_fixed_fee_type(world: World) -> None:
    world.contract()

    assert "fixed_fee_period_unbilled" not in world.evaluate().ran


# -- on_completion_unbilled --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "severity"),
    [
        (0, None),
        (1, "warning"),
        (29, "warning"),
        (30, "serious"),
        (59, "serious"),
        (60, "critical"),
    ],
)
def test_a_contract_billed_on_completion_is_counted_from_its_end(
    world: World, days: int, severity: str | None
) -> None:
    contract = world.contract("a_consuntivo", fine=_ago(days))
    world.day(contract, date(2026, 2, 3))

    assert world.severity("on_completion_unbilled", contract.id) == severity


def test_a_contract_still_running_or_without_an_end_has_not_completed(world: World) -> None:
    running = world.contract("a_consuntivo", fine=TODAY + timedelta(days=5))
    world.day(running, date(2026, 2, 3))
    open_ended = world.contract("a_consuntivo")
    world.day(open_ended, date(2026, 2, 3))
    periodic = world.contract("mensile", fine=_ago(40))
    world.day(periodic, date(2026, 2, 3))

    assert world.raised("on_completion_unbilled") == {}


def test_a_completed_contract_with_nothing_unbilled_or_deleted_is_not_raised(world: World) -> None:
    billed = world.contract("a_consuntivo", fine=_ago(10))
    world.day(billed, date(2026, 2, 3), invoice_line_id=world.line(world.invoice()).id)
    gone = world.contract("a_consuntivo", fine=_ago(10))
    world.day(gone, date(2026, 2, 3))
    gone.deleted_at = _at(_ago(1))
    assert world.raised("on_completion_unbilled") == {}

    world.contract("a_consuntivo", fine=_ago(10))

    assert world.raised("on_completion_unbilled") == {}


def test_on_completion_is_told_to_the_customers_owner(world: World) -> None:
    contract = world.contract("a_consuntivo", fine=_ago(10))
    world.day(contract, date(2026, 2, 3))

    assert world.raised("on_completion_unbilled") == {contract.id: ("warning", {world.carol.id})}


def test_a_space_without_a_completion_contract_does_not_run_on_completion(world: World) -> None:
    world.contract("mensile")

    assert "on_completion_unbilled" not in world.evaluate().ran


# -- invoice_days_unlinked ---------------------------------------------------------------------


def test_an_invoice_with_no_days_on_its_lines_while_days_remain_is_serious_and_never_urgent(
    world: World,
) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 10))
    invoice = world.invoice(data_emissione=date(2026, 9, 20))

    raised = world.raised("invoice_days_unlinked")

    assert raised == {invoice.id: ("serious", {world.carol.id})}
    assert not _urgent(world, world.carol.id, "invoice_days_unlinked", "serious")
    assert "critical" not in REMINDER_TYPES["invoice_days_unlinked"].ladder, "no urgent step"


def test_an_invoice_older_than_every_unbilled_day_cannot_be_about_them(world: World) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 10))
    world.invoice(data_emissione=date(2026, 9, 9))
    same_day = world.invoice(data_emissione=date(2026, 9, 10))

    assert set(world.raised("invoice_days_unlinked")) == {same_day.id}


def test_an_invoice_whose_lines_carry_a_day_is_linked(world: World) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 10))
    linked = world.invoice(data_emissione=date(2026, 9, 20))
    world.day(contract, date(2026, 9, 5), invoice_line_id=world.line(linked).id)

    assert world.raised("invoice_days_unlinked") == {}


def test_a_draft_annulled_or_other_customers_invoice_is_not_unlinked(world: World) -> None:
    contract = world.contract()
    world.day(contract, date(2026, 9, 10))
    world.invoice(stato="bozza")
    world.invoice(stato="annullata", annullata_il=_ago(1), motivo_annullamento="errore")
    other = Customer(ragione_sociale="Beta Srl", azienda_id=world.azienda)
    world.session.add(other)
    world.session.flush()
    world.invoice(customer_id=other.id, data_emissione=date(2026, 9, 20))

    assert world.raised("invoice_days_unlinked") == {}


def test_unlinked_days_clear_once_the_days_are_billed_and_the_type_needs_days_to_run(
    world: World,
) -> None:
    assert "invoice_days_unlinked" not in world.evaluate().ran
    contract = world.contract()
    unit = world.day(contract, date(2026, 9, 10))
    world.invoice(data_emissione=date(2026, 9, 20))
    assert len(world.raised("invoice_days_unlinked")) == 1

    unit.invoice_line_id = world.line(world.invoice(stato="bozza")).id

    assert world.raised("invoice_days_unlinked") == {}


def test_a_paid_or_old_invoice_is_not_raised_as_unlinked(world: World) -> None:
    contract = world.contract()
    world.day(contract, _ago(150))
    world.invoice(data_emissione=_ago(10), stato_pagamento="incassato", data_incasso=_ago(1))
    world.invoice(data_emissione=_ago(91))
    inside = world.invoice(data_emissione=_ago(90))

    assert set(world.raised("invoice_days_unlinked")) == {inside.id}


# -- lookbacks and the Italian ---------------------------------------------------------------


def test_an_invoice_issued_over_180_days_ago_is_no_longer_asked_to_be_transmitted(
    world: World,
) -> None:
    inside = world.invoice(data_emissione=_ago(180))
    world.invoice(data_emissione=_ago(181))

    assert set(world.raised("invoice_not_transmitted")) == {inside.id}


def test_the_details_say_one_day_and_yesterday_and_never_one_days(world: World) -> None:
    world.overdue(1, numero=3, anno=2026)
    world.invoice(data_emissione=_ago(2))
    details = {r.type: r.detail for r in world.evaluate().reminders}

    assert "(ieri)" in details["invoice_overdue"]
    assert (
        "scaduta il " in details["invoice_overdue"] and "da incassare" in details["invoice_overdue"]
    )


def test_the_italian_helpers_pluralise_and_use_a_decimal_comma() -> None:
    from pigrocrm.core.reminders.detectors.billing_text import (
        giornate,
        giorni,
        giorni_fa,
        ore,
        voci,
    )

    assert giorni(1) == "1 giorno" and giorni(0) == "0 giorni" and giorni(5) == "5 giorni"
    assert giornate(Decimal("1.00")) == "1 giornata"
    assert (
        giornate(Decimal("1.50")) == "1,5 giornate" and giornate(Decimal("12.00")) == "12 giornate"
    )
    assert voci(1) == "1 voce" and voci(3) == "3 voci"
    assert ore(Decimal("1")) == "1 ora" and ore(Decimal("2.25")) == "2,25 ore"
    assert giorni_fa(1) == "ieri" and giorni_fa(2) == "2 giorni fa"


def test_the_hours_detail_counts_in_italian(world: World) -> None:
    world.hours(_ago(40), ore=Decimal("1.00"))
    world.hours(_ago(41), ore=Decimal("0.50"))

    reminder = next(r for r in world.evaluate().reminders if r.type == "hours_unbilled")

    assert "1,5 ore in 2 voci" in reminder.detail
