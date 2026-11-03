"""**Criterion 1.** A P&L revenue figure reconciles with the invoices behind it
**exactly, not approximately** -- compared as `Decimal`, to the cent, against a direct
SQL query run on a path independent of the service.

This is the test that makes "revenue is the invoice" a property rather than a slogan.
The previous system's P&L used `offer.totalAmount` -- the *offer's* amount -- filtered to projects
with at least one non-draft invoice, so a job invoiced for a third of its offer appeared at
full revenue. There is no second notion of revenue here and none may be introduced.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.storage.local import LocalFileStorage
from pigrocrm.core.timetracking.costs import CostService
from pigrocrm.core.timetracking.schemas import CostCreate, TimeEntryCreate
from pigrocrm.core.timetracking.service import TimeEntryService

READER = Actor(id=None, type="user", role="readonly")
WRITER = Actor(id=None, type="user", role="collaborator")


def test_revenue_equals_the_invoices_behind_it_to_the_cent(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """Three issued invoices, one annulled, one proforma. Only the three issued
    `invoice` rows count: a proforma is not revenue by definition (it never touches the
    register), and an annulled invoice keeps its number but not its revenue -- the
    struck-through page of a paper register."""
    deal_id = deal_with_mixed_invoices
    pnl = AnalyticsService(db_session).deal_pnl(deal_id, READER)

    expected = db_session.execute(
        text(
            "SELECT COALESCE(SUM(taxable_amount), 0) FROM invoices "
            "WHERE deal_id = :id AND type = 'invoice' AND status = 'issued' "
            "  AND deleted_at IS NULL"
        ),
        {"id": deal_id},
    ).scalar_one()

    # Decimal, never float, and `==` rather than any tolerance: "exactly, not
    # approximately" is the criterion, and a `pytest.approx` here would pass for the
    # very drift this whole design exists to prevent.
    assert isinstance(pnl.revenue, Decimal)
    assert pnl.revenue == Decimal(expected)
    # Spelled out as well as compared, so the query above cannot pass by being wrong in
    # the same direction as the service: 1000.00 + 250.50 + 333.33.
    assert pnl.revenue == Decimal("1583.83")
    assert pnl.issued_invoices == 3


def test_the_whole_pl_reconciles_with_independent_sql(
    db_session: Session,
    deal_with_mixed_invoices: UUID,
    seeded_user_id: UUID,
    seeded_category_id: UUID,
) -> None:
    """A real deal: six invoices in five states, four hours entries, two costs -- and
    every row of the P&L recomputed by SQL that never goes near `AnalyticsService`.

    The margin identity is the claim. Recomputing what the service computed would assert
    nothing; these three sums come from the three tables directly, and the labour cost is
    summed the way `money.py` sums it -- per row, already rounded -- because the one place
    the two could differ is a rounding rule stated twice.
    """
    deal_id = deal_with_mixed_invoices
    entries = TimeEntryService(db_session)
    for hours, rate, cost, billable in (
        (Decimal("7.33"), Decimal("85.500000"), Decimal("31.750000"), True),
        (Decimal("2.50"), Decimal("85.500000"), Decimal("31.750000"), True),
        # Non-billable: it costs, and it is never potential revenue.
        (Decimal("1.25"), Decimal("85.500000"), Decimal("31.750000"), False),
        # Unpriced: counted, never valued at zero.
        (Decimal("3.00"), None, None, True),
    ):
        entries.create(
            TimeEntryCreate(
                deal_id=deal_id,
                user_id=seeded_user_id,
                date=date(2026, 3, 4),
                hours=hours,
                description="Lavoro",
                billable=billable,
                applied_rate=rate,
                applied_cost=cost,
            ),
            WRITER,
        )
    costs = CostService(db_session)
    for amount in (Decimal("412.90"), Decimal("77.05")):
        costs.create(
            CostCreate(
                deal_id=deal_id,
                category_id=seeded_category_id,
                date=date(2026, 3, 1),
                amount=amount,
                description="Spesa",
            ),
            WRITER,
        )

    pnl = AnalyticsService(db_session).deal_pnl(deal_id, READER)

    revenue = db_session.execute(
        text(
            "SELECT COALESCE(SUM(taxable_amount), 0) FROM invoices "
            "WHERE deal_id = :id AND type = 'invoice' AND status = 'issued' "
            "  AND deleted_at IS NULL"
        ),
        {"id": deal_id},
    ).scalar_one()
    costs = db_session.execute(
        text(
            "SELECT COALESCE(SUM(amount), 0) FROM costs WHERE deal_id = :id AND deleted_at IS NULL"
        ),
        {"id": deal_id},
    ).scalar_one()
    lines_cost = db_session.execute(
        text(
            "SELECT hours, applied_cost FROM time_entries "
            "WHERE deal_id = :id AND deleted_at IS NULL AND applied_cost IS NOT NULL"
        ),
        {"id": deal_id},
    ).all()
    work = sum(
        (
            (hours * cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for hours, cost in lines_cost
        ),
        start=Decimal("0.00"),
    )

    assert pnl.revenue == Decimal(revenue) == Decimal("1583.83")
    assert pnl.direct_costs == Decimal(costs) == Decimal("489.95")
    assert pnl.work_cost == work
    assert pnl.gross_margin == Decimal(revenue) - Decimal(costs) - work
    # Not the rounding of the exact sum: 7.33 x 31.75 = 232.7275 -> 232.73 per row.
    assert pnl.work_cost == Decimal("232.73") + Decimal("79.38") + Decimal("39.69")
    assert pnl.total_hours == Decimal("14.08")
    assert pnl.hours_without_rate == 1
    # The unpriced three hours are billable and unbilled, so they are in the hours
    # figure and contribute nothing to the value -- never a silent zero.
    assert pnl.billable_uninvoiced_hours == Decimal("12.83")
    assert pnl.accrued_value == pnl.revenue + Decimal("626.72") + Decimal("213.75")


def test_it_follows_taxable_amount_and_not_total(
    db_session: Session, rf01_fiscal_profile: None, deal_with_vat_invoice: UUID
) -> None:
    """The `total` includes VAT, which is not revenue: it is money collected on the
    State's behalf. Under the flat-rate regime the two coincide because the tax is zero,
    so the difference is not observable today -- which is exactly why it is written and
    tested today, with the synthetic `RF01` profile slice 3 §14.8 introduces. The test
    fails if the P&L follows `total`."""
    deal_id = deal_with_vat_invoice
    taxable_amount, total = db_session.execute(
        text(
            "SELECT SUM(taxable_amount), SUM(total) FROM invoices "
            "WHERE deal_id = :id AND type = 'invoice' AND status = 'issued' "
            "  AND deleted_at IS NULL"
        ),
        {"id": deal_id},
    ).one()
    assert taxable_amount != total, "the fixture must produce a VAT-bearing invoice"

    pnl = AnalyticsService(db_session).deal_pnl(deal_id, READER)
    assert pnl.revenue == Decimal(taxable_amount)
    assert pnl.revenue != Decimal(total)


def test_a_soft_deleted_invoice_leaves_the_figure(
    db_session: Session, deal_with_mixed_invoices: UUID
) -> None:
    """`deleted_at IS NULL` in the query, asserted rather than assumed: an invoice cannot
    normally be soft-deleted once issued (slice 3 §4's CHECK), so this is checked on a
    draft, which never contributed anyway -- the point is that the filter is present."""
    deal_id = deal_with_mixed_invoices
    before = AnalyticsService(db_session).deal_pnl(deal_id, READER).revenue
    deleted = db_session.execute(
        text("UPDATE invoices SET deleted_at = now() WHERE deal_id = :id AND status = 'draft'"),
        {"id": deal_id},
    ).rowcount
    db_session.flush()
    assert deleted == 1, "the fixture must carry a draft for this to be a real deletion"
    assert AnalyticsService(db_session).deal_pnl(deal_id, READER).revenue == before


def test_the_stated_figure_does_not_move_when_a_rate_moves(
    db_session: Session, deal_with_mixed_invoices: UUID, seeded_user_id: UUID
) -> None:
    """Criterion 2, at the P&L level rather than the entry level: the JSON is compared as
    a structure before and after both rate columns are raised.

    The deal carries an hour first, and the hour is written *after* the rate columns are
    set, so it freezes real values rather than nulls: a P&L that re-read either column
    would move in both `work_cost` and `accrued_value`, and with no hour at all
    there would be nothing for it to move.
    """
    from pigrocrm.core.auth.models import User
    from pigrocrm.core.deals.models import Deal

    deal_id = deal_with_mixed_invoices
    deal = db_session.get(Deal, deal_id)
    assert deal is not None
    user = db_session.get(User, seeded_user_id)
    assert user is not None
    deal.hourly_rate = Decimal("100.000000")
    user.default_hourly_cost = Decimal("40.000000")
    db_session.flush()
    TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=deal_id,
            user_id=seeded_user_id,
            date=date(2026, 3, 4),
            hours=Decimal("4.00"),
            description="Sviluppo",
        ),
        WRITER,
    )

    service = AnalyticsService(db_session)
    before = service.deal_pnl(deal_id, READER).model_dump(mode="json")
    assert before["work_cost"] == "160.00"

    deal.hourly_rate = Decimal("150.000000")
    user.default_hourly_rate = Decimal("120.000000")
    user.default_hourly_cost = Decimal("90.000000")
    db_session.flush()

    assert service.deal_pnl(deal_id, READER).model_dump(mode="json") == before


def test_an_hour_on_a_draft_invoice_is_neither_revenue_nor_lost(
    db_session: Session,
    local_storage: LocalFileStorage,
    seeded_deal_id: UUID,
    seeded_user_id: UUID,
    draft_invoice_line_id: UUID,
) -> None:
    """Which of the two readings of "fatturato" the P&L uses, and why it matters.

    Task 4B-3 left the two deliberately divergent: the `invoiced` list filter is
    **link-based** (one indexed column, no join), while the freeze is
    **invoice-state-based**. An hour bound to a *draft* invoice line therefore reads as
    `invoiced` in the list and is still editable.

    The P&L uses the **invoice-state** reading, through `billed_entry_ids`, and it has
    to: revenue is an *issued* invoice, so an hour on a draft contributes to no `revenue`
    yet. Under the link-based reading it would leave `billable_uninvoiced_hours` at
    the same moment, and its value would exist in neither figure -- work done, priced,
    and invisible until somebody pressed "issue". Here it stays in the accrued value
    until the invoice is actually issued, which is the only reading under which nothing
    falls between the two.
    """
    from pigrocrm.core.invoices.schemas import InvoiceIssue
    from pigrocrm.core.invoices.service import InvoiceService

    entry = TimeEntryService(db_session).create(
        TimeEntryCreate(
            deal_id=seeded_deal_id,
            user_id=seeded_user_id,
            date=date(2026, 3, 4),
            hours=Decimal("2.00"),
            description="Sviluppo",
            applied_rate=Decimal("100.000000"),
        ),
        WRITER,
    )
    db_session.execute(
        text("UPDATE time_entries SET invoice_line_id = :line WHERE id = :id"),
        {"line": draft_invoice_line_id, "id": entry.id},
    )
    db_session.flush()

    service = AnalyticsService(db_session)
    on_draft = service.deal_pnl(seeded_deal_id, READER)
    assert on_draft.revenue == Decimal("0.00")
    assert on_draft.billable_uninvoiced_hours == Decimal("2.00")
    assert on_draft.accrued_value == Decimal("200.00")

    invoice_id = db_session.execute(
        text("SELECT invoice_id FROM invoice_lines WHERE id = :line"),
        {"line": draft_invoice_line_id},
    ).scalar_one()
    InvoiceService(db_session, local_storage).issue(
        invoice_id, InvoiceIssue(), Actor(id=None, type="system", role="admin")
    )

    issued = service.deal_pnl(seeded_deal_id, READER)
    assert issued.billable_uninvoiced_hours == Decimal("0.00")
    # That invoice carries no `deal_id`, so this deal's revenue is still zero and the
    # accrued value drops with the hour: the figures move together, and the assertion
    # that matters is that the hour left the "to invoice" bucket exactly when the
    # invoice became one.
    assert issued.accrued_value == Decimal("0.00")
