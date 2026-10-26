"""REB-732: the bookkeeping around a reminder (spec 2026-10-26 §8).

Two properties are the card's: a reminder one user acknowledged still shows to another,
and a delivery is never made again on the same channel.
"""

from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.errors import Conflict, ValidationFailed
from pigrocrm.core.reminders.catalogue import (
    REMINDER_TYPES,
    reminder_key,
    severity_for,
    validate_ladder,
)
from pigrocrm.core.reminders.ledger import ReminderLedger
from pigrocrm.core.reminders.models import (
    ReminderAck,
    ReminderDelivery,
    ReminderJobRun,
    ReminderSnooze,
)
from pigrocrm.core.reminders.state import (
    KeyState,
    ReminderStateService,
    snooze_until,
    states_for,
)

TODAY = date(2026, 10, 26)  # a Monday


def _user(session: Session, ruolo: str = "collaboratore") -> User:
    user = User(email=f"u-{uuid4()}@example.test", password_hash="x", nome="Persona", ruolo=ruolo)
    session.add(user)
    session.flush()
    return user


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.ruolo)  # type: ignore[arg-type]


KEY = reminder_key("attivita_overdue", uuid4())


# -- the card's two proofs -----------------------------------------------------------------


def test_a_reminder_acknowledged_by_one_user_still_shows_to_another(db_session: Session) -> None:
    one, other = _user(db_session), _user(db_session)

    ReminderStateService(db_session).acknowledge(_actor(one), KEY, "serious")

    states = states_for(db_session, [one.id, other.id])
    assert states[(one.id, KEY)].covers("serious", TODAY) is True
    assert (other.id, KEY) not in states, "the other person has done nothing about it"
    assert states.get((other.id, KEY), KeyState()).covers("serious", TODAY) is False


def test_a_delivered_reminder_is_not_delivered_again_on_the_same_channel(
    db_session: Session,
) -> None:
    user = _user(db_session)
    ledger = ReminderLedger(db_session)
    identity = (KEY, user.id, "email", "immediate", None)

    assert not ReminderLedger.covered(ledger.delivered([KEY]), identity, "serious")
    ledger.record(identity, "serious")

    delivered = ledger.delivered([KEY])
    assert ReminderLedger.covered(delivered, identity, "serious"), "same rank, same channel"
    assert ReminderLedger.covered(delivered, identity, "warning"), "lower rank is covered too"
    # Another channel of the same reminder is its own delivery.
    assert not ReminderLedger.covered(
        delivered, (KEY, user.id, "slack", "immediate", None), "serious"
    )
    # And another person is theirs.
    other = _user(db_session)
    assert not ReminderLedger.covered(
        delivered, (KEY, other.id, "email", "immediate", None), "serious"
    )


# -- acknowledge and snooze ----------------------------------------------------------------


def test_an_acknowledgement_covers_its_rank_and_below_and_never_a_higher_one(
    db_session: Session,
) -> None:
    user = _user(db_session)
    ReminderStateService(db_session).acknowledge(_actor(user), KEY, "serious")
    state = states_for(db_session, [user.id])[(user.id, KEY)]

    assert state.covers("warning", TODAY)
    assert state.covers("serious", TODAY)
    assert not state.covers("critical", TODAY), "seen at serious is not seen at critical"


def test_a_later_acknowledgement_at_a_lower_rank_does_not_uncover_the_higher_one(
    db_session: Session,
) -> None:
    user = _user(db_session)
    service = ReminderStateService(db_session)
    service.acknowledge(_actor(user), KEY, "critical")

    again = service.acknowledge(_actor(user), KEY, "warning")

    assert again.acknowledged_severity == "critical"
    rows = db_session.scalars(select(ReminderAck).where(ReminderAck.key == KEY)).all()
    assert len(rows) == 1, "an upsert, not a second row"


def test_a_snooze_covers_until_its_day_and_stops_that_day(db_session: Session) -> None:
    user = _user(db_session)
    snoozed = ReminderStateService(db_session).snooze(_actor(user), KEY, "serious", "in_a_week")
    state = states_for(db_session, [user.id])[(user.id, KEY)]

    until = snoozed.snoozed_until
    assert until is not None
    assert state.covers("serious", date.fromordinal(until.toordinal() - 1))
    assert not state.covers("serious", until), "the day it names, it is back"
    assert not state.covers("critical", TODAY), "a higher severity is never snoozed away"


def test_a_later_snooze_replaces_the_rank_and_the_day_together(db_session: Session) -> None:
    user = _user(db_session)
    service = ReminderStateService(db_session)
    service.snooze(_actor(user), KEY, "critical", "tomorrow")

    again = service.snooze(_actor(user), KEY, "warning", "in_a_month")

    assert again.snoozed_severity == "warning", "what was snoozed is what the last click saw"
    state = states_for(db_session, [user.id])[(user.id, KEY)]
    assert not state.covers("critical", TODAY), "a climb back to critical is not hidden for a month"


def test_a_snooze_is_per_user_like_an_acknowledgement(db_session: Session) -> None:
    one, other = _user(db_session), _user(db_session)
    ReminderStateService(db_session).snooze(_actor(one), KEY, "serious", "tomorrow")

    states = states_for(db_session, [one.id, other.id])
    assert (other.id, KEY) not in states


def test_the_snooze_options_are_computed_once_from_the_installations_today() -> None:
    assert snooze_until("tomorrow", TODAY) == date(2026, 10, 27)
    assert snooze_until("next_monday", TODAY) == date(2026, 11, 2), "never today, a Monday included"
    assert snooze_until("next_monday", date(2026, 10, 30)) == date(2026, 11, 2)
    assert snooze_until("in_a_week", TODAY) == date(2026, 11, 2)
    assert snooze_until("in_a_month", TODAY) == date(2026, 11, 26)
    assert snooze_until("in_a_month", date(2026, 1, 31)) == date(2026, 2, 28), "clamped"
    assert snooze_until("in_a_month", date(2026, 12, 15)) == date(2027, 1, 15)


def test_a_snooze_option_that_is_not_offered_is_refused_by_the_schema() -> None:
    from pydantic import ValidationError

    from pigrocrm.core.reminders.schemas import SnoozeRequest

    with pytest.raises(ValidationError):
        SnoozeRequest(key=KEY, severity="serious", until="2026-12-01")  # type: ignore[arg-type]


def test_a_key_must_name_a_known_type_and_a_subject(db_session: Session) -> None:
    user = _user(db_session)
    service = ReminderStateService(db_session)

    for bad in ("nonsense", "nope:1", "attivita_overdue:", "attivita_overdue:a b"):
        with pytest.raises(ValidationFailed):
            service.acknowledge(_actor(user), bad, "warning")


def test_a_job_has_no_inbox(db_session: Session) -> None:
    with pytest.raises(Conflict):
        ReminderStateService(db_session).acknowledge(Actor.system(), KEY, "warning")


def test_a_readonly_member_may_acknowledge_their_own_reminders(db_session: Session) -> None:
    reader = _user(db_session, "readonly")

    done = ReminderStateService(db_session).acknowledge(_actor(reader), KEY, "warning")

    assert done.acknowledged_severity == "warning"
    assert [state.key for state in ReminderStateService(db_session).mine(_actor(reader))] == [KEY]


# -- the ledger ----------------------------------------------------------------------------


def test_an_escalation_is_an_upsert_that_keeps_the_highest_rank(db_session: Session) -> None:
    user = _user(db_session)
    ledger = ReminderLedger(db_session)
    identity = (KEY, user.id, "push", "immediate", None)

    ledger.record(identity, "serious")
    ledger.record(identity, "critical")
    ledger.record(identity, "warning")  # a threshold edit lowered it: nothing is lowered
    db_session.flush()

    rows = db_session.scalars(select(ReminderDelivery).where(ReminderDelivery.key == KEY)).all()
    assert [row.severity_rank for row in rows] == [3]
    delivered = ledger.delivered([KEY])
    assert ReminderLedger.covered(delivered, identity, "critical")


def test_a_daily_type_is_delivered_once_a_day_through_the_period(db_session: Session) -> None:
    user = _user(db_session)
    ledger = ReminderLedger(db_session)
    monday = (KEY, user.id, "push", "immediate", TODAY)
    tuesday = (KEY, user.id, "push", "immediate", date(2026, 10, 27))

    ledger.record(monday, "serious")

    delivered = ledger.delivered([KEY])
    assert ReminderLedger.covered(delivered, monday, "serious")
    assert not ReminderLedger.covered(delivered, tuesday, "serious"), "the next morning, again"


def test_two_deliveries_without_a_period_are_one_row_not_two(db_session: Session) -> None:
    """`NULLS NOT DISTINCT`: a plain unique constraint accepts two rows whose `period` is
    null, which is every type but the daily one."""
    user = _user(db_session)
    ledger = ReminderLedger(db_session)
    identity = (KEY, user.id, "slack", "immediate", None)

    ledger.record(identity, "warning")
    ledger.record(identity, "warning")
    db_session.flush()

    count = db_session.scalar(
        select(func.count()).select_from(ReminderDelivery).where(ReminderDelivery.key == KEY)
    )
    assert count == 1


def test_the_end_of_a_pass_forgets_the_keys_a_type_no_longer_raises(db_session: Session) -> None:
    user = _user(db_session)
    service = ReminderStateService(db_session)
    ledger = ReminderLedger(db_session)
    still = reminder_key("attivita_overdue", uuid4())
    cleared = reminder_key("attivita_overdue", uuid4())
    other_type = reminder_key("deal_stale", uuid4())
    for key in (still, cleared, other_type):
        service.acknowledge(_actor(user), key, "warning")
        service.snooze(_actor(user), key, "warning", "tomorrow")
        ledger.record((key, user.id, "push", "immediate", None), "warning")
    db_session.flush()

    ledger.forget_unraised(ran={"attivita_overdue"}, raised={still})
    db_session.flush()

    for model in (ReminderAck, ReminderSnooze, ReminderDelivery):
        keys = set(db_session.scalars(select(model.key)))
        assert still in keys, "the cause is still there"
        assert cleared not in keys, "the cause went, the occurrence ended"
        assert other_type in keys, "a type that did not run leaves its rows alone"


def test_a_pass_that_raised_nothing_of_a_type_that_ran_forgets_all_of_it(
    db_session: Session,
) -> None:
    user = _user(db_session)
    key = reminder_key("attivita_overdue", uuid4())
    ReminderStateService(db_session).acknowledge(_actor(user), key, "warning")

    ReminderLedger(db_session).forget_unraised(ran={"attivita_overdue"}, raised=set())
    db_session.flush()

    assert db_session.scalar(select(func.count()).select_from(ReminderAck)) == 0


def test_the_summary_marker_and_the_job_clock(db_session: Session) -> None:
    user = _user(db_session)
    ledger = ReminderLedger(db_session)

    assert not ledger.summary_sent(user.id, TODAY)
    ledger.mark_summary_sent(user.id, TODAY)
    ledger.mark_summary_sent(user.id, TODAY)
    db_session.flush()
    assert ledger.summary_sent(user.id, TODAY)
    assert not ledger.summary_sent(user.id, date(2026, 10, 27))

    first = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    ledger.touch_job("reminders", first)
    ledger.touch_job("reminders", datetime(2026, 10, 26, 8, 15, tzinfo=UTC))
    db_session.flush()
    assert ledger.last_run("reminders") == datetime(2026, 10, 26, 8, 15, tzinfo=UTC)
    assert ledger.last_run("digest") is None
    assert db_session.scalar(select(func.count()).select_from(ReminderJobRun)) == 1


# -- the catalogue's arithmetic --------------------------------------------------------------


def test_a_since_ladder_reaches_its_highest_rung() -> None:
    overdue = REMINDER_TYPES["attivita_overdue"]  # serious from 1 day, critical from 14

    assert severity_for(overdue, 0, overdue.ladder) is None
    assert severity_for(overdue, 1, overdue.ladder) == "serious"
    assert severity_for(overdue, 13, overdue.ladder) == "serious"
    assert severity_for(overdue, 14, overdue.ladder) == "critical"


def test_an_until_ladder_reaches_its_rungs_as_the_measure_shrinks() -> None:
    due = REMINDER_TYPES["attivita_due"]

    assert severity_for(due, 0, {"warning": 0}) == "warning"
    assert severity_for(due, 1, {"warning": 0}) is None
    assert severity_for(due, 3, {"warning": 3}) == "warning", "a lead time of three days"
    assert severity_for(due, 2, {"warning": 14, "serious": 3, "critical": 1}) == "serious"


def test_a_ladder_that_breaks_the_order_is_refused_in_italian() -> None:
    overdue = REMINDER_TYPES["attivita_overdue"]
    due = REMINDER_TYPES["attivita_due"]

    assert validate_ladder(overdue, {"warning": 1, "serious": 7, "critical": 14}) == {
        "warning": 1,
        "serious": 7,
        "critical": 14,
    }
    with pytest.raises(ValidationFailed, match="più alta"):
        validate_ladder(overdue, {"warning": 5, "serious": 5})
    with pytest.raises(ValidationFailed, match="più alta"):
        validate_ladder(overdue, {"serious": 20, "critical": 14})
    with pytest.raises(ValidationFailed, match="più bassa"):
        validate_ladder(due, {"warning": 1, "serious": 3})
    with pytest.raises(ValidationFailed):
        validate_ladder(overdue, {})
    with pytest.raises(ValidationFailed):
        validate_ladder(overdue, {"urgent": 3})  # type: ignore[dict-item]
    with pytest.raises(ValidationFailed):
        validate_ladder(overdue, {"warning": -1})


def test_quiet_hours_are_read_as_the_window_that_crosses_midnight() -> None:
    from pigrocrm.core.reminders.preferences import EffectiveGeneral
    from pigrocrm.core.reminders.schemas import QuietHours

    general = EffectiveGeneral(QuietHours(), 8)  # 20:00 to 08:00 and the weekend

    assert general.quiet_at(0, time(21, 0)), "Monday evening"
    assert general.quiet_at(1, time(7, 59)), "Tuesday before eight"
    assert not general.quiet_at(1, time(8, 0)), "the window ends at eight"
    assert not general.quiet_at(2, time(14, 0)), "Wednesday afternoon"
    assert general.quiet_at(5, time(14, 0)), "a Saturday afternoon, the weekend being quiet"
    assert not EffectiveGeneral(QuietHours(enabled=False), 8).quiet_at(5, time(23, 0))
    weekdays_only = EffectiveGeneral(QuietHours(weekend=False), 8)
    assert not weekdays_only.quiet_at(6, time(14, 0))
