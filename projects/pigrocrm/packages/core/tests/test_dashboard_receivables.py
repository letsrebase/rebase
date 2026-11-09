"""Slice 8 part A, criteria 1, 2 and 3 (REB-329).

The bucket sums must add up, to the cent, to `unpaid` as the economic dashboard
computes it by another road (`sum_unpaid`), on a corpus with one row per bucket
including one with no due date; that row lands in «senza scadenza» and in no dated
bucket; and the predicate is `_receivable_filter` imported, never a second copy.

Committed sessions of its own, like `test_dashboard_economic.py` and for the same reason:
`DashboardService` sets the isolation level as its first statement, which `db_session`'s
open transaction forbids. The whole-register figures have no scope, so the file also
requires an empty register before it writes.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fakes.legal_entity_fixtures import committed_default_legal_entity, remove_legal_entity
from sqlalchemy import Engine, delete, func, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.dashboard.schemas import ReceivablesDashboard
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.gmail.models import PaymentReminder
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.receivables import BANDS_RECEIVABLES
from pigrocrm.core.organizations.models import Organization

READONLY = Actor(id=uuid7(), type="user", role="readonly")
_PREFIX = "SCAD"
_CORE = Path(__file__).resolve().parents[1] / "src" / "pigrocrm" / "core"

# One row per bucket, amounts chosen so no two sums coincide and the total is a literal
# a reader can check by hand: 100 + 200 + 300 + 400 + 500 + 600 = 2100. The paid and the
# annulled rows are in no figure at all.
_TOTAL = Decimal("2100.00")


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[tuple[Engine, date, UUID, UUID]]:
    factory = session_factory(db_engine)
    today = today_local()
    with factory() as session:
        leftovers = session.execute(select(func.count(Invoice.id))).scalar_one()
        assert leftovers == 0, f"{leftovers} committed invoice(s) already in the register"
        legal_entity_id = committed_default_legal_entity(session)
        acme = Organization(legal_name=f"{_PREFIX} Acme", country="IT", custom_fields={})
        beta = Organization(legal_name=f"{_PREFIX} Beta", country="IT", custom_fields={})
        session.add_all([acme, beta])
        session.flush()
        rows = [
            # scaduto: strictly before today, the worst first when listed
            (acme.id, "100.00", today - timedelta(days=40), 1),
            # entro 30: due today counts here, not as overdue
            (acme.id, "200.00", today, 2),
            (beta.id, "300.00", today + timedelta(days=45), 3),
            (beta.id, "400.00", today + timedelta(days=90), 4),
            (acme.id, "500.00", today + timedelta(days=91), 5),
            (beta.id, "600.00", None, 6),
        ]
        for customer_id, total, due_date, number in rows:
            session.add(_invoice(customer_id, total, due_date, number))
        paid = _invoice(acme.id, "7000.00", today - timedelta(days=10), 7)
        paid.payment_status = "paid"
        paid.payment_date = today
        annulled = _invoice(beta.id, "8000.00", today - timedelta(days=10), 8)
        annulled.status = "cancelled"
        annulled.cancelled_at = today
        annulled.cancellation_reason = "storno"
        session.add_all([paid, annulled])
        session.flush()
        overdue = session.execute(
            select(Invoice).where(Invoice.total == Decimal("100.00"))
        ).scalar_one()
        # Two reminders prepared, one sent: the page must count the letter, not the draft.
        session.add(PaymentReminder(invoice_id=overdue.id, sequence=1, sent_at=None))
        session.add(
            PaymentReminder(
                invoice_id=overdue.id,
                sequence=2,
                sent_at=datetime.fromisoformat("2026-09-15T09:00:00+00:00"),
            )
        )
        session.commit()
        ids = (db_engine, today, acme.id, beta.id)
    try:
        yield ids
    finally:
        with factory() as session:
            organizations = select(Organization.id).where(
                Organization.legal_name.like(f"{_PREFIX} %")
            )
            invoices = select(Invoice.id).where(Invoice.customer_id.in_(organizations))
            session.execute(delete(PaymentReminder).where(PaymentReminder.invoice_id.in_(invoices)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(organizations)))
            session.execute(delete(Organization).where(Organization.id.in_(organizations)))
            remove_legal_entity(session, legal_entity_id)
            session.commit()


def _invoice(customer_id: UUID, total: str, due_date: date | None, number: int) -> Invoice:
    return Invoice(
        customer_id=customer_id,
        type="invoice",
        status="issued",
        year=2026,
        number=number,
        payment_status="unpaid",
        taxable_amount=Decimal(total),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("0.00"),
        total=Decimal(total),
        issue_date=date(2026, 9, 1),
        due_date=due_date,
        sdi_document_type="TD01",
        currency="EUR",
        custom_fields={},
    )


def _dashboard(engine: Engine) -> ReceivablesDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_receivables_dashboard(READONLY)


def test_the_six_buckets_add_up_to_unpaid_to_the_cent(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, today, _, _ = corpus
    page = _dashboard(engine)
    assert [f.code for f in page.bands] == list(BANDS_RECEIVABLES)
    assert sum(f.amount for f in page.bands) == page.total == _TOTAL
    assert {f.code: str(f.amount) for f in page.bands} == {
        "expired": "100.00",
        "entro_30": "200.00",
        "da_31_a_60": "300.00",
        "da_61_a_90": "400.00",
        "oltre_90": "500.00",
        "without_due_date": "600.00",
    }
    assert all(f.number == 1 for f in page.bands)
    assert page.today == today
    # Only the overdue bucket drills through, to the list's own overdue filter.
    assert [f.link for f in page.bands] == ["/app/invoices?overdue=true"] + [None] * 5
    # The largest bucket scales to 1, the others to their share.
    assert max(f.share for f in page.bands) == 1.0


def test_a_row_without_a_due_date_is_in_no_dated_bucket(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, _, _, _ = corpus
    page = _dashboard(engine)
    dated = [f for f in page.bands if f.code != "without_due_date"]
    assert sum(f.amount for f in dated) == Decimal("1500.00")
    # And no month holds it either: the month view is only ever of dated rows.
    assert sum(m.amount for m in page.by_month) == Decimal("1500.00")
    assert all(m.month.day == 1 for m in page.by_month)
    assert [m.month for m in page.by_month] == sorted(m.month for m in page.by_month)


def test_exposure_per_customer_names_the_overdue_share(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, _, acme, beta = corpus
    page = _dashboard(engine)
    by_id = {row.customer_id: row for row in page.by_customer}
    assert by_id[beta].amount == Decimal("1300.00")  # 300 + 400 + 600, largest first
    assert page.by_customer[0].customer_id == beta
    assert by_id[acme].amount == Decimal("800.00")  # 100 + 200 + 500
    assert by_id[acme].expired == Decimal("100.00")
    assert by_id[beta].expired == Decimal("0.00")
    assert by_id[acme].number == 3


def test_the_overdue_rows_count_the_reminders_that_left(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, today, acme, _ = corpus
    page = _dashboard(engine)
    assert page.total_overdue == 1
    [row] = page.overdue
    assert row.customer_id == acme
    assert row.number == "2026/1"
    assert row.amount == Decimal("100.00")
    assert row.days_of_delay == 40
    assert row.due_date == today - timedelta(days=40)
    assert row.sent_payment_reminders == 1
    assert row.last_payment_reminder_at == date(2026, 9, 15)


def test_the_receivable_predicate_is_defined_once() -> None:
    """Criterion 3: `_receivable_filter` is imported, never restated. Counted as
    definitions across core: exactly one `def _receivable_filter`."""
    definitions = 0
    for path in _CORE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        definitions += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_receivable_filter"
        )
    assert definitions == 1


def test_no_dashboard_query_restates_the_receivable_predicate() -> None:
    """The four readings this page is made of all spell `*_receivable_filter()` and
    none writes `payment_status == "unpaid"` by hand."""
    source = (_CORE / "invoices" / "repository.py").read_text(encoding="utf-8")
    for name in (
        "def ageing_receivables",
        "def receivables_by_due_month",
        "def receivables_by_customer",
        "def overdue_with_reminders",
    ):
        start = source.index(name)
        end = source.index("\n    def ", start + 1)
        body = source[start:end]
        assert "_receivable_filter()" in body, name
        assert 'payment_status == "unpaid"' not in body, name
