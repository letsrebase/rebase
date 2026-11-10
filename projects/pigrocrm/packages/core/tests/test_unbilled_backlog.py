"""§6.3's method: the backlog, with no period.

Every figure here is slice 4's formula, called rather than reimplemented. The point of the
method existing at all is that `core/dashboard/` cannot contain
`Σ ROUND(ore × tariffa_applicata, 2)` -- it is a product of two columns, and §3 forbids the
dashboard package any multiplication at all.

Two families of test carry the weight.

The first is about hours with no rate: they are counted separately, they contribute
nothing to the accrued value, and they are never treated as zero-rate hours. A rate of
zero and no rate are different facts (slice 4 §5.1), and a silent zero would say the work
was free. Both directions are asserted, because a test that only shows the unpriced row
excluded from the money passes just as happily against an implementation that drops every
row it cannot value.

The second is about what "not yet invoiced" means. Slice 4 settled that question once, in
`billed_entry_ids`, and settled it as a fact about the **invoice's state** rather than
about the presence of the link: an hour attached to a line of a *draft* is still freely
editable, is not revenue, and must therefore still appear in the backlog -- otherwise the
work is priced, done, and invisible in every figure until somebody presses "issue".
`deal_pnl` and `period_pnl` both already read it that way, so the backlog reads it that
way too. The link-based reading is a real alternative and it is the wrong one here; the
tests below pin the difference in both directions, because a predicate nobody has watched
disagree with its rival is a predicate nobody has chosen.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.timetracking.models import TimeEntry

READONLY = Actor(id=uuid7(), type="user", role="readonly")

EntryFactory = Callable[..., TimeEntry]

TOOLS_DIR = Path(__file__).resolve().parents[3] / "apps" / "mcp" / "src" / "pigrocrm_mcp" / "tools"


def test_an_empty_database_returns_zeroes_and_not_none(db_session: Session) -> None:
    """A fresh installation must render, not crash. `None` here would reach the browser as
    an empty card indistinguishable from a failed request.

    `"0.00"` and not `"0"`: the browser prints what it is given, and money that sometimes
    carries two decimal places and sometimes none is the visible half of a defect no
    numeric comparison in this file would catch -- `Decimal("0") == Decimal("0.00")`.
    """
    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("0.00")
    assert backlog.accrued_value == Decimal("0.00")
    assert str(backlog.accrued_value) == "0.00"
    assert str(backlog.billable_uninvoiced_hours) == "0.00"
    assert backlog.entries_without_rate == 0
    assert backlog.entries == 0


def test_it_sums_billable_unbilled_hours_across_every_period(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """No period. "Quanto ho da fatturare" is not a question about March (§6.3), so an
    entry from two years ago counts exactly as much as one from last month."""
    time_entry_factory(date=date(2024, 1, 15), hours="8.00", rate="50.000000")
    time_entry_factory(date=date(2026, 8, 1), hours="2.50", rate="50.000000")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("10.50")
    assert backlog.accrued_value == Decimal("525.00")
    assert backlog.entries == 2


def test_an_hour_on_a_line_of_an_issued_invoice_is_not_backlog(
    db_session: Session, time_entry_factory: EntryFactory, issued_invoice_line_id: UUID
) -> None:
    """The moment `issue()` commits, the hours are revenue and stop being arrears."""
    time_entry_factory(
        date=date(2026, 8, 1),
        hours="8.00",
        rate="50.000000",
        invoice_line_id=issued_invoice_line_id,
    )
    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("0.00")
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries == 0


def test_an_hour_on_a_line_of_a_draft_is_still_backlog(
    db_session: Session, time_entry_factory: EntryFactory, draft_invoice_line_id: UUID
) -> None:
    """The other half of the same decision, and the one a link-based reading gets wrong.

    A draft is not revenue and its lines are rewritten wholesale by slice 3 without
    orphaning anything, so work sitting on one is still work waiting to be invoiced.
    Reading `invoice_line_id IS NULL` instead would make this row vanish from the backlog
    while appearing in no revenue figure either.
    """
    time_entry_factory(
        date=date(2026, 8, 1),
        hours="8.00",
        rate="50.000000",
        invoice_line_id=draft_invoice_line_id,
    )
    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("8.00")
    assert backlog.accrued_value == Decimal("400.00")
    assert backlog.entries == 1


def test_an_hour_on_an_annulled_invoice_is_backlog_again(
    db_session: Session, time_entry_factory: EntryFactory, annulled_invoice_line_id: UUID
) -> None:
    """An annulled invoice keeps its number and loses its revenue (§7.1) -- the
    struck-through page of a paper register. Its hours are CRM data again, and they are
    still owed by somebody."""
    time_entry_factory(
        date=date(2026, 8, 1),
        hours="8.00",
        rate="50.000000",
        invoice_line_id=annulled_invoice_line_id,
    )
    assert AnalyticsService(db_session).unbilled_backlog(READONLY).entries == 1


def test_an_hour_on_a_confirmed_proforma_is_still_backlog(
    db_session: Session, time_entry_factory: EntryFactory, proforma_invoice_line_id: UUID
) -> None:
    """A proforma never touches the register (slice 3 §5), `confirmed` included -- the
    state closest to an emission that is not one."""
    time_entry_factory(
        date=date(2026, 8, 1),
        hours="8.00",
        rate="50.000000",
        invoice_line_id=proforma_invoice_line_id,
    )
    assert AnalyticsService(db_session).unbilled_backlog(READONLY).entries == 1


def test_a_non_billable_hour_is_not_backlog(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """`billable` is a property of the work, and internal work is never arrears."""
    time_entry_factory(date=date(2026, 8, 1), hours="8.00", rate="50.000000", billable=False)
    assert AnalyticsService(db_session).unbilled_backlog(READONLY).entries == 0


def test_hours_without_a_rate_are_counted_and_valued_at_nothing(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """Slice 4 §5.1 already names this as a figure of its own. A rate of zero and no rate
    are different facts, and treating the second as the first understates the backlog
    with nothing on screen to say so."""
    time_entry_factory(date=date(2026, 8, 1), hours="4.00", rate=None)
    time_entry_factory(date=date(2026, 8, 2), hours="1.00", rate="100.000000")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("5.00")
    assert backlog.accrued_value == Decimal("100.00")
    assert backlog.entries_without_rate == 1
    assert backlog.entries == 2


def test_a_rate_of_zero_is_a_rate(db_session: Session, time_entry_factory: EntryFactory) -> None:
    """The mirror of the test above, and the reason the two columns cannot be collapsed:
    an hour priced at nothing is priced. It is worth `0.00`, and it is **not** one of the
    `entries_without_rate`."""
    time_entry_factory(date=date(2026, 8, 1), hours="4.00", rate="0.000000")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("4.00")
    assert backlog.accrued_value == Decimal("0.00")
    assert backlog.entries_without_rate == 0
    assert backlog.entries == 1


def test_the_value_rounds_per_row_then_sums(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """Slice 4's rule, and the reason it is a rule: three rows at 0.005 differ between
    `Σ ROUND(row)` and `ROUND(Σ exact)` by a cent, which is how a reconciliation stops
    reconciling."""
    for _ in range(3):
        time_entry_factory(date=date(2026, 8, 1), hours="0.10", rate="0.050000")

    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    # ROUND(0.005, 2) = 0.01 half-up, three times. `ROUND(Σ 0.015, 2)` would be 0.02, and
    # `ROUND_HALF_EVEN` per row would be 0.00.
    assert backlog.accrued_value == Decimal("0.03")


def test_a_soft_deleted_entry_is_not_backlog(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    entry = time_entry_factory(date=date(2026, 8, 1), hours="8.00", rate="50.000000")
    entry.deleted_at = datetime.now(UTC)
    db_session.flush()
    assert AnalyticsService(db_session).unbilled_backlog(READONLY).entries == 0


def test_the_period_pnl_carries_the_same_three_figures_for_its_period(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """§5 sources them from `period_pnl`, and slice 4's `PeriodPnl` did not carry them.

    Added to the owning service (§3 permits exactly this), with the **same field names**
    as `DealPnl`: §5's requirement is that the *label* carries the scope -- "nel periodo"
    on the economic dashboard, "in totale" on the operational one -- and that the two are
    never shown side by side.
    """
    time_entry_factory(date=date(2026, 3, 10), hours="4.00", rate="50.000000")
    time_entry_factory(date=date(2026, 6, 10), hours="4.00", rate="50.000000")

    pnl = AnalyticsService(db_session).period_pnl(
        PeriodPnlQuery(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31)), READONLY
    )
    assert pnl.billable_uninvoiced_hours == Decimal("4.00")
    assert pnl.accrued_value == Decimal("200.00")
    assert pnl.hours_without_rate == 0

    # The period-less total sees both.
    backlog = AnalyticsService(db_session).unbilled_backlog(READONLY)
    assert backlog.billable_uninvoiced_hours == Decimal("8.00")
    assert backlog.accrued_value == Decimal("400.00")


def test_the_period_rows_count_their_own_unpriced_hours(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """`hours_without_rate` on a `PeriodPnl` is scoped to the same rows as the two figures
    beside it -- the unbilled billable hours **of that period** -- because the dashboard
    prints the three under one heading ("Maturato e non fatturato — nel periodo"), and a
    count taken over a different population than the sum above it is a figure that cannot
    be reconciled with anything on the same card."""
    time_entry_factory(date=date(2026, 3, 10), hours="4.00", rate=None)
    time_entry_factory(date=date(2026, 6, 10), hours="7.00", rate=None)

    pnl = AnalyticsService(db_session).period_pnl(
        PeriodPnlQuery(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31)), READONLY
    )
    assert pnl.hours_without_rate == 1
    assert pnl.billable_uninvoiced_hours == Decimal("4.00")
    assert pnl.accrued_value == Decimal("0.00")


def test_the_period_rows_honour_the_customer_filter(
    db_session: Session, time_entry_factory: EntryFactory, seeded_deal_id: UUID
) -> None:
    """A customer-scoped P&L that showed the whole backlog would be a figure belonging to
    somebody else, printed on this customer's page."""
    mine = db_session.get(Deal, seeded_deal_id)
    assert mine is not None
    other_customer = Organization(legal_name=f"Altro cliente {uuid4()}")
    db_session.add(other_customer)
    db_session.flush()
    other_deal = Deal(
        name="Progetto altrui",
        customer_id=other_customer.id,
        pipeline_stage_id=mine.pipeline_stage_id,
        probability=10,
    )
    db_session.add(other_deal)
    db_session.flush()

    time_entry_factory(date=date(2026, 3, 10), hours="4.00", rate="50.000000")
    time_entry_factory(
        date=date(2026, 3, 11), hours="9.00", rate="50.000000", deal_id=other_deal.id
    )

    pnl = AnalyticsService(db_session).period_pnl(
        PeriodPnlQuery(
            date_from=date(2026, 3, 1), date_to=date(2026, 3, 31), customer_id=mine.customer_id
        ),
        READONLY,
    )
    assert pnl.billable_uninvoiced_hours == Decimal("4.00")
    assert pnl.accrued_value == Decimal("200.00")


def test_a_readonly_actor_may_read_the_backlog(
    db_session: Session, time_entry_factory: EntryFactory
) -> None:
    """Slice 4 §11 gives every analytics read to every role; the one admin-only figure is
    the fiscal estimate, which is not this. Asserted against a backlog that is not empty,
    so a `PermissionDenied` raised before any row is read would still be caught."""
    time_entry_factory(date=date(2026, 8, 1), hours="4.00", rate="50.000000")
    assert AnalyticsService(db_session).unbilled_backlog(READONLY).entries == 1


def test_unbilled_backlog_is_reachable_from_an_mcp_tool() -> None:
    """§6.3: slice 4's exclusion list must not grow, which is only true if this method has
    a tool. `apps/mcp/tests/test_mcp_surface_coverage.py` is the real, AST-resolved guard;
    this is the cheap one that fires in the same commit that adds the method."""
    source = "\n".join(path.read_text(encoding="utf-8") for path in TOOLS_DIR.rglob("*.py"))
    assert ".unbilled_backlog(" in source
