"""What a switched-off module does to the jobs (spec 2026-11-09, A2): the reminder detectors
of its types are not run, the automation rules and the weekly digest's sections that read it
are skipped, and nothing is deleted: switched on again, the same pass raises them again."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session
from test_automation_daily import _customer, _deal, _report, _run, _stage
from test_digest_build import SETTINGS as DIGEST_SETTINGS
from test_digest_build import Corpus, _build, corpus, frozen_day  # noqa: F401
from test_reminders_detectors import TODAY, World, world  # noqa: F401

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory
from pigrocrm.core.reminders.detectors import DETECTORS
from pigrocrm.core.space_settings import SpaceSetting
from pigrocrm.core.tasks.models import Task


def _switch(session: Session, **modules: bool) -> None:
    for key, value in modules.items():
        session.add(SpaceSetting(key=f"module_{key}", value="true" if value else "false"))
    session.flush()


# -- reminders --------------------------------------------------------------------------


def test_every_detector_of_a_module_is_tied_to_it() -> None:
    by_type = {detector.kind.key: detector.modules for detector in DETECTORS}

    assert by_type["deal_past_close"] == by_type["deal_stale"] == ("deals",)
    assert by_type["timer_running_long"] == ("time",) == by_type["hours_unbilled"]
    assert by_type["contract_expiring"] == ("contracts",)
    assert by_type["worked_without_approval"] == ("contracts",)
    assert by_type["ceiling_approaching"] == ("taxes",)
    # The core is never tied: a task, an invoice and the reminders' own health.
    assert by_type["task_due"] == by_type["invoice_overdue"] == by_type["job_late"] == ()


def test_the_deal_reminders_are_not_raised_while_deals_are_off_and_return_with_them(
    world: World,  # noqa: F811
) -> None:
    world.deal.expected_close_date = TODAY - timedelta(days=5)
    world.task(TODAY, assigned_to=world.alice.id)
    assert ("deal_past_close", world.deal.id) in world.raised(world.evaluate())

    _switch(world.session, deals=False, time=False)
    off = world.evaluate()
    raised = world.raised(off)

    assert not any(kind in {"deal_past_close", "deal_stale"} for kind, _ in raised)
    assert "deal_past_close" not in off.ran
    # The core's reminders go on.
    assert any(kind == "task_due" for kind, _ in raised)

    world.session.execute(delete(SpaceSetting))
    assert ("deal_past_close", world.deal.id) in world.raised(world.evaluate())


# -- automations ------------------------------------------------------------------------


def test_the_deal_rules_are_skipped_while_deals_are_off(db_session: Session) -> None:
    db_session.execute(delete(Task))
    _deal(db_session, _customer(db_session), _stage(db_session), created_days_ago=31)
    assert _report(_run(db_session), "stalled_deal") == (False, 1, 0)
    db_session.execute(delete(Task))

    _switch(db_session, deals=False, time=False)
    report = _run(db_session)

    assert "stalled_deal" not in {r.rule for r in report.rules}
    assert "sent_offer_follow_up" not in {r.rule for r in report.rules}
    # The core's rule still runs, and no task was opened for the deal.
    assert "overdue_invoice" in {r.rule for r in report.rules}
    assert db_session.query(Task).filter(Task.rule == "stalled_deal").count() == 0


# -- the weekly digest ------------------------------------------------------------------


@pytest.fixture
def _no_switch_left(corpus: Corpus) -> Iterator[None]:  # noqa: F811
    yield
    with session_factory(corpus.engine)() as session:
        session.execute(delete(SpaceSetting))
        session.commit()


def test_the_digest_leaves_out_what_a_switched_off_module_owns(
    corpus: Corpus,  # noqa: F811
    _no_switch_left: None,
    db_engine: Engine,
) -> None:
    week = (corpus.date_from, corpus.date_to)
    before = _build(corpus.engine, week)
    assert before.pipeline, "the corpus has deals, or this proves nothing"

    with session_factory(corpus.engine)() as session:
        _switch(session, deals=False, time=False)
        session.commit()
    after = _build(corpus.engine, week)

    assert (after.pipeline, after.deal_moves, after.offers_pending) == ([], [], [])
    assert after.hours is None and after.won_to_invoice == 0
    assert after.unbilled_hours == 0 and after.accrued_value == 0
    assert not {signal.code for signal in after.signals} & {"invoiced_unwon", "won_to_invoice"}
    # The register is the core's and is the same.
    assert [line.number for line in after.issued] == [line.number for line in before.issued]
    assert DIGEST_SETTINGS.space_profile is None


def test_the_operational_dashboard_drops_the_signals_of_a_module_that_is_off(
    db_engine: Engine,
) -> None:
    def codes(modules: frozenset[str] | None) -> list[str]:
        with session_factory(db_engine)() as session:
            board = DashboardService(session, DIGEST_SETTINGS).get_operational_dashboard(
                Actor(id=None, type="user", role="readonly"), modules=modules
            )
        return [signal.code for signal in board.signals]

    everything = codes(None)
    assert {"invoiced_unwon", "won_to_invoice", "overdue_unpaid"} <= set(everything)

    assert codes(frozenset({"contracts", "purchases", "taxes"})) == [
        "overdue_unpaid",
        "concentration_above_threshold",
    ]
    # Deals on but time off: the signal that needs both goes, the one about deals stays.
    assert "won_to_invoice" not in codes(frozenset({"deals"}))
    assert "invoiced_unwon" in codes(frozenset({"deals"}))


def test_the_operational_dashboard_leaves_out_what_a_module_that_is_off_owns(
    db_engine: Engine,
) -> None:
    kind = "modules_jobs.feed_test"
    with session_factory(db_engine)() as session:
        for entity in ("deal", "time_entry", "contract", "organization"):
            session.add(
                Activity(
                    entity_type=entity,
                    entity_id=uuid4(),
                    kind=kind,
                    actor_id=None,
                    actor_type="system",
                    payload={},
                    occurred_at=datetime.now(UTC),
                )
            )
        session.commit()

    def board(modules: frozenset[str] | None, role: str = "readonly") -> Any:
        with session_factory(db_engine)() as session:
            return DashboardService(session, DIGEST_SETTINGS).get_operational_dashboard(
                Actor(id=None, type="user", role=role),  # type: ignore[arg-type]
                modules=modules,
            )

    def mine(read: Any) -> set[str]:
        return {a.entity_type for a in read.recent_activity if a.kind == kind}

    try:
        everything = frozenset({"deals", "contracts", "time", "purchases", "taxes"})
        on = board(everything)
        assert on.week is not None and on.backlog.billable_uninvoiced_hours is not None
        assert mine(on) == {"deal", "time_entry", "contract", "organization"}

        off = board(everything - {"time", "deals"})
        # Without `time` there is no week of hours and the arrears carry no hours.
        assert off.week is None and off.backlog.billable_uninvoiced_hours is None
        assert mine(off) == {"contract", "organization"}
        # An administrator's feed is not the filtered one, and follows the modules too.
        assert mine(board(everything - {"contracts"}, role="admin")) == {
            "deal",
            "time_entry",
            "organization",
        }
    finally:
        with session_factory(db_engine)() as session:
            session.execute(delete(Activity).where(Activity.kind == kind))
            session.commit()


def test_the_catalogue_and_the_preferences_list_only_the_types_a_space_can_receive(
    db_session: Session,
) -> None:
    from pigrocrm.core.reminders.catalogue import REMINDER_TYPES
    from pigrocrm.core.reminders.detectors import off_types
    from pigrocrm.core.reminders.preferences import ReminderPreferenceService

    actor = Actor(id=None, type="system", role="admin")
    every_module = frozenset({"deals", "contracts", "time", "purchases", "taxes"})
    assert off_types(None) == off_types(every_module) == frozenset()

    off = off_types(every_module - {"deals", "time", "taxes"})
    assert {"deal_stale", "timer_running_long", "hours_unbilled", "ceiling_approaching"} <= off
    assert not off & {"task_due", "invoice_overdue", "contract_expiring"}
    assert off <= set(REMINDER_TYPES)

    everything = ReminderPreferenceService(db_session)
    scoped = ReminderPreferenceService(db_session, off)
    assert [item.key for item in everything.catalogue()] == list(REMINDER_TYPES)
    assert {item.key for item in scoped.catalogue()} == set(REMINDER_TYPES) - off
    read = scoped.read(actor)
    assert {item.key for item in read.types} == {item.type for item in read.effective}
    assert not {item.type for item in read.effective} & off
