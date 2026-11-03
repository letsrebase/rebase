"""What one week of a space becomes, list by list, before any HTML exists.

Spec 2026-09-16 §3.1 and §3.2. `WeeklyDigest` is the contract the mail (§3.5) and the
command (§3.4) are both written against, so these tests read the *object* and never a
rendering of it: a section that is right here is right in every surface that shows it.

The corpus is committed, and it lives in this file rather than in `conftest.py` for the
reason `test_dashboard_operational.py` records: `DigestService.build` opens the
operational dashboard's `REPEATABLE READ` snapshot first, and that call refuses a session
with a transaction already in progress -- which is exactly what `db_session` holds open.
So the fixture commits on the engine and removes exactly what it wrote, and every read
here goes through a session of its own.

The week is derived from `today_local()` rather than written down as four literals.
`overdue` is «due on or before today», so an overdue invoice pinned to a fixed calendar
day stops being overdue the day somebody re-reads it. The two pure week helpers are the
one thing tested against literals, because they are the one thing that has no clock in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import clock, month_bounds, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.digest.schemas import WeeklyDigest
from pigrocrm.core.digest.service import (
    _MOVEMENTS_READ,
    IN_DUE_DATE_DAYS,
    DigestService,
    iso_week,
    previous_week,
    week_containing,
)
from pigrocrm.core.documents.models import Document
from pigrocrm.core.gmail.models import PaymentReminder
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage
from pigrocrm.core.timetracking.models import TimeEntry

SETTINGS = Settings(_env_file=None, timezone="Europe/Rome")  # type: ignore[call-arg]
# A `readonly` actor on purpose: building the report is a read from end to end, and a
# digest that needed write rights would be a digest the cron could not send for a space
# whose owner is a collaborator.
ACTOR = Actor(id=uuid7(), type="user", role="readonly")

_PREFIX = "DIGEST"


class Corpus(NamedTuple):
    engine: Engine
    date_from: date
    date_to: date
    # `(data_emissione, totale)` of every issued invoice this file writes, so the two
    # month totals can be checked against the corpus instead of against a second call to
    # the same sum.
    issues: tuple[tuple[date, Decimal], ...]


def _last_week() -> tuple[date, date]:
    """The last complete week, computed here rather than through `previous_week`.

    Deriving the fixture's window from the function under test would make a shift of one
    day invisible: the corpus would move with the bug.
    """
    today = today_local(SETTINGS)
    monday = today - timedelta(days=today.isoweekday() - 1 + 7)
    return monday, monday + timedelta(days=6)


def _require_empty(session: Session) -> None:
    """`empty`, the pipeline and the three signals are reads over the whole space with no
    period at all, so the assertions below are only true if this file's rows are the only
    committed ones. A loud precondition beats an off-by-N nobody can read."""
    for model in (Invoice, TimeEntry, Deal, Document):
        leftovers = session.execute(select(func.count(model.id))).scalar_one()
        assert leftovers == 0, (
            f"{leftovers} committed {model.__tablename__} row(s) were already present; "
            "a weekly report has no scope beyond the space, so leftovers change it."
        )


@pytest.fixture(params=[date(2026, 9, 30), date(2026, 10, 4)], ids=["wednesday", "sunday"])
def frozen_day(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> date:
    """Freeze the project's one clock at midday UTC on a Wednesday and on a Sunday, the
    day that *is* the last day of the «in scadenza» window (REB-618), so both readings
    of that window run on every pull request and not only on the weekday CI happens to
    pick. Midday and not midnight, for the reason `test_week_hours.py::_frozen` gives:
    at 00:30 UTC it is already the next day in Rome. The corpus's dates stay derived from
    the clock, as this file's own header asks; what is fixed is the clock, not a date.
    """
    frozen: date = request.param
    monkeypatch.setattr(
        clock,
        "_now",
        lambda: datetime(frozen.year, frozen.month, frozen.day, 12, 0, tzinfo=UTC),
    )
    return frozen


@pytest.fixture
def corpus(db_engine: Engine, frozen_day: date) -> Iterator[Corpus]:
    """One week with something in every section, plus the rows each section must exclude.

    Every date is an offset from the week itself, and the two that must not drift are
    written down here once:

      * the overdue invoice is due thirty days before today, comfortably past
        `payment_reminders_grace_days` (7), so it is a reminder candidate whatever day this runs;
      * the one due `a + 7` is the last day of the «in scadenza» window, or the day after
        on the Sunday that *is* `a + 7`, so it is never also in `overdue` -- the two
        halves of «Da incassare» must not name the same invoice twice (REB-618).
    """
    factory = session_factory(db_engine)
    date_from, date_to = _last_week()
    today = today_local(SETTINGS)
    issues: list[tuple[date, Decimal]] = []
    with factory() as session:
        _require_empty(session)
        legal_entity_id = committed_default_legal_entity(session)
        open = PipelineStage(
            name=f"{_PREFIX} Aperto", position=0, default_probability=20, type="open"
        )
        won = PipelineStage(
            name=f"{_PREFIX} Vinto", position=1, default_probability=100, type="won"
        )
        customer = Customer(legal_name=f"{_PREFIX} Cliente", country="IT", custom_fields={})
        user = User(
            email=f"{_PREFIX.lower()}-{uuid7()}@example.test",
            password_hash="x",
            name="Digestore",
            role="collaborator",
        )
        session.add_all([open, won, customer, user])
        session.flush()

        def _deal(name: str, stage: PipelineStage, value: str) -> Deal:
            deal = Deal(
                name=f"{_PREFIX} {name}",
                customer_id=customer.id,
                pipeline_stage_id=stage.id,
                probability=stage.default_probability,
                expected_value=Decimal(value),
                custom_fields={},
            )
            session.add(deal)
            session.flush()
            return deal

        def _invoice(
            number: int,
            *,
            total: str,
            issue_date: date,
            due_date: date | None = None,
            payment_date: date | None = None,
            deal: Deal | None = None,
        ) -> Invoice:
            amount = Decimal(total)
            invoice = Invoice(
                customer_id=customer.id,
                deal_id=None if deal is None else deal.id,
                type="invoice",
                status="issued",
                payment_status="paid" if payment_date is not None else "unpaid",
                year=today.year,
                number=number,
                taxable_amount=amount,
                vat_amount=Decimal("0.00"),
                stamp_duty=Decimal("0.00"),
                total=amount,
                issue_date=issue_date,
                due_date=due_date,
                payment_date=payment_date,
                sdi_document_type="TD01",
                currency="EUR",
                custom_fields={},
            )
            session.add(invoice)
            session.flush()
            issues.append((issue_date, amount))
            return invoice

        deal_open = _deal("Deal aperto", open, "5000.00")
        deal_moved = _deal("Deal mosso", won, "3000.00")

        # Issued in the week. On an *open* deal, so it also lights the «invoiced but not
        # won» signal and the report has a `signals` list that is not empty.
        _invoice(
            1,
            total="1000.00",
            issue_date=date_from + timedelta(days=1),
            due_date=date_to + timedelta(days=40),
            deal=deal_open,
        )
        # Issued long before, collected inside the week: the row that makes «Incassate»
        # a different section from «Emesse» rather than a second rendering of it.
        _invoice(
            2,
            total="1500.00",
            issue_date=date_from - timedelta(days=60),
            due_date=date_from - timedelta(days=30),
            payment_date=date_from + timedelta(days=2),
        )
        # Overdue by thirty days: the reminder candidate.
        _invoice(
            3,
            total="800.00",
            issue_date=today - timedelta(days=90),
            due_date=today - timedelta(days=30),
        )
        # Due on the last day of the «in scadenza» window, pushed to tomorrow when that
        # day is today: the window's last day is the Sunday of the current week, and on
        # a Sunday in Europe/Rome `overdue` («due on or before today») would count it as
        # overdue by zero days (REB-618).
        _invoice(
            4,
            total="600.00",
            issue_date=date_from - timedelta(days=10),
            due_date=max(date_to + timedelta(days=7), today + timedelta(days=1)),
        )

        session.add(
            TimeEntry(
                deal_id=deal_open.id,
                user_id=user.id,
                date=date_from + timedelta(days=1),
                hours=Decimal("6.00"),
                description=f"{_PREFIX} lavorazione",
                billable=True,
                applied_rate=Decimal("50.000000"),
                rate_source="manual",
                applied_cost=None,
                cost_source="absent",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        session.add(
            Document(
                deal_id=deal_moved.id,
                type="offer",
                title=f"{_PREFIX} Offerta",
                status="sent",
                status_since=date_from - timedelta(days=10),
                custom_fields={},
            )
        )

        def _stage_changed(day: date) -> Activity:
            return Activity(
                entity_type="deal",
                entity_id=deal_moved.id,
                kind="stage_changed",
                actor_id=user.id,
                actor_type="user",
                payload={"from": open.name, "to": won.name},
                # Midday, so the instant lands on the same calendar day in Rome as in UTC
                # and the test is not asserting the timezone conversion by accident --
                # `test_the_week_lists_...` is about the week filter.
                occurred_at=datetime.combine(day, datetime.min.time(), tzinfo=UTC)
                + timedelta(hours=12),
            )

        session.add(_stage_changed(date_from + timedelta(days=2)))
        # The same movement three days before the week opened: the filter must drop it.
        session.add(_stage_changed(date_from - timedelta(days=3)))
        session.commit()
    try:
        yield Corpus(db_engine, date_from, date_to, tuple(issues))
    finally:
        with factory() as session:
            corpus_customers = select(Customer.id).where(Customer.legal_name.like(f"{_PREFIX} %"))
            corpus_deals = select(Deal.id).where(Deal.name.like(f"{_PREFIX} %"))
            session.execute(delete(Activity).where(Activity.entity_id.in_(corpus_deals)))
            session.execute(delete(Document).where(Document.title.like(f"{_PREFIX} %")))
            session.execute(delete(TimeEntry).where(TimeEntry.deal_id.in_(corpus_deals)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(corpus_customers)))
            session.execute(delete(Deal).where(Deal.name.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.legal_name.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.name.like(f"{_PREFIX} %")))
            session.execute(delete(User).where(User.name == "Digestore"))
            remove_legal_entity(session, legal_entity_id)
            session.commit()


def _invoice_in_more(
    engine: Engine, *, number: int, total: str, issue_date: date, due_date: date
) -> UUID:
    """One more unpaid, issued invoice on the corpus's customer, committed.

    On that customer deliberately: the fixture's teardown deletes by `customer_id`, so a
    row added here leaves with the rest of the corpus -- and `payment_reminders` hangs off
    `invoices.id` with `ON DELETE CASCADE`, so the reminders of the test below go too.
    """
    with session_factory(engine)() as session:
        customer_id = session.execute(
            select(Customer.id).where(Customer.legal_name == f"{_PREFIX} Cliente")
        ).scalar_one()
        amount = Decimal(total)
        invoice = Invoice(
            customer_id=customer_id,
            type="invoice",
            status="issued",
            payment_status="unpaid",
            year=today_local(SETTINGS).year,
            number=number,
            taxable_amount=amount,
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=amount,
            issue_date=issue_date,
            due_date=due_date,
            payment_date=None,
            sdi_document_type="TD01",
            currency="EUR",
            custom_fields={},
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def _build(engine: Engine, week: tuple[date, date]) -> WeeklyDigest:
    with session_factory(engine)() as session:
        return DigestService(session, SETTINGS).build(ACTOR, week)


# --- the empty space ------------------------------------------------------------------


def test_an_empty_space_builds_an_empty_digest(db_engine: Engine) -> None:
    """§2: silence for an empty space. The command decides that by asking the report, so
    `empty` has to be true of a space with nothing in it and of nothing else."""
    with session_factory(db_engine)() as session:
        _require_empty(session)
    digest = _build(db_engine, (date(2026, 9, 7), date(2026, 9, 13)))
    assert digest.empty is True
    assert digest.week_stalled is True
    assert digest.week == "2026-W37"
    assert (digest.date_from, digest.date_to) == (date(2026, 9, 7), date(2026, 9, 13))


# --- the week that had something in it ------------------------------------------------


def test_the_week_lists_issued_collected_overdue_and_due(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))

    assert [i.amount for i in digest.issued] == [Decimal("1000.00")]
    assert [i.date for i in digest.issued] == [corpus.date_from + timedelta(days=1)]
    assert [i.number for i in digest.issued] == ["2026/1"]
    assert [i.customer for i in digest.issued] == [f"{_PREFIX} Cliente"]
    assert [i.status for i in digest.issued] == ["issued"]
    assert [i.payment_status for i in digest.issued] == ["unpaid"]

    assert [i.amount for i in digest.paid] == [Decimal("1500.00")]
    # Attributed to the day the money arrived, not to the day the invoice was written.
    assert [i.date for i in digest.paid] == [corpus.date_from + timedelta(days=2)]
    assert [i.payment_status for i in digest.paid] == ["paid"]

    assert [i.days_of_delay for i in digest.overdue] == [30]
    assert [i.amount for i in digest.overdue] == [Decimal("800.00")]
    assert [i.number for i in digest.overdue] == ["2026/3"]

    # The window opens the day after the closed week and runs seven days, so the invoice
    # due on `a + 7` is in it and the overdue one is not.
    # The window is `a + 1` to `a + 7`, minus whatever `overdue` already names (due on
    # or before today). On the Sunday that is `a + 7` nothing in it is still to come,
    # and the corpus's invoice sits on `a + 8` (REB-618): both readings are asserted.
    if corpus.date_to + timedelta(days=7) > today_local(SETTINGS):
        assert [i.amount for i in digest.in_due_date] == [Decimal("600.00")]
        assert [i.date for i in digest.in_due_date] == [corpus.date_to + timedelta(days=7)]
    else:
        assert digest.in_due_date == []

    assert digest.hours is not None
    assert digest.hours.total_hours == Decimal("6.00")
    assert (digest.hours.date_from, digest.hours.date_to) == (corpus.date_from, corpus.date_to)

    assert [m.stage_name for m in digest.deal_moves] == [f"{_PREFIX} Vinto"]
    assert [m.title for m in digest.deal_moves] == [f"{_PREFIX} Deal mosso"]
    assert [m.when for m in digest.deal_moves] == [corpus.date_from + timedelta(days=2)]

    assert len(digest.offers_pending) == 1
    assert digest.offers_pending[0].title == f"{_PREFIX} Offerta"
    assert (
        digest.offers_pending[0].days
        == (today_local(SETTINGS) - (corpus.date_from - timedelta(days=10))).days
    )

    assert digest.week_stalled is False
    assert digest.empty is False


def test_the_months_are_the_register_sums_and_not_a_second_count(corpus: Corpus) -> None:
    """§3.1: the running month beside the one before it. Both come from
    `sum_issued_in_period`, so they are checked against the corpus's own emission dates
    rather than against another call to the same sum."""
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    current_from, current_to = month_bounds(corpus.date_to.year, corpus.date_to.month)
    previous = current_from - timedelta(days=1)
    previous_from, previous_to = month_bounds(previous.year, previous.month)

    def _sum(first: date, last: date) -> Decimal:
        return sum(
            (amount for day, amount in corpus.issues if first <= day <= last),
            Decimal("0.00"),
        )

    assert digest.total_month_current == _sum(current_from, current_to)
    assert digest.total_month_previous == _sum(previous_from, previous_to)


def test_the_backlog_and_the_signals_come_from_the_operational_dashboard(
    corpus: Corpus,
) -> None:
    """§3.1's «Da emettere» and «Da sistemare». The hours and the accrued value are
    `AnalyticsService`'s backlog verbatim, and only the signals that fired are carried.

    `won_to_invoice` is asserted twice, at zero and at one: a field checked only
    against zero is a field a `return 0` would satisfy, and this one is a count of the
    deals somebody is supposed to invoice this week.

    `concentration_above_threshold` (REB-371) is in the list too: the corpus's one
    customer holds the whole register, past the default 0.30 threshold, which is what
    makes this also a test of the `conteggio > 0` filter carrying that fourth signal
    exactly the way it already carries the first two.
    """
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    assert digest.unbilled_hours == Decimal("6.00")
    assert digest.accrued_value == Decimal("300.00")
    # The won deal has no unbilled hours, so «vinto ma da fatturare» is zero -- which is
    # what makes this the test of the `conteggio > 0` filter and not only of the list.
    assert digest.won_to_invoice == 0
    assert [s.code for s in digest.signals] == [
        "invoiced_unwon",
        "overdue_unpaid",
        "concentration_above_threshold",
    ]
    assert all(s.count > 0 for s in digest.signals)
    assert all(s.link for s in digest.signals)

    # One won deal with billable hours nobody has put on a document: exactly
    # `won_with_unbilled_hours_predicate`, which is where «da fatturare» is defined.
    with session_factory(corpus.engine)() as session:
        won_id = session.execute(
            select(PipelineStage.id).where(PipelineStage.name == f"{_PREFIX} Vinto")
        ).scalar_one()
        customer_id = session.execute(
            select(Customer.id).where(Customer.legal_name == f"{_PREFIX} Cliente")
        ).scalar_one()
        user_id = session.execute(select(User.id).where(User.name == "Digestore")).scalar_one()
        deal = Deal(
            name=f"{_PREFIX} Deal vinto da fatturare",
            customer_id=customer_id,
            pipeline_stage_id=won_id,
            probability=100,
            expected_value=Decimal("2000.00"),
            custom_fields={},
        )
        session.add(deal)
        session.flush()
        session.add(
            TimeEntry(
                deal_id=deal.id,
                user_id=user_id,
                date=corpus.date_from + timedelta(days=3),
                hours=Decimal("4.00"),
                description=f"{_PREFIX} da fatturare",
                billable=True,
                applied_rate=Decimal("50.000000"),
                rate_source="manual",
                applied_cost=None,
                cost_source="absent",
                invoice_line_id=None,
                custom_fields={},
            )
        )
        session.commit()

    updated = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    assert updated.won_to_invoice == 1
    assert "won_to_invoice" in [s.code for s in updated.signals]


def test_the_pipeline_carries_only_the_stages_that_hold_something(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    assert [(s.stage_name, s.number) for s in digest.pipeline] == [
        (f"{_PREFIX} Aperto", 1),
        (f"{_PREFIX} Vinto", 1),
    ]
    assert [s.total_value for s in digest.pipeline] == [
        Decimal("5000.00"),
        Decimal("3000.00"),
    ]


def test_a_movement_just_after_midnight_belongs_to_the_week_it_opens(corpus: Corpus) -> None:
    """`activities.occurred_at` is an instant; the week is seven days in the emitter's zone.

    23:30 UTC on the Sunday before the week is 00:30 or 01:30 on its Monday in Rome,
    whichever side of the DST change it falls. Read as a UTC day it lands in the week
    before -- the week the report already went out for -- and the movement is never
    mentioned at all. This is the one test that fails if the conversion is dropped.
    """
    with session_factory(corpus.engine)() as session:
        deal_id = session.execute(
            select(Deal.id).where(Deal.name == f"{_PREFIX} Deal mosso")
        ).scalar_one()
        session.add(
            Activity(
                entity_type="deal",
                entity_id=deal_id,
                kind="stage_changed",
                actor_id=None,
                actor_type="system",
                payload={"from": f"{_PREFIX} Aperto", "to": f"{_PREFIX} Vinto"},
                occurred_at=datetime.combine(
                    corpus.date_from - timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                + timedelta(hours=23, minutes=30),
            )
        )
        session.commit()
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    assert sorted(m.when for m in digest.deal_moves) == [
        corpus.date_from,
        corpus.date_from + timedelta(days=2),
    ]


def test_a_week_keeps_its_movements_however_many_came_after_it(corpus: Corpus) -> None:
    """A resend of an old week (`pigrocrm digest --data`) must report *that* week.

    The read is bounded -- `_MOVEMENTS_READ` rows, newest first -- so a read by kind
    alone answers the latest movements of the whole space and this week's are filtered
    out of a list they were never in. Here every one of the two hundred and one rows
    below happened *after* the week, which is enough to push its own movement past the
    ceiling: with a window in the query the week still has its deal mosso, with a filter
    in Python it silently has none.
    """
    with session_factory(corpus.engine)() as session:
        deal_id = session.execute(
            select(Deal.id).where(Deal.name == f"{_PREFIX} Deal mosso")
        ).scalar_one()
        after = datetime.combine(
            corpus.date_to + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        ) + timedelta(hours=12)
        for i in range(_MOVEMENTS_READ + 1):
            session.add(
                Activity(
                    entity_type="deal",
                    entity_id=deal_id,
                    kind="stage_changed",
                    actor_id=None,
                    actor_type="system",
                    payload={"from": f"{_PREFIX} Aperto", "to": f"{_PREFIX} Vinto"},
                    occurred_at=after + timedelta(seconds=i),
                )
            )
        session.commit()

    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))

    assert [m.when for m in digest.deal_moves] == [corpus.date_from + timedelta(days=2)]


def test_an_invoice_that_fell_due_inside_the_week_is_money_to_collect(corpus: Corpus) -> None:
    """The one «Da incassare» most needs to name, and the one it used to lose.

    `overdue` was `PaymentRemindersService.candidates`, whose predicate is «overdue by more than
    `payment_reminders_grace_days`» -- seven days by default -- because chasing the day after the
    due date is aggressive. That is a property of a *reminder*. An invoice that fell due
    on the last day of the reported week is at most seven days late on the Monday the
    report goes out, so it was inside the grace period every single time and never
    appeared at all: the report said nothing about the money that had just come due.

    The due date is `corpus.a`, the last day of the week, precisely because that is the
    worst case: whatever day this test runs, `a` is between one and seven days back.
    """
    invoice = _invoice_in_more(
        corpus.engine,
        number=10,
        total="900.00",
        issue_date=corpus.date_from - timedelta(days=20),
        due_date=corpus.date_to,
    )
    delay = (today_local(SETTINGS) - corpus.date_to).days
    # The grace period the old source applied, and the proof this row was inside it.
    assert 1 <= delay <= SETTINGS.payment_reminders_grace_days

    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))

    lines = {line.invoice_id: line for line in digest.overdue}
    assert invoice in lines
    assert lines[invoice].days_of_delay == delay
    assert lines[invoice].amount == Decimal("900.00")
    assert lines[invoice].customer == f"{_PREFIX} Cliente"
    assert lines[invoice].payment_status == "unpaid"
    # Worst first: the corpus's thirty-day invoice comes before this week's own.
    assert [line.days_of_delay for line in digest.overdue] == [30, delay]


def test_an_invoice_due_today_is_overdue_with_no_days_of_delay(corpus: Corpus) -> None:
    """Due today is due today: `list_overdue` is `<=` and not `_overdue_predicate()`'s
    `<`, so the invoice appears with zero days of delay rather than being left out of the
    section until tomorrow.

    Today is also inside the «in scadenza» window on the Monday the cron runs -- that
    window opens on `a + 1`, which *is* today -- so this is the row that proves the two
    halves still name one invoice once.
    """
    today = today_local(SETTINGS)
    invoice = _invoice_in_more(
        corpus.engine,
        number=11,
        total="700.00",
        issue_date=today - timedelta(days=30),
        due_date=today,
    )

    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))

    lines = {line.invoice_id: line for line in digest.overdue}
    assert lines[invoice].days_of_delay == 0
    assert (
        corpus.date_to + timedelta(days=1)
        <= today
        <= corpus.date_to + timedelta(days=IN_DUE_DATE_DAYS)
    )
    assert invoice not in [line.invoice_id for line in digest.in_due_date]


def test_an_invoice_chased_to_the_ceiling_is_still_owed(corpus: Corpus) -> None:
    """`PaymentRemindersService.candidates` also drops an invoice with
    `payment_reminders_max_reminders` rows and one chased inside
    `payment_reminders_min_interval_days`: there is no further reminder to prepare,
    and nothing to prepare one from today. Neither says the money arrived.

    Three reminders, all sent today, is both conditions at once on the corpus's own
    thirty-day invoice -- and it still has to be the first line of «Da incassare».
    """
    with session_factory(corpus.engine)() as session:
        overdue = session.execute(
            select(Invoice.id).where(Invoice.year == 2026, Invoice.number == 3)
        ).scalar_one()
        for sequence in range(1, SETTINGS.payment_reminders_max_reminders + 1):
            session.add(
                PaymentReminder(invoice_id=overdue, sequence=sequence, sent_at=datetime.now(UTC))
            )
        session.commit()

    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))

    lines = {line.invoice_id: line for line in digest.overdue}
    assert overdue in lines
    assert lines[overdue].days_of_delay == 30
    assert lines[overdue].amount == Decimal("800.00")


def test_an_invoice_that_is_already_overdue_is_not_also_in_due_date(corpus: Corpus) -> None:
    """The two halves of «Da incassare» never name the same invoice.

    `overdue` is «due on or before today» and `in_due_date` is «due in the seven days
    after the week», and the two windows meet: on the Monday the cron runs they share
    exactly today, and for a week recomputed with `--date` the whole of the second one has
    since gone by and lies inside the first. Here it is the corpus's own overdue invoice,
    and the week is chosen so that its due date falls inside the «in scadenza» window that
    opens the day after.
    """
    today = today_local(SETTINGS)
    week = week_containing(today - timedelta(days=IN_DUE_DATE_DAYS + 30))
    with session_factory(corpus.engine)() as session:
        overdue = session.execute(
            select(Invoice.id).where(Invoice.year == 2026, Invoice.number == 3)
        ).scalar_one()

    digest = _build(corpus.engine, week)

    # The invoice is due inside the window the week opens: without the dedupe it is read
    # twice, once as overdue and once as still coming.
    assert week[1] + timedelta(days=1) <= today - timedelta(days=30)
    assert today - timedelta(days=30) <= week[1] + timedelta(days=IN_DUE_DATE_DAYS)
    assert overdue in [i.invoice_id for i in digest.overdue]
    assert overdue not in [i.invoice_id for i in digest.in_due_date]


def test_an_invoice_with_no_date_at_all_is_skipped_and_logged_by_its_id(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """`_line`'s three predicates cannot produce a row with no date, so the row that
    arrives without one is one this report has no honest line for: `date.min` would put
    «lun 1 gen» of the year 1 in a mail every Monday. That row alone is left out, so one
    odd invoice does not cost the whole space its week, and its id is in the log because
    that is what the operator has to look up."""
    invoice = Invoice(
        id=uuid7(),
        customer_id=uuid7(),
        type="invoice",
        status="draft",
        year=None,
        number=None,
        taxable_amount=Decimal("10.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal("10.00"),
        issue_date=None,
        due_date=None,
        payment_date=None,
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )
    with session_factory(db_engine)() as session, caplog.at_level("WARNING"):
        service = DigestService(session, SETTINGS)
        assert service._line(invoice, {}, None) is None
        # The row is left out of a list and the rest of the list survives it.
        assert service._lines([invoice], {}, lambda row: row.payment_date) == []
    assert str(invoice.id) in caplog.text


def test_a_quiet_week_is_flagged_but_the_space_is_not_empty(corpus: Corpus) -> None:
    """§3.1's variable closing. Nothing was issued, collected, logged or moved in the week
    of 2020 -- but the space still owes money and still has a pipeline, so the report goes
    out with its «settimana ferma» closing rather than not going out at all."""
    digest = _build(corpus.engine, week_containing(date(2020, 1, 8)))
    assert (digest.issued, digest.paid, digest.deal_moves) == ([], [], [])
    assert digest.hours is None
    assert digest.week_stalled is True
    assert digest.empty is False
    assert digest.overdue != []
    assert digest.pipeline != []


# --- the three week helpers -----------------------------------------------------------


def test_previous_week_and_iso_week() -> None:
    assert week_containing(date(2026, 9, 16)) == (date(2026, 9, 14), date(2026, 9, 20))
    # A Monday and a Sunday are both inside their own week, both ends included.
    assert week_containing(date(2026, 9, 14)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert week_containing(date(2026, 9, 20)) == (date(2026, 9, 14), date(2026, 9, 20))
    assert iso_week(date(2026, 9, 14)) == "2026-W38"
    # Two digits, always: `2026-W7` sorts after `2026-W38` as a string.
    assert iso_week(date(2026, 2, 9)) == "2026-W07"
    # The last days of December belong to the first ISO week of the next year, which is
    # why the year comes from `isocalendar()` and never from `day.year`.
    assert iso_week(date(2026, 12, 31)) == "2026-W53"
    assert iso_week(date(2027, 1, 1)) == "2026-W53"


def test_previous_week_is_the_current_one_shifted_back_seven_days() -> None:
    date_from, date_to = previous_week(SETTINGS)
    assert (date_from, date_to) == _last_week()
    assert date_to - date_from == timedelta(days=6)
    assert date_from.isoweekday() == 1


def test_the_week_the_digest_reports_is_the_one_it_was_given(corpus: Corpus) -> None:
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    assert (digest.date_from, digest.date_to) == (corpus.date_from, corpus.date_to)
    assert digest.week == iso_week(corpus.date_from)


def test_deal_ids_and_invoice_ids_are_the_real_rows(corpus: Corpus) -> None:
    """Every row carries the id of the thing it describes, because §3.1 requires every
    line of the mail to link to the record it came from."""
    digest = _build(corpus.engine, (corpus.date_from, corpus.date_to))
    with session_factory(corpus.engine)() as session:
        issued = session.execute(
            select(Invoice.id).where(Invoice.year == 2026, Invoice.number == 1)
        ).scalar_one()
        moved = session.execute(
            select(Deal.id).where(Deal.name == f"{_PREFIX} Deal mosso")
        ).scalar_one()
    assert [i.invoice_id for i in digest.issued] == [issued]
    assert [m.deal_id for m in digest.deal_moves] == [moved]
    assert isinstance(digest.issued[0].invoice_id, UUID)
