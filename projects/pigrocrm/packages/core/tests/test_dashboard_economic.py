"""**Criterion 1**, in both directions. §5's economic dashboard.

A figure on this page has to reconcile *down* -- the dashboard agrees with the service
that owns the quantity -- and *up* -- that service agrees with the rows somebody actually
wrote. A test that recomputes its expectation with the same query the code uses proves
only that the code is deterministic, so every figure here is pinned to a literal derived
by hand from the fixture's own rows, and cross-read once through a raw `SELECT` that
touches no service at all.

The fixture writes an **RF01** shape, where `taxable_amount` and `total` diverge: `1000.00`
net at 22% is `1220.00` gross. Under the forfettario profile the suite ships the two
columns are equal, so a corpus built on the default cannot tell a correct implementation
from one that reads the wrong column -- revenue must follow `taxable_amount` and the
receivable must follow `total`, and each assertion below states the wrong value it must
*not* equal so the mutation is named rather than merely excluded.

The invoices are attached to deals on purpose. `AnalyticsRepository.revenue_in_range`
groups by `deal_id` and skips `deal_id IS NULL` entirely, so a corpus of unlinked
invoices would report zero revenue and every reconciliation here would pass against an
implementation that read nothing at all. Two deals, one on an `open` stage and one on a
`won` stage with no unbilled hours, also put a known figure in *each* of §7.4's two
columns rather than only in their sum.

This file uses committed sessions of its own, like `test_dashboard_commercial.py`:
`DashboardService` sets the isolation level as its first statement and Postgres refuses
that once a transaction has begun, so `db_session` -- which holds an outer one open --
cannot be used.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, Select, delete, func, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.schemas import PeriodPnlQuery
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import EconomicDashboard, PeriodQuery
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import month_bounds, session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.pipeline.models import PipelineStage

READONLY = Actor(id=uuid7(), type="user", role="readonly")

_PREFIX = "ECON"
_DA = date(2026, 3, 1)
_A = date(2026, 3, 31)

# Every figure below, derived by hand from the four rows the fixture writes. They are
# literals rather than expressions for the reason criterion 1 exists: an expectation
# recomputed the way the code computes it agrees with a wrong implementation.
_REVENUE_IN_PROGRESS = Decimal("1000.00")  # the March invoice on the open deal, imponibile
_REVENUE_CLOSED = Decimal("500.00")  # the March invoice on the won deal, imponibile
_REVENUE_PERIOD = Decimal("1500.00")  # their sum, which is what the raw SELECT returns
_REVENUE_SE_SEGUISSE_TOTAL = Decimal("1830.00")  # 1220.00 + 610.00
_UNPAID = Decimal("14028.78")  # 1220.00 + 610.00 + 12198.78, no period
_UNPAID_SE_SEGUISSE_TAXABLE_AMOUNT = Decimal("11499.00")  # 1000 + 500 + 9999
_EXPIRED = Decimal("1220.00")  # the one row whose data_scadenza is already past
_ISSUED_INVOICES = 2


class Corpus(NamedTuple):
    engine: Engine
    open_id: UUID
    won_id: UUID


@pytest.fixture
def rf01_corpus(db_engine: Engine) -> Iterator[Corpus]:
    """Four invoices whose `taxable_amount` and `total` differ, committed.

    Two deals, because the P&L splits its rows into `closed` and `in_progress` by the state
    of the deal each invoice belongs to (`TimeEntryService.deal_summary`): a deal on an
    `open` stage is "in corso", a deal on a `won` stage with no unbilled billable hours is
    "chiuso". One known figure in each column is what makes the split assertable rather
    than only their total.

    The stages carry the `_PREFIX` in their names and are deleted individually in the
    teardown -- not with a wholesale `DELETE FROM pipeline_stages`, which is what
    `test_dashboard_commercial.py` does and can only do because it is the file that seeds
    the defaults.
    """
    factory = session_factory(db_engine)
    with factory() as session:
        _require_an_empty_register(session)
        legal_entity_id = committed_default_legal_entity(session)
        open = PipelineStage(
            nome=f"{_PREFIX} aperto", position=0, default_probability=20, type="open"
        )
        won = PipelineStage(
            nome=f"{_PREFIX} vinto", position=1, default_probability=100, type="won"
        )
        customer = Customer(legal_name=f"{_PREFIX} Cliente", country="IT", custom_fields={})
        session.add_all([open, won, customer])
        session.flush()
        deal_open = Deal(
            nome=f"{_PREFIX} in corso",
            customer_id=customer.id,
            pipeline_stage_id=open.id,
            probability=20,
            custom_fields={},
        )
        deal_won = Deal(
            nome=f"{_PREFIX} chiuso",
            customer_id=customer.id,
            pipeline_stage_id=won.id,
            probability=100,
            closed_at=date(2026, 3, 25),
            custom_fields={},
        )
        session.add_all([deal_open, deal_won])
        session.flush()
        # In the period, on the deal still in progress, and already past its due date:
        # this row alone is `expired`, which is what makes that figure distinguishable
        # from `unpaid` instead of trivially equal to it.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_open.id,
            taxable_amount="1000.00",
            total="1220.00",
            issue_date=date(2026, 3, 10),
            due_date=date(2026, 4, 10),
        )
        # In the period, on the won deal, not yet due.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_won.id,
            taxable_amount="500.00",
            total="610.00",
            issue_date=date(2026, 3, 20),
            due_date=None,
        )
        # Outside the period and unpaid: in `unpaid`, in neither revenue column and
        # not in `issued_invoices`. The asymmetry §5.2 is about.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_open.id,
            taxable_amount="9999.00",
            total="12198.78",
            issue_date=date(2026, 4, 1),
            due_date=None,
        )
        # In the period and annulled: in no figure at all. A row that only a filter can
        # exclude, and the register keeps it.
        _add_invoice(
            session,
            customer_id=customer.id,
            deal_id=deal_won.id,
            status="cancelled",
            taxable_amount="7777.00",
            total="9487.94",
            issue_date=date(2026, 3, 15),
            due_date=date(2026, 4, 15),
        )
        session.commit()
        ids = Corpus(db_engine, deal_open.id, deal_won.id)
    try:
        yield ids
    finally:
        with factory() as session:
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(_corpus_customers())))
            session.execute(delete(Deal).where(Deal.nome.like(f"{_PREFIX} %")))
            session.execute(delete(Customer).where(Customer.legal_name.like(f"{_PREFIX} %")))
            session.execute(delete(PipelineStage).where(PipelineStage.nome.like(f"{_PREFIX} %")))
            remove_legal_entity(session, legal_entity_id)
            session.commit()


def _corpus_customers() -> Select[UUID]:
    return select(Customer.id).where(Customer.legal_name.like(f"{_PREFIX} %"))


def _require_an_empty_register(session: Session) -> None:
    """`unpaid` and `expired` have **no period and no scope**: they are sums over the
    whole register. So the literals above are only true if this file's rows are the only
    committed invoices in the database, and that is a precondition worth failing loudly on
    rather than an off-by-N nobody can read. Every other suite in `packages/core/tests`
    writes its invoices on `db_session`, which is rolled back.
    """
    leftovers = session.execute(select(func.count(Invoice.id))).scalar_one()
    assert leftovers == 0, (
        f"{leftovers} committed invoice(s) were already in the register. The whole-register "
        "figures on this dashboard have no period and no scope, so another suite leaving "
        "invoices behind changes this file's expected values."
    )


def _add_invoice(
    session: Session,
    *,
    customer_id: UUID,
    deal_id: UUID,
    taxable_amount: str,
    total: str,
    issue_date: date,
    due_date: date | None,
    status: str = "issued",
) -> None:
    """Written directly, not through `InvoiceService`: that path recomputes `taxable_amount`
    and `total` from the lines under the forfettario profile the suite ships, which makes
    them equal -- exactly the case these tests must be able to step outside of.

    `year`/`number` stay null: `ck_invoices_year_number_together` requires them to move as
    a pair, and no figure on this page reads either.
    """
    session.add(
        Invoice(
            customer_id=customer_id,
            deal_id=deal_id,
            type="invoice",
            status=status,
            payment_status="unpaid",
            taxable_amount=Decimal(taxable_amount),
            vat_amount=Decimal("0.00"),
            stamp_duty=Decimal("0.00"),
            total=Decimal(total),
            issue_date=issue_date,
            due_date=due_date,
            sdi_document_type="TD01",
            currency="EUR",
            custom_fields={},
        )
    )


def _dashboard(
    engine: Engine, date_from: date | None = _DA, date_to: date | None = _A
) -> EconomicDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_economic_dashboard(
            PeriodQuery(date_from=date_from, date_to=date_to), READONLY
        )


# --- criterion 1 downward: the dashboard against the service that owns the figure ----


def test_the_revenue_equals_the_owning_service_to_the_cent(rf01_corpus: Corpus) -> None:
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        pnl = AnalyticsService(session).period_pnl(
            PeriodPnlQuery(date_from=_DA, date_to=_A), READONLY
        )
    assert result.pnl.closed.revenue == pnl.closed.revenue
    assert result.pnl.in_progress.revenue == pnl.in_progress.revenue
    assert isinstance(result.pnl.closed.revenue, Decimal)


def test_the_pnl_is_embedded_verbatim_and_not_flattened(rf01_corpus: Corpus) -> None:
    """§5: there is no new aggregate on this page. Embedding the owning service's own model
    makes the reconciliation an identity rather than a comparison, and leaves no place for
    a field to be renamed or recombined on the way through."""
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        pnl = AnalyticsService(session).period_pnl(
            PeriodPnlQuery(date_from=_DA, date_to=_A), READONLY
        )
    assert result.pnl == pnl


def test_the_period_reaches_the_owning_service_unchanged(rf01_corpus: Corpus) -> None:
    """The composition's one real opportunity to lie: `period_pnl` echoes back the window
    it was given, so a dashboard that resolved one period and queried another is visible
    here and nowhere else. `customer_id` is `None` because §5 has no customer filter -- the
    dashboard is the whole business."""
    result = _dashboard(rf01_corpus.engine)
    assert (result.pnl.date_from, result.pnl.date_to) == (_DA, _A)
    assert result.period.date_from == _DA
    assert result.period.date_to == _A
    assert result.pnl.customer_id is None


# --- criterion 1 upward: the service against the rows somebody wrote -----------------


def test_the_revenue_equals_a_direct_sql_sum_on_an_independent_path(
    rf01_corpus: Corpus,
) -> None:
    """A path that touches no service, no repository and no ORM filter. If the dashboard
    and the P&L were both wrong in the same way, the comparison above would still agree --
    this one would not.

    Adding the two columns *inside a test* is not the total §7.4 forbids showing: the claim
    being checked is that `closed` and `in_progress` partition the same rows the raw `SELECT`
    sees, which is the property that makes the split honest rather than a place for a row
    to fall between two cards.
    """
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        direct = session.execute(
            text(
                "SELECT COALESCE(SUM(imponibile), 0) FROM invoices "
                "WHERE tipo = 'fattura' AND stato = 'emessa' AND deleted_at IS NULL "
                "AND data_emissione BETWEEN :da AND :a"
            ),
            {"da": _DA, "a": _A},
        ).scalar_one()
    assert Decimal(direct) == _REVENUE_PERIOD
    assert result.pnl.closed.revenue + result.pnl.in_progress.revenue == Decimal(direct)


def test_each_column_carries_its_own_known_figure(rf01_corpus: Corpus) -> None:
    """Not only the sum. A dashboard whose two columns were swapped, or which put every
    invoice in one of them, reconciles perfectly against a single total."""
    result = _dashboard(rf01_corpus.engine)
    assert result.pnl.closed.revenue == _REVENUE_CLOSED
    assert result.pnl.in_progress.revenue == _REVENUE_IN_PROGRESS


def test_the_revenue_follows_taxable_amount_and_fails_if_it_follows_total(
    rf01_corpus: Corpus,
) -> None:
    """§5.1: `fatturato = Σ imponibile`, and this slice introduces no third meaning."""
    result = _dashboard(rf01_corpus.engine)
    total = result.pnl.closed.revenue + result.pnl.in_progress.revenue
    assert total == _REVENUE_PERIOD
    assert total != _REVENUE_SE_SEGUISSE_TOTAL, "revenue must follow `taxable_amount`, not `total`"


def test_unpaid_follows_total_and_fails_if_it_follows_taxable_amount(
    rf01_corpus: Corpus,
) -> None:
    """§5.2, specularly. The two quantities are not interchangeable: a receivable is what
    must arrive in the bank, VAT included -- money collected on the State's behalf."""
    result = _dashboard(rf01_corpus.engine)
    assert result.unpaid == _UNPAID
    assert result.unpaid != _UNPAID_SE_SEGUISSE_TAXABLE_AMOUNT, (
        "da_incassare must follow `total`, not `taxable_amount`"
    )


def test_expired_is_to_strict_subset_of_unpaid(rf01_corpus: Corpus) -> None:
    """A subset, and neither equal to it nor zero: `<=` alone passes against a `expired`
    wired to the same aggregate as `unpaid` *and* against one wired to nothing."""
    result = _dashboard(rf01_corpus.engine)
    assert result.expired == _EXPIRED
    assert result.expired < result.unpaid


def test_the_receivable_has_no_period_and_the_revenue_does(rf01_corpus: Corpus) -> None:
    """The asymmetry, made explicit: an invoice issued in April and unpaid is still owed
    when the period asked about is March."""
    result = _dashboard(rf01_corpus.engine)
    assert result.issued_invoices == _ISSUED_INVOICES
    assert result.unpaid > (result.pnl.closed.revenue + result.pnl.in_progress.revenue)


def test_a_period_with_no_invoices_still_reports_the_whole_receivable(
    rf01_corpus: Corpus,
) -> None:
    """The sharpest form of the same asymmetry, and the one that catches a `unpaid`
    that was quietly given the period: on a window where nothing was issued, the revenue
    and the count go to zero and the receivable does not move."""
    result = _dashboard(rf01_corpus.engine, date(2026, 1, 1), date(2026, 1, 31))
    assert result.issued_invoices == 0
    assert result.pnl.closed.revenue + result.pnl.in_progress.revenue == Decimal("0.00")
    assert result.unpaid == _UNPAID
    assert result.expired == _EXPIRED


def test_an_annulled_invoice_is_in_no_figure(rf01_corpus: Corpus) -> None:
    """The corpus carries an annulled March invoice of 7777.00/9487.94, already past due.
    It is in the register and in none of the four figures -- the struck-through page."""
    result = _dashboard(rf01_corpus.engine)
    assert result.pnl.closed.revenue + result.pnl.in_progress.revenue == _REVENUE_PERIOD
    assert result.issued_invoices == _ISSUED_INVOICES
    assert result.unpaid == _UNPAID
    assert result.expired == _EXPIRED


# --- the shape of the page ------------------------------------------------------------


def test_the_period_defaults_to_the_current_month(rf01_corpus: Corpus) -> None:
    result = _dashboard(rf01_corpus.engine, None, None)
    today = today_local()
    assert (result.period.date_from, result.period.date_to) == month_bounds(today.year, today.month)
    assert (result.pnl.date_from, result.pnl.date_to) == (
        result.period.date_from,
        result.period.date_to,
    )


def test_calculated_at_is_the_instant_of_one_transaction(rf01_corpus: Corpus) -> None:
    """Bracketed by the database's own clock, as `test_dashboard_commercial.py` does: the
    container's clock and the host's drift by milliseconds."""
    with session_factory(rf01_corpus.engine)() as session:
        before = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    result = _dashboard(rf01_corpus.engine)
    with session_factory(rf01_corpus.engine)() as session:
        after = session.execute(text("SELECT clock_timestamp()")).scalar_one()
    assert before <= result.calculated_at <= after
    assert result.calculated_at.tzinfo is not None


def test_the_service_runs_in_repeatable_read(rf01_corpus: Corpus) -> None:
    """One endpoint, one transaction, one instant. Proved for the commercial dashboard by
    a second connection committing mid-flight; asserted here because this page adds a
    second *service* to the same transaction and the level must survive that."""
    with session_factory(rf01_corpus.engine)() as session:
        DashboardService(session).get_economic_dashboard(
            PeriodQuery(date_from=_DA, date_to=_A), READONLY
        )
        level = session.execute(text("SHOW transaction_isolation")).scalar_one()
    assert level == "repeatable read"


def test_the_margin_is_reported_in_two_columns_with_no_sum_of_them(
    rf01_corpus: Corpus,
) -> None:
    """Slice 4 §7.4: closed and in-progress, and the reportable figure is the first. There
    is deliberately no field holding their sum -- adding a finished job's margin to a
    half-done one produces a figure that is neither, and that moves every week for reasons
    which are not business performance."""
    result = _dashboard(rf01_corpus.engine)
    fields = set(type(result).model_fields)
    assert "margine_totale" not in fields
    assert "margine_complessivo" not in fields
    assert result.pnl.closed is not None
    assert result.pnl.in_progress is not None


def test_whether_the_period_can_still_move_is_on_the_response(rf01_corpus: Corpus) -> None:
    """Slice 4 §6.4: the only information that tells a reader whether the number can still
    change. Shown beside the total, never in a footnote."""
    result = _dashboard(rf01_corpus.engine)
    assert isinstance(result.pnl.closed_period, bool)
    assert isinstance(result.pnl.written_entries_overdue, int)


def test_no_fiscal_field_appears_anywhere_on_this_dashboard(rf01_corpus: Corpus) -> None:
    """§5.3: the fiscal estimate stays at /app/analisi/fiscale, admin-only. A dashboard is
    the screen most likely to end up in a screenshot or a screen share. Checked by field
    name over the rendered JSON, not by intention."""
    result = _dashboard(rf01_corpus.engine)
    rendered = result.model_dump_json()
    for forbidden in (
        "imponibile_fiscale",
        "substitute_tax",
        "contributi",
        "netto_stimato",
        "profitability_coefficient",
        "substitute_tax_rate",
        "social_security_rate",
    ):
        assert forbidden not in rendered, forbidden


def test_no_deal_level_margin_and_no_year_on_year_comparison() -> None:
    """§5.3's other two absences. Ranking deals by margin is the feature slice 4 §13
    refuses to enable by inertia, and a year-on-year comparison whose prior period is
    partly written is worse than none."""
    fields = set(EconomicDashboard.model_fields)
    for forbidden in ("deal_in_evidenza", "margine_per_deal", "anno_precedente", "confronto"):
        assert forbidden not in fields, forbidden
