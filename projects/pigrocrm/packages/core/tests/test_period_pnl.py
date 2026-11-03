"""§7.4. Two columns, never one total; general expenses in a row of their own and
apportioned onto nobody; and the honesty flags that say whether the figure can still
move."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session
from tax_period import DAY_BEFORE, FIRST_OF_MONTH, TODAY

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.pnl import deal_states
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.locks import PeriodLockService
from pigrocrm.core.timetracking.schemas import (
    CostCreate,
    PeriodLockCreate,
    TimeEntryCreate,
)
from pigrocrm.core.timetracking.service import TimeEntryService

READER = Actor(id=None, type="user", role="readonly")
WRITER = Actor(id=None, type="user", role="collaborator")
# The window the P&L fixtures are dated into, derived and never a literal month: the
# closed deal carries an *issued* invoice, and `issue()` refuses a `issue_date`
# outside the current fiscal year -- so a literal `2026-03` here was a date on which this
# whole file would begin failing. `periodo_fiscale.py` explains why "this year, March"
# would not have been a fix either.
PERIOD = PeriodPnlQuery(date_from=FIRST_OF_MONTH, date_to=TODAY)
# A second window, for the one test that needs the period to be *over*:
# `written_entries_overdue` counts rows whose `created_at` is later than `a`, so a window
# ending today can never contain a late row -- a row written now is written on the last
# day of the period, not after it. The previous calendar month is the nearest window that
# has certainly ended, and it is available on every run day including 1 January. It may
# fall in the previous fiscal year, which is why no test that issues an invoice may use
# it -- and none does: nothing here needs a `issue_date`.
MONTH_CLOSED = PeriodPnlQuery(date_from=DAY_BEFORE.replace(day=1), date_to=DAY_BEFORE)


def test_an_open_deal_and_a_closed_one_never_share_a_total(
    db_session: Session, open_deal_with_hours: UUID, closed_deal_with_invoice: UUID
) -> None:
    """Adding a finished job's margin to a half-done one produces a figure that is
    neither, and that changes every week for reasons which are not business performance.
    The reportable number is `closed`."""
    pnl = AnalyticsService(db_session).period_pnl(PERIOD, READER)
    assert pnl.closed.deal == 1
    assert pnl.in_progress.deal == 1
    # The closed column, in full: 2000.00 invoiced, 200.00 of costs, no hours.
    assert pnl.closed.revenue == Decimal("2000.00")
    assert pnl.closed.direct_costs == Decimal("200.00")
    assert pnl.closed.work_cost == Decimal("0.00")
    assert pnl.closed.gross_margin == Decimal("1800.00")
    assert pnl.closed.percentage_margin == Decimal("90.00")
    # The open one, in full: nothing invoiced, 150.00 of costs, 10h at 40.00 internal.
    assert pnl.in_progress.revenue == Decimal("0.00")
    assert pnl.in_progress.direct_costs == Decimal("150.00")
    assert pnl.in_progress.work_cost == Decimal("400.00")
    assert pnl.in_progress.gross_margin == Decimal("-550.00")
    assert pnl.in_progress.percentage_margin is None
    # And there is deliberately no combined field to read by mistake.
    assert not hasattr(pnl, "total")


def test_the_state_of_each_deal_is_read_in_one_batch_and_agrees_with_its_own_summary(
    db_session: Session,
    open_deal_with_hours: UUID,
    closed_deal_with_invoice: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`period_pnl` used to build a full `deal_summary` (every entry of the deal, summed in
    Python) once per deal in the window. It now asks for the states of all of them at once
    and must never go back to the per-deal read; and the batch has to give each deal the
    state its own summary gives, because that is what `deal_pnl` shows beside it."""
    expected = {
        deal_id: TimeEntryService(db_session).deal_summary(deal_id, READER).status
        for deal_id in (open_deal_with_hours, closed_deal_with_invoice)
    }
    assert deal_states(db_session, list(expected)) == expected
    assert set(expected.values()) == {"in_progress", "closed"}

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("period_pnl read a deal summary per deal")

    monkeypatch.setattr(TimeEntryService, "deal_summary", refuse)
    pnl = AnalyticsService(db_session).period_pnl(PERIOD, READER)
    assert (pnl.closed.deal, pnl.in_progress.deal) == (1, 1)


def test_a_general_expense_has_its_own_row_and_touches_no_deal(
    db_session: Session,
    seeded_category_id: UUID,
    open_deal_with_hours: UUID,
    closed_deal_with_invoice: UUID,
) -> None:
    """Any apportionment key -- on revenue, on hours -- has one precise and unacceptable
    consequence: a deal's margin would move when a *different* deal was invoiced. That is
    exactly the property that makes a number unreportable, and it is the defect the previous
    system's P&L has for personal taxation.

    Both columns are non-zero before the expense lands, so "nothing moved" is a statement
    about real figures and not about two pairs of zeros.
    """
    service = AnalyticsService(db_session)
    before = service.period_pnl(PERIOD, READER)
    assert before.general_expenses == Decimal("0.00")
    assert before.closed.gross_margin == Decimal("1800.00")
    assert before.in_progress.gross_margin == Decimal("-550.00")

    CostService(db_session).create(
        CostCreate(
            deal_id=None,
            category_id=seeded_category_id,
            date=TODAY,
            amount=Decimal("300.00"),
            description="Commercialista",
        ),
        WRITER,
    )
    after = service.period_pnl(PERIOD, READER)
    assert after.general_expenses == Decimal("300.00")
    # Not one deal figure moved -- in either column, and including the percentages, which
    # is where a share of 300.00 would show up even if the absolute rows were rounded
    # back to where they started.
    assert after.closed == before.closed
    assert after.in_progress == before.in_progress


def test_a_customer_filter_excludes_general_expenses_entirely(
    db_session: Session, seeded_category_id: UUID, open_deal_with_hours: UUID
) -> None:
    """A general expense belongs to no customer by definition, so scoping to one must not
    show a share of it -- which would be apportionment through the back door."""
    CostService(db_session).create(
        CostCreate(
            deal_id=None,
            category_id=seeded_category_id,
            date=TODAY,
            amount=Decimal("300.00"),
            description="Commercialista",
        ),
        WRITER,
    )
    unscoped = AnalyticsService(db_session).period_pnl(PERIOD, READER)
    assert unscoped.general_expenses == Decimal("300.00")

    customer_id = db_session.get(Deal, open_deal_with_hours).customer_id  # type: ignore[union-attr]
    scoped = AnalyticsService(db_session).period_pnl(
        PeriodPnlQuery(date_from=PERIOD.date_from, date_to=PERIOD.date_to, customer_id=customer_id),
        READER,
    )
    assert scoped.general_expenses == Decimal("0.00")
    # And the customer's own deal is still there in full: a scope that returned nothing
    # at all would satisfy the assertion above while answering a different question.
    assert scoped.in_progress.deal == 1
    assert scoped.in_progress.direct_costs == Decimal("150.00")
    assert scoped.in_progress.work_cost == Decimal("400.00")


def test_each_quantity_is_attributed_by_its_own_date(
    db_session: Session,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    seeded_category_id: UUID,
) -> None:
    """Revenue by `invoices.issue_date`, costs by `costs.date`, labour cost by
    `time_entries.date`. Not by the deal's date, which does not exist, and not by one
    common date, which none of the three has.

    The boundary is tested on the day *before* the window opens rather than the day after
    it closes. The window ends today, and a `time_entry` dated after today is refused
    outright by `TimeEntryService` -- so "one day past the end" is a row that cannot be
    written at all, and an assertion that it is excluded would be an assertion about
    nothing. `DAY_BEFORE` is the same off-by-one on the other edge, and it is a day that
    always exists.
    """
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            date=FIRST_OF_MONTH,
            hours=Decimal("1.00"),
            description="nel periodo",
            applied_cost=Decimal("50.000000"),
        ),
        WRITER,
    )
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            date=DAY_BEFORE,
            hours=Decimal("1.00"),
            description="fuori periodo",
            applied_cost=Decimal("50.000000"),
        ),
        WRITER,
    )
    CostService(db_session).create(
        CostCreate(
            deal_id=seeded_deal_id,
            category_id=seeded_category_id,
            date=DAY_BEFORE,
            amount=Decimal("10.00"),
            description="fuori periodo",
        ),
        WRITER,
    )
    # A general expense one day before the window, so `general_expenses` is subject to the
    # same boundary as every other row rather than to the whole table.
    CostService(db_session).create(
        CostCreate(
            deal_id=None,
            category_id=seeded_category_id,
            date=DAY_BEFORE,
            amount=Decimal("77.00"),
            description="fuori periodo generale",
        ),
        WRITER,
    )
    pnl = AnalyticsService(db_session).period_pnl(PERIOD, READER)
    assert pnl.in_progress.work_cost == Decimal("50.00")
    assert pnl.in_progress.direct_costs == Decimal("0.00")
    assert pnl.general_expenses == Decimal("0.00")


def test_an_open_period_reports_the_late_entries_and_a_closed_one_says_so(
    db_session: Session, seeded_deal_id: UUID, seeded_user_id: UUID
) -> None:
    """**Criterion 2's second half.** With the period open, a back-dated write succeeds,
    the P&L changes, and the response declares `periodo_chiuso = false` and
    `voci_scritte_in_ritardo = 1`. It is the information that tells a reader whether the
    number can still move.

    `MONTH_CLOSED` and not `PERIOD`: "written late" is only a statement a window that has
    ended can make about itself.
    """
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            date=MONTH_CLOSED.date_to,
            hours=Decimal("2.00"),
            description="retrodatata",
            applied_cost=Decimal("50.000000"),
        ),
        WRITER,
    )
    service = AnalyticsService(db_session)
    open_period = service.period_pnl(MONTH_CLOSED, READER)
    assert open_period.closed_period is False
    # Written today, dated inside a month that ended before today: `created_at > a`.
    assert open_period.written_entries_overdue == 1
    assert open_period.in_progress.work_cost == Decimal("100.00")

    admin = Actor(id=seeded_user_id, type="user", role="admin")
    PeriodLockService(db_session).close_period(
        PeriodLockCreate(year=MONTH_CLOSED.date_to.year, month=MONTH_CLOSED.date_to.month), admin
    )
    closed = service.period_pnl(MONTH_CLOSED, READER)
    assert closed.closed_period is True
    # Closing changes what can still be written, never what was already read: the figures
    # are the same ones, and the late count does not reset on the way through.
    assert closed.in_progress == open_period.in_progress
    assert closed.closed == open_period.closed
    assert closed.written_entries_overdue == 1


def test_a_multi_month_window_is_closed_only_when_every_month_is(
    db_session: Session, seeded_user_id: UUID
) -> None:
    """A window is only as closed as its least-closed month: reporting a quarter as
    closed because one of its months is would be the wrong reassurance in the one place
    it matters."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=1), admin)
    quarter = PeriodPnlQuery(date_from=date(2026, 1, 1), date_to=date(2026, 3, 31))
    assert AnalyticsService(db_session).period_pnl(quarter, READER).closed_period is False
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=2), admin)
    assert AnalyticsService(db_session).period_pnl(quarter, READER).closed_period is False
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=3), admin)
    assert AnalyticsService(db_session).period_pnl(quarter, READER).closed_period is True


def test_a_window_across_a_year_end_enumerates_every_month_it_touches(
    db_session: Session, seeded_user_id: UUID
) -> None:
    """December to January is where month arithmetic goes wrong, and the wrong answer
    here is `periodo_chiuso = true` on a window nobody has closed."""
    admin = Actor(id=seeded_user_id, type="user", role="admin")
    across = PeriodPnlQuery(date_from=date(2025, 12, 1), date_to=date(2026, 1, 31))
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2025, month=12), admin)
    assert AnalyticsService(db_session).period_pnl(across, READER).closed_period is False
    PeriodLockService(db_session).close_period(PeriodLockCreate(year=2026, month=1), admin)
    assert AnalyticsService(db_session).period_pnl(across, READER).closed_period is True


def test_an_inverted_window_is_refused(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        AnalyticsService(db_session).period_pnl(
            PeriodPnlQuery(date_from=date(2026, 3, 31), date_to=date(2026, 3, 1)), READER
        )
    assert excinfo.value.details["field"] == "to"


def test_an_empty_window_returns_typed_zeros_and_a_null_percentage(db_session: Session) -> None:
    pnl = AnalyticsService(db_session).period_pnl(
        PeriodPnlQuery(date_from=date(1999, 1, 1), date_to=date(1999, 1, 31)), READER
    )
    assert pnl.closed.revenue == Decimal("0.00")
    assert pnl.closed.percentage_margin is None
    assert pnl.general_expenses == Decimal("0.00")
    assert pnl.closed.deal == 0
    assert pnl.in_progress.deal == 0
    assert pnl.closed_period is False
    assert pnl.written_entries_overdue == 0


# --- the second reading: revenue by accrual period (ORB-61) --------------------------
#
# Invoicing runs late: August's work is issued in September, and by emission date it is
# September's revenue. The default reading stays emission (§7.1 is a recorded decision,
# and the fiscal reports keep it); `base="accrual"` is a second reading of the same
# report that attributes each invoice to `coalesce(accrual_from, issue_date)`.
# Costs and hours are unaffected: each quantity still goes by its own date.

# The month before the window, whole: the invoice issued today says its work belongs
# here, so under the accrual reading its revenue lands here and nowhere else.
MONTH_PREVIOUS = (DAY_BEFORE.replace(day=1), DAY_BEFORE)


def _won_deal_with_period_invoice(
    session: Session,
    storage: LocalFileStorage,
    *,
    type: str = "invoice",
    amount: Decimal = Decimal("2000.00"),
) -> UUID:
    """A closed-won deal whose single invoice is issued **today** for **last month's**
    work; with `type="proforma"` the document is a dated, confirmed proforma instead,
    which is not revenue under any reading (slice 3 §5: it never touches the register)."""
    from pigrocrm.core.customers.models import Customer
    from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
    from pigrocrm.core.fiscal.service import FiscalProfileService
    from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
    from pigrocrm.core.invoices.service import InvoiceService
    from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
    from pigrocrm.core.legal_entities.service import LegalEntityService
    from pigrocrm.core.pipeline.models import PipelineStage

    admin = Actor(id=None, type="system", role="admin")
    FiscalProfileService(session).upsert(FiscalProfileUpsert(regime_code="RF19"), admin)
    LegalEntityService(session).upsert_default(
        LegalEntityUpsert(
            legal_name="Studio Rossi",
            vat_number="01234567890",
            address="Via Vittorio Veneto 12",
            postal_code="20124",
            city="Milano",
            province="MI",
            country="IT",
        ),
        admin,
    )
    customer = Customer(
        legal_name=f"Acme {uuid4()}",
        vat_number="12345678901",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        postal_code="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    stage = PipelineStage(name=f"Vinto {uuid4()}", position=9, default_probability=100, type="won")
    session.add(stage)
    session.flush()
    deal = Deal(
        name="Lavoro di agosto",
        customer_id=customer.id,
        pipeline_stage_id=stage.id,
        probability=100,
    )
    session.add(deal)
    session.flush()
    service = InvoiceService(session, storage)
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer.id,
            deal_id=deal.id,
            type=type,  # type: ignore[arg-type]
            accrual_from=MONTH_PREVIOUS[0],
            accrual_to=MONTH_PREVIOUS[1],
            lines=[InvoiceLineIn(description="Consulenza", unit_price=amount)],
            **({"issue_date": TODAY} if type == "proforma" else {}),
        ),
        admin,
    )
    if type == "proforma":
        service.confirm_proforma(invoice.id, admin)
    else:
        service.issue(invoice.id, InvoiceIssue(issue_date=TODAY), admin)
    deal_id: UUID = deal.id
    return deal_id


def test_by_default_revenue_follows_the_emission_date(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The recorded decision (§7.1) is untouched: an omitted `base` reads exactly what
    the report read before the second reading existed."""
    _won_deal_with_period_invoice(db_session, local_storage)
    service = AnalyticsService(db_session)
    assert PeriodPnlQuery(date_from=TODAY, date_to=TODAY).base == "issue"
    this_month = service.period_pnl(PERIOD, READER)
    last_month = service.period_pnl(
        PeriodPnlQuery(date_from=MONTH_PREVIOUS[0], date_to=MONTH_PREVIOUS[1]), READER
    )
    assert this_month.closed.revenue == Decimal("2000.00")
    assert this_month.closed.deal == 1
    assert last_month.closed.revenue == Decimal("0.00")
    assert last_month.closed.deal == 0
    # The response says which reading produced it, so a figure read later is not
    # mistaken for the other one.
    assert this_month.base == "issue"


def test_by_accrual_period_the_revenue_lands_in_the_month_the_work_belongs_to(
    db_session: Session, local_storage: LocalFileStorage, seeded_category_id: UUID
) -> None:
    """August's invoice issued in September is August's revenue under this reading, and
    it leaves September, taking the deal with it: a deal listed in a window with no
    figure in it would be revenue silently dropped. Costs keep their own date, so a cost
    dated today stays in this month under both readings."""
    deal_id = _won_deal_with_period_invoice(db_session, local_storage)
    CostService(db_session).create(
        CostCreate(
            deal_id=deal_id,
            category_id=seeded_category_id,
            date=TODAY,
            amount=Decimal("200.00"),
            description="Stampa",
        ),
        WRITER,
    )
    service = AnalyticsService(db_session)
    this_month = service.period_pnl(
        PeriodPnlQuery(date_from=PERIOD.date_from, date_to=PERIOD.date_to, base="accrual"), READER
    )
    last_month = service.period_pnl(
        PeriodPnlQuery(date_from=MONTH_PREVIOUS[0], date_to=MONTH_PREVIOUS[1], base="accrual"),
        READER,
    )
    assert last_month.base == "accrual"
    assert last_month.closed.revenue == Decimal("2000.00")
    assert last_month.closed.deal == 1
    assert last_month.closed.direct_costs == Decimal("0.00")
    # This month keeps the cost and loses the revenue: the deal is still active here
    # (a cost dated today) and its column says what this month actually earned.
    assert this_month.closed.revenue == Decimal("0.00")
    assert this_month.closed.direct_costs == Decimal("200.00")
    assert this_month.closed.deal == 1


def test_an_invoice_with_no_period_falls_back_to_its_emission_date_under_both_readings(
    db_session: Session, closed_deal_with_invoice: UUID
) -> None:
    """`coalesce(accrual_from, issue_date)`: a document that never said what period
    it was for is read by the only date it has, so the accrual reading of a register
    with no periods is the emission reading, figure for figure."""
    service = AnalyticsService(db_session)
    issue = service.period_pnl(PERIOD, READER)
    accrual = service.period_pnl(
        PeriodPnlQuery(date_from=PERIOD.date_from, date_to=PERIOD.date_to, base="accrual"), READER
    )
    assert issue.closed.revenue == Decimal("2000.00")
    assert accrual.closed == issue.closed
    assert accrual.in_progress == issue.in_progress


def test_a_dated_proforma_is_not_revenue_under_either_reading(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """ORB-63 gives a proforma a `issue_date` of its own. `_revenue_filter` selects
    `type = 'invoice' AND status = 'issued'`, so a proforma dated inside the window, with
    a period inside the window, contributes nothing to either column under either base;
    the deal does not even appear, since a proforma is not activity the report counts."""
    _won_deal_with_period_invoice(db_session, local_storage, type="proforma")
    service = AnalyticsService(db_session)
    for base in ("issue", "accrual"):
        for date_from, date_to in ((PERIOD.date_from, PERIOD.date_to), MONTH_PREVIOUS):
            pnl = service.period_pnl(
                PeriodPnlQuery(date_from=date_from, date_to=date_to, base=base), READER
            )  # type: ignore[arg-type]
            assert pnl.closed.revenue == Decimal("0.00"), (base, date_from)
            assert pnl.in_progress.revenue == Decimal("0.00"), (base, date_from)
            assert pnl.closed.deal == 0 and pnl.in_progress.deal == 0, (base, date_from)
