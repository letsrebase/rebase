"""The daily automation pass: three rules that open a task, once each (REB-727, REB-728).

Slice 7 §10.4 and §10.5 are what this file is for: a rule that runs twice on the same subject
opens **one** task and the second run says why it did not open another; closing the
task lets the next pass open a new one. The concurrent case is proved on two real
connections with a barrier, as the spec asks, and not with two calls in a row.

The ordinary cases run inside `db_session`'s rolled-back transaction. The race cannot: it
needs rows another connection can see, so it commits its own and removes them afterwards.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.automations.config_service import AutomationConfigService
from pigrocrm.core.automations.daily import (
    OFFER_PENDING_DAYS,
    OFFER_WINDOW_DAYS,
    AutomationPass,
    PassReport,
)
from pigrocrm.core.automations.models import AutomationConfig
from pigrocrm.core.automations.schemas import (
    KIND_TASK_OPENED,
    AutomationConfigUpdate,
    TaskRule,
)
from pigrocrm.core.config import get_settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.tasks.models import Task
from pigrocrm.core.tasks.service import TaskService

ADMIN = Actor(id=None, type="user", role="admin")


# -- fixtures: made-up data ------------------------------------------------------------


def _local_noon(days_ago: int) -> datetime:
    """Noon of the emitter's day `days_ago` days back. Not `now() - N days`: that is the
    day in UTC for an hour or two a night, and these tests count whole days."""
    day = today_local() - timedelta(days=days_ago)
    return datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(get_settings().timezone))


def _customer(session: Session, name: str = "Acme S.r.l.") -> Customer:
    customer = Customer(legal_name=name, country="IT", custom_fields={})
    session.add(customer)
    session.flush()
    return customer


def _stage(session: Session, type: str = "open") -> PipelineStage:
    stage = PipelineStage(
        name=f"Fase {type} {datetime.now(UTC).timestamp()}",
        position=0,
        default_probability=10,
        type=type,
    )
    session.add(stage)
    session.flush()
    return stage


def _owner(session: Session, *, active: bool = True) -> User:
    user = User(
        email=f"titolare-{datetime.now(UTC).timestamp()}@example.test",
        password_hash="x",
        name="Maria Rossi",
        role="collaborator",
        active=active,
    )
    session.add(user)
    session.flush()
    return user


def _deal(
    session: Session,
    customer: Customer,
    stage: PipelineStage,
    *,
    name: str = "Sito Acme",
    owner: User | None = None,
    created_days_ago: int = 0,
) -> Deal:
    deal = Deal(
        name=name,
        customer_id=customer.id,
        pipeline_stage_id=stage.id,
        probability=10,
        owner_id=owner.id if owner else None,
        custom_fields={},
    )
    session.add(deal)
    session.flush()
    if created_days_ago:
        deal.created_at = _local_noon(created_days_ago)
        session.flush()
    return deal


def _offer(
    session: Session,
    *,
    deal: Deal | None = None,
    customer: Customer | None = None,
    sent_days_ago: int,
    title: str = "Offerta sito",
    status: str = "sent",
) -> Document:
    document = Document(
        deal_id=deal.id if deal else None,
        customer_id=customer.id if customer else None,
        type="offer",
        title=title,
        status=status,
        status_since=today_local() - timedelta(days=sent_days_ago),
        current_version=1,
        custom_fields={},
    )
    session.add(document)
    session.flush()
    return document


def _invoice(
    session: Session,
    customer: Customer,
    *,
    deal: Deal | None = None,
    overdue_days: int = 3,
    payment: str = "unpaid",
    number: int | None = 12,
    status: str = "issued",
) -> Invoice:
    invoice = Invoice(
        customer_id=customer.id,
        deal_id=deal.id if deal else None,
        type="invoice",
        status=status,
        payment_status=payment,
        year=2026 if number is not None else None,
        number=number,
        taxable_amount=Decimal("100.00"),
        vat_amount=Decimal("22.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("122.00"),
        issue_date=today_local() - timedelta(days=overdue_days + 30),
        due_date=today_local() - timedelta(days=overdue_days),
    )
    session.add(invoice)
    session.flush()
    return invoice


def _run(session: Session, **kwargs: object) -> PassReport:
    return AutomationPass(session).run(**kwargs)  # type: ignore[arg-type]


def _report(report: PassReport, rule: TaskRule) -> tuple[bool, int, int]:
    (one,) = [r for r in report.rules if r.rule == rule]
    return one.disabled, one.open, one.already_open


def _rows(session: Session, rule: TaskRule) -> list[Task]:
    return list(session.scalars(select(Task).where(Task.rule == rule).order_by(Task.created_at)))


@pytest.fixture(autouse=True)
def _clean_slate(db_session: Session) -> None:
    """The shared engine may hold rows another module committed; these tests count."""
    db_session.execute(delete(Task))


# -- offerta_inviata_richiama ----------------------------------------------------------


def test_an_offer_waiting_seven_days_opens_one_task_for_the_deals_owner(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    owner = _owner(db_session)
    deal = _deal(db_session, customer, _stage(db_session), owner=owner)
    _offer(db_session, deal=deal, sent_days_ago=7)

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "sent_offer_follow_up") == (False, 1, 0)
    # The second run names why it opened nothing: the subject already has its task.
    assert _report(second, "sent_offer_follow_up") == (False, 0, 1)
    (row,) = _rows(db_session, "sent_offer_follow_up")
    assert row.title == "Richiamare Acme S.r.l. sull'offerta Offerta sito"
    assert row.status == "open"
    assert row.origin == "automation"
    assert row.deal_id == deal.id
    assert row.assigned_to == owner.id
    # Due seven days after the sending, which is today when the pass runs on day seven.
    assert row.due_date == today_local()


def test_an_offer_sent_six_days_ago_is_not_yet_called_back(db_session: Session) -> None:
    deal = _deal(db_session, _customer(db_session), _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=OFFER_PENDING_DAYS - 1)

    assert _report(_run(db_session), "sent_offer_follow_up") == (False, 0, 0)


def test_an_offer_older_than_the_window_is_not_the_follow_up_of_its_sending(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    deal = _deal(db_session, customer, _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=OFFER_PENDING_DAYS + OFFER_WINDOW_DAYS + 1)
    _offer(
        db_session,
        customer=customer,
        sent_days_ago=OFFER_PENDING_DAYS + OFFER_WINDOW_DAYS,
        title="Offerta limite",
    )

    assert _report(_run(db_session), "sent_offer_follow_up") == (False, 1, 0)
    (row,) = _rows(db_session, "sent_offer_follow_up")
    # Born late when the pass is late: due seven days after the sending, in the past.
    assert row.due_date == today_local() - timedelta(days=OFFER_WINDOW_DAYS)
    assert row.customer_id == customer.id
    assert row.deal_id is None


def test_an_answered_or_draft_offer_is_left_alone(db_session: Session) -> None:
    deal = _deal(db_session, _customer(db_session), _stage(db_session))
    _offer(db_session, deal=deal, sent_days_ago=10, status="accepted")
    _offer(db_session, deal=deal, sent_days_ago=10, status="rejected", title="Altra")
    _offer(db_session, deal=deal, sent_days_ago=10, status="draft", title="Bozza")

    assert _report(_run(db_session), "sent_offer_follow_up") == (False, 0, 0)


# -- deal_fermo ------------------------------------------------------------------------


def test_to_deal_in_the_same_stage_past_the_threshold_opens_one_task_due_today(
    db_session: Session,
) -> None:
    owner = _owner(db_session)
    deal = _deal(
        db_session, _customer(db_session), _stage(db_session), owner=owner, created_days_ago=31
    )

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "stalled_deal") == (False, 1, 0)
    assert _report(second, "stalled_deal") == (False, 0, 1)
    (row,) = _rows(db_session, "stalled_deal")
    assert row.title == "Il deal Sito Acme è fermo da 31 giorni"
    assert row.deal_id == deal.id
    assert row.assigned_to == owner.id
    assert row.due_date == today_local()
    assert row.origin == "automation"


def test_a_recent_stage_change_resets_the_clock_and_a_closed_deal_is_never_stalled(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    moved = _deal(db_session, customer, _stage(db_session), name="Spostato", created_days_ago=90)
    db_session.add(
        Activity(
            entity_type="deal",
            entity_id=moved.id,
            kind="stage_changed",
            actor_type="user",
            payload={},
            occurred_at=_local_noon(2),
        )
    )
    _deal(db_session, customer, _stage(db_session, "won"), name="Vinto", created_days_ago=90)
    _deal(db_session, customer, _stage(db_session, "lost"), name="Perso", created_days_ago=90)
    db_session.flush()

    assert _report(_run(db_session), "stalled_deal") == (False, 0, 0)


def test_the_threshold_is_the_configured_one(db_session: Session) -> None:
    _deal(db_session, _customer(db_session), _stage(db_session), created_days_ago=10)
    assert _report(_run(db_session), "stalled_deal") == (False, 0, 0)

    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(a4_stalled_deal_days=10), ADMIN
    )

    assert _report(_run(db_session), "stalled_deal") == (False, 1, 0)


def test_the_day_a_deal_stalled_since_is_the_installations_not_utcs(db_session: Session) -> None:
    """A stage change a little after local midnight is still the day before in UTC, and the
    threshold is counted from the local day: at 29 local days a deal is under the default
    30, at 30 it is on it, whichever way the query reads the instant."""
    customer = _customer(db_session)
    zone = ZoneInfo(get_settings().timezone)
    just_after_midnight = {
        "under": _deal(db_session, customer, _stage(db_session), name="Sotto", created_days_ago=90),
        "on": _deal(db_session, customer, _stage(db_session), name="Sopra", created_days_ago=90),
    }
    for name, days in (("under", 29), ("on", 30)):
        moved_at = datetime.combine(today_local() - timedelta(days=days), time(0, 30), tzinfo=zone)
        db_session.add(
            Activity(
                entity_type="deal",
                entity_id=just_after_midnight[name].id,
                kind="stage_changed",
                actor_type="user",
                payload={},
                occurred_at=moved_at,
            )
        )
    db_session.flush()

    assert _report(_run(db_session), "stalled_deal") == (False, 1, 0)
    (row,) = _rows(db_session, "stalled_deal")
    assert row.deal_id == just_after_midnight["on"].id


# -- fattura_scaduta -------------------------------------------------------------------


def test_an_overdue_unpaid_invoice_opens_one_task_for_its_deals_owner(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    owner = _owner(db_session)
    deal = _deal(db_session, customer, _stage(db_session), owner=owner)
    invoice = _invoice(db_session, customer, deal=deal)

    first = _run(db_session)
    second = _run(db_session)

    assert _report(first, "overdue_invoice") == (False, 1, 0)
    assert _report(second, "overdue_invoice") == (False, 0, 1)
    (row,) = _rows(db_session, "overdue_invoice")
    assert row.title == "Sollecitare Acme S.r.l. per 2026/12"
    assert row.invoice_id == invoice.id
    assert row.assigned_to == owner.id
    assert row.due_date == today_local()


def test_due_today_paid_draft_and_annulled_invoices_are_not_overdue(db_session: Session) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer, overdue_days=0, number=1)  # due today is not late
    _invoice(db_session, customer, payment="paid", number=2)
    _invoice(db_session, customer, number=None, status="draft")
    cancelled = _invoice(db_session, customer, number=4)
    cancelled.status = "cancelled"
    cancelled.cancelled_at = today_local()
    cancelled.cancellation_reason = "emessa per errore"
    db_session.flush()

    assert _report(_run(db_session), "overdue_invoice") == (False, 0, 0)


def test_closing_the_task_lets_the_next_pass_open_to_new_one(db_session: Session) -> None:
    """Slice 7 §10.5. «Chase Rossi» closed without Rossi paying means «I spoke to him»: the
    next pass opens the next one, and a cancelled one frees the subject the same way."""
    customer = _customer(db_session)
    _invoice(db_session, customer)
    _run(db_session)
    (first,) = _rows(db_session, "overdue_invoice")

    TaskService(db_session).complete(first.id, ADMIN)
    assert _report(_run(db_session), "overdue_invoice") == (False, 1, 0)
    second = [r for r in _rows(db_session, "overdue_invoice") if r.status == "open"]
    assert len(second) == 1 and second[0].id != first.id

    TaskService(db_session).cancel(second[0].id, ADMIN)
    assert _report(_run(db_session), "overdue_invoice") == (False, 1, 0)
    assert len(_rows(db_session, "overdue_invoice")) == 3


def test_a_deal_without_an_owner_falls_back_to_the_customers_owner(db_session: Session) -> None:
    owner = _owner(db_session)
    customer = _customer(db_session)
    customer.owner_id = owner.id
    deal = _deal(db_session, customer, _stage(db_session))
    _invoice(db_session, customer, deal=deal, number=5)
    _invoice(db_session, customer, number=6)  # no deal at all

    _run(db_session)

    rows = _rows(db_session, "overdue_invoice")
    assert [row.assigned_to for row in rows] == [owner.id, owner.id]


def test_an_inactive_owner_leaves_the_task_for_whoever_opens_the_list(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    gone = _owner(db_session, active=False)
    deal = _deal(db_session, customer, _stage(db_session), owner=gone)
    _invoice(db_session, customer, deal=deal)

    _run(db_session)

    (row,) = _rows(db_session, "overdue_invoice")
    assert row.assigned_to is None


# -- switches, rehearsal, timeline -----------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "field"),
    [
        ("sent_offer_follow_up", "a3_sent_offer_follow_up"),
        ("stalled_deal", "a4_stalled_deal"),
        ("overdue_invoice", "a5_overdue_invoice"),
    ],
)
def test_switching_a_rule_off_stops_it_and_the_report_says_so(
    db_session: Session, rule: TaskRule, field: str
) -> None:
    customer = _customer(db_session)
    deal = _deal(db_session, customer, _stage(db_session), created_days_ago=40)
    _offer(db_session, deal=deal, sent_days_ago=9)
    _invoice(db_session, customer)
    AutomationConfigService(db_session).update_automation_config(
        AutomationConfigUpdate(**{field: False}), ADMIN
    )

    report = _run(db_session)

    assert _report(report, rule) == (True, 0, 0)
    assert _rows(db_session, rule) == []
    # The other two are untouched by one rule's switch.
    others = [r for r in report.rules if r.rule != rule]
    assert [(r.disabled, r.open) for r in others] == [(False, 1), (False, 1)]


def test_every_opened_task_leaves_to_system_entry_on_its_own_timeline(
    db_session: Session,
) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer)

    _run(db_session)

    (row,) = _rows(db_session, "overdue_invoice")
    (entry,) = db_session.scalars(
        select(Activity).where(Activity.entity_type == "task", Activity.entity_id == row.id)
    )
    assert entry.kind == KIND_TASK_OPENED
    assert entry.actor_type == "system" and entry.actor_id is None
    assert entry.payload["rule"] == "overdue_invoice"
    assert entry.payload["invoice_id"] == str(row.invoice_id)


def test_a_rehearsal_reports_what_would_open_and_writes_nothing(db_session: Session) -> None:
    customer = _customer(db_session)
    _invoice(db_session, customer)

    rehearsal = _run(db_session, dry_run=True)
    assert _report(rehearsal, "overdue_invoice") == (False, 1, 0)
    assert _rows(db_session, "overdue_invoice") == []

    assert _report(_run(db_session), "overdue_invoice") == (False, 1, 0)


def test_the_index_refuses_to_second_open_task_of_one_rule_and_subject(
    db_session: Session,
) -> None:
    """The constraint itself, below the pass: a writer that skipped the read still cannot
    open two. Different rules, or the same rule after a close, are not refused."""
    customer = _customer(db_session)
    invoice = _invoice(db_session, customer)

    def add(rule: str, status: str = "open") -> None:
        with db_session.begin_nested():
            db_session.add(
                Task(
                    title="x",
                    status=status,
                    completed_at=today_local() if status == "completed" else None,
                    origin="automation",
                    rule=rule,
                    invoice_id=invoice.id,
                    custom_fields={},
                )
            )

    add("overdue_invoice")
    add("overdue_invoice", "completed")
    add("altra_regola")
    with pytest.raises(IntegrityError, match="uq_tasks_rule_open"):
        add("overdue_invoice")


def test_reopening_an_old_task_next_to_the_one_the_pass_opened_is_to_conflict(
    db_session: Session,
) -> None:
    """The index that keeps the pass to one open task also stops a person bringing a
    second one back: a sentence (409 at the API), never a database error."""
    from pigrocrm.core.errors import Conflict

    _invoice(db_session, _customer(db_session))
    _run(db_session)
    (first,) = _rows(db_session, "overdue_invoice")
    service = TaskService(db_session)
    service.complete(first.id, ADMIN)
    _run(db_session)

    with pytest.raises(Conflict, match="chiudi o annulla"):
        service.reopen(first.id, ADMIN)


# -- the race --------------------------------------------------------------------------


@pytest.fixture
def committed_overdue_invoice(db_engine: Engine) -> Iterator[UUID]:
    factory = session_factory(db_engine)
    with factory() as session:
        customer = _customer(session, "RACE Cliente")
        invoice = _invoice(session, customer, number=99)
        # The configuration row exists before the passes start: two first reads racing to
        # create it is a different race, and not the one this test is about.
        session.add(AutomationConfig())
        session.commit()
        invoice_id, customer_id = invoice.id, customer.id
    try:
        yield invoice_id
    finally:
        with factory() as session:
            ids = [invoice_id]
            session.execute(delete(Activity).where(Activity.entity_type == "task"))
            session.execute(delete(Task).where(Task.invoice_id.in_(ids)))
            session.execute(delete(Invoice).where(Invoice.id == invoice_id))
            session.execute(delete(Customer).where(Customer.id == customer_id))
            session.execute(delete(AutomationConfig))
            session.commit()


def test_the_pass_sees_the_space_through_the_application_role(
    db_engine: Engine, committed_overdue_invoice: UUID
) -> None:
    """Under the role the API and the cron connect as, a session with no scope reads nothing
    (row-level policies, REB-634): the pass binds the whole-space scope itself. The suite's
    own user is a superuser, which is never subject to a policy, so every other test here
    would pass on a pass that bound nothing."""
    from sqlalchemy import create_engine

    from pigrocrm.core.db.role import ensure_application_role

    app_url = db_engine.url.set(username="pigrocrm_app_test", password="pigrocrm_app_test")
    assert ensure_application_role(db_engine.url, app_url) is True
    app = create_engine(app_url, future=True)
    try:
        with session_factory(app)() as session:
            report = AutomationPass(session).run()
        assert _report(report, "overdue_invoice") == (False, 1, 0)
        with session_factory(app)() as session:
            assert _report(AutomationPass(session).run(), "overdue_invoice") == (False, 0, 1)
    finally:
        app.dispose()


def test_two_passes_at_the_same_instant_open_one_task(
    db_engine: Engine,
    committed_overdue_invoice: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice 7 §10.4, on two real connections. Both passes read «no open task» before
    either has written (the barrier holds each after its read), both try to insert, and the
    index lets exactly one through: the loser's insert fails on `uq_tasks_rule_open`,
    is absorbed under its savepoint and counted as «già aperta», not raised."""
    factory = session_factory(db_engine)
    after_read = Barrier(2)
    original = AutomationPass._already_open

    def read_then_wait(self: AutomationPass, rule: TaskRule, subjects: set[UUID]) -> set[UUID]:
        seen = original(self, rule, subjects)
        if rule == "overdue_invoice":
            after_read.wait(timeout=30)
        return seen

    monkeypatch.setattr(AutomationPass, "_already_open", read_then_wait)

    def one_pass() -> tuple[bool, int, int]:
        with factory() as session:
            return _report(AutomationPass(session).run(), "overdue_invoice")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(
            f.result(timeout=60) for f in [pool.submit(one_pass), pool.submit(one_pass)]
        )

    # One opened it and one found it already there: never two, never an error.
    assert results == [(False, 0, 1), (False, 1, 0)]
    with factory() as session:
        rows = session.scalars(
            select(Task).where(Task.invoice_id == committed_overdue_invoice)
        ).all()
    assert len(rows) == 1
