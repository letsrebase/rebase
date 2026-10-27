"""REB-737 and REB-738: a reminder leaves the app by email and by web push.

The pass is run for real, against a database of this module's own: users, attività, the
preferences and the push subscriptions are rows, the clock is the `now` the pass is handed,
and the transports are the seams the production ones sit behind. Email goes through the
real `ResendSender` over a fake HTTP call (`fake_resend`), so the body that would reach
Resend is read, and push through `RecordingPushSender`; `test_reminders_push.py` proves the
real `WebPushSender` against a push service of its own.

Dates: 2026-10-27 is a Tuesday and Rome is on UTC+1 after the clock change of the 25th, so
10:00 in Rome is 09:00 UTC. The default quiet hours are 20:00 to 08:00 and the weekend, and
the default summary hour is 08:00.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.mail import ResendSender
from pigrocrm.core.reminders.models import ReminderPreference
from pigrocrm.core.reminders.push import RecordingPushSender
from pigrocrm.core.reminders.push_subscriptions import PushSubscription
from pigrocrm.core.reminders.run import ReminderOutcome, ReminderRun
from pigrocrm.core.tenants.service import migrate_to_head

DB = "prova_promemoria_canali"
PUBLIC = "https://pigro.example.test/acme"
ROME = ZoneInfo("Europe/Rome")
TUESDAY = date(2026, 10, 27)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """A wall-clock time in Rome, as the aware instant the pass is handed."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ROME).astimezone(UTC)


class FakeResend:
    """The HTTP call under `ResendSender`: keeps every body it is handed and answers what
    Resend would, or a refusal."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []
        self.status = 200

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        if self.status == 200:
            self.bodies.append(json.loads(body))
        return self.status, b'{"id": "mail-1"}'

    @property
    def subjects(self) -> list[str]:
        return [str(body["subject"]) for body in self.bodies]


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
    shared_url = db_engine.url.render_as_string(hide_password=False)
    url = make_url(shared_url).set(database=DB)
    helper = Settings(database_url=shared_url, _env_file=None)  # type: ignore[call-arg]
    configured = Settings(
        database_url=url.render_as_string(hide_password=False),
        timezone="Europe/Rome",
        public_url=PUBLIC,
        _env_file=None,  # type: ignore[call-arg]
    )
    create_database_if_missing(helper, url)
    try:
        migrate_to_head(configured, configured.database_url)
        yield configured
    finally:
        drop_database(helper, url)


@pytest.fixture(scope="module")
def engine(settings: Settings) -> Iterator[Engine]:
    engine = create_engine(settings.database_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean(engine: Engine) -> Iterator[None]:
    yield
    with engine.begin() as connection:
        for table in (
            "reminder_delivery",
            "reminder_ack",
            "reminder_snooze",
            "reminder_summary_run",
            "reminder_job_run",
            "reminder_preference",
            "push_subscription",
            "attivita",
            "users",
        ):
            connection.execute(text(f"delete from {table}"))  # noqa: S608 - fixed names


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with session_factory(engine)() as session:
        yield session


@pytest.fixture
def owner(session: Session) -> User:
    """The person every reminder here is for. Not an admin: an admin is also told what is
    wrong with the space itself (§3.5), and these tests are about one attività each."""
    user = User(
        email="titolare@studio.example", nome="Titolare", ruolo="collaboratore", attivo=True
    )
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def resend() -> FakeResend:
    return FakeResend()


@pytest.fixture
def pusher() -> RecordingPushSender:
    return RecordingPushSender()


def _run(
    settings: Settings,
    engine: Engine,
    resend: FakeResend | None,
    pusher: RecordingPushSender | None,
    when: datetime,
    *,
    dry_run: bool = False,
    sender_enabled: bool = True,
) -> ReminderOutcome:
    sender = (
        ResendSender("re_test", "PigroCRM <ciao@example.test>", http=resend)
        if (resend is not None and sender_enabled)
        else None
    )
    run = ReminderRun(settings, sender=sender, pusher=pusher)
    return run.run_for_space(
        "acme",
        engine,
        today=when.astimezone(ROME).date(),
        now=when,
        dry_run=dry_run,
        public_url=PUBLIC,
    )


def _overdue(
    session: Session, user: User, days: int, today: date = TUESDAY, title: str = "Preventivo"
) -> UUID:
    """An open attività `days` past its due date: `serious` from day 1 and `critical`
    (urgent) from day 14."""
    row = Attivita(titolo=title, scadenza=today - timedelta(days=days), assegnata_a=user.id)
    session.add(row)
    session.commit()
    return row.id


def _due_today(
    session: Session, user: User, today: date = TUESDAY, title: str = "Chiamare Acme"
) -> UUID:
    """An open attività due today: a `warning`, never urgent."""
    row = Attivita(titolo=title, scadenza=today, assegnata_a=user.id)
    session.add(row)
    session.commit()
    return row.id


def _count(engine: Engine, table: str, where: str = "true") -> int:
    with engine.connect() as connection:
        return connection.execute(text(f"select count(*) from {table} where {where}")).scalar_one()  # noqa: S608


# -- email: urgent at once, the rest in the next summary -----------------------------------------


def test_an_urgent_reminder_is_mailed_at_once_and_not_again(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    attivita = _overdue(session, owner, days=20, title="Mandare il contratto firmato")

    first = _run(settings, engine, resend, None, at(TUESDAY, 10))

    assert first.emails == 1
    assert resend.subjects == ["Urgente: Attività scaduta: Mandare il contratto firmato"]
    sent = resend.bodies[0]
    assert sent["to"] == ["titolare@studio.example"]
    assert f"{PUBLIC}/app/activities?apri={attivita}" in str(sent["text"]), "it opens its record"
    assert f"{PUBLIC}/app/settings/notifications" in str(sent["text"]), (
        "and says where to change it"
    )

    again = _run(settings, engine, resend, None, at(TUESDAY, 10, 15))
    assert again.emails == 0
    assert len(resend.bodies) == 1, "neither arrives twice"
    assert _count(engine, "reminder_delivery", "channel = 'email' and mode = 'immediate'") == 1


def test_a_reminder_due_today_waits_for_the_daily_summary_and_arrives_once(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _due_today(session, owner, title="Chiamare Acme")

    # Before the summary hour (08:00) the day's summary has not started and nothing is urgent.
    assert _run(settings, engine, resend, None, at(TUESDAY, 7, 30)).emails == 0
    assert resend.bodies == []

    morning = _run(settings, engine, resend, None, at(TUESDAY, 8, 15))
    assert morning.emails == 1
    assert resend.subjects == ["Promemoria di oggi: 1 da vedere"]
    assert "Chiamare Acme" in str(resend.bodies[0]["text"])

    # A later pass that day does not repeat it: the ledger covers the reminder, and a
    # day with nothing undelivered sends nothing.
    assert _run(settings, engine, resend, None, at(TUESDAY, 9)).emails == 0
    assert _run(settings, engine, resend, None, at(TUESDAY, 13)).emails == 0
    assert len(resend.bodies) == 1
    assert _count(engine, "reminder_summary_run") == 1


def test_the_summary_is_severest_first_and_one_mail(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _due_today(session, owner, title="Prima cosa leggera")
    _overdue(session, owner, days=3, title="Cosa in ritardo")

    assert _run(settings, engine, resend, None, at(TUESDAY, 8, 5)).emails == 1

    body = str(resend.bodies[0]["text"])
    assert resend.subjects == ["Promemoria di oggi: 2 da vedere"]
    assert body.index("Cosa in ritardo") < body.index("Prima cosa leggera"), "grave before warning"
    assert _count(engine, "reminder_delivery", "channel = 'email' and mode = 'summary'") == 2


def test_urgent_at_once_and_the_rest_in_the_summary_are_two_mails(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")
    _due_today(session, owner, title="Chiamare Acme")

    _run(settings, engine, resend, None, at(TUESDAY, 8, 5))

    assert sorted(resend.subjects) == [
        "Promemoria di oggi: 1 da vedere",
        "Urgente: Attività scaduta: Contratto firmato",
    ]
    summary = next(b for b in resend.bodies if str(b["subject"]).startswith("Promemoria"))
    assert "Contratto firmato" not in str(summary["text"]), "the urgent one is not said twice"


def test_a_summary_reminder_that_climbs_to_urgent_is_mailed_again_at_once(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    attivita = Attivita(
        titolo="Fattura da rivedere",
        scadenza=TUESDAY - timedelta(days=3),
        assegnata_a=owner.id,
    )
    session.add(attivita)
    session.commit()
    _run(settings, engine, resend, None, at(TUESDAY, 8, 5))
    assert resend.subjects == ["Promemoria di oggi: 1 da vedere"]

    # Seventeen days on (a Friday) it is 20 days late: `critical`, which is urgent.
    later = TUESDAY + timedelta(days=17)
    _run(settings, engine, resend, None, at(later, 9))

    assert resend.subjects[-1] == "Urgente: Attività scaduta: Fattura da rivedere"


# -- quiet hours, a failed send and no transport -------------------------------------------------


def test_quiet_hours_hold_urgent_mail_until_they_end(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")

    assert _run(settings, engine, resend, None, at(TUESDAY, 21)).emails == 0
    assert _run(settings, engine, resend, None, at(TUESDAY, 23, 45)).emails == 0
    assert resend.bodies == []

    wednesday = TUESDAY + timedelta(days=1)
    assert _run(settings, engine, resend, None, at(wednesday, 7, 45)).emails == 0
    assert _run(settings, engine, resend, None, at(wednesday, 8, 0)).emails == 1
    assert resend.subjects == ["Urgente: Attività scaduta: Contratto firmato"]


def test_the_weekend_holds_urgent_mail_until_monday_morning(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    friday = date(2026, 10, 30)
    _overdue(session, owner, days=20, today=friday, title="Contratto firmato")

    assert _run(settings, engine, resend, None, at(friday, 21)).emails == 0
    assert _run(settings, engine, resend, None, at(date(2026, 10, 31), 11)).emails == 0
    assert _run(settings, engine, resend, None, at(date(2026, 11, 1), 11)).emails == 0

    assert _run(settings, engine, resend, None, at(date(2026, 11, 2), 8, 30)).emails == 1


def test_a_failed_send_writes_nothing_and_the_next_pass_sends_it(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")
    resend.status = 500

    failed = _run(settings, engine, resend, None, at(TUESDAY, 10))

    assert failed.emails == 0
    assert _count(engine, "reminder_delivery", "channel = 'email'") == 0, "undelivered"
    assert _count(engine, "reminder_delivery", "channel = 'in_app'") == 1, "the list is its own"

    resend.status = 200
    assert _run(settings, engine, resend, None, at(TUESDAY, 10, 15)).emails == 1
    assert resend.subjects == ["Urgente: Attività scaduta: Contratto firmato"]


def test_a_failed_summary_does_not_mark_the_day_and_is_tried_again(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _due_today(session, owner)
    resend.status = 500
    _run(settings, engine, resend, None, at(TUESDAY, 8, 15))
    assert _count(engine, "reminder_summary_run") == 0

    resend.status = 200
    assert _run(settings, engine, resend, None, at(TUESDAY, 8, 30)).emails == 1
    assert _count(engine, "reminder_summary_run") == 1


def test_with_no_transport_nothing_is_sent_or_recorded_and_the_key_catches_up(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")

    none = _run(settings, engine, resend, None, at(TUESDAY, 10), sender_enabled=False)

    assert none.emails == 0 and none.delivered == 1, "the app still lists it"
    assert _count(engine, "reminder_delivery", "channel = 'email'") == 0
    assert _run(settings, engine, resend, None, at(TUESDAY, 10, 15)).emails == 1


def test_a_rehearsal_counts_the_mail_and_sends_and_writes_nothing(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20)

    rehearsal = _run(settings, engine, resend, None, at(TUESDAY, 10), dry_run=True)

    assert rehearsal.emails == 1
    assert resend.bodies == []
    assert _count(engine, "reminder_delivery") == 0


# -- what a person chose -------------------------------------------------------------------------


def test_a_person_who_switched_email_off_is_not_mailed(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20)
    session.add(
        ReminderPreference(user_id=owner.id, type="attivita_overdue", channels=["push", "slack"])
    )
    session.commit()

    assert _run(settings, engine, resend, None, at(TUESDAY, 10)).emails == 0
    assert resend.bodies == []


def test_a_person_who_wants_everything_in_the_summary_gets_the_urgent_one_there(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")
    session.add(ReminderPreference(user_id=owner.id, type="attivita_overdue", email_mode="summary"))
    session.commit()

    assert _run(settings, engine, resend, None, at(TUESDAY, 10)).emails == 1
    assert resend.subjects == ["Promemoria di oggi: 1 da vedere"]


def test_the_summary_hour_is_the_persons_own(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _due_today(session, owner)
    session.add(ReminderPreference(user_id=owner.id, summary_hour=14))
    session.commit()

    assert _run(settings, engine, resend, None, at(TUESDAY, 13, 45)).emails == 0
    assert _run(settings, engine, resend, None, at(TUESDAY, 14, 0)).emails == 1


def test_a_reminder_the_person_acknowledged_is_not_mailed(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    attivita = _overdue(session, owner, days=20)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into reminder_ack (id, key, type, user_id, severity_rank) "
                "values (gen_random_uuid(), :key, 'attivita_overdue', :user, 3)"
            ),
            {"key": f"attivita_overdue:{attivita}", "user": owner.id},
        )

    assert _run(settings, engine, resend, None, at(TUESDAY, 10)).emails == 0


def test_every_value_a_person_typed_is_escaped_in_the_html_and_the_subject_is_one_line(
    settings: Settings, engine: Engine, session: Session, owner: User, resend: FakeResend
) -> None:
    _overdue(session, owner, days=20, title="Fattura <script>alert(1)</script>\nBcc: x@y.z")

    _run(settings, engine, resend, None, at(TUESDAY, 10))

    sent = resend.bodies[0]
    assert "<script>" not in str(sent["html"])
    assert "&lt;script&gt;" in str(sent["html"])
    assert "\n" not in str(sent["subject"])


# -- push ----------------------------------------------------------------------------------------


def _subscribe(
    session: Session, user: User, endpoint: str = "https://push.example.test/a", *, at_: datetime
) -> None:
    session.add(
        PushSubscription(
            user_id=user.id,
            endpoint=endpoint,
            p256dh="p",
            auth="a",
            created_at=at_,
            updated_at=at_,
        )
    )
    session.commit()


def test_a_reminder_is_pushed_once_to_the_browser_that_subscribed_before_it(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    attivita = _overdue(session, owner, days=20, title="Contratto firmato")

    first = _run(settings, engine, None, pusher, at(TUESDAY, 10))

    assert first.pushes == 1
    target, message = pusher.sent[0]
    assert target.endpoint == "https://push.example.test/a"
    assert message.title == "Attività scaduta: Contratto firmato"
    assert message.url == f"/acme/app/activities?apri={attivita}", "on the space's own path"
    assert message.tag == f"attivita_overdue:{attivita}"
    assert message.urgent is True

    assert _run(settings, engine, None, pusher, at(TUESDAY, 10, 15)).pushes == 0
    assert len(pusher.sent) == 1
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 1


def test_a_browser_announces_nothing_that_reached_the_list_before_it_subscribed(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _overdue(session, owner, days=20, title="Contratto firmato")
    _run(settings, engine, None, pusher, at(TUESDAY, 10))  # listed, nobody subscribed
    _subscribe(session, owner, at_=at(TUESDAY, 11))

    assert _run(settings, engine, None, pusher, at(TUESDAY, 12)).pushes == 0
    assert pusher.sent == [], "turning push on does not send the backlog"


def test_push_is_held_by_quiet_hours_and_sent_after_them(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20)

    assert _run(settings, engine, None, pusher, at(TUESDAY, 22)).pushes == 0
    wednesday = TUESDAY + timedelta(days=1)
    assert _run(settings, engine, None, pusher, at(wednesday, 8, 0)).pushes == 1


def test_a_subscription_the_service_reports_gone_is_deleted_and_nothing_is_recorded(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20)
    pusher.outcomes["https://push.example.test/a"] = "gone"

    result = _run(settings, engine, None, pusher, at(TUESDAY, 10))

    assert result.pushes == 0
    assert _count(engine, "push_subscription") == 0
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 0


def test_a_failed_push_is_tried_again_by_the_next_pass(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20)
    pusher.outcomes["https://push.example.test/a"] = "failed"
    _run(settings, engine, None, pusher, at(TUESDAY, 10))
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 0
    assert _count(engine, "push_subscription") == 1, "a failure is not a reason to forget it"

    pusher.outcomes.clear()
    assert _run(settings, engine, None, pusher, at(TUESDAY, 10, 15)).pushes == 1


def test_one_dead_browser_does_not_cost_the_live_one_its_notification(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, "https://push.example.test/dead", at_=at(TUESDAY, 9))
    _subscribe(session, owner, "https://push.example.test/live", at_=at(TUESDAY, 9, 1))
    _overdue(session, owner, days=20)
    pusher.outcomes["https://push.example.test/dead"] = "gone"

    result = _run(settings, engine, None, pusher, at(TUESDAY, 10))

    assert result.pushes == 1
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 1
    assert _count(engine, "push_subscription", "endpoint like '%/live'") == 1
    assert _count(engine, "push_subscription", "endpoint like '%/dead'") == 0


def test_a_person_who_switched_push_off_is_not_pushed(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20)
    session.add(ReminderPreference(user_id=owner.id, type="attivita_overdue", channels=["email"]))
    session.commit()

    assert _run(settings, engine, None, pusher, at(TUESDAY, 10)).pushes == 0


def test_a_reminder_is_pushed_only_to_the_person_it_is_for(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    other = User(email="altro@studio.example", nome="Altro", ruolo="collaboratore", attivo=True)
    session.add(other)
    session.commit()
    _subscribe(session, other, "https://push.example.test/other", at_=at(TUESDAY, 9))
    _subscribe(session, owner, "https://push.example.test/owner", at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20)

    _run(settings, engine, None, pusher, at(TUESDAY, 10))

    assert [target.endpoint for target, _ in pusher.sent] == ["https://push.example.test/owner"]


class _RaisingPusher:
    def send(self, target: object, message: object) -> str:
        raise RuntimeError("a bug nobody foresaw: postgresql://user:secret@host/db")


def test_a_channel_that_raises_does_not_cost_the_pass_its_writes_or_the_other_channel(
    settings: Settings,
    engine: Engine,
    session: Session,
    owner: User,
    resend: FakeResend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _subscribe(session, owner, at_=at(TUESDAY, 9))
    _overdue(session, owner, days=20, title="Contratto firmato")

    result = _run(settings, engine, resend, _RaisingPusher(), at(TUESDAY, 10))  # type: ignore[arg-type]

    assert result.delivered == 1, "the list is written whatever a channel does"
    assert result.pushes == 0
    assert result.emails == 1, "and the other channel still sent"
    assert _count(engine, "reminder_job_run") == 1, "the clock too"
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 0, "so push is retried"
    assert "RuntimeError" in caplog.text and "secret" not in caplog.text, "the type, never the text"


def test_a_reminder_that_has_been_listed_longer_than_a_push_service_holds_it_is_not_news(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    """Turning a channel back on, or a long outage, must not send the backlog."""
    _subscribe(session, owner, at_=at(TUESDAY, 9) - timedelta(days=3))
    attivita = _overdue(session, owner, days=20)
    with engine.begin() as connection:
        connection.execute(
            text(
                "insert into reminder_delivery "
                "(id, key, type, user_id, channel, mode, period, severity_rank, delivered_at) "
                "values (gen_random_uuid(), :key, 'attivita_overdue', :user, 'in_app', "
                "'immediate', :period, 3, :at)"
            ),
            {
                "key": f"attivita_overdue:{attivita}",
                "user": owner.id,
                "period": TUESDAY,
                "at": at(TUESDAY, 10) - timedelta(hours=25),
            },
        )

    assert _run(settings, engine, None, pusher, at(TUESDAY, 10)).pushes == 0
    assert pusher.sent == []


def test_a_browser_a_second_person_took_over_gets_them_only_what_comes_after(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    other = User(email="altro@studio.example", nome="Altro", ruolo="collaboratore", attivo=True)
    session.add(other)
    session.commit()
    _overdue(session, other, days=20, title="Cosa di Altro")
    _run(settings, engine, None, pusher, at(TUESDAY, 10))  # listed for Altro, nobody subscribed
    _subscribe(session, owner, "https://push.example.test/a", at_=at(TUESDAY, 9))
    # The browser moves to Altro at 11:00, as `PushSubscriptionService.subscribe` does.
    with engine.begin() as connection:
        connection.execute(
            text("update push_subscription set user_id = :user, created_at = :at"),
            {"user": other.id, "at": at(TUESDAY, 11)},
        )

    assert _run(settings, engine, None, pusher, at(TUESDAY, 12)).pushes == 0
    assert pusher.sent == []


def test_a_reminder_held_by_the_weekend_is_still_news_on_monday_morning(
    settings: Settings, engine: Engine, session: Session, owner: User, pusher: RecordingPushSender
) -> None:
    friday = date(2026, 10, 30)
    _subscribe(session, owner, at_=at(friday, 9))
    _overdue(session, owner, days=20, today=friday)
    assert _run(settings, engine, None, pusher, at(friday, 21)).pushes == 0  # listed, held
    assert _run(settings, engine, None, pusher, at(date(2026, 10, 31), 11)).pushes == 0

    monday = _run(settings, engine, None, pusher, at(date(2026, 11, 2), 8, 30))

    assert monday.pushes == 1, "listed in quiet hours: held, not left behind"


def test_what_a_browser_took_before_the_deadline_is_recorded_and_not_sent_again(
    settings: Settings,
    engine: Engine,
    session: Session,
    owner: User,
    pusher: RecordingPushSender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import itertools

    import pigrocrm.core.reminders.push as push_module

    _subscribe(session, owner, "https://push.example.test/one", at_=at(TUESDAY, 9))
    _subscribe(session, owner, "https://push.example.test/two", at_=at(TUESDAY, 9, 1))
    _overdue(session, owner, days=20)
    # The deadline is set (0), the first browser is reached (0), the second finds it passed.
    clock = itertools.chain([0.0, 0.0], itertools.repeat(10_000.0))
    monkeypatch.setattr(push_module.time, "monotonic", lambda: next(clock))

    first = _run(settings, engine, None, pusher, at(TUESDAY, 10))

    assert first.pushes == 1 and len(pusher.sent) == 1
    assert _count(engine, "reminder_delivery", "channel = 'push'") == 1, "recorded"
    monkeypatch.undo()
    assert _run(settings, engine, None, pusher, at(TUESDAY, 10, 15)).pushes == 0
