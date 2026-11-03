"""Two booleans, and every change to them audited.

Spec §9.6: `automation_config` is a single row like `legal_entities` and
`fiscal_profile`, admin-only, and every modification writes an activity -- residuo **R5**
closed for this table, with slice 3 §7.1's argument: changing what the system will do by
itself to future data is of a different order of seriousness from renaming a stage.

R5 itself stays open, and it is worth being exact about what it says, because "closing R5"
is a claim this task cannot make. R5 (`2026-08-07-slice-1a-residui.md`) is *"Nessun audit
trail per configurazione e token"*: no timeline entries for **field definitions, pipeline
stages, users and personal access tokens**. None of those four gains one here. What this
task does is keep a *fifth* piece of configuration off that list on the day it is
introduced, which is the only kind of progress against R5 a new table can make.

No flow builder, no configurable conditions, no second effect per rule (§15). Two booleans
are the configuration surface, and that is a property to defend rather than a starting
point to grow from.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.schemas import AutomationConfigUpdate
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.errors import PermissionDenied

ADMIN = Actor(id=uuid7(), type="user", role="admin")
COLLABORATOR = Actor(id=uuid7(), type="user", role="collaborator")
READONLY = Actor(id=uuid7(), type="user", role="readonly")


def test_every_rule_is_on_by_default(db_session: Session) -> None:
    """Default `true`, and it is a decision: an automation nobody switched on is an
    automation nobody knows exists."""
    described = AutomationConfigService(db_session).describe_automations(ADMIN)
    assert described.configuration.a1_wins_accepted_offer_deal is True
    assert described.configuration.a2_advances_sent_offer_deal is True
    assert described.configuration.a3_sent_offer_follow_up is True
    assert described.configuration.a4_stalled_deal is True
    assert described.configuration.a5_overdue_invoice is True
    # The one threshold: thirty days in the same stage.
    assert described.configuration.a4_stalled_deal_days == 30


def test_the_row_is_created_on_first_read_and_not_duplicated(db_session: Session) -> None:
    """Single-row table, like `legal_entities`. Reading it twice must not leave two."""
    from sqlalchemy import func, select

    from pigrocrm.core.automations.models import AutomationConfig

    service = AutomationConfigService(db_session)
    service.describe_automations(ADMIN)
    service.describe_automations(ADMIN)
    assert db_session.scalar(select(func.count()).select_from(AutomationConfig)) == 1


def test_a_second_service_instance_finds_the_same_row(db_session: Session) -> None:
    """The duplication guard above shares one repository instance, so it would also pass
    against a repository that cached the row on `self`. Two services, one row."""
    from sqlalchemy import func, select

    from pigrocrm.core.automations.models import AutomationConfig

    AutomationConfigService(db_session).describe_automations(ADMIN)
    AutomationConfigService(db_session).describe_automations(READONLY)
    assert db_session.scalar(select(func.count()).select_from(AutomationConfig)) == 1


def test_describe_lists_every_rule_with_their_state(db_session: Session) -> None:
    described = AutomationConfigService(db_session).describe_automations(ADMIN)
    assert [rule.code for rule in described.rules] == [
        "A1",
        "A2",
        "sent_offer_follow_up",
        "stalled_deal",
        "overdue_invoice",
    ]
    assert all(rule.active for rule in described.rules)
    # The description is what an agent reads to know what the system does by itself.
    assert "won" in described.rules[0].description.lower()


def test_a_switched_off_rule_is_reported_as_inactive(db_session: Session) -> None:
    """`active` must follow the column it describes. A hard-coded `True` -- or a mapping
    that reads A1's flag for both rules -- passes every other test in this file."""
    service = AutomationConfigService(db_session)
    service.update_automation_config(
        AutomationConfigUpdate(a2_advances_sent_offer_deal=False), ADMIN
    )
    described = service.describe_automations(ADMIN)
    active = {rule.code: rule.active for rule in described.rules}
    assert active == {
        "A1": True,
        "A2": False,
        "sent_offer_follow_up": True,
        "stalled_deal": True,
        "overdue_invoice": True,
    }


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("a3_sent_offer_follow_up", "sent_offer_follow_up"),
        ("a4_stalled_deal", "stalled_deal"),
        ("a5_overdue_invoice", "overdue_invoice"),
    ],
)
def test_each_new_rule_reports_its_own_switch(db_session: Session, field: str, code: str) -> None:
    """Five flags, five codes: a mapping that read one rule's column for another passes the
    test above, which switches off a single rule."""
    service = AutomationConfigService(db_session)
    service.update_automation_config(AutomationConfigUpdate(**{field: False}), ADMIN)
    active = {rule.code: rule.active for rule in service.describe_automations(ADMIN).rules}
    assert [name for name, on in active.items() if not on] == [code]


@pytest.mark.parametrize("days", [0, -1, 366])
def test_the_threshold_is_bounded_like_its_column(days: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AutomationConfigUpdate(a4_stalled_deal_days=days)


def test_changing_the_threshold_is_audited_and_an_equal_value_is_not(db_session: Session) -> None:
    from pigrocrm.core.activities.repository import ActivityRepository

    service = AutomationConfigService(db_session)
    service.update_automation_config(AutomationConfigUpdate(a4_stalled_deal_days=30), ADMIN)
    assert ActivityRepository(db_session).by_kind(["automation.config_changed"]) == []

    service.update_automation_config(AutomationConfigUpdate(a4_stalled_deal_days=45), ADMIN)
    (entry,) = ActivityRepository(db_session).by_kind(["automation.config_changed"])
    assert entry.payload["a4_stalled_deal_days"] == {"da": 30, "a": 45}


def test_an_admin_can_switch_a_rule_off(db_session: Session) -> None:
    service = AutomationConfigService(db_session)
    updated = service.update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    assert updated.a1_wins_accepted_offer_deal is False
    assert updated.a2_advances_sent_offer_deal is True


def test_an_omitted_field_changes_nothing(db_session: Session) -> None:
    """`exclude_unset`, not `exclude_none`: with two booleans, `None` and "not sent" have
    to be distinguishable or switching A1 off would silently switch A2 on."""
    service = AutomationConfigService(db_session)
    service.update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    after = service.update_automation_config(
        AutomationConfigUpdate(a2_advances_sent_offer_deal=False), ADMIN
    )
    assert after.a1_wins_accepted_offer_deal is False
    assert after.a2_advances_sent_offer_deal is False


def test_false_is_a_value_and_not_a_blank(db_session: Session) -> None:
    """The backend mirror of the frontend rule. A truthiness check here would make
    "switch it off" impossible to express."""
    service = AutomationConfigService(db_session)
    service.update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    described = service.describe_automations(ADMIN)
    assert described.configuration.a1_wins_accepted_offer_deal is False


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    """`extra="forbid"`. A settings form that silently drops a misspelled key reports
    success for a change it did not make."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AutomationConfigUpdate(to_3_offer_rejected_perde_deal=True)  # type: ignore[call-arg]


@pytest.mark.parametrize("actor", [COLLABORATOR, READONLY])
def test_only_an_admin_may_change_it(db_session: Session, actor: Actor) -> None:
    with pytest.raises(PermissionDenied):
        AutomationConfigService(db_session).update_automation_config(
            AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), actor
        )


def test_a_refused_change_is_not_written(db_session: Session) -> None:
    """`require_admin` must run *before* the mutation, not merely before the commit. A
    check placed after `setattr` would leave the change in the session for whatever
    commits next."""
    service = AutomationConfigService(db_session)
    with pytest.raises(PermissionDenied):
        service.update_automation_config(
            AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), READONLY
        )
    described = service.describe_automations(ADMIN)
    assert described.configuration.a1_wins_accepted_offer_deal is True


def test_every_change_writes_an_activity(db_session: Session) -> None:
    """Residuo R5, closed for this table."""
    from pigrocrm.core.activities.repository import ActivityRepository

    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    rows = ActivityRepository(db_session).by_kind(
        ["automation.config_changed"], limit=10
    )
    assert len(rows) == 1
    assert rows[0].actor_id == ADMIN.id
    assert rows[0].payload["a1_wins_accepted_offer_deal"] == {"da": True, "a": False}
    # The unchanged rule is absent, not recorded as unchanged: an audit entry listing
    # every field on every edit is an audit entry nobody reads.
    assert "a2_advances_sent_offer_deal" not in rows[0].payload


def test_a_no_op_update_writes_no_activity(db_session: Session) -> None:
    """Saving a form without touching anything is not a change to audit."""
    from pigrocrm.core.activities.repository import ActivityRepository

    service = AutomationConfigService(db_session)
    service.update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=True), ADMIN
    )
    assert (
        ActivityRepository(db_session).by_kind(["automation.config_changed"], limit=10)
        == []
    )


def test_describe_returns_the_recent_runs(db_session: Session) -> None:
    """§9.5's third surface: the last executions with their outcome, read from
    `activities` by `kind` -- not a new table (§9.4)."""
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a2_advances_sent_offer_deal=False), ADMIN
    )
    described = AutomationConfigService(db_session).describe_automations(ADMIN)
    assert [run.kind for run in described.runs] == ["automation.config_changed"]


def test_the_run_log_carries_the_deal_the_rule_acted_on(db_session: Session) -> None:
    """`deal_id` is filled only when the activity belongs to a deal, and `rule`/`reason`
    come out of the payload Task B5 writes. Without this the run list would show the rule
    fired and not on what, which is the only part a person can check.
    """
    from pigrocrm.core.activities.service import ActivityService

    deal_id = uuid7()
    ActivityService(db_session).record(
        "deal",
        deal_id,
        "automation.not_executed",
        ADMIN,
        {"rule": "A1", "reason": "target_stage_missing"},
    )
    db_session.flush()

    described = AutomationConfigService(db_session).describe_automations(ADMIN)
    run = described.runs[0]
    assert run.kind == "automation.not_executed"
    assert run.deal_id == deal_id
    assert run.rule == "A1"
    assert run.reason == "target_stage_missing"


def test_a_run_on_a_non_deal_entity_reports_no_deal(db_session: Session) -> None:
    """The configuration change itself is recorded against `automation_config`, not a
    deal. `deal_id` must be `None` there rather than the config row's own id -- which is
    what a bare `entity_id` would give, and which would render as a link to a deal that
    does not exist."""
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a1_wins_accepted_offer_deal=False), ADMIN
    )
    described = AutomationConfigService(db_session).describe_automations(ADMIN)
    assert described.runs[0].deal_id is None


def test_by_kind_orders_newest_first_and_respects_the_limit(db_session: Session) -> None:
    """The insertion order is deliberately not the chronological order.

    The brief's own version records five rows in a loop and expects `[4, 3, 2]`, which is
    both the newest-first answer and the reverse-insertion answer -- so it passes against
    `ORDER BY id DESC`, against `ORDER BY occurred_at DESC`, and against anything that
    happens to return rows backwards. Shuffling `occurred_at` separates the three.
    """
    from pigrocrm.core.activities.models import Activity
    from pigrocrm.core.activities.repository import ActivityRepository
    from pigrocrm.core.activities.service import ActivityService

    # Insertion order 0..4; chronological order is 3, 1, 4, 2, 0.
    days = {0: 5, 1: 2, 2: 4, 3: 1, 4: 3}
    service = ActivityService(db_session)
    for index in range(5):
        row: Activity = service.record(
            "deal", uuid7(), "automation.stage_moved", ADMIN, {"n": index}
        )
        row.occurred_at = datetime(2026, 1, days[index], 12, 0, tzinfo=UTC)
    db_session.flush()

    rows = ActivityRepository(db_session).by_kind(["automation.stage_moved"], limit=3)
    assert len(rows) == 3
    assert [r.payload["n"] for r in rows] == [0, 2, 4]


def test_by_kind_matches_every_kind_it_is_given(db_session: Session) -> None:
    """More than one kind in one query, because `describe_automations` asks for all three
    at once. A repository that read only `kinds[0]` would answer every test above."""
    from pigrocrm.core.activities.repository import ActivityRepository
    from pigrocrm.core.activities.service import ActivityService

    service = ActivityService(db_session)
    service.record("deal", uuid7(), "automation.stage_moved", ADMIN, {})
    service.record("deal", uuid7(), "automation.not_executed", ADMIN, {})
    service.record("deal", uuid7(), "deleted", ADMIN, {})
    db_session.flush()

    rows = ActivityRepository(db_session).by_kind(
        ["automation.stage_moved", "automation.not_executed"], limit=10
    )
    assert {r.kind for r in rows} == {"automation.stage_moved", "automation.not_executed"}


def test_by_kind_with_an_empty_kind_list_returns_nothing(db_session: Session) -> None:
    """`IN ()` is a syntax error in some dialects and matches everything if written
    carelessly. An empty filter must mean "nothing", never "all"."""
    from pigrocrm.core.activities.repository import ActivityRepository
    from pigrocrm.core.activities.service import ActivityService

    # A row that a broken empty filter would return, so the assertion is not vacuous.
    ActivityService(db_session).record("deal", uuid7(), "automation.stage_moved", ADMIN, {})
    db_session.flush()

    assert ActivityRepository(db_session).by_kind([], limit=10) == []


def test_the_config_service_exposes_exactly_two_public_methods() -> None:
    """Task B12's exclusion list must be exactly `update_automation_config`, so every
    other public method needs a tool. Two methods, one tool, one exclusion."""
    import inspect

    from pigrocrm.core.automations.config_service import AutomationConfigService

    public = {
        name
        for name, member in inspect.getmembers(
            AutomationConfigService, predicate=inspect.isfunction
        )
        if not name.startswith("_") and member.__qualname__.startswith("AutomationConfigService.")
    }
    assert public == {"describe_automations", "update_automation_config"}
