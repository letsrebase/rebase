"""REB-732: two levels of preference and the one function that resolves them (spec
2026-10-26 §5, §7)."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.errors import Conflict, PermissionDenied, ScopedAdmin, ValidationFailed
from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
from pigrocrm.core.reminders.models import ReminderPreference
from pigrocrm.core.reminders.preferences import ReminderPreferenceService, ReminderSettings
from pigrocrm.core.reminders.schemas import QuietHours, ReminderPreferenceWrite

OVERDUE = "attivita_overdue"


def _user(session: Session, ruolo: str = "collaboratore") -> User:
    user = User(email=f"u-{uuid4()}@example.test", password_hash="x", nome="P", ruolo=ruolo)
    session.add(user)
    session.flush()
    return user


def _actor(user: User) -> Actor:
    return Actor(id=user.id, type="user", role=user.ruolo)  # type: ignore[arg-type]


def _effective(session: Session, user: User, type_key: str = OVERDUE):
    return ReminderSettings.load(session).for_user(user.id).type(type_key)


def test_with_nothing_set_a_person_gets_the_catalogues_defaults(db_session: Session) -> None:
    user = _user(db_session)

    effective = _effective(db_session, user)

    assert effective.enabled is True
    assert effective.ladder == {"serious": 1, "critical": 14}
    assert effective.channels == ("in_app", "push", "slack", "email")
    assert effective.urgent_from == "critical"
    assert effective.is_urgent("critical") and not effective.is_urgent("serious")
    assert effective.email_mode_for("critical") == "immediate"
    assert effective.email_mode_for("serious") == "summary"
    general = ReminderSettings.load(db_session).for_user(user.id).general()
    assert general.summary_hour == 8
    assert general.quiet_hours == QuietHours()


def test_the_resolution_is_override_then_space_then_built_in(db_session: Session) -> None:
    admin, member, other = _user(db_session, "admin"), _user(db_session), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(
        _actor(admin),
        "space",
        OVERDUE,
        ReminderPreferenceWrite(ladder={"warning": 2, "serious": 9}),
    )
    service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(urgent_from="serious"))

    mine = _effective(db_session, member)
    theirs = _effective(db_session, other)

    assert mine.ladder == {"warning": 2, "serious": 9}, "the space's default, nothing of mine"
    assert mine.urgent_from == "serious", "my override"
    assert theirs.ladder == {"warning": 2, "serious": 9}
    assert theirs.urgent_from == "critical", "the built-in default, nobody overrode it"


def test_the_way_back_is_a_delete_so_a_later_default_still_reaches_the_person(
    db_session: Session,
) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(channels=["email"]))
    assert _effective(db_session, member).channels == ("in_app", "email")

    service.reset(_actor(member), "me", OVERDUE)
    service.write(_actor(admin), "space", OVERDUE, ReminderPreferenceWrite(channels=["slack"]))

    assert _effective(db_session, member).channels == ("in_app", "slack"), (
        "no copy of the old default was left behind in the person's row"
    )
    rows = db_session.scalar(
        select(func.count())
        .select_from(ReminderPreference)
        .where(ReminderPreference.user_id == member.id)
    )
    assert rows == 0


def test_sending_null_clears_one_column_and_the_last_clears_the_row(db_session: Session) -> None:
    member = _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(
        _actor(member),
        "me",
        OVERDUE,
        ReminderPreferenceWrite(urgent_from="serious", email_mode="summary"),
    )

    left = service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(urgent_from=None))
    assert left is not None and left.urgent_from is None and left.email_mode == "summary"

    gone = service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(email_mode=None))
    assert gone is None, "nothing left to override, so the row went"


def test_a_type_an_admin_switched_off_cannot_be_switched_back_on_by_a_person(
    db_session: Session,
) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(_actor(admin), "space", OVERDUE, ReminderPreferenceWrite(enabled=False))
    service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(enabled=True))

    settings = ReminderSettings.load(db_session)
    assert settings.space_enabled(OVERDUE) is False
    assert settings.for_user(member.id).type(OVERDUE).enabled is False


def test_a_person_can_switch_a_type_off_for_themselves_only(db_session: Session) -> None:
    member, other = _user(db_session), _user(db_session)
    ReminderPreferenceService(db_session).write(
        _actor(member), "me", OVERDUE, ReminderPreferenceWrite(enabled=False)
    )

    settings = ReminderSettings.load(db_session)
    assert settings.space_enabled(OVERDUE) is True
    assert settings.for_user(member.id).type(OVERDUE).enabled is False
    assert settings.for_user(other.id).type(OVERDUE).enabled is True


def test_a_channel_off_by_default_is_still_on_for_the_person_who_turned_it_on(
    db_session: Session,
) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(_actor(admin), "space", OVERDUE, ReminderPreferenceWrite(channels=[]))
    service.write(
        _actor(member), "me", OVERDUE, ReminderPreferenceWrite(channels=["push", "email"])
    )

    assert _effective(db_session, member).channels == ("in_app", "push", "email")
    assert _effective(db_session, _user(db_session)).channels == ("in_app",), "in-app, always"


def test_in_app_is_not_a_setting(db_session: Session) -> None:
    member = _user(db_session)

    row = ReminderPreferenceService(db_session).write(
        _actor(member), "me", OVERDUE, ReminderPreferenceWrite(channels=["in_app", "email"])
    )

    assert row is not None and row.channels == ["email"]


def test_quiet_hours_and_the_summary_hour_are_set_apart_from_any_type(db_session: Session) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(
        _actor(admin),
        "space",
        "general",
        ReminderPreferenceWrite(quiet_hours=QuietHours(start="22:00", end="07:00"), summary_hour=9),
    )
    service.write(_actor(member), "me", "general", ReminderPreferenceWrite(summary_hour=7))

    settings = ReminderSettings.load(db_session)
    mine = settings.for_user(member.id).general()
    assert (mine.summary_hour, mine.quiet_hours.start) == (7, "22:00")
    assert settings.for_user(_user(db_session).id).general().summary_hour == 9


def test_per_type_and_general_settings_cannot_be_mixed(db_session: Session) -> None:
    member = _user(db_session)
    service = ReminderPreferenceService(db_session)

    with pytest.raises(ValidationFailed) as general_on_type:
        service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite(summary_hour=9))
    assert general_on_type.value.details["field"] == "summary_hour"
    with pytest.raises(ValidationFailed) as type_on_general:
        service.write(_actor(member), "me", "general", ReminderPreferenceWrite(enabled=False))
    assert type_on_general.value.details["field"] == "enabled"
    with pytest.raises(ValidationFailed):
        service.write(_actor(member), "me", OVERDUE, ReminderPreferenceWrite())


def test_a_threshold_save_that_breaks_the_ladders_order_is_refused_and_saves_nothing(
    db_session: Session,
) -> None:
    member = _user(db_session)
    service = ReminderPreferenceService(db_session)

    with pytest.raises(ValidationFailed, match="più alta"):
        service.write(
            _actor(member),
            "me",
            OVERDUE,
            ReminderPreferenceWrite(ladder={"warning": 9, "serious": 3}),
        )
    db_session.rollback()

    assert _effective(db_session, member).ladder == {"serious": 1, "critical": 14}


def test_an_unknown_type_is_refused(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        ReminderPreferenceService(db_session).write(
            _actor(_user(db_session)), "me", "nope", ReminderPreferenceWrite(enabled=False)
        )


def test_only_an_unscoped_admin_sets_the_spaces_defaults(db_session: Session) -> None:
    member, admin = _user(db_session), _user(db_session, "admin")
    service = ReminderPreferenceService(db_session)
    write = ReminderPreferenceWrite(enabled=False)

    with pytest.raises(PermissionDenied):
        service.write(_actor(member), "space", OVERDUE, write)
    with pytest.raises(PermissionDenied):
        service.reset(_actor(member), "space", OVERDUE)
    scoped = Actor(id=admin.id, type="user", role="admin", aziende=())
    with pytest.raises(ScopedAdmin):
        service.write(scoped, "space", OVERDUE, write)

    assert service.write(_actor(admin), "space", OVERDUE, write) is not None


def test_a_readonly_member_sets_their_own_preferences(db_session: Session) -> None:
    reader = _user(db_session, "readonly")

    row = ReminderPreferenceService(db_session).write(
        _actor(reader), "me", OVERDUE, ReminderPreferenceWrite(enabled=False)
    )

    assert row is not None and row.enabled is False


def test_a_job_has_no_preferences_of_its_own(db_session: Session) -> None:
    with pytest.raises(Conflict):
        ReminderPreferenceService(db_session).write(
            Actor.system().model_copy(update={"role": "collaboratore"}),
            "me",
            OVERDUE,
            ReminderPreferenceWrite(enabled=False),
        )


def test_the_read_carries_both_layers_and_what_they_resolve_to(db_session: Session) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    service.write(_actor(admin), "space", OVERDUE, ReminderPreferenceWrite(urgent_from="serious"))
    service.write(_actor(member), "me", "deal_stale", ReminderPreferenceWrite(enabled=False))

    read = service.read(_actor(member))

    assert [kind.key for kind in read.types] == list(REMINDER_TYPES)
    assert [(row.scope, row.type) for row in read.space] == [("space", OVERDUE)]
    assert [(row.scope, row.type) for row in read.mine] == [("me", "deal_stale")]
    effective = {item.type: item for item in read.effective}
    assert effective[OVERDUE].urgent_from == "serious"
    assert effective["deal_stale"].enabled is False
    assert effective["deal_past_close"].enabled is True
    assert read.general.summary_hour == 8


def test_the_candidate_bound_is_the_loosest_rung_of_any_layer(db_session: Session) -> None:
    admin, member = _user(db_session, "admin"), _user(db_session)
    service = ReminderPreferenceService(db_session)
    stale, due = REMINDER_TYPES["deal_stale"], REMINDER_TYPES["attivita_due"]
    assert ReminderSettings.load(db_session).candidate_bound(stale) == 21
    assert ReminderSettings.load(db_session).candidate_bound(due) == 0

    service.write(
        _actor(member), "me", "deal_stale", ReminderPreferenceWrite(ladder={"warning": 7})
    )
    service.write(
        _actor(admin), "space", "attivita_due", ReminderPreferenceWrite(ladder={"warning": 3})
    )

    settings = ReminderSettings.load(db_session)
    assert settings.candidate_bound(stale) == 7, "a detector must offer what a person's ladder asks"
    assert settings.candidate_bound(due) == 3
