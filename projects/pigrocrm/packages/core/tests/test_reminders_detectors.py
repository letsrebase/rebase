"""REB-733: the detector framework and the first group of the catalogue (spec
2026-10-26 §3.2, §4, §8.2).

Every test evaluates on a date it chooses (`TODAY`), which is the point of a pure detector.
The card's proof is the first one: an attività due today, an overdue one and a deal past
its expected close, raised by one pass for the right people.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.auth.models import User, UserLegalEntity
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.emitter.models import LegalEntity
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink, GoogleAccount
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES, reminder_key
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
from pigrocrm.core.reminders.models import ReminderAck, ReminderDelivery, ReminderSnooze
from pigrocrm.core.reminders.preferences import ReminderPreferenceService
from pigrocrm.core.reminders.schemas import ReminderPreferenceWrite
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm.core.timetracking.models import TimeEntry, TimeTimer

TODAY = date(2026, 10, 26)
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 9, 0, tzinfo=UTC)


@dataclass
class World:
    session: Session
    owner: User
    alice: User  # assigns things
    bob: User  # owns the deal
    carol: User  # owns the customer
    customer: Customer
    deal: Deal
    open_stage: PipelineStage
    azienda: UUID

    def user(self, ruolo: str = "collaboratore", attivo: bool = True) -> User:
        user = User(
            email=f"u-{uuid4()}@example.test",
            password_hash="x",
            nome="Persona",
            ruolo=ruolo,
            attivo=attivo,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def attivita(self, scadenza: date | None, **fields: object) -> Attivita:
        row = Attivita(titolo=fields.pop("titolo", "Richiamare"), scadenza=scadenza, **fields)
        self.session.add(row)
        self.session.flush()
        return row

    def evaluate(self, today: date = TODAY, now: datetime = NOON) -> Evaluation:
        self.session.flush()
        return ReminderEngine(self.session, SETTINGS, owner_email=self.owner.email).evaluate(
            today=today, now=now
        )

    def raised(self, evaluation: Evaluation) -> dict[tuple[str, UUID], set[UUID]]:
        """`(type, subject)` to the people told."""
        found: dict[tuple[str, UUID], set[UUID]] = {}
        for reminder in evaluation.reminders:
            subject = UUID(reminder.key.partition(":")[2])
            found.setdefault((reminder.type, subject), set()).add(reminder.user_id)
        return found


@pytest.fixture
def world(db_session: Session) -> World:
    azienda = db_session.scalars(
        select(LegalEntity.id).where(LegalEntity.predefinita.is_(True))
    ).one()
    users = {}
    for name, ruolo in (
        ("owner", "admin"),
        ("alice", "collaboratore"),
        ("bob", "collaboratore"),
        ("carol", "collaboratore"),
    ):
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
        created_at=_at(TODAY - timedelta(days=2)),
    )
    db_session.add(deal)
    db_session.flush()
    return World(
        db_session,
        users["owner"],
        users["alice"],
        users["bob"],
        users["carol"],
        customer,
        deal,
        stage,
        azienda,
    )


# -- the card's proof ----------------------------------------------------------------------


def test_one_pass_raises_a_due_an_overdue_and_a_past_close_deal_for_the_right_people(
    world: World,
) -> None:
    due = world.attivita(TODAY, assegnata_a=world.alice.id, customer_id=world.customer.id)
    overdue = world.attivita(
        TODAY - timedelta(days=3), assegnata_a=world.alice.id, deal_id=world.deal.id
    )
    world.deal.data_chiusura_prevista = TODAY - timedelta(days=5)

    raised = world.raised(world.evaluate())

    # An attività due today, assigned to Alice and linked to the customer Carol owns.
    assert raised[("attivita_due", due.id)] == {world.alice.id, world.carol.id}
    # An overdue one on the deal Bob owns, whose customer Carol owns, assigned to Alice.
    assert raised[("attivita_overdue", overdue.id)] == {
        world.alice.id,
        world.bob.id,
        world.carol.id,
    }
    # The deal itself, past its expected close: its owner and its customer's.
    assert raised[("deal_past_close", world.deal.id)] == {world.bob.id, world.carol.id}
    assert len(raised) == 3, "nothing else is raised: the deal is two days old, the timer absent"


def test_the_severity_is_each_recipients_own(world: World) -> None:
    world.attivita(TODAY - timedelta(days=3), assegnata_a=world.alice.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.alice),
        "me",
        "attivita_overdue",
        ReminderPreferenceWrite(ladder={"serious": 1, "critical": 3}),
    )

    evaluation = world.evaluate()

    severities = {reminder.user_id: reminder.severity for reminder in evaluation.reminders}
    assert severities[world.alice.id] == "critical", "her own ladder: critical from 3 days"
    # Nobody else is told about an attività with no link and an assignee, but the space's
    # owner is not either: the assignee is a recipient, so the fallback does not apply.
    assert set(severities) == {world.alice.id}


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.ruolo)  # type: ignore[arg-type]


# -- attività ------------------------------------------------------------------------------


def test_an_attivita_with_no_deadline_is_not_late_and_not_due(world: World) -> None:
    world.attivita(None, assegnata_a=world.alice.id)

    assert world.evaluate().reminders == ()


def test_only_an_open_not_archived_attivita_counts(world: World) -> None:
    yesterday = TODAY - timedelta(days=1)
    world.attivita(
        yesterday, assegnata_a=world.alice.id, stato="completata", completata_il=yesterday
    )
    world.attivita(yesterday, assegnata_a=world.alice.id, stato="annullata")
    world.attivita(yesterday, assegnata_a=world.alice.id, deleted_at=NOON)

    assert world.evaluate().reminders == ()


def test_on_the_day_it_is_due_and_the_day_after_it_is_overdue(world: World) -> None:
    row = world.attivita(TODAY, assegnata_a=world.alice.id)

    today = world.raised(world.evaluate(TODAY))
    tomorrow = world.raised(world.evaluate(TODAY + timedelta(days=1)))

    assert ("attivita_due", row.id) in today and ("attivita_overdue", row.id) not in today
    assert ("attivita_overdue", row.id) in tomorrow and ("attivita_due", row.id) not in tomorrow


def test_an_overdue_attivita_climbs_the_ladder_as_days_pass(world: World) -> None:
    world.attivita(TODAY - timedelta(days=1), assegnata_a=world.alice.id)

    def severity(day: int) -> str:
        (reminder,) = world.evaluate(TODAY + timedelta(days=day)).reminders
        return reminder.severity

    assert severity(0) == "serious"
    assert severity(12) == "serious"
    assert severity(13) == "critical", "fourteen days late"


def test_a_lead_time_raises_a_due_attivita_early_for_the_person_who_asked(world: World) -> None:
    row = world.attivita(TODAY + timedelta(days=3), assegnata_a=world.alice.id)
    world.attivita(TODAY + timedelta(days=3), assegnata_a=world.bob.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.alice), "me", "attivita_due", ReminderPreferenceWrite(ladder={"warning": 3})
    )

    raised = world.raised(world.evaluate())

    assert raised == {("attivita_due", row.id): {world.alice.id}}, (
        "Bob keeps the default of the day itself; Alice asked for three days' notice"
    )


def test_an_attivita_linked_to_a_person_or_invoice_reaches_the_customers_owner(
    world: World,
) -> None:
    from pigrocrm.core.people.models import Person

    person = Person(nome="Mario", cognome="Rossi", customer_id=world.customer.id)
    world.session.add(person)
    world.session.flush()
    row = world.attivita(TODAY, person_id=person.id)

    assert world.raised(world.evaluate())[("attivita_due", row.id)] == {world.carol.id}


def test_an_attivita_with_nobody_goes_to_the_spaces_owner(world: World) -> None:
    row = world.attivita(TODAY)

    assert world.raised(world.evaluate()) == {("attivita_due", row.id): {world.owner.id}}


def test_an_archived_assignee_is_dropped_and_the_reminder_goes_on(world: World) -> None:
    gone = world.user(attivo=False)
    row = world.attivita(TODAY, assegnata_a=gone.id, customer_id=world.customer.id)

    assert world.raised(world.evaluate())[("attivita_due", row.id)] == {world.carol.id}


def test_when_nobody_can_be_told_it_goes_to_the_spaces_owner(world: World) -> None:
    gone = world.user(attivo=False)
    row = world.attivita(TODAY, assegnata_a=gone.id)

    assert world.raised(world.evaluate())[("attivita_due", row.id)] == {world.owner.id}


def test_a_scoped_member_is_told_only_about_what_their_scope_covers(world: World) -> None:
    other = LegalEntity(nome="Altra", ragione_sociale="Altra Srl", nazione="IT")
    world.session.add(other)
    world.session.flush()
    scoped = world.user()
    scoped.ambito_limitato = True
    scoped.scopes = [UserLegalEntity(user_id=scoped.id, azienda_id=other.id)]
    world.session.flush()
    row = world.attivita(TODAY, assegnata_a=scoped.id, customer_id=world.customer.id)
    unlinked = world.attivita(TODAY, assegnata_a=scoped.id)

    raised = world.raised(world.evaluate())

    assert raised[("attivita_due", row.id)] == {world.carol.id}, "the customer is another azienda's"
    assert scoped.id not in raised[("attivita_due", unlinked.id)], "no azienda, no scoped reader"
    assert raised[("attivita_due", unlinked.id)] == {world.owner.id}


def test_a_scoped_member_is_told_about_their_own_azienda(world: World) -> None:
    scoped = world.user()
    scoped.ambito_limitato = True
    scoped.scopes = [UserLegalEntity(user_id=scoped.id, azienda_id=world.azienda)]
    world.session.flush()
    row = world.attivita(TODAY, assegnata_a=scoped.id, customer_id=world.customer.id)

    assert scoped.id in world.raised(world.evaluate())[("attivita_due", row.id)]


def test_a_type_the_space_switched_off_is_not_run_and_not_raised(world: World) -> None:
    world.attivita(TODAY - timedelta(days=2), assegnata_a=world.alice.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "attivita_overdue", ReminderPreferenceWrite(enabled=False)
    )

    evaluation = world.evaluate()

    assert evaluation.reminders == ()
    assert "attivita_overdue" not in evaluation.ran
    assert "attivita_due" in evaluation.ran


def test_a_person_can_switch_a_type_off_for_themselves(world: World) -> None:
    row = world.attivita(
        TODAY - timedelta(days=2), assegnata_a=world.alice.id, deal_id=world.deal.id
    )
    ReminderPreferenceService(world.session).write(
        _actor(world.alice), "me", "attivita_overdue", ReminderPreferenceWrite(enabled=False)
    )

    assert world.raised(world.evaluate())[("attivita_overdue", row.id)] == {
        world.bob.id,
        world.carol.id,
    }


# -- deals ---------------------------------------------------------------------------------


def test_a_deal_past_its_close_climbs_from_warning_to_serious(world: World) -> None:
    def severity(days_late: int) -> str | None:
        world.deal.data_chiusura_prevista = TODAY - timedelta(days=days_late)
        found = [r for r in world.evaluate().reminders if r.type == "deal_past_close"]
        return found[0].severity if found else None

    assert severity(0) is None, "closing today is not late"
    assert severity(1) == "warning"
    assert severity(13) == "warning"
    assert severity(14) == "serious"


def test_a_won_lost_or_archived_deal_is_not_past_anything(world: World) -> None:
    won = PipelineStage(nome=f"Vinto {uuid4()}", posizione=9, probabilita_default=100, tipo="won")
    world.session.add(won)
    world.session.flush()
    world.deal.data_chiusura_prevista = TODAY - timedelta(days=30)
    world.deal.pipeline_stage_id = won.id

    assert world.evaluate().reminders == ()

    world.deal.pipeline_stage_id = world.open_stage.id
    world.deal.deleted_at = NOON
    assert world.evaluate().reminders == ()


def test_a_deal_with_nothing_on_it_for_three_weeks_is_stale(world: World) -> None:
    world.deal.created_at = _at(TODAY - timedelta(days=21))

    (reminder,) = [
        r
        for r in world.evaluate().reminders
        if r.type == "deal_stale" and r.user_id == world.bob.id
    ]

    assert reminder.severity == "warning"
    assert reminder.measure == 21


def test_a_deal_three_weeks_old_with_hours_this_week_is_not_stale(world: World) -> None:
    world.deal.created_at = _at(TODAY - timedelta(days=60))
    world.session.add(
        TimeEntry(
            deal_id=world.deal.id,
            user_id=world.alice.id,
            data=TODAY - timedelta(days=5),
            ore=Decimal("2.00"),
            descrizione="Analisi",
            tariffa_applicata=Decimal("80.000000"),
            tariffa_origine="manuale",
        )
    )

    assert [r for r in world.evaluate().reminders if r.type == "deal_stale"] == []


def test_a_mail_ten_days_ago_keeps_a_deal_from_being_stale_and_a_month_ago_does_not(
    world: World,
) -> None:
    world.deal.created_at = _at(TODAY - timedelta(days=60))
    account = GoogleAccount(
        user_id=world.alice.id,
        google_sub=f"sub-{uuid4()}",
        email_address=f"a-{uuid4()}@example.test",
        refresh_token_ciphertext=b"x",
        refresh_token_nonce=b"x",
    )
    world.session.add(account)
    world.session.flush()
    message = GmailMessage(
        google_account_id=account.id,
        gmail_message_id=f"m-{uuid4()}",
        gmail_thread_id=f"t-{uuid4()}",
        direction="inbound",
        subject="Preventivo",
        internal_date=_at(TODAY - timedelta(days=10)),
    )
    world.session.add(message)
    world.session.flush()
    world.session.add(
        GmailMessageLink(gmail_message_id=message.id, entity_type="deal", entity_id=world.deal.id)
    )
    world.session.flush()

    def stale_for_bob() -> list[float]:
        return [
            r.measure
            for r in world.evaluate().reminders
            if r.type == "deal_stale" and r.user_id == world.bob.id
        ]

    assert stale_for_bob() == []
    message.internal_date = _at(TODAY - timedelta(days=30))
    assert stale_for_bob() == [30.0]


def test_a_todo_on_the_deal_counts_as_activity(world: World) -> None:
    world.deal.created_at = _at(TODAY - timedelta(days=60))
    row = world.attivita(
        TODAY + timedelta(days=30), deal_id=world.deal.id, assegnata_a=world.alice.id
    )
    row.created_at = _at(TODAY - timedelta(days=4))

    assert [r for r in world.evaluate().reminders if r.type == "deal_stale"] == []


# -- timers --------------------------------------------------------------------------------


def test_a_timer_left_running_climbs_from_serious_to_critical(world: World) -> None:
    timer = TimeTimer(
        user_id=world.alice.id,
        deal_id=world.deal.id,
        descrizione="Call",
        started_at=NOON - timedelta(hours=11),
    )
    world.session.add(timer)
    world.session.flush()

    found = {r.user_id: r for r in world.evaluate().reminders if r.type == "timer_running_long"}

    assert set(found) == {world.alice.id, world.bob.id, world.carol.id}, "the person and the deal's"
    assert found[world.alice.id].severity == "serious"
    assert found[world.alice.id].measure == 11

    timer.started_at = NOON - timedelta(hours=25)
    later = {r.user_id: r for r in world.evaluate().reminders if r.type == "timer_running_long"}
    assert later[world.alice.id].severity == "critical"


def test_a_timer_started_this_morning_is_not_a_reminder(world: World) -> None:
    world.session.add(TimeTimer(user_id=world.alice.id, started_at=NOON - timedelta(hours=3)))

    assert world.evaluate().reminders == ()


def test_a_timer_with_no_deal_goes_to_its_person_alone(world: World) -> None:
    timer = TimeTimer(user_id=world.alice.id, started_at=NOON - timedelta(hours=12))
    world.session.add(timer)

    assert world.raised(world.evaluate()) == {("timer_running_long", timer.id): {world.alice.id}}


# -- the pass ------------------------------------------------------------------------------


def _delivered(world: World) -> int:
    return world.session.scalar(select(func.count()).select_from(ReminderDelivery)) or 0


def test_a_pass_puts_each_reminder_in_each_persons_list_once(world: World) -> None:
    world.attivita(TODAY, assegnata_a=world.alice.id)
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)

    first = engine.record_pass(world.evaluate(), today=TODAY)
    second = engine.record_pass(world.evaluate(), today=TODAY)

    assert (first.raised, first.delivered) == (1, 1)
    assert (second.raised, second.delivered) == (1, 0), "the ledger covers it: not delivered again"
    assert _delivered(world) == 1


def test_an_escalation_is_delivered_again_and_a_daily_type_the_next_day(world: World) -> None:
    world.attivita(TODAY - timedelta(days=1), assegnata_a=world.alice.id)
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)
    day = TODAY

    assert engine.record_pass(world.evaluate(day), today=day).delivered == 1
    assert engine.record_pass(world.evaluate(day), today=day).delivered == 0
    day = TODAY + timedelta(days=1)
    assert engine.record_pass(world.evaluate(day), today=day).delivered == 1, "every morning"
    day = TODAY + timedelta(days=13)
    assert engine.record_pass(world.evaluate(day), today=day).delivered == 1, "the morning after"
    rows = world.session.scalars(select(ReminderDelivery).order_by(ReminderDelivery.period)).all()
    assert len({row.period for row in rows}) == len(rows), "one row per day, not per pass"
    assert max(row.severity_rank for row in rows) == 3, "and it reached critical"


def test_a_threshold_type_is_delivered_once_per_rank_not_once_a_day(world: World) -> None:
    world.deal.data_chiusura_prevista = TODAY - timedelta(days=2)
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)

    first = engine.record_pass(world.evaluate(TODAY), today=TODAY)
    next_day = TODAY + timedelta(days=1)
    second = engine.record_pass(world.evaluate(next_day), today=next_day)
    climbed = TODAY + timedelta(days=12)
    third = engine.record_pass(world.evaluate(climbed), today=climbed)

    assert (first.delivered, second.delivered, third.delivered) == (2, 0, 2)


def test_a_reminder_a_person_acknowledged_is_not_delivered_to_them_again(world: World) -> None:
    row = world.attivita(TODAY, assegnata_a=world.alice.id, customer_id=world.customer.id)
    ReminderStateService(world.session).acknowledge(
        _actor(world.alice), reminder_key("attivita_due", row.id), "warning"
    )
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)

    result = engine.record_pass(world.evaluate(), today=TODAY)

    assert result.raised == 2 and result.delivered == 1, "Carol is told, Alice has seen it"
    told = {r.user_id for r in world.session.scalars(select(ReminderDelivery))}
    assert told == {world.carol.id}


def test_a_snoozed_reminder_comes_back_when_the_snooze_ends(world: World) -> None:
    row = world.attivita(TODAY - timedelta(days=2), assegnata_a=world.alice.id)
    key = reminder_key("attivita_overdue", row.id)
    ReminderStateService(world.session).snooze(_actor(world.alice), key, "serious", "tomorrow")
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)

    assert engine.record_pass(world.evaluate(TODAY), today=TODAY).delivered == 0
    ends = world.session.scalar(select(ReminderSnooze.until).where(ReminderSnooze.key == key))
    assert ends is not None
    assert engine.record_pass(world.evaluate(ends), today=ends).delivered == 1


def test_a_cleared_cause_ends_the_occurrence_with_everything_behind_it(world: World) -> None:
    row = world.attivita(TODAY - timedelta(days=2), assegnata_a=world.alice.id)
    key = reminder_key("attivita_overdue", row.id)
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)
    ReminderStateService(world.session).acknowledge(_actor(world.alice), key, "serious")
    engine.record_pass(world.evaluate(), today=TODAY)

    row.stato = "completata"
    row.completata_il = TODAY
    result = engine.record_pass(world.evaluate(), today=TODAY)

    assert result.raised == 0 and result.ended >= 1
    for model in (ReminderAck, ReminderDelivery, ReminderSnooze):
        assert (
            world.session.scalar(select(func.count()).select_from(model).where(model.key == key))
            == 0
        )

    # Reopened, it is a new occurrence that nothing earlier suppresses.
    row.stato = "aperta"
    row.completata_il = None
    assert engine.record_pass(world.evaluate(), today=TODAY).delivered == 1


def test_a_type_switched_off_leaves_its_rows_alone(world: World) -> None:
    row = world.attivita(TODAY - timedelta(days=2), assegnata_a=world.alice.id)
    key = reminder_key("attivita_overdue", row.id)
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)
    engine.record_pass(world.evaluate(), today=TODAY)
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "attivita_overdue", ReminderPreferenceWrite(enabled=False)
    )

    engine.record_pass(world.evaluate(), today=TODAY)

    assert (
        world.session.scalar(
            select(func.count()).select_from(ReminderDelivery).where(ReminderDelivery.key == key)
        )
        == 1
    ), "a switch turned back on resumes where it was"


# -- what a person reads ---------------------------------------------------------------------


def test_a_person_lists_their_reminders_with_what_they_did_about_each(world: World) -> None:
    due = world.attivita(TODAY, assegnata_a=world.alice.id, titolo="Mandare il preventivo")
    overdue = world.attivita(TODAY - timedelta(days=2), assegnata_a=world.alice.id)
    world.attivita(TODAY, assegnata_a=world.bob.id)
    ReminderStateService(world.session).acknowledge(
        _actor(world.alice), reminder_key("attivita_due", due.id), "warning"
    )
    engine = ReminderEngine(world.session, SETTINGS, owner_email=world.owner.email)
    engine.record_pass(world.evaluate(), today=TODAY)
    world.session.flush()

    listed = ReminderService(world.session, SETTINGS).list(
        _actor(world.alice), today=TODAY, now=NOON
    )

    assert [(item.type, item.status) for item in listed] == [
        ("attivita_overdue", "open"),
        ("attivita_due", "acknowledged"),
    ], "the most severe first, Bob's own attività not among Alice's"
    assert listed[0].entity_id == overdue.id and listed[0].entity == "attivita"
    assert listed[0].delivered_at is not None, "the pass put it in her list"
    assert listed[1].title == "Attività in scadenza: Mandare il preventivo"
    assert listed[1].detail == "Scade oggi"
    assert listed[0].detail.startswith("Scaduta da 2 giorni")


def test_the_catalogue_and_the_detectors_name_the_same_types() -> None:
    assert [detector.kind.key for detector in DETECTORS] == [
        "attivita_due",
        "attivita_overdue",
        "deal_past_close",
        "deal_stale",
        "timer_running_long",
    ]
    assert {detector.kind.key for detector in DETECTORS} == set(REMINDER_TYPES)
