"""`TaskService`: the commitment, its three states, and the date that may be absent.

Most of this file defends one decision of the spec (2026-09-03 §3.2): **`due_date` is
nullable, and an undated activity is not late.** Everything downstream -- the calendar's
«senza scadenza» section, the `due_within` filter, the counts -- is only correct while
that holds, so it is asserted from several sides rather than once.
"""

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from frozen_clock import TODAY_IN_ITALY, freeze
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.schemas import (
    TaskCreate,
    TaskListQuery,
    TaskTodayQuery,
    TaskUpdate,
)
from pigrocrm.core.tasks.service import TaskService

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")


@pytest.fixture
def service(db_session: Session) -> TaskService:
    return TaskService(db_session)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
    row = Customer(legal_name=f"Cliente {uuid4()}")
    db_session.add(row)
    db_session.flush()
    return row.id


def _create(service: TaskService, **kwargs: object) -> object:
    payload: dict[str, object] = {"title": "Chiedere il codice SDI"}
    payload.update(kwargs)
    return service.create(TaskCreate(**payload), ADMIN)  # type: ignore[arg-type]


# --- what a commitment is --------------------------------------------------------------


def test_an_activity_can_have_no_date_at_all(service: TaskService) -> None:
    """The commonest to-do. A required date would produce either an invented one --
    which poisons every «what is due» list -- or no record at all."""
    created = _create(service)

    assert created.due_date is None
    assert created.status == "open"
    assert created.completed_at is None
    assert created.origin == "manual"


def test_it_is_created_open_and_manual_whatever_the_caller_says(
    service: TaskService,
) -> None:
    """`status` and `origin` are not fields of `TaskCreate` at all: a caller that
    could set them could claim to be an automation, and `origin` exists to answer
    exactly that question. Pydantic's default is to ignore an undeclared key rather than
    refuse it, so what is asserted is the *outcome* -- the row is open and manual
    whatever arrived -- and not an exception this schema does not raise."""
    created = service.create(
        TaskCreate.model_validate(
            {"title": "Non sono un'automazione", "origin": "automation", "status": "completed"}
        ),
        ADMIN,
    )

    assert (created.origin, created.status, created.rule) == ("manual", "open", None)


def test_a_readonly_actor_cannot_create_one(service: TaskService) -> None:
    with pytest.raises(PermissionDenied):
        service.create(TaskCreate(title="Non mia"), READONLY)


# --- the one reference -----------------------------------------------------------------


def test_it_may_hang_from_one_entity(service: TaskService, customer_id: UUID) -> None:
    created = _create(service, customer_id=customer_id)
    assert created.customer_id == customer_id


def test_two_references_are_refused_by_the_service(
    service: TaskService, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """The database has the same check and is the line that holds under concurrency;
    this is the one that turns it into a sentence somebody can read."""
    with pytest.raises(ValidationFailed) as excinfo:
        _create(service, customer_id=customer_id, deal_id=seeded_deal_id)

    assert "un solo" in str(excinfo.value)


def test_two_references_are_refused_by_the_database_too(
    db_session: Session, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """Straight past the service, because a check constraint that is never exercised is
    a check constraint that might not exist."""
    db_session.add(Task(title="Due riferimenti", customer_id=customer_id, deal_id=seeded_deal_id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_a_reference_that_does_not_exist_is_not_found(service: TaskService) -> None:
    with pytest.raises(NotFound):
        _create(service, deal_id=uuid4())


def test_an_activity_may_belong_to_nobody(service: TaskService) -> None:
    """«Do March's e-invoicing» belongs to no customer -- the spec's own example."""
    created = _create(service, title="Fare la fatturazione elettronica di marzo")

    assert (created.customer_id, created.person_id, created.deal_id, created.invoice_id) == (
        None,
        None,
        None,
        None,
    )


# --- the three states ------------------------------------------------------------------


def test_completing_records_the_day_it_was_done(
    service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    created = _create(service)

    done = service.complete(created.id, ADMIN)

    assert done.status == "completed"
    # `today_local()`, which is the emitter's day and not the process's: an hour logged
    # at 23:30 in Rome is that day, not the next one in UTC.
    assert done.completed_at == TODAY_IN_ITALY


def test_completing_twice_does_not_move_the_date(
    service: TaskService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second click on a checkbox. The date is when the work happened, so it must not
    slide to today every time somebody presses it again."""
    freeze(monkeypatch)
    created = _create(service)
    first = service.complete(created.id, ADMIN)
    again = service.complete(created.id, ADMIN)

    assert again.completed_at == first.completed_at


def test_cancelling_closes_it_without_deleting_it(service: TaskService) -> None:
    """The whole reason the third state exists: six months later the question is not
    «where did it go» but *why* it stopped mattering."""
    created = _create(service, title="Sollecitare Rossi")

    cancelled = service.cancel(created.id, ADMIN)

    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is None
    # Still there, and still readable.
    assert service.get(created.id, ADMIN).title == "Sollecitare Rossi"


def test_a_cancelled_activity_is_not_completed_by_mistake(service: TaskService) -> None:
    created = _create(service)
    service.cancel(created.id, ADMIN)

    with pytest.raises(Conflict):
        service.complete(created.id, ADMIN)


def test_reopening_clears_the_completion_date(service: TaskService) -> None:
    """Otherwise the row would be open and also say when it was finished."""
    created = _create(service)
    service.complete(created.id, ADMIN)

    reopened = service.reopen(created.id, ADMIN)

    assert (reopened.status, reopened.completed_at) == ("open", None)


def test_the_database_refuses_a_completion_date_on_an_open_activity(
    db_session: Session,
) -> None:
    """The equivalence check, from the side the service never takes."""
    db_session.add(Task(title="Incoerente", status="open", completed_at=date(2026, 9, 9)))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_the_database_refuses_a_rule_on_a_manual_activity(db_session: Session) -> None:
    db_session.add(Task(title="Manuale con regola", origin="manual", rule="sollecito_1"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# --- rewriting -------------------------------------------------------------------------


def test_a_date_can_be_added_and_then_removed(service: TaskService) -> None:
    """Removing it has to be possible: an activity whose date turns out to be wrong must
    not be stuck with it, and §3.2 refuses invented dates."""
    created = _create(service)

    with_date = service.update(created.id, TaskUpdate(due_date=date(2026, 9, 18)), ADMIN)
    assert with_date.due_date == date(2026, 9, 18)

    without = service.update(created.id, TaskUpdate(due_date_to_remove=True), ADMIN)
    assert without.due_date is None


def test_setting_and_clearing_a_date_in_one_request_is_refused(
    service: TaskService,
) -> None:
    created = _create(service)

    with pytest.raises(ValidationFailed):
        service.update(
            created.id,
            TaskUpdate(due_date=date(2026, 9, 18), due_date_to_remove=True),
            ADMIN,
        )


def test_moving_it_to_a_second_entity_is_refused_by_reading_the_result(
    service: TaskService, customer_id: UUID, seeded_deal_id: UUID
) -> None:
    """The check reads the row as it *would be*, not the patch: a request that adds a
    deal to an activity already on a customer supplies one reference and produces two."""
    created = _create(service, customer_id=customer_id)

    with pytest.raises(ValidationFailed):
        service.update(created.id, TaskUpdate(deal_id=seeded_deal_id), ADMIN)


def test_it_can_be_unhooked_from_every_entity(service: TaskService, customer_id: UUID) -> None:
    created = _create(service, customer_id=customer_id)

    detached = service.update(created.id, TaskUpdate(reference_to_remove=True), ADMIN)

    assert detached.customer_id is None


def test_an_unknown_key_is_refused_rather_than_ignored(service: TaskService) -> None:
    with pytest.raises(ValueError):
        TaskUpdate(titel="typo")  # type: ignore[call-arg]


def test_assigning_to_a_disabled_user_is_refused(service: TaskService, db_session: Session) -> None:
    """Work assigned to a disabled account is a commitment nobody will ever see."""
    user = User(
        email=f"spento-{uuid4()}@example.test",
        password_hash="x",
        name="Spento",
        role="collaborator",
        active=False,
    )
    db_session.add(user)
    db_session.flush()

    with pytest.raises(Conflict):
        _create(service, assigned_to=user.id)


# --- archiving, which is not cancelling ------------------------------------------------


def test_archiving_hides_it_and_restoring_brings_it_back(service: TaskService) -> None:
    created = _create(service)
    service.soft_delete(created.id, ADMIN)

    with pytest.raises(NotFound):
        service.get(created.id, ADMIN)

    restored = service.restore(created.id, ADMIN)
    assert restored.status == "open"


# --- the list --------------------------------------------------------------------------


def test_a_date_filter_excludes_the_undated_ones(service: TaskService) -> None:
    """The property everything else rests on. `NULL <= date` is NULL, which is not true
    -- and a filter that quietly included them would answer «due by Friday» with things
    that are not due at all."""
    _create(service, title="Senza data")
    _create(service, title="Con data", due_date=date(2026, 9, 18))

    page = service.list(TaskListQuery(due_within=date(2026, 9, 30)), ADMIN)

    assert [item.title for item in page.items] == ["Con data"]


def test_the_undated_ones_can_be_asked_for_explicitly(service: TaskService) -> None:
    _create(service, title="Senza data")
    _create(service, title="Con data", due_date=date(2026, 9, 18))

    page = service.list(TaskListQuery(without_due_date=True), ADMIN)

    assert [item.title for item in page.items] == ["Senza data"]


def test_the_archived_ones_are_their_own_list(service: TaskService) -> None:
    """`archived` is the complement of the live list, never a union with it: a screen
    that offers «ripristina» has to be able to find what it restores, and the live list
    has to keep hiding it."""
    kept = _create(service, title="Resta")
    gone = _create(service, title="Per errore")
    service.soft_delete(gone.id, ADMIN)

    live = service.list(TaskListQuery(), ADMIN)
    archived = service.list(TaskListQuery(archived=True), ADMIN)

    assert [item.id for item in live.items] == [kept.id]
    assert [item.id for item in archived.items] == [gone.id]

    service.restore(gone.id, ADMIN)
    assert service.list(TaskListQuery(archived=True), ADMIN).items == []


def test_the_list_does_not_hide_what_was_completed(service: TaskService) -> None:
    """`status` defaults to «every state»: a list that silently dropped the completed
    ones is a list somebody will read as «the work was never done»."""
    created = _create(service)
    service.complete(created.id, ADMIN)

    page = service.list(TaskListQuery(), ADMIN)

    assert [item.status for item in page.items] == ["completed"]


def test_sorting_by_a_date_column_pages_without_a_broken_cursor(
    service: TaskService,
) -> None:
    """`due_date` is the project's first `Date` sort key, which is why `SortKind` grew a
    `date` branch: encoded as text it would reach Postgres as `date > text`, an operator
    that does not exist, and the second page would fail at the database."""
    for day in (10, 11, 12):
        _create(service, title=f"Giorno {day}", due_date=date(2026, 9, day))

    first = service.list(TaskListQuery(sort="due_date", limit=2), ADMIN)
    assert [item.title for item in first.items] == ["Giorno 10", "Giorno 11"]
    assert first.next_cursor is not None

    second = service.list(TaskListQuery(sort="due_date", limit=2, cursor=first.next_cursor), ADMIN)
    assert [item.title for item in second.items] == ["Giorno 12"]


def test_the_timeline_records_what_happened_to_it(
    service: TaskService, db_session: Session
) -> None:
    """Three closures of the same row are three different facts, and the timeline is
    where «why» lives."""
    created = _create(service)
    service.complete(created.id, ADMIN)
    service.reopen(created.id, ADMIN)
    service.cancel(created.id, ADMIN)

    kinds = db_session.execute(
        text(
            "SELECT kind FROM activities WHERE entity_type = 'task' AND entity_id = :id "
            "ORDER BY occurred_at, id"
        ),
        {"id": str(created.id)},
    ).scalars()

    assert list(kinds) == ["created", "completed", "reopened", "cancelled"]


# --- the Oggi panel (spec 2026-09-03 §5, REB-725) ---------------------------------------


def _person(db_session: Session, name: str) -> UUID:
    user = User(
        email=f"{name.lower()}-{uuid4()}@example.test",
        password_hash="x",
        name=name,
        role="collaborator",
    )
    db_session.add(user)
    db_session.flush()
    return user.id


def test_today_cuts_the_open_commitments_into_four_sections(
    service: TaskService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overdue is before today, «oggi» is today, the week opens tomorrow and closes at
    today plus seven, and the undated stand apart. The boundary days are the ones a
    off-by-one would move, so each side of each is asserted."""
    freeze(monkeypatch)
    today = TODAY_IN_ITALY
    io = _person(db_session, "Io")
    other = _person(db_session, "Altri")

    def title(days: int | None) -> str:
        return "senza data" if days is None else f"giorno {days:+d}"

    for days in (-1, 0, 1, 7, 8, None):
        _create(
            service,
            title=title(days),
            due_date=None if days is None else today + timedelta(days=days),
            assigned_to=io,
        )
    _create(service, title="di un altro", due_date=today - timedelta(days=2), assigned_to=other)

    panel = service.today(ADMIN, TaskTodayQuery(assigned_to=io))

    assert panel.today == today
    assert [a.title for a in panel.overdue.items] == ["giorno -1"]
    assert [a.title for a in panel.today_due.items] == ["giorno +0"]
    assert [a.title for a in panel.next_seven_days.items] == ["giorno +1", "giorno +7"]
    assert [a.title for a in panel.without_due_date.items] == ["senza data"]


def test_today_counts_only_open_commitments_and_reports_the_whole_total(
    service: TaskService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completing an overdue commitment takes it out of the count; and a section holds
    `limit` rows while `total` stays the real number, because the sidebar prints it."""
    freeze(monkeypatch)
    today = TODAY_IN_ITALY
    io = _person(db_session, "Io")
    for n in range(3):
        _create(service, title=f"ieri {n}", due_date=today - timedelta(days=1), assigned_to=io)

    before = service.today(ADMIN, TaskTodayQuery(assigned_to=io, limit=2))
    assert before.overdue.total == 3
    assert len(before.overdue.items) == 2

    service.complete(before.overdue.items[0].id, ADMIN)
    after = service.today(ADMIN, TaskTodayQuery(assigned_to=io, limit=2))
    assert after.overdue.total == 2


def test_today_without_an_assignee_is_the_whole_space(
    service: TaskService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    today = TODAY_IN_ITALY
    _create(service, title="di nessuno", due_date=today - timedelta(days=1))
    _create(
        service,
        title="di qualcuno",
        due_date=today - timedelta(days=1),
        assigned_to=_person(db_session, "Qualcuno"),
    )

    assert {a.title for a in service.today(ADMIN, TaskTodayQuery()).overdue.items} >= {
        "di nessuno",
        "di qualcuno",
    }


def test_today_mine_and_nobodys_leaves_out_to_colleagues(
    service: TaskService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    freeze(monkeypatch)
    yesterday = TODAY_IN_ITALY - timedelta(days=1)
    io, colleague = _person(db_session, "Io"), _person(db_session, "Collega")
    _create(service, title="mia", due_date=yesterday, assigned_to=io)
    _create(service, title="di nessuno", due_date=yesterday)
    _create(service, title="del collega", due_date=yesterday, assigned_to=colleague)
    archived = _create(service, title="archived", due_date=yesterday)
    service.soft_delete(archived.id, ADMIN)  # type: ignore[attr-defined]

    only_my = service.today(ADMIN, TaskTodayQuery(assigned_to=io))
    assert [a.title for a in only_my.overdue.items] == ["mia"]

    also = service.today(ADMIN, TaskTodayQuery(assigned_to=io, include_unassigned=True))
    assert {a.title for a in also.overdue.items} == {"mia", "di nessuno"}
    assert also.overdue.total == 2


def test_list_mine_and_nobodys_is_an_or_and_needs_an_assignee(
    service: TaskService, db_session: Session
) -> None:
    io, colleague = _person(db_session, "Io"), _person(db_session, "Collega")
    _create(service, title="mia", assigned_to=io)
    _create(service, title="di nessuno")
    _create(service, title="del collega", assigned_to=colleague)

    def titles(**kw: object) -> set[str]:
        return {a.title for a in service.list(TaskListQuery(**kw), ADMIN).items}  # type: ignore[arg-type]

    assert titles(assigned_to=io) == {"mia"}
    assert titles(assigned_to=io, include_unassigned=True) == {"mia", "di nessuno"}
    assert titles(assigned_to=colleague, include_unassigned=True) == {"del collega", "di nessuno"}
    assert {"mia", "di nessuno", "del collega"} <= titles(include_unassigned=True)


def test_the_window_stays_strict_unless_asked_otherwise(
    service: TaskService, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The weekly digest reads `open_in_the_window` with a person and no flag: it lists
    what is theirs, so a task nobody holds must stay out of it."""
    from pigrocrm.core.tasks.repository import TaskRepository

    freeze(monkeypatch)
    yesterday = TODAY_IN_ITALY - timedelta(days=1)
    io = _person(db_session, "Io")
    _create(service, title="mia", due_date=yesterday, assigned_to=io)
    _create(service, title="di nessuno", due_date=yesterday)

    repo = TaskRepository(db_session)
    total, lines = repo.open_in_the_window(assigned_to=io, limit=10, before_of=TODAY_IN_ITALY)

    assert total == 1
    assert [r.title for r in lines] == ["mia"]
