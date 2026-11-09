"""The issued invoices fold into due items (REB-968, spec 2026-11-09, due items, § 1.1).

Every issued invoice has its due item; the invoice's `payment_status` and `payment_date` are a
projection that `DueItemService` alone writes; and the readers of the receivables read the
item's remaining amount and date, so a partly paid invoice is one figure everywhere.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID

import pytest
from fakes.gmail_fixtures import actor_for, connected_account, gmail_settings
from fakes.hand_built_invoices import suspended
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.reconcile import reconcile_sales_invoices
from pigrocrm.core.due_items.sales import SALES_INVOICE
from pigrocrm.core.due_items.schemas import SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.fiscal.ceiling import paid_revenue_for_calendar_year
from pigrocrm.core.gmail.payment_reminders import PaymentRemindersService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import InvoiceAnnul, InvoiceIssue, PaymentState
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
USER = Actor(id=None, type="user", role="collaborator")
TODAY = today_local()


def _days(offset: int) -> dt.date:
    return TODAY + dt.timedelta(days=offset)


def _item(session: Session, invoice: Invoice) -> DueItem:
    item = DueItemService(session).repo.by_source(SALES_INVOICE, invoice.id)
    assert item is not None
    return item


def _legacy(
    session: Session, columns: dict[str, object] | None = None, **kwargs: object
) -> Invoice:
    """An invoice as the previous release wrote it: the row alone, no due item. `columns` are
    set on the row before it is flushed."""
    with suspended():
        invoice = unpaid_invoice(session, **kwargs)  # type: ignore[arg-type]
        for name, value in (columns or {}).items():
            setattr(invoice, name, value)
        session.flush()
    assert DueItemService(session).repo.by_source(SALES_INVOICE, invoice.id) is None
    return invoice


def _with_item(session: Session, **kwargs: object) -> Invoice:
    """An issued invoice with its due item, as emission leaves it."""
    invoice = unpaid_invoice(session, **kwargs)  # type: ignore[arg-type]
    assert DueItemService(session).repo.by_source(SALES_INVOICE, invoice.id) is not None
    return invoice


def _pay(session: Session, invoice: Invoice, amount: str, on: dt.date) -> None:
    DueItemService(session).settle(
        _item(session, invoice).id, SettlementCreate(amount=Decimal(amount), settled_on=on), USER
    )


def _assert_projection_agrees(session: Session) -> None:
    """The invoice's two columns and its due item never disagree (§ 1.1)."""
    for invoice in session.scalars(select(Invoice).where(Invoice.status == "issued")):
        item = DueItemService(session).repo.by_source(SALES_INVOICE, invoice.id)
        if item is None:
            continue
        session.refresh(invoice)
        live = DueItemService(session).repo.live_settlements(item.id)
        paid = item.state == "settled"
        assert (invoice.payment_status == "paid") == paid
        assert invoice.payment_date == (max(s.settled_on for s in live) if paid else None)


# --- the card and the buckets ---------------------------------------------------------------


def test_the_card_and_the_buckets_agree_on_a_partly_paid_invoice(db_session: Session) -> None:
    """«Da incassare» and the six buckets are one query shape over the due items' remaining
    amounts: a 1000 invoice paid 400 counts for 600 in all of them, and the overdue part of the
    card is the expired bucket."""
    late = _with_item(db_session, due=_days(-10), total=Decimal("1000.00"))
    _with_item(db_session, due=_days(10), total=Decimal("500.00"))
    _pay(db_session, late, "400.00", _days(-1))
    repo = InvoiceRepository(db_session)

    assert repo.sum_unpaid() == Decimal("1100.00")
    assert repo.sum_expired() == Decimal("600.00")
    bands = {band.code: band for band in repo.ageing_receivables(TODAY)}
    assert sum(band.amount for band in bands.values()) == repo.sum_unpaid()
    assert bands["expired"].amount == repo.sum_expired() == Decimal("600.00")
    assert bands["entro_30"].amount == Decimal("500.00")
    assert sum(row.amount for row in repo.receivables_by_due_month()) == repo.sum_unpaid()
    by_customer = repo.receivables_by_customer(TODAY, 10)
    assert sum(row.amount for row in by_customer) == repo.sum_unpaid()
    assert sum(row.expired for row in by_customer) == repo.sum_expired()
    assert repo.count_overdue_not_paid() == 1
    (overdue,) = repo.overdue_with_reminders(TODAY, 10)
    assert overdue.amount == Decimal("600.00")
    _assert_projection_agrees(db_session)


def test_a_fully_paid_invoice_leaves_the_card_and_the_buckets(db_session: Session) -> None:
    invoice = _with_item(db_session, due=_days(-10), total=Decimal("1000.00"))
    _pay(db_session, invoice, "1000.00", _days(-2))
    repo = InvoiceRepository(db_session)
    assert repo.sum_unpaid() == Decimal("0.00")
    assert all(band.amount == 0 for band in repo.ageing_receivables(TODAY))
    db_session.refresh(invoice)
    assert (invoice.payment_status, invoice.payment_date) == ("paid", _days(-2))
    _assert_projection_agrees(db_session)


def test_the_weekly_lists_carry_what_is_owed_and_each_payment(db_session: Session) -> None:
    invoice = _with_item(db_session, due=_days(-3), total=Decimal("1000.00"))
    _pay(db_session, invoice, "300.00", _days(-2))
    _pay(db_session, invoice, "200.00", _days(-1))
    repo = InvoiceRepository(db_session)
    (owed,) = repo.list_overdue(TODAY)
    assert (owed.invoice.id, owed.remaining, owed.due_date) == (
        invoice.id,
        Decimal("500.00"),
        _days(-3),
    )
    paid = repo.list_paid_in_period(_days(-7), TODAY)
    assert [(p.amount, p.settled_on) for p in paid] == [
        (Decimal("300.00"), _days(-2)),
        (Decimal("200.00"), _days(-1)),
    ]


def test_a_payment_reminder_asks_for_what_remains(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = _with_item(db_session, due=_days(-30), total=Decimal("1000.00"))
    _pay(db_session, invoice, "400.00", _days(-5))
    (candidate,) = PaymentRemindersService(db_session, settings=gmail_settings()).candidates(
        actor_for(account)
    )
    assert candidate.amount == Decimal("600.00")
    _pay(db_session, invoice, "600.00", _days(-1))
    assert (
        PaymentRemindersService(db_session, settings=gmail_settings()).candidates(
            actor_for(account)
        )
        == []
    )


# --- the invoice's two columns are a projection ---------------------------------------------


def test_issuing_writes_the_invoices_due_item(
    db_session: Session, local_storage: LocalFileStorage, draft_invoice_line_id: UUID
) -> None:
    service = InvoiceService(db_session, local_storage)
    invoice_id = db_session.scalars(select(Invoice.id).where(Invoice.status == "draft")).one()
    issued = service.issue(invoice_id, InvoiceIssue(), ADMIN)
    invoice = db_session.get(Invoice, issued.id)
    assert invoice is not None
    item = _item(db_session, invoice)
    assert (item.direction, item.state, item.amount) == ("in", "expected", invoice.total)
    assert item.due_date == invoice.due_date
    assert item.organization_id == invoice.customer_id
    assert item.legal_entity_id == invoice.legal_entity_id


def test_the_payment_state_tool_records_a_settlement_and_projects_the_columns(
    db_session: Session, local_storage: LocalFileStorage, issued_invoice_line_id: UUID
) -> None:
    service = InvoiceService(db_session, local_storage)
    invoice = db_session.scalars(select(Invoice).where(Invoice.status == "issued")).one()
    paid_on = _days(-1)

    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=paid_on), USER
    )
    item = _item(db_session, invoice)
    (settlement,) = DueItemService(db_session).repo.live_settlements(item.id)
    assert (item.state, settlement.amount, settlement.settled_on) == (
        "settled",
        invoice.total,
        paid_on,
    )
    assert (settlement.recorded_by_type) == "user"
    assert (invoice.payment_status, invoice.payment_date) == ("paid", paid_on)

    # The same call on another day moves the payment, it does not add one.
    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=_days(-2)), USER
    )
    (moved,) = DueItemService(db_session).repo.live_settlements(item.id)
    assert (moved.amount, moved.settled_on) == (invoice.total, _days(-2))
    assert invoice.payment_date == _days(-2)

    service.set_payment_state(invoice.id, PaymentState(payment_status="unpaid"), USER)
    db_session.refresh(item)
    assert item.state == "expected"
    assert DueItemService(db_session).repo.live_settlements(item.id) == []
    assert (invoice.payment_status, invoice.payment_date) == ("unpaid", None)
    # Nothing was deleted: the payment, its replacement and the reversals are the history.
    history = db_session.scalars(
        select(DueItemSettlement).where(DueItemSettlement.due_item_id == item.id)
    ).all()
    assert len(history) == 2 and all(row.reversed_at is not None for row in history)
    _assert_projection_agrees(db_session)


def test_marking_paid_settles_only_what_remains_of_a_partly_paid_invoice(
    db_session: Session, local_storage: LocalFileStorage, issued_invoice_line_id: UUID
) -> None:
    service = InvoiceService(db_session, local_storage)
    invoice = db_session.scalars(select(Invoice).where(Invoice.status == "issued")).one()
    first = invoice.total / 4
    _pay(db_session, invoice, str(first), _days(-5))
    assert invoice.payment_status == "unpaid"

    service.set_payment_state(
        invoice.id, PaymentState(payment_status="paid", payment_date=_days(-1)), USER
    )
    amounts = [
        (s.amount, s.settled_on)
        for s in DueItemService(db_session).repo.live_settlements(_item(db_session, invoice).id)
    ]
    assert amounts == [(first, _days(-5)), (invoice.total - first, _days(-1))]
    assert (invoice.payment_status, invoice.payment_date) == ("paid", _days(-1))
    _assert_projection_agrees(db_session)


def test_the_item_endpoint_projects_onto_the_invoice_too(db_session: Session) -> None:
    """The other door: settling the item directly keeps the invoice's columns in step, and so
    does reversing the settlement."""
    invoice = _with_item(db_session, due=_days(-3), total=Decimal("100.00"))
    item_id = _item(db_session, invoice).id
    service = DueItemService(db_session)
    detail = service.settle(
        item_id, SettlementCreate(amount=Decimal("100.00"), settled_on=_days(-1)), USER
    )
    assert (invoice.payment_status, invoice.payment_date) == ("paid", _days(-1))
    service.reverse_settlement(detail.settlements[0].id, "wrong", USER)
    assert (invoice.payment_status, invoice.payment_date) == ("unpaid", None)
    _assert_projection_agrees(db_session)


def test_annulling_an_invoice_drops_its_item(
    db_session: Session, local_storage: LocalFileStorage, issued_invoice_line_id: UUID
) -> None:
    service = InvoiceService(db_session, local_storage)
    invoice = db_session.scalars(select(Invoice).where(Invoice.status == "issued")).one()
    service.annul(invoice.id, InvoiceAnnul(reason="errore"), ADMIN)
    assert _item(db_session, invoice).state == "dropped"
    assert InvoiceRepository(db_session).sum_unpaid() == Decimal("0.00")


# --- the rollout ---------------------------------------------------------------------------


def test_the_boot_gives_a_previous_release_invoice_its_item_and_a_second_boot_adds_nothing(
    db_session: Session,
) -> None:
    unpaid = _legacy(db_session, due=_days(-10), total=Decimal("1000.00"))
    paid = _legacy(
        db_session,
        {"payment_status": "paid", "payment_date": _days(-4)},
        due=_days(-20),
        total=Decimal("250.00"),
    )
    undated = _legacy(
        db_session, {"payment_status": "paid"}, due=_days(-30), total=Decimal("80.00")
    )

    assert reconcile_sales_invoices(db_session) == 3

    item = _item(db_session, unpaid)
    assert (item.state, item.amount, item.due_date) == ("expected", Decimal("1000.00"), _days(-10))
    done = _item(db_session, paid)
    (settlement,) = DueItemService(db_session).repo.live_settlements(done.id)
    assert (done.state, settlement.amount, settlement.settled_on) == (
        "settled",
        Decimal("250.00"),
        _days(-4),
    )
    # A paid invoice with no date was dated by its issue date everywhere; it still is.
    (fallback,) = DueItemService(db_session).repo.live_settlements(_item(db_session, undated).id)
    assert fallback.settled_on == undated.issue_date
    assert undated.payment_date == undated.issue_date
    assert InvoiceRepository(db_session).sum_unpaid() == Decimal("1000.00")
    _assert_projection_agrees(db_session)

    before = db_session.scalar(select(DueItem.id).order_by(DueItem.id).limit(1))
    count = len(db_session.scalars(select(DueItem.id)).all())
    assert reconcile_sales_invoices(db_session) == 0
    assert len(db_session.scalars(select(DueItem.id)).all()) == count
    assert db_session.scalar(select(DueItem.id).order_by(DueItem.id).limit(1)) == before
    assert len(db_session.scalars(select(DueItemSettlement.id)).all()) == 2


def test_the_boot_leaves_a_cancelled_invoice_and_one_with_an_item_alone(
    db_session: Session,
) -> None:
    cancelled = _legacy(
        db_session,
        {"status": "cancelled", "cancelled_at": TODAY, "cancellation_reason": "errore"},
        due=_days(-10),
    )
    done = _with_item(db_session, due=_days(-10))
    _pay(db_session, done, "10.00", _days(-1))

    assert reconcile_sales_invoices(db_session) == 0
    assert DueItemService(db_session).repo.by_source(SALES_INVOICE, cancelled.id) is None
    assert _item(db_session, done).state == "expected"


# --- the ceiling and the estimate read each settlement --------------------------------------


def _year_end_invoice(session: Session) -> Invoice:
    """A 1000 invoice due in March of the year before the current, paid 400 on 30 December and
    600 on 2 January."""
    year = TODAY.year - 2
    invoice = _with_item(session, due=dt.date(year, 3, 1), total=Decimal("1000.00"))
    service = DueItemService(session)
    item = _item(session, invoice)
    for amount, on in (("400.00", dt.date(year, 12, 30)), ("600.00", dt.date(year + 1, 1, 2))):
        service.settle(item.id, SettlementCreate(amount=Decimal(amount), settled_on=on), USER)
    return invoice


def test_the_ceiling_attributes_instalments_to_the_year_they_arrived_in(
    db_session: Session,
) -> None:
    invoice = _year_end_invoice(db_session)
    year = invoice.issue_date.year
    assert paid_revenue_for_calendar_year(db_session, year) == Decimal("400.00")
    assert paid_revenue_for_calendar_year(db_session, year + 1) == Decimal("600.00")
    # A reversed payment is not revenue of any year.
    service = DueItemService(db_session)
    last = service.repo.live_settlements(_item(db_session, invoice).id)[-1]
    service.reverse_settlement(last.id, "wrong", USER)
    assert paid_revenue_for_calendar_year(db_session, year + 1) == Decimal("0.00")


def test_the_ceiling_counts_the_taxable_share_of_a_payment(db_session: Session) -> None:
    """The settlement is gross and the ceiling's basis is taxable: each payment counts for its
    share of the invoice's `taxable_amount`, and the shares add up to it."""
    year = TODAY.year - 3
    invoice = _with_item(db_session, due=dt.date(year, 3, 1), total=Decimal("1220.00"))
    invoice.taxable_amount = Decimal("1000.00")
    db_session.flush()
    _pay(db_session, invoice, "610.00", dt.date(year, 4, 1))
    assert paid_revenue_for_calendar_year(db_session, year) == Decimal("500.00")
    _pay(db_session, invoice, "610.00", dt.date(year, 5, 1))
    assert paid_revenue_for_calendar_year(db_session, year) == Decimal("1000.00")


def test_the_collection_view_reads_each_settlement_by_its_date(db_session: Session) -> None:
    invoice = _year_end_invoice(db_session)
    year = invoice.issue_date.year
    repo = AnalyticsRepository(db_session)
    assert repo.monthly_paid(year, "collection") == {12: Decimal("400.00")}
    assert repo.monthly_paid(year + 1, "collection") == {1: Decimal("600.00")}
    assert repo.monthly_unpaid(year, "collection") == {}


def test_the_collection_view_keeps_the_unpaid_rest_in_the_month_it_falls_due(
    db_session: Session,
) -> None:
    year = TODAY.year - 3
    invoice = _with_item(db_session, due=dt.date(year, 3, 1), total=Decimal("1000.00"))
    _pay(db_session, invoice, "400.00", dt.date(year, 3, 20))
    repo = AnalyticsRepository(db_session)
    assert repo.monthly_paid(year, "collection") == {3: Decimal("400.00")}
    assert repo.monthly_unpaid(year, "collection") == {3: Decimal("600.00")}


@pytest.mark.parametrize("paid", [True, False])
def test_an_invoice_with_nothing_to_collect_has_no_item_and_can_still_be_marked(
    db_session: Session, paid: bool
) -> None:
    invoice = _legacy(db_session, due=_days(-1), total=Decimal("0.00"))
    service = DueItemService(db_session)
    assert service.reconcile_invoice(invoice) is None
    service.record_invoice_payment(
        invoice, paid=paid, paid_on=_days(-1) if paid else None, actor=USER
    )
    assert invoice.payment_status == ("paid" if paid else "unpaid")
