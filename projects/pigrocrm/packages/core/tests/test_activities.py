from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.activities.repository import ActivityRepository
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed

USER = Actor(id=uuid4(), type="user", role="admin")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")


def test_record_stores_who_did_what(db_session: Session) -> None:
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER, {"legal_name": "ACME"})
    db_session.commit()

    entries = service.timeline("organization", entity_id)
    assert len(entries) == 1
    assert entries[0].kind == "created"
    assert entries[0].payload == {"legal_name": "ACME"}
    assert entries[0].actor_id == USER.id


def test_actor_type_distinguishes_a_human_from_an_agent(db_session: Session) -> None:
    """In an AI-first CRM this is the first thing you want to know when something
    looks wrong: did I do that, or did Claude?"""
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("deal", entity_id, "created", USER)
    service.record("deal", entity_id, "stage_changed", AGENT)
    db_session.commit()

    assert {e.actor_type for e in service.timeline("deal", entity_id)} == {"user", "mcp"}


def test_timeline_is_newest_first_and_limited(db_session: Session) -> None:
    service = ActivityService(db_session)
    entity_id = uuid4()
    for index in range(5):
        service.record("organization", entity_id, f"kind_{index}", USER)
    db_session.commit()

    entries = service.timeline("organization", entity_id, limit=3)
    assert len(entries) == 3
    assert entries[0].kind == "kind_4"


def test_timeline_is_scoped_to_one_entity(db_session: Session) -> None:
    service = ActivityService(db_session)
    mine, theirs = uuid4(), uuid4()
    service.record("organization", mine, "created", USER)
    service.record("organization", theirs, "created", USER)
    db_session.commit()

    assert len(service.timeline("organization", mine)) == 1


def test_record_does_not_commit_so_it_joins_the_callers_transaction(db_session: Session) -> None:
    """If recording committed on its own, a service that later fails would leave a
    timeline entry for a change that never happened."""
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER)
    db_session.rollback()

    assert service.timeline("organization", entity_id) == []


# --- Fix round 1 ------------------------------------------------------------------


def test_record_sanitizes_non_finite_floats(db_session: Session) -> None:
    """Postgres JSONB rejects NaN/Infinity outright; a naive audit write must not be
    able to take the operation it is recording down with it."""
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record(
        "organization",
        entity_id,
        "created",
        USER,
        {"a": float("nan"), "b": float("inf"), "c": float("-inf")},
    )
    db_session.commit()

    entry = service.timeline("organization", entity_id)[0]
    assert entry.payload == {"a": "NaN", "b": "Infinity", "c": "-Infinity"}


def test_record_strips_null_bytes_from_strings(db_session: Session) -> None:
    """Postgres text storage cannot hold a NUL byte at all."""
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER, {"note": "abc\x00def"})
    db_session.commit()

    entry = service.timeline("organization", entity_id)[0]
    assert entry.payload == {"note": "abcdef"}


def test_record_truncates_long_strings(db_session: Session) -> None:
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER, {"note": "x" * 3000})
    db_session.commit()

    saved = service.timeline("organization", entity_id)[0].payload["note"]
    assert len(saved) == 2001
    assert saved.endswith("…")


def test_record_never_fails_on_a_value_json_has_no_encoder_for(db_session: Session) -> None:
    """A `Decimal`, an `Enum` or `bytes` in a payload used to reach `json.dumps` raw and
    fail the flush of the operation being audited."""

    class Kind(Enum):
        PLAIN = "plain"

    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record(
        "organization",
        entity_id,
        "created",
        USER,
        {
            "total": Decimal("12.50"),
            "type": Kind.PLAIN,
            "raw": b"ab\xffc",
            "pair": (1, float("nan")),
            "when": datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
        },
    )
    db_session.commit()

    entry = service.timeline("organization", entity_id)[0]
    assert entry.payload == {
        "total": "12.50",
        "type": "plain",
        "raw": "ab\ufffdc",
        "pair": [1, "NaN"],
        "when": "2026-11-02T09:30:00+00:00",
    }


def test_record_caps_nesting_depth(db_session: Session) -> None:
    """Must not raise on pathological input and must not preserve unbounded nesting.
    The exact placeholder text is an implementation detail; only that recursion stops
    at a bounded depth is the contract."""
    nested: dict[str, Any] = {"leaf": "bottom"}
    for _ in range(20):
        nested = {"next": nested}

    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER, nested)
    db_session.commit()

    cursor: Any = service.timeline("organization", entity_id)[0].payload
    for _ in range(10):
        assert isinstance(cursor, dict)
        cursor = cursor["next"]
    # ten "next" unwraps land on the placeholder: recursion stopped, not a dict.
    assert isinstance(cursor, str)


def test_record_truncates_kind_and_entity_type_to_column_widths(db_session: Session) -> None:
    """`entity_type`/`kind` are developer-controlled literals, not user input -- plain
    truncation is enough, unlike the richer sanitization the payload needs."""
    long_entity_type = "y" * 50
    long_kind = "x" * 100
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record(long_entity_type, entity_id, long_kind, USER)
    db_session.commit()

    entry = service.timeline(long_entity_type[:30], entity_id)[0]
    assert entry.entity_type == long_entity_type[:30]
    assert entry.kind == long_kind[:50]


def test_session_remains_usable_after_a_hostile_payload(db_session: Session) -> None:
    """Before sanitization, recording a NaN reached `flush()` raw and Postgres
    rejected it with a `DataError`, leaving the session in `PendingRollbackError` --
    poisoning every later operation in the same transaction, including the unrelated
    change the activity was supposed to be auditing."""
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER, {"x": float("nan")})

    # The session must still accept further writes in the same transaction.
    service.record("organization", entity_id, "updated", USER, {"y": 1})
    db_session.commit()

    assert len(service.timeline("organization", entity_id)) == 2


# --- Final review item 5 (CRITICAL): timeline's limit was unbounded, unlike REST --
#
# All three REST timeline routes declare `Query(ge=1, le=200)`; the shared service
# behind both adapters -- and therefore the MCP `get_timeline` tool, which called
# straight into it with a bare `int` parameter -- enforced no bound at all. `-1`
# reaches Postgres as `InvalidRowCountInLimitClause`; `10**9` succeeds where REST
# would refuse the same value outright. Bounding it here, at the one place both
# adapters share, is what makes the MCP tool match REST without the tool itself
# needing its own copy of the rule -- the same pattern `_check_numbers` already
# uses for `probability` on `create_deal`'s tool parameter.


def test_timeline_rejects_a_limit_below_one(db_session: Session) -> None:
    service = ActivityService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        service.timeline("organization", uuid4(), limit=0)
    assert exc.value.details["field"] == "limit"


def test_timeline_rejects_a_negative_limit(db_session: Session) -> None:
    """Before the bound existed, this reached Postgres raw as
    `psycopg.errors.InvalidRowCountInLimitClause` ('LIMIT must not be negative')."""
    service = ActivityService(db_session)
    with pytest.raises(ValidationFailed):
        service.timeline("organization", uuid4(), limit=-1)


def test_timeline_rejects_a_limit_above_two_hundred(db_session: Session) -> None:
    """Matches every REST timeline route's own `Query(ge=1, le=200)` exactly."""
    service = ActivityService(db_session)
    with pytest.raises(ValidationFailed) as exc:
        service.timeline("organization", uuid4(), limit=201)
    assert exc.value.details["expected"] == "1-200"


def test_timeline_accepts_the_boundary_values(db_session: Session) -> None:
    service = ActivityService(db_session)
    entity_id = uuid4()
    service.record("organization", entity_id, "created", USER)
    db_session.commit()

    assert service.timeline("organization", entity_id, limit=1) == service.timeline(
        "organization", entity_id, limit=200
    )


# --- slice 6 §6.1: the global feed ---------------------------------------------------
#
# Planted with an explicit `occurred_at` rather than through `ActivityService.record`,
# which stamps `datetime.now(UTC)`. Two rows written in one flush can land on the same
# microsecond, and a test whose expected order depends on whether they did is a test that
# fails once a month on a fast machine for no reason anybody can reproduce. Here the
# instants are chosen, so the ordering assertion is about the query and nothing else.

FEED_ORIGIN = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def _plant(session: Session, entity_type: str, n: int, *, minute: int) -> Activity:
    activity = Activity(
        entity_type=entity_type,
        entity_id=uuid4(),
        kind="created",
        actor_id=USER.id,
        actor_type=USER.type,
        payload={"n": n},
        occurred_at=FEED_ORIGIN + timedelta(minutes=minute),
    )
    session.add(activity)
    session.flush()
    return activity


@pytest.fixture
def empty_feed(db_session: Session) -> Session:
    """The four `recent` tests below read the *installation's* feed, so they can only
    assert an exact list when the table holds nothing but what they planted.

    Until 2026-09-09 that was true by accident: this file is alphabetically first, so it
    ran before every file that commits activity rows outside the rolled-back transaction
    (`test_automation_atomicity.py`, `test_cli_gmail.py`, `test_dashboard_*`). Run the
    suite in any other order -- which is what `pytest -n` does -- and all four fail on
    rows they never wrote. The delete runs inside the same savepoint every other test
    uses, so it is undone with the rest of the test and leaves nothing behind.
    """
    db_session.execute(delete(Activity))
    return db_session


def test_recent_returns_the_newest_activities_across_every_entity(empty_feed: Session) -> None:
    """§6.1's feed. Across every entity, which is what distinguishes it from `timeline`.

    The rows are inserted in an order that is neither their chronological order nor its
    reverse, so an implementation that returned them in insertion order -- which is what a
    `select()` with no `ORDER BY` very often does on a freshly written table -- gives a
    different answer from the one asserted.
    """
    _plant(empty_feed, "deal", 3, minute=3)
    _plant(empty_feed, "organization", 102, minute=7)
    _plant(empty_feed, "deal", 0, minute=0)
    _plant(empty_feed, "organization", 100, minute=5)
    _plant(empty_feed, "deal", 4, minute=4)
    _plant(empty_feed, "organization", 101, minute=6)
    _plant(empty_feed, "deal", 1, minute=1)
    _plant(empty_feed, "deal", 2, minute=2)

    rows = ActivityRepository(empty_feed).recent(limit=4)
    assert len(rows) == 4
    # Newest first, and both entity types present in the newest four -- the whole point of
    # a feed that is not a timeline.
    assert [row.payload["n"] for row in rows] == [102, 101, 100, 4]
    assert {row.entity_type for row in rows} == {"organization", "deal"}


def test_recent_is_bounded_by_its_limit(empty_feed: Session) -> None:
    """Fifty rows at most and no cursor: a complete history is the entity's own timeline,
    and a paginated global feed would be a second way to browse the same rows."""
    for index in range(60):
        _plant(empty_feed, "deal", index, minute=index)

    repo = ActivityRepository(empty_feed)
    assert len(repo.recent(limit=50)) == 50
    # The default is the same fifty, so a caller that passes nothing gets the bounded feed
    # rather than the whole table.
    assert len(repo.recent()) == 50
    assert [row.payload["n"] for row in repo.recent(limit=3)] == [59, 58, 57]


def test_recent_breaks_the_tie_on_the_identifier(empty_feed: Session) -> None:
    """`occurred_at` alone is not a total order: entries written in one transaction share
    it to the microsecond often enough to matter, and a feed that reorders between reads
    looks like data changing.

    Every row here carries the *same* instant, so `ORDER BY occurred_at DESC` on its own
    leaves the order entirely to the planner and this assertion is the tie-break's alone.
    """
    planted: list[UUID] = [_plant(empty_feed, "deal", index, minute=0).id for index in range(20)]

    rows = ActivityRepository(empty_feed).recent(limit=20)
    assert [row.id for row in rows] == sorted(planted, reverse=True)


def test_recent_is_not_scoped_to_one_entity_the_way_timeline_is(empty_feed: Session) -> None:
    """The two methods answer different questions, and the difference is observable: the
    same rows, read one way, are one entity's history and read the other way are the
    installation's."""
    service = ActivityService(empty_feed)
    mine = uuid4()
    service.record("deal", mine, "created", USER)
    service.record("deal", uuid4(), "created", USER)
    empty_feed.flush()

    assert len(service.timeline("deal", mine)) == 1
    assert len(ActivityRepository(empty_feed).recent()) == 2
