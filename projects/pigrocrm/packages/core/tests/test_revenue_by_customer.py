"""Whole-practice, calendar-year client concentration (REB-370):
`AnalyticsRepository.revenue_by_customer` and its embedding in
`AnalyticsService.economic_overview` (§1.5, §5 item 1 of
`docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`).

Buildable off existing `Invoice`/`Organization` data alone, with no dependency on the
ledger milestone: every row here is a raw `Invoice`, never issued through
`InvoiceService`, the same shortcut `test_dashboard_receivables.py` takes for the
same reason -- this is a repository aggregate, not a fiscal-document lifecycle test.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.organizations.models import Organization

ADMIN = Actor(id=None, type="system", role="admin")
COLLABORATOR = Actor(id=None, type="mcp", role="collaborator")
# A year no other fixture in this suite writes an invoice against.
YEAR = 2031


def _invoice(customer_id: UUID, number: int, amount: str, *, year: int = YEAR) -> Invoice:
    return Invoice(
        customer_id=customer_id,
        type="invoice",
        status="issued",
        year=year,
        number=number,
        payment_status="unpaid",
        taxable_amount=Decimal(amount),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal(amount),
        issue_date=date(year, 6, 1),
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )


def _corpus(session: Session) -> tuple[UUID, UUID, UUID]:
    """`grande` at 6000.00 over two invoices, `piccolo` at 3000.00 over one --
    `annual_revenue(YEAR)` is exactly 9000.00, and neither share (2/3, 1/3) is a
    round number the largest-row formula (`InvoiceRepository._share`) would also
    produce, so the two are not accidentally indistinguishable. `absent` has an
    invoice only in the *previous* year: present in the register, absent from this
    method's result, the way `receivables_by_customer` omits a customer with
    nothing outstanding.

    `grande` also carries an annulled invoice for 50000.00, far larger than every
    counted row: a `revenue_by_customer` that forgot `_revenue_filter()` would fail
    both the total and the ranking, not just an edge case nobody would notice.
    """
    large = Organization(legal_name="Grande S.r.l.", country="IT", custom_fields={})
    small = Organization(legal_name="Piccolo S.r.l.", country="IT", custom_fields={})
    absent = Organization(legal_name="Assente S.r.l.", country="IT", custom_fields={})
    session.add_all([large, small, absent])
    session.flush()

    cancelled = _invoice(large.id, 4, "50000.00")
    cancelled.status = "cancelled"
    cancelled.cancelled_at = date(YEAR, 6, 2)
    cancelled.cancellation_reason = "storno di prova"

    session.add_all(
        [
            _invoice(large.id, 1, "4000.00"),
            _invoice(large.id, 2, "2000.00"),
            _invoice(small.id, 3, "3000.00"),
            cancelled,
            _invoice(absent.id, 5, "9999.00", year=YEAR - 1),
        ]
    )
    session.flush()
    return large.id, small.id, absent.id


def test_revenue_by_customer_ranks_by_share_of_the_years_whole_revenue(
    db_session: Session,
) -> None:
    large_id, small_id, absent_id = _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    rows = repo.revenue_by_customer(YEAR)

    # Ranked, largest first -- the acceptance criterion's own word.
    assert [row.customer_id for row in rows] == [large_id, small_id]
    large, small = rows
    assert large.revenue == Decimal("6000.00")
    assert large.invoices == 2
    assert small.revenue == Decimal("3000.00")
    assert small.invoices == 1

    # The share of the *whole year's* revenue (9000.00), never of the largest row:
    # `InvoiceRepository._share`'s formula on this same data would give the top row
    # exactly 1.0, which is the bug this assertion is written to catch.
    assert abs(large.share - 2 / 3) < 1e-9
    assert abs(small.share - 1 / 3) < 1e-9
    assert large.share != 1.0
    assert abs(sum(row.share for row in rows) - 1.0) < 1e-9

    # A customer with no invoice this year is simply absent from the result.
    assert absent_id not in [row.customer_id for row in rows]


def test_revenue_by_customer_excludes_an_annulled_invoice_from_both_total_and_count(
    db_session: Session,
) -> None:
    large_id, _small_id, _absent_id = _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    large = next(row for row in repo.revenue_by_customer(YEAR) if row.customer_id == large_id)

    # The annulled 50000.00 invoice would dwarf every other row; its absence from
    # both the sum and the count is what proves `_revenue_filter()` was applied.
    assert large.revenue == Decimal("6000.00")
    assert large.invoices == 2


def test_revenue_by_customer_agrees_with_annual_revenue_to_the_cent(db_session: Session) -> None:
    _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    rows = repo.revenue_by_customer(YEAR)

    assert sum((row.revenue for row in rows), Decimal("0.00")) == repo.annual_revenue(YEAR)


def test_economic_overview_carries_the_same_concentration_for_every_role(
    db_session: Session,
) -> None:
    large_id, small_id, _absent_id = _corpus(db_session)
    service = AnalyticsService(db_session)

    for actor in (ADMIN, COLLABORATOR):
        overview = service.economic_overview(YEAR, actor)
        assert [row.customer_id for row in overview.customer_concentration] == [
            large_id,
            small_id,
        ]
        assert overview.customer_concentration[0].revenue == Decimal("6000.00")
        assert overview.customer_concentration[0].legal_name == "Grande S.r.l."


# --- count_over_concentration_threshold (REB-371) --------------------------------------


def test_count_over_concentration_threshold_counts_organizations_above_the_share(
    db_session: Session,
) -> None:
    """The COUNT `OperationalDashboard`'s fourth signal reads (§3, §5 item 2 of
    `docs/superpowers/specs/2026-09-23-forecasting-and-analytics-from-mastro-design.md`):
    both `grande` (2/3) and `piccolo` (1/3) cross a permissive 0.30 threshold; only
    `grande` crosses a 0.50 one."""
    _corpus(db_session)
    repo = AnalyticsRepository(db_session)

    assert repo.count_over_concentration_threshold(YEAR, 0.30) == 2
    assert repo.count_over_concentration_threshold(YEAR, 0.50) == 1


def test_count_over_concentration_threshold_is_strictly_above_not_at_it(
    db_session: Session,
) -> None:
    """A customer sitting exactly on the configured share has not yet crossed it -- the
    boundary the signal's own name promises."""
    tied_first = Organization(legal_name="Pari Uno S.r.l.", country="IT", custom_fields={})
    tied_second = Organization(legal_name="Pari Due S.r.l.", country="IT", custom_fields={})
    db_session.add_all([tied_first, tied_second])
    db_session.flush()
    db_session.add_all(
        [
            _invoice(tied_first.id, 101, "5000.00", year=YEAR + 1),
            _invoice(tied_second.id, 102, "5000.00", year=YEAR + 1),
        ]
    )
    db_session.flush()
    repo = AnalyticsRepository(db_session)

    assert repo.count_over_concentration_threshold(YEAR + 1, 0.5) == 0


def test_count_over_concentration_threshold_is_zero_when_the_year_has_no_revenue(
    db_session: Session,
) -> None:
    """`revenue_by_customer` returns nothing for a year with no invoice at all; the
    signal must read that as "nobody is over the threshold", not raise on an empty
    result."""
    repo = AnalyticsRepository(db_session)
    assert repo.count_over_concentration_threshold(YEAR + 2, 0.30) == 0
