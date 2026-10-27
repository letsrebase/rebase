"""Reminders on Slack: a direct message per person, a post to the space's channel
(REB-740), through the real pass against a fake of Slack's HTTP surface.

The proof of the card is the first test: an overdue attività reaches its assignee by direct
message once, and not again on the next pass. The pass is `ReminderRun.run_for_space` over a
database of this module's own (the pass commits, so it cannot run inside the rolled-back
`db_session`), migrated to head as `test_cli_reminders.py` does it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fakes.fake_slack import BOT_TOKEN, FakeSlack
from fakes.slack_fixtures import actor_of, connect, slack_client, slack_settings
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from pigrocrm.core.attivita.models import Attivita
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.sidecar import create_database_if_missing, drop_database
from pigrocrm.core.reminders.models import (
    ReminderDelivery,
    ReminderJobRun,
    ReminderPreference,
)
from pigrocrm.core.reminders.run import ReminderRun
from pigrocrm.core.slack.models import SlackChannelPost, SlackUserLink, SlackWorkspace
from pigrocrm.core.slack.service import SlackService
from pigrocrm.core.tenants import space_base_settings
from pigrocrm.core.tenants.service import migrate_to_head

DB = "prova_slack_promemoria"
PUBLIC_URL = "https://crm.example.it"
NO_QUIET = {"enabled": False, "start": "20:00", "end": "08:00", "weekend": False}


@pytest.fixture(scope="module")
def settings(db_engine: Engine) -> Iterator[Settings]:
    shared = db_engine.url.render_as_string(hide_password=False)
    url = make_url(shared).set(database=DB)
    helper = Settings(database_url=shared, _env_file=None)  # type: ignore[call-arg]
    configured = slack_settings(
        database_url=url.render_as_string(hide_password=False), timezone="Europe/Rome"
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


@dataclass
class World:
    engine: Engine
    settings: Settings
    fake: FakeSlack
    admin: UUID
    ada: UUID
    today: date

    def session(self) -> Session:
        return session_factory(self.engine)()

    def connect(self, settings: Settings | None = None) -> None:
        with self.session() as session:
            admin = session.get(User, self.admin)
            assert admin is not None
            connect(session, self.fake, admin, settings or self.settings)

    def attivita(self, titolo: str, *, days_late: int = 3, assignee: UUID | None = None) -> UUID:
        with self.session() as session:
            row = Attivita(
                titolo=titolo,
                scadenza=self.today - timedelta(days=days_late),
                assegnata_a=assignee or self.ada,
            )
            session.add(row)
            session.commit()
            return row.id

    def preference(self, *, user: UUID | None = None, **columns: object) -> None:
        with self.session() as session:
            session.add(ReminderPreference(user_id=user, **columns))
            session.commit()

    def pass_(
        self,
        *,
        slug: str = "root",
        now: datetime | None = None,
        today: date | None = None,
        dry_run: bool = False,
        settings: Settings | None = None,
    ):  # noqa: ANN201
        run = ReminderRun(settings or self.settings, slack=slack_client(self.fake))
        return run.run_for_space(
            slug,
            self.engine,
            today=today,
            now=now,
            dry_run=dry_run,
            public_url=PUBLIC_URL,
        )

    def deliveries(self, channel: str = "slack") -> list[ReminderDelivery]:
        with self.session() as session:
            return list(
                session.scalars(select(ReminderDelivery).where(ReminderDelivery.channel == channel))
            )


@pytest.fixture
def world(settings: Settings, engine: Engine) -> Iterator[World]:
    """Two people in an otherwise empty space and a Slack with both in it. Every table the
    pass writes is emptied first: the pass commits, and one test must not see another's."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE slack_channel_posts, slack_user_links, slack_workspaces, "
                "reminder_delivery, reminder_ack, reminder_snooze, reminder_preference, "
                "reminder_job_run, attivita CASCADE"
            )
        )
        connection.execute(text("UPDATE users SET attivo = false"))
    with session_factory(engine)() as session:
        admin = User(
            email=f"mario-{uuid4().hex[:6]}@example.it",
            nome="Mario",
            password_hash="x",
            ruolo="admin",
        )
        ada = User(
            email=f"ada-{uuid4().hex[:6]}@example.it",
            nome="Ada",
            password_hash="x",
            ruolo="collaboratore",
        )
        session.add_all([admin, ada])
        session.commit()
        fake = FakeSlack()
        fake.add_member(admin.email, "UMARIO")
        fake.add_member(ada.email, "UADA")
        fake.add_channel("C1", "promemoria")
        yield World(engine, settings, fake, admin.id, ada.id, today_local(settings))


def _text_of(fake: FakeSlack, channel: str) -> str:
    return "\n".join(text_ for target, text_ in fake.posts if target == channel)


# --- the card's proof --------------------------------------------------------------------


def test_an_overdue_attivita_reaches_its_assignee_by_direct_message_once(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")

    first = world.pass_()
    assert first.slacks == 1, first
    assert [target for target, _ in world.fake.posts] == ["DUADA"]
    message = _text_of(world.fake, "DUADA")
    assert "Richiamare Rossi" in message
    assert "Hai un promemoria" in message
    assert f"{PUBLIC_URL}/app/activities?apri=" in message
    # The direct message was opened with the member's id, and posted with the bot's token.
    (opened,) = world.fake.calls("conversations.open")
    assert opened.form == {"users": "UADA"}
    assert opened.bearer == BOT_TOKEN
    (row,) = world.deliveries()
    assert (row.channel, row.mode, row.period) == ("slack", "immediate", world.today)

    again = world.pass_()
    assert again.slacks == 0
    assert len(world.fake.posts) == 1
    assert len(world.fake.calls("chat.postMessage")) == 1


def test_a_daily_reminder_is_sent_again_the_next_morning(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")
    world.pass_()
    tomorrow = datetime.now(UTC) + timedelta(days=1)

    world.pass_(today=world.today + timedelta(days=1), now=tomorrow)

    # (The admin is also told, that morning, that the pass itself ran late.)
    assert [target for target, _ in world.fake.posts].count("DUADA") == 2


def test_the_reminder_a_person_switched_off_for_slack_is_not_sent(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.preference(user=world.ada, type="attivita_overdue", channels=["push"])
    world.connect()
    world.attivita("Richiamare Rossi")

    outcome = world.pass_()

    assert outcome.slacks == 0
    assert world.fake.posts == []
    # Still in the app: the in-app list is the source of truth.
    assert outcome.delivered == 1


def test_a_space_that_turned_slack_off_by_default_does_not_send_either(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.preference(type="attivita_overdue", channels=["push", "email"])
    world.connect()
    world.attivita("Richiamare Rossi")
    assert world.pass_().slacks == 0


def test_quiet_hours_hold_slack_and_the_first_pass_after_them_sends(world: World) -> None:
    # The default window: the weekend. A Saturday, then the Monday after it.
    # Both after the real now, whatever day the suite runs: a pass that lists a reminder
    # before the connection was made would be announcing backlog, which Slack does not.
    ahead = datetime.now(UTC) + timedelta(days=1)
    saturday = (ahead + timedelta(days=(5 - ahead.weekday()) % 7)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    monday = saturday + timedelta(days=2)
    world.connect()
    world.attivita("Richiamare Rossi", days_late=3)
    # The attività is overdue by three days on the Saturday's date.
    with world.session() as session:
        session.execute(
            text("UPDATE attivita SET scadenza = :d"), {"d": saturday.date() - timedelta(days=3)}
        )
        session.commit()

    held = world.pass_(today=saturday.date(), now=saturday)
    assert held.slacks == 0
    assert held.delivered == 1  # in the app at once
    assert world.fake.posts == []
    assert world.deliveries() == []

    world.pass_(today=monday.date(), now=monday)
    # (The admin is also told that the pass itself ran late over the weekend.)
    assert [target for target, _ in world.fake.posts].count("DUADA") == 1


def test_a_reminder_that_was_open_before_the_workspace_was_connected_is_not_sent(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.attivita("Richiamare Rossi")
    world.pass_()  # in the app only: no workspace yet
    world.connect()

    outcome = world.pass_()

    assert outcome.slacks == 0
    assert world.fake.posts == []


def test_a_person_not_in_the_workspace_costs_one_lookup_a_day_and_hears_nothing(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    del world.fake.members[next(iter(m for m in world.fake.members if m.startswith("ada-")))]
    world.connect()
    lookups = len(world.fake.calls("users.lookupByEmail"))
    world.attivita("Richiamare Rossi")

    first = world.pass_()
    second = world.pass_()

    assert (first.slacks, second.slacks) == (0, 0)
    assert world.fake.posts == []
    assert len(world.fake.calls("users.lookupByEmail")) == lookups  # `none` is young


def test_a_person_who_joined_slack_later_is_matched_in_the_pass_and_told_what_is_new(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    ada_email = next(m for m in world.fake.members if m.startswith("ada-"))
    ada_member = world.fake.members.pop(ada_email)
    world.connect()
    # A day passes; Ada joins Slack; the unmatched row is old enough to be asked again.
    world.fake.members[ada_email] = ada_member
    with world.session() as session:
        session.execute(text("UPDATE slack_user_links SET checked_at = now() - interval '2 days'"))
        session.commit()
    world.attivita("Richiamare Rossi")

    outcome = world.pass_()

    assert outcome.slacks == 1
    assert [target for target, _ in world.fake.posts] == ["DUADA"]
    with world.session() as session:
        link = session.execute(
            select(SlackUserLink).where(SlackUserLink.user_id == world.ada)
        ).scalar_one()
        assert (link.source, link.slack_user_id) == ("email", "UADA")
        assert link.matched_at is not None


def test_one_message_carries_every_reminder_of_a_person_severest_first(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Poco in ritardo", days_late=1)
    world.attivita("Molto in ritardo", days_late=30)

    outcome = world.pass_()

    assert outcome.slacks == 1
    (message,) = [text_ for _, text_ in world.fake.posts]
    assert "Hai 2 promemoria" in message
    assert message.index("Molto in ritardo") < message.index("Poco in ritardo")
    assert message.index("Urgente") < message.index("Grave")


def test_a_message_carries_at_most_twenty_and_the_rest_follow_in_the_next_pass(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    for number in range(25):
        world.attivita(f"Attività {number:02d}")

    first = world.pass_()
    second = world.pass_()
    third = world.pass_()

    assert (first.slacks, second.slacks, third.slacks) == (1, 1, 0)
    assert len(world.deliveries()) == 25
    first_text, second_text = (text_ for _, text_ in world.fake.posts)
    assert first_text.count("•") == 20
    assert second_text.count("•") == 5


def test_a_title_cannot_mention_a_channel_or_inject_a_link(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("<!channel> chiama <https://evil.example|qui> & basta")

    world.pass_()

    (message,) = [text_ for _, text_ in world.fake.posts]
    assert "<!channel>" not in message
    assert "evil.example|qui>" not in message.replace("&lt;https://evil.example|qui&gt;", "")
    assert "&lt;!channel&gt;" in message
    assert "&amp; basta" in message


def test_a_rehearsal_counts_the_message_and_sends_and_writes_nothing(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")

    rehearsal = world.pass_(dry_run=True)

    assert rehearsal.slacks == 1
    assert world.fake.posts == []
    assert world.deliveries() == []
    assert world.pass_().slacks == 1  # and the real pass still sends


# --- failures write nothing ---------------------------------------------------------------


def test_a_message_slack_refused_is_tried_again_next_pass(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")
    world.fake.fail["chat.postMessage"] = ["is_archived"]

    failed = world.pass_()
    assert failed.slacks == 0
    assert world.deliveries() == []

    retried = world.pass_()
    assert retried.slacks == 1
    assert len(world.deliveries()) == 1


def test_slack_down_during_the_pass_sends_nothing_records_nothing_and_does_not_raise(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")
    world.fake.fail_status = [503] * 12

    outcome = world.pass_()

    assert outcome.esito == "eseguito"
    assert outcome.slacks == 0
    assert outcome.delivered == 1  # the app still has it
    assert world.deliveries() == []
    world.fake.fail_status.clear()
    assert world.pass_().slacks == 1


def test_a_token_slack_revoked_ends_the_pass_and_asks_the_admin_to_reconnect(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")
    world.fake.revoked = True

    outcome = world.pass_()

    assert outcome.slacks == 0
    with world.session() as session:
        workspace = session.execute(select(SlackWorkspace)).scalar_one()
        assert workspace.status == "revoked"
        assert workspace.last_error is not None and "ricollega Slack" in workspace.last_error
    # And the next pass does not call Slack at all.
    calls = len(world.fake.requests)
    assert world.pass_().slacks == 0
    assert len(world.fake.requests) == calls


def test_no_workspace_no_app_or_no_client_is_a_pass_like_before(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.attivita("Richiamare Rossi")
    # No workspace connected.
    assert world.pass_().slacks == 0
    assert world.fake.requests == []

    world.connect()
    assert world.pass_(settings=slack_settings(slack_client_id="")).slacks == 0

    # A run with no Slack transport at all.
    run = ReminderRun(world.settings)
    outcome = run.run_for_space("root", world.engine, public_url=PUBLIC_URL)
    assert outcome.slacks == 0
    assert world.fake.posts == []


def test_a_disconnected_workspace_receives_nothing(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    with world.session() as session:
        from pigrocrm.core.slack.oauth import SlackOAuthService

        admin = session.get(User, world.admin)
        assert admin is not None
        SlackOAuthService(
            session, settings=world.settings, client=slack_client(world.fake)
        ).disconnect(actor_of(admin))
    world.attivita("Richiamare Rossi")

    assert world.pass_().slacks == 0
    assert world.fake.posts == []


# --- a space's own key -------------------------------------------------------------------


def test_the_pass_of_a_space_opens_the_token_sealed_for_that_space_only(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    space = space_base_settings(world.settings, "studio-uno")
    world.connect(space)
    world.attivita("Richiamare Rossi")

    # The root's pass cannot open a token sealed for a space, and does not raise.
    assert world.pass_(slug="root").slacks == 0
    assert world.fake.posts == []
    assert world.pass_(slug="studio-due").slacks == 0
    assert world.pass_(slug="studio-uno").slacks == 1


# --- the space's channel -------------------------------------------------------------------


def _late_job(world: World) -> None:
    """`job_late` is raised for the admins when the reminder pass last ran long ago."""
    with world.session() as session:
        session.add(ReminderJobRun(job="reminders", ran_at=datetime.now(UTC) - timedelta(hours=1)))
        session.commit()


def _choose_channel(world: World, channel_id: str | None = "C1") -> None:
    with world.session() as session:
        admin = session.get(User, world.admin)
        assert admin is not None
        SlackService(session, settings=world.settings, client=slack_client(world.fake)).set_channel(
            actor_of(admin), channel_id
        )


def test_a_space_wide_reminder_is_also_posted_to_the_chosen_channel_once(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _choose_channel(world)
    _late_job(world)

    first = world.pass_()

    # The admin's own direct message, and one post to the channel.
    assert first.slacks == 2, first
    targets = sorted(target for target, _ in world.fake.posts)
    assert targets == ["C1", "DUMARIO"]
    post = _text_of(world.fake, "C1")
    assert "per lo spazio" in post
    assert "Il passaggio dei promemoria è in ritardo" in post
    with world.session() as session:
        (row,) = session.scalars(select(SlackChannelPost)).all()
        assert (row.channel_id, row.type, row.severity_rank) == ("C1", "job_late", 2)

    again = world.pass_()
    assert again.slacks == 0
    assert len(world.fake.posts) == 2


def test_a_personal_reminder_is_never_posted_to_the_channel(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _choose_channel(world)
    world.attivita("Richiamare Rossi")

    world.pass_()

    assert [target for target, _ in world.fake.posts] == ["DUADA"]


def test_the_channel_post_follows_the_spaces_defaults_not_a_persons(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.preference(type="job_late", channels=["push"])  # the space: no Slack for this type
    world.connect()
    _choose_channel(world)
    _late_job(world)

    world.pass_()

    assert "C1" not in [target for target, _ in world.fake.posts]


def test_no_channel_chosen_no_post(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _late_job(world)

    world.pass_()

    assert [target for target, _ in world.fake.posts] == ["DUMARIO"]


def test_a_channel_post_climbs_with_the_severity_and_ends_with_the_cause(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _choose_channel(world)
    _late_job(world)
    world.pass_()  # `serious`, three hours later `critical`
    with world.session() as session:
        session.execute(text("UPDATE reminder_job_run SET ran_at = now() - interval '4 hours'"))
        session.commit()

    climbed = world.pass_()

    assert climbed.slacks == 2  # the admin's message and the channel's, both at `critical`
    with world.session() as session:
        (row,) = session.scalars(select(SlackChannelPost)).all()
        assert row.severity_rank == 3
    # The pass wrote the job clock, so the cause has cleared: the occurrence ends.
    world.pass_()
    with world.session() as session:
        assert session.scalars(select(SlackChannelPost)).all() == []


def test_a_post_the_channel_refused_is_tried_again(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _choose_channel(world)
    _late_job(world)
    world.fake.fail["chat.postMessage"] = ["is_archived", "channel_not_found"]

    first = world.pass_()
    assert first.slacks == 0  # neither the admin's message nor the post got through
    with world.session() as session:
        assert session.scalars(select(SlackChannelPost)).all() == []

    # The admin's message is covered by the lateness the first pass wrote away, so the
    # channel post is what the second pass has to send.
    with world.session() as session:
        session.execute(text("DELETE FROM reminder_job_run"))
        session.add(ReminderJobRun(job="reminders", ran_at=datetime.now(UTC) - timedelta(hours=1)))
        session.commit()
    second = world.pass_()
    assert second.slacks == 2
    assert "C1" in [target for target, _ in world.fake.posts]
    with world.session() as session:
        assert len(session.scalars(select(SlackChannelPost)).all()) == 1


# --- the command's line ------------------------------------------------------------------


def test_the_roots_label_is_the_one_the_command_uses() -> None:
    import pigrocrm.core.cli as cli
    from pigrocrm.core.reminders import run

    assert run.ROOT_LABEL == cli.ROOT_LABEL


def test_the_line_counts_slack_beside_push_and_email_only_when_it_sent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import pigrocrm.core.cli as cli
    from pigrocrm.core.reminders.run import ReminderOutcome

    cli._stampa_promemoria(
        ReminderOutcome("root", "eseguito", raised=3, delivered=2, ended=0, slacks=2, emails=1),
        dry_run=False,
    )
    cli._stampa_promemoria(
        ReminderOutcome("root", "eseguito", raised=3, delivered=2, ended=0), dry_run=True
    )

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "root: promemoria 3, nuovi 2, chiusi 0, slack 2, email 1"
    assert lines[1] == "root: promemoria 3, nuovi 2, chiusi 0 (prova)"


# --- a channel's write cannot take the pass down ---------------------------------------


def test_a_write_a_channel_asked_for_that_fails_loses_only_itself(db_session: Session) -> None:
    from pigrocrm.core.reminders.dispatch import ChannelResult
    from pigrocrm.core.reminders.engine import Evaluation, ReminderEngine
    from pigrocrm.core.reminders.ledger import ReminderLedger

    def refused(session: Session) -> None:
        for _ in range(2):  # the second insert breaks the unique key
            session.add(ReminderJobRun(job="duplicated", ran_at=datetime.now(UTC)))
            session.flush()

    def fine(session: Session) -> None:
        session.add(ReminderJobRun(job="kept", ran_at=datetime.now(UTC)))

    engine = ReminderEngine(db_session, slack_settings())
    result = engine.record_pass(
        Evaluation((), frozenset(), frozenset()),
        job="reminders",
        sent=ChannelResult(writes=[refused, fine]),
    )

    assert result.raised == 0
    ledger = ReminderLedger(db_session)
    assert ledger.last_run("reminders") is not None  # the pass's own write stands
    assert ledger.last_run("kept") is not None  # a later hook still runs
    assert ledger.last_run("duplicated") is None  # and the one that failed left nothing


# --- the findings of the reviewers on the first round ------------------------------------


def test_the_channel_follows_what_the_space_raised_not_what_one_admin_still_has_open(
    world: World,
) -> None:
    from pigrocrm.core.reminders.models import ReminderAck

    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    _late_job(world)
    world.pass_()  # no channel yet: the admin is told in the app and by message
    with world.session() as session:
        delivery = session.scalars(
            select(ReminderDelivery).where(ReminderDelivery.channel == "in_app")
        ).first()
        assert delivery is not None
        session.add(
            ReminderAck(key=delivery.key, type=delivery.type, user_id=world.admin, severity_rank=3)
        )
        session.commit()
    _choose_channel(world)
    with world.session() as session:
        session.execute(text("DELETE FROM reminder_job_run"))
        session.add(ReminderJobRun(job="reminders", ran_at=datetime.now(UTC) - timedelta(hours=1)))
        session.commit()

    outcome = world.pass_()

    # The only admin acknowledged it, so nothing is due to them; the channel is the space's.
    assert outcome.slacks == 1
    assert [target for target, _ in world.fake.posts].count("C1") == 1


def test_a_person_unmatched_for_a_day_is_looked_up_even_when_nothing_new_reached_them(
    world: World,
) -> None:
    world.preference(quiet_hours=NO_QUIET)
    ada_email = next(m for m in world.fake.members if m.startswith("ada-"))
    ada_member = world.fake.members.pop(ada_email)
    world.connect()
    world.attivita("Richiamare Rossi")
    world.pass_()  # in the app; Ada is not in Slack
    world.fake.members[ada_email] = ada_member
    with world.session() as session:
        session.execute(text("UPDATE slack_user_links SET checked_at = now() - interval '2 days'"))
        session.commit()

    outcome = world.pass_()

    assert outcome.slacks == 0  # what was open before is not announced
    assert world.fake.posts == []
    with world.session() as session:
        link = session.execute(
            select(SlackUserLink).where(SlackUserLink.user_id == world.ada)
        ).scalar_one()
        assert (link.source, link.slack_user_id) == ("email", "UADA")


def test_what_a_pass_writes_for_an_older_connection_is_ignored(world: World) -> None:
    from pigrocrm.core.reminders import slack as channel

    world.connect()
    with world.session() as session:
        workspace = session.execute(select(SlackWorkspace)).scalar_one()
        stale = (workspace.id, b"nonce-of-an-older-install")
        channel._revoke(session, workspace.id, b"nonce-of-an-older-install", "invalid_auth")
        channel._remember(session, stale, world.ada, "UGHOST", "x@y.example", datetime.now(UTC))
        session.flush()
        assert workspace.status == "active"
        link = session.execute(
            select(SlackUserLink).where(SlackUserLink.user_id == world.ada)
        ).scalar_one()
        assert link.slack_user_id != "UGHOST"


def test_a_key_that_is_not_a_key_stops_slack_and_nothing_else(world: World) -> None:
    world.preference(quiet_hours=NO_QUIET)
    world.connect()
    world.attivita("Richiamare Rossi")

    outcome = world.pass_(settings=slack_settings(slack_token_key="not base64 at all!"))

    assert outcome.esito == "eseguito"
    assert outcome.slacks == 0
    assert outcome.delivered == 1  # the app list still has it
    with world.session() as session:
        health = SlackService(
            session,
            settings=slack_settings(slack_token_key="not base64 at all!"),
            client=slack_client(world.fake),
        ).health(actor_of(session.get(User, world.admin)))  # type: ignore[arg-type]
        assert health.problem is not None and "PIGROCRM_SLACK_TOKEN_KEY" in health.problem
