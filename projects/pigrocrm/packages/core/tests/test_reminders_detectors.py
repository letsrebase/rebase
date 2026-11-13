"""REB-733: the detector framework and the first group of the catalogue (spec
2026-10-26 §3.2, §4, §8.2).

Every test evaluates on a date it chooses (`TODAY`), which is the point of a pure detector.
The card's proof is the first one: a task due today, an overdue one and a deal past
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
from pigrocrm.core.auth.models import User, UserLegalEntity
from pigrocrm.core.config import Settings
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.gmail.models import GmailMessage, GmailMessageLink, GoogleAccount
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.reminders import state as reminder_state
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES, reminder_key
from pigrocrm.core.reminders.detect import DetectorContext
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.reminders.directory import Directory
from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
from pigrocrm.core.reminders.jobs import REMINDER_PASS_JOB
from pigrocrm.core.reminders.models import (
    ReminderAck,
    ReminderDelivery,
    ReminderJobRun,
    ReminderSnooze,
)
from pigrocrm.core.reminders.preferences import ReminderPreferenceService, ReminderSettings
from pigrocrm.core.reminders.schemas import ReminderPreferenceWrite
from pigrocrm.core.reminders.service import ReminderService
from pigrocrm.core.reminders.state import ReminderStateService
from pigrocrm.core.tasks.models import Task
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
    customer: Organization
    deal: Deal
    open_stage: PipelineStage
    legal_entity: UUID

    def user(self, role: str = "collaborator", active: bool = True) -> User:
        user = User(
            email=f"u-{uuid4()}@example.test",
            password_hash="x",
            name="Persona",
            role=role,
            active=active,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def task(self, due_date: date | None, **fields: object) -> Task:
        row = Task(title=fields.pop("title", "Richiamare"), due_date=due_date, **fields)
        self.session.add(row)
        self.session.flush()
        return row

    def evaluate(self, today: date = TODAY, now: datetime = NOON) -> Evaluation:
        self.session.flush()
        return ReminderEngine(self.session, SETTINGS).evaluate(today=today, now=now)

    def raised(self, evaluation: Evaluation) -> dict[tuple[str, UUID], set[UUID]]:
        """`(type, subject)` to the people told."""
        found: dict[tuple[str, UUID], set[UUID]] = {}
        for reminder in evaluation.reminders:
            subject = UUID(reminder.key.partition(":")[2])
            found.setdefault((reminder.type, subject), set()).add(reminder.user_id)
        return found


@pytest.fixture
def world(db_session: Session) -> World:
    legal_entity = db_session.scalars(
        select(LegalEntity.id).where(LegalEntity.is_default.is_(True))
    ).one()
    users = {}
    for name, role in (
        ("owner", "admin"),
        ("alice", "collaborator"),
        ("bob", "collaborator"),
        ("carol", "collaborator"),
    ):
        user = User(
            email=f"{name}-{uuid4()}@example.test",
            password_hash="x",
            name=name.title(),
            role=role,
        )
        db_session.add(user)
        users[name] = user
    stage = PipelineStage(name=f"Aperto {uuid4()}", position=0, default_probability=10, type="open")
    db_session.add(stage)
    db_session.flush()
    customer = Organization(
        legal_name="ACME Srl", legal_entity_id=legal_entity, owner_id=users["carol"].id
    )
    db_session.add(customer)
    db_session.flush()
    deal = Deal(
        name="Sito web",
        customer_id=customer.id,
        legal_entity_id=legal_entity,
        pipeline_stage_id=stage.id,
        probability=10,
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
        legal_entity,
    )


# -- the card's proof ----------------------------------------------------------------------


def test_one_pass_raises_a_due_an_overdue_and_a_past_close_deal_for_the_right_people(
    world: World,
) -> None:
    due = world.task(TODAY, assigned_to=world.alice.id, customer_id=world.customer.id)
    overdue = world.task(
        TODAY - timedelta(days=3), assigned_to=world.alice.id, deal_id=world.deal.id
    )
    world.deal.expected_close_date = TODAY - timedelta(days=5)

    raised = world.raised(world.evaluate())

    # An task due today, assigned to Alice and linked to the customer Carol owns.
    assert raised[("task_due", due.id)] == {world.alice.id, world.carol.id}
    # An overdue one on the deal Bob owns, whose customer Carol owns, assigned to Alice.
    assert raised[("task_overdue", overdue.id)] == {
        world.alice.id,
        world.bob.id,
        world.carol.id,
    }
    # The deal itself, past its expected close: its owner and its customer's.
    assert raised[("deal_past_close", world.deal.id)] == {world.bob.id, world.carol.id}
    assert len(raised) == 3, "nothing else is raised: the deal is two days old, the timer absent"


def test_the_severity_is_each_recipients_own(world: World) -> None:
    world.task(TODAY - timedelta(days=3), assigned_to=world.alice.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.alice),
        "me",
        "task_overdue",
        ReminderPreferenceWrite(ladder={"serious": 1, "critical": 3}),
    )

    evaluation = world.evaluate()

    severities = {reminder.user_id: reminder.severity for reminder in evaluation.reminders}
    assert severities[world.alice.id] == "critical", "her own ladder: critical from 3 days"
    # Nobody else is told about an task with no link and an assignee, but the space's
    # owner is not either: the assignee is a recipient, so the fallback does not apply.
    assert set(severities) == {world.alice.id}


@pytest.mark.parametrize("who", ["owner", "alice", "bob", "carol"])
def test_a_persons_list_is_what_the_whole_space_raises_for_them(world: World, who: str) -> None:
    """`reminders_of` runs less than `evaluate` (a type only the admins are told is not run
    for somebody who is not one, and nobody else's severity is resolved) and says the same
    about the person."""
    world.task(TODAY - timedelta(days=3), assigned_to=world.alice.id, deal_id=world.deal.id)
    world.task(TODAY, customer_id=world.customer.id)
    world.deal.expected_close_date = TODAY - timedelta(days=5)
    # Something only the admins are told: the reminder pass itself has not run for hours.
    world.session.add(ReminderJobRun(job=REMINDER_PASS_JOB, ran_at=NOON - timedelta(hours=5)))
    user = getattr(world, who)
    everything = world.evaluate()
    expected = [r for r in everything.reminders if r.user_id == user.id]
    assert expected, f"{who} is told of nothing, so the comparison proves nothing"
    assert ("job_late" in {r.type for r in expected}) == (who == "owner")

    mine = ReminderEngine(world.session, SETTINGS).reminders_of(user.id, today=TODAY, now=NOON)

    assert mine == expected


def test_somebody_who_cannot_be_told_has_no_list(world: World) -> None:
    world.task(TODAY - timedelta(days=3), assigned_to=world.alice.id)
    left = world.user(active=False)

    mine = ReminderEngine(world.session, SETTINGS).reminders_of(left.id, today=TODAY, now=NOON)

    assert mine == []


def test_what_detectors_share_is_read_once_per_evaluation_and_never_across_two(
    world: World,
) -> None:
    reads: list[DetectorContext] = []

    def load(ctx: DetectorContext) -> int:
        reads.append(ctx)
        return len(reads)

    def context() -> DetectorContext:
        return DetectorContext(
            session=world.session,
            today=TODAY,
            now=NOON,
            timezone=SETTINGS.timezone,
            directory=Directory([], None),
            settings=ReminderSettings([]),
        )

    first, second = context(), context()

    assert [first.once(load), first.once(load), first.once(load)] == [1, 1, 1]
    assert second.once(load) == 2, "a later evaluation is never shown an earlier one's data"
    assert reads == [first, second]


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.role)  # type: ignore[arg-type]


# -- task ------------------------------------------------------------------------------


def test_an_task_with_no_deadline_is_not_late_and_not_due(world: World) -> None:
    world.task(None, assigned_to=world.alice.id)

    assert world.evaluate().reminders == ()


def test_only_an_open_not_archived_task_counts(world: World) -> None:
    yesterday = TODAY - timedelta(days=1)
    world.task(yesterday, assigned_to=world.alice.id, status="completed", completed_at=yesterday)
    world.task(yesterday, assigned_to=world.alice.id, status="cancelled")
    world.task(yesterday, assigned_to=world.alice.id, deleted_at=NOON)

    assert world.evaluate().reminders == ()


def test_on_the_day_it_is_due_and_the_day_after_it_is_overdue(world: World) -> None:
    row = world.task(TODAY, assigned_to=world.alice.id)

    today = world.raised(world.evaluate(TODAY))
    tomorrow = world.raised(world.evaluate(TODAY + timedelta(days=1)))

    assert ("task_due", row.id) in today and ("task_overdue", row.id) not in today
    assert ("task_overdue", row.id) in tomorrow and ("task_due", row.id) not in tomorrow


def test_what_the_pass_opened_for_a_late_invoice_is_not_announced_twice_to_one_person(
    world: World,
) -> None:
    """`overdue_invoice`'s task is the to-do of the reminder `invoice_overdue`: a person
    already told of the invoice is not told of its task as well, and a person who is not
    (here the assignee, who owns nothing) still is. The other rules' task and manual
    ones are announced like any other."""
    invoice = Invoice(
        customer_id=world.customer.id,
        legal_entity_id=world.legal_entity,
        type="invoice",
        status="issued",
        year=2026,
        number=1,
        issue_date=TODAY - timedelta(days=40),
        due_date=TODAY - timedelta(days=3),
        total=Decimal("1220.00"),
    )
    world.session.add(invoice)
    world.session.flush()
    covered = world.task(
        TODAY,
        assigned_to=world.alice.id,
        invoice_id=invoice.id,
        origin="automation",
        rule="overdue_invoice",
    )
    other = world.task(TODAY, assigned_to=world.alice.id, origin="automation", rule="stalled_deal")
    manual = world.task(TODAY, assigned_to=world.alice.id, invoice_id=invoice.id)

    evaluation = world.evaluate()
    raised = world.raised(evaluation)

    assert ("invoice_overdue", invoice.id) in raised
    # Still detected for everybody, so a person's acknowledge or snooze of it is not forgotten
    # on the day it is not raised for them.
    assert reminder_key("task_due", covered.id) in evaluation.raised
    # The customer's owner hears of the invoice already; the assignee does not.
    assert raised[("task_due", covered.id)] == {world.alice.id}
    assert world.carol.id in raised[("task_due", manual.id)]
    assert raised[("task_due", other.id)] == {world.alice.id}

    # And a person who switched `invoice_overdue` off keeps the task's reminders.
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "invoice_overdue", ReminderPreferenceWrite(enabled=False)
    )
    after = world.raised(world.evaluate())
    assert ("invoice_overdue", invoice.id) not in after
    assert world.carol.id in after[("task_due", covered.id)]


def test_an_overdue_task_climbs_the_ladder_as_days_pass(world: World) -> None:
    world.task(TODAY - timedelta(days=1), assigned_to=world.alice.id)

    def severity(day: int) -> str:
        (reminder,) = world.evaluate(TODAY + timedelta(days=day)).reminders
        return reminder.severity

    assert severity(0) == "serious"
    assert severity(12) == "serious"
    assert severity(13) == "critical", "fourteen days late"


def test_to_lead_time_raises_to_due_task_early_for_the_person_who_asked(world: World) -> None:
    row = world.task(TODAY + timedelta(days=3), assigned_to=world.alice.id)
    world.task(TODAY + timedelta(days=3), assigned_to=world.bob.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.alice), "me", "task_due", ReminderPreferenceWrite(ladder={"warning": 3})
    )

    raised = world.raised(world.evaluate())

    assert raised == {("task_due", row.id): {world.alice.id}}, (
        "Bob keeps the default of the day itself; Alice asked for three days' notice"
    )


def test_an_task_linked_to_to_person_or_invoice_reaches_the_organizations_owner(
    world: World,
) -> None:
    from pigrocrm.core.people.models import Person

    person = Person(first_name="Mario", last_name="Rossi", customer_id=world.customer.id)
    world.session.add(person)
    world.session.flush()
    row = world.task(TODAY, person_id=person.id)

    assert world.raised(world.evaluate())[("task_due", row.id)] == {world.carol.id}


def test_an_task_with_nobody_goes_to_the_spaces_owner(world: World) -> None:
    row = world.task(TODAY)

    assert world.raised(world.evaluate()) == {("task_due", row.id): {world.owner.id}}


def test_an_archived_assignee_is_dropped_and_the_reminder_goes_on(world: World) -> None:
    gone = world.user(active=False)
    row = world.task(TODAY, assigned_to=gone.id, customer_id=world.customer.id)

    assert world.raised(world.evaluate())[("task_due", row.id)] == {world.carol.id}


def test_when_nobody_can_be_told_it_goes_to_the_spaces_owner(world: World) -> None:
    gone = world.user(active=False)
    row = world.task(TODAY, assigned_to=gone.id)

    assert world.raised(world.evaluate())[("task_due", row.id)] == {world.owner.id}


def test_a_scoped_member_is_told_only_about_what_their_scope_covers(world: World) -> None:
    other = LegalEntity(name="Altra", legal_name="Altra Srl", country="IT")
    world.session.add(other)
    world.session.flush()
    scoped = world.user()
    scoped.limited_scope = True
    scoped.scopes = [UserLegalEntity(user_id=scoped.id, legal_entity_id=other.id)]
    world.session.flush()
    row = world.task(TODAY, assigned_to=scoped.id, customer_id=world.customer.id)
    unlinked = world.task(TODAY, assigned_to=scoped.id)

    raised = world.raised(world.evaluate())

    assert raised[("task_due", row.id)] == {world.carol.id}, (
        "the customer is another legal entity's"
    )
    assert scoped.id not in raised[("task_due", unlinked.id)], "no legal entity, no scoped reader"
    assert raised[("task_due", unlinked.id)] == {world.owner.id}


def test_to_scoped_member_is_told_about_their_own_legal_entity(world: World) -> None:
    scoped = world.user()
    scoped.limited_scope = True
    scoped.scopes = [UserLegalEntity(user_id=scoped.id, legal_entity_id=world.legal_entity)]
    world.session.flush()
    row = world.task(TODAY, assigned_to=scoped.id, customer_id=world.customer.id)

    assert scoped.id in world.raised(world.evaluate())[("task_due", row.id)]


def test_a_type_the_space_switched_off_is_not_run_and_not_raised(world: World) -> None:
    world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "task_overdue", ReminderPreferenceWrite(enabled=False)
    )

    evaluation = world.evaluate()

    assert evaluation.reminders == ()
    assert "task_overdue" not in evaluation.ran
    assert "task_due" in evaluation.ran


def test_a_person_can_switch_a_type_off_for_themselves(world: World) -> None:
    row = world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id, deal_id=world.deal.id)
    ReminderPreferenceService(world.session).write(
        _actor(world.alice), "me", "task_overdue", ReminderPreferenceWrite(enabled=False)
    )

    assert world.raised(world.evaluate())[("task_overdue", row.id)] == {
        world.bob.id,
        world.carol.id,
    }


# -- deals ---------------------------------------------------------------------------------


def test_a_deal_past_its_close_climbs_from_warning_to_serious(world: World) -> None:
    def severity(days_late: int) -> str | None:
        world.deal.expected_close_date = TODAY - timedelta(days=days_late)
        found = [r for r in world.evaluate().reminders if r.type == "deal_past_close"]
        return found[0].severity if found else None

    assert severity(0) is None, "closing today is not late"
    assert severity(1) == "warning"
    assert severity(13) == "warning"
    assert severity(14) == "serious"


def test_a_won_lost_or_archived_deal_is_not_past_anything(world: World) -> None:
    won = PipelineStage(name=f"Vinto {uuid4()}", position=9, default_probability=100, type="won")
    world.session.add(won)
    world.session.flush()
    world.deal.expected_close_date = TODAY - timedelta(days=30)
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
            date=TODAY - timedelta(days=5),
            hours=Decimal("2.00"),
            description="Analisi",
            applied_rate=Decimal("80.000000"),
            rate_source="manual",
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
    row = world.task(TODAY + timedelta(days=30), deal_id=world.deal.id, assigned_to=world.alice.id)
    row.created_at = _at(TODAY - timedelta(days=4))

    assert [r for r in world.evaluate().reminders if r.type == "deal_stale"] == []


# -- timers --------------------------------------------------------------------------------


def test_a_timer_left_running_climbs_from_serious_to_critical(world: World) -> None:
    timer = TimeTimer(
        user_id=world.alice.id,
        deal_id=world.deal.id,
        description="Call",
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
    world.task(TODAY, assigned_to=world.alice.id)
    engine = ReminderEngine(world.session, SETTINGS)

    first = engine.record_pass(world.evaluate(), today=TODAY)
    second = engine.record_pass(world.evaluate(), today=TODAY)

    assert (first.raised, first.delivered) == (1, 1)
    assert (second.raised, second.delivered) == (1, 0), "the ledger covers it: not delivered again"
    assert _delivered(world) == 1


def test_an_escalation_is_delivered_again_and_a_daily_type_the_next_day(world: World) -> None:
    world.task(TODAY - timedelta(days=1), assigned_to=world.alice.id)
    engine = ReminderEngine(world.session, SETTINGS)
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
    world.deal.expected_close_date = TODAY - timedelta(days=2)
    engine = ReminderEngine(world.session, SETTINGS)

    first = engine.record_pass(world.evaluate(TODAY), today=TODAY)
    next_day = TODAY + timedelta(days=1)
    second = engine.record_pass(world.evaluate(next_day), today=next_day)
    climbed = TODAY + timedelta(days=12)
    third = engine.record_pass(world.evaluate(climbed), today=climbed)

    assert (first.delivered, second.delivered, third.delivered) == (2, 0, 2)


def test_a_reminder_a_person_acknowledged_is_not_delivered_to_them_again(world: World) -> None:
    row = world.task(TODAY, assigned_to=world.alice.id, customer_id=world.customer.id)
    ReminderStateService(world.session).acknowledge(
        _actor(world.alice), reminder_key("task_due", row.id), "warning"
    )
    engine = ReminderEngine(world.session, SETTINGS)

    result = engine.record_pass(world.evaluate(), today=TODAY)

    assert result.raised == 2 and result.delivered == 1, "Carol is told, Alice has seen it"
    told = {r.user_id for r in world.session.scalars(select(ReminderDelivery))}
    assert told == {world.carol.id}


def test_a_snoozed_reminder_comes_back_when_the_snooze_ends(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A person's snooze counts from the installation's real today, which the engine's passes do
    # not own: pin it to the fixture's day, or the snooze would end on a day the fixture has no
    # reminders for once the real date passes it.
    monkeypatch.setattr(reminder_state, "today_local", lambda: TODAY)
    row = world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id)
    key = reminder_key("task_overdue", row.id)
    ReminderStateService(world.session).snooze(_actor(world.alice), key, "serious", "tomorrow")
    engine = ReminderEngine(world.session, SETTINGS)

    assert engine.record_pass(world.evaluate(TODAY), today=TODAY).delivered == 0
    ends = world.session.scalar(select(ReminderSnooze.until).where(ReminderSnooze.key == key))
    assert ends is not None
    assert engine.record_pass(world.evaluate(ends), today=ends).delivered == 1


def test_a_cleared_cause_ends_the_occurrence_with_everything_behind_it(world: World) -> None:
    row = world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id)
    key = reminder_key("task_overdue", row.id)
    engine = ReminderEngine(world.session, SETTINGS)
    ReminderStateService(world.session).acknowledge(_actor(world.alice), key, "serious")
    engine.record_pass(world.evaluate(), today=TODAY)

    row.status = "completed"
    row.completed_at = TODAY
    result = engine.record_pass(world.evaluate(), today=TODAY)

    assert result.raised == 0 and result.ended >= 1
    for model in (ReminderAck, ReminderDelivery, ReminderSnooze):
        assert (
            world.session.scalar(select(func.count()).select_from(model).where(model.key == key))
            == 0
        )

    # Reopened, it is a new occurrence that nothing earlier suppresses.
    row.status = "open"
    row.completed_at = None
    assert engine.record_pass(world.evaluate(), today=TODAY).delivered == 1


def test_a_type_switched_off_leaves_its_rows_alone(world: World) -> None:
    row = world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id)
    key = reminder_key("task_overdue", row.id)
    engine = ReminderEngine(world.session, SETTINGS)
    engine.record_pass(world.evaluate(), today=TODAY)
    ReminderPreferenceService(world.session).write(
        _actor(world.owner), "space", "task_overdue", ReminderPreferenceWrite(enabled=False)
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
    due = world.task(TODAY, assigned_to=world.alice.id, title="Mandare il preventivo")
    overdue = world.task(TODAY - timedelta(days=2), assigned_to=world.alice.id)
    world.task(TODAY, assigned_to=world.bob.id)
    ReminderStateService(world.session).acknowledge(
        _actor(world.alice), reminder_key("task_due", due.id), "warning"
    )
    engine = ReminderEngine(world.session, SETTINGS)
    engine.record_pass(world.evaluate(), today=TODAY)
    world.session.flush()

    listed = ReminderService(world.session, SETTINGS).list(
        _actor(world.alice), today=TODAY, now=NOON
    )

    assert [(item.type, item.status) for item in listed] == [
        ("task_overdue", "open"),
        ("task_due", "acknowledged"),
    ], "the most severe first, Bob's own task not among Alice's"
    assert listed[0].entity_id == overdue.id and listed[0].entity == "task"
    assert listed[0].delivered_at is not None, "the pass put it in her list"
    assert listed[1].title == "Attività in scadenza: Mandare il preventivo"
    assert listed[1].detail == "Scade oggi"
    assert listed[0].detail.startswith("Scaduta da 2 giorni")


def test_the_catalogue_and_the_detectors_name_the_same_types() -> None:
    assert [detector.kind.key for detector in DETECTORS] == [
        "task_due",
        "task_overdue",
        "deal_past_close",
        "deal_stale",
        "timer_running_long",
        "invoice_overdue",
        "invoice_not_transmitted",
        "proforma_not_invoiced",
        "hours_unbilled",
        "billable_period_closed",
        "fixed_fee_period_unbilled",
        "on_completion_unbilled",
        "invoice_days_unlinked",
        "contract_expiring",
        "renewal_window_open",
        "worked_without_approval",
        "approval_unanswered",
        "approval_requirement_unmet",
        "work_unit_outside_term",
        "ceiling_approaching",
        "year_end_overrun_projected",
        "tax_payment_due",
        "tax_figure_missing",
        "month_not_closed",
        "proposal_pending",
        "connection_failing",
        "job_late",
    ]
    assert {detector.kind.key for detector in DETECTORS} == set(REMINDER_TYPES)
