"""Slice 8 part A, criteria 1, 2 and 3 (REB-329).

The bucket sums must add up, to the cent, to `da_incassare` as the economic dashboard
computes it by another road (`sum_da_incassare`), on a corpus with one row per bucket
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
from sqlalchemy import Engine, delete, func, select

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.dashboard.schemas import ReceivablesDashboard
from pigrocrm.core.dashboard.service import DashboardService
from pigrocrm.core.db import session_factory, today_local
from pigrocrm.core.db.base import uuid7
from pigrocrm.core.gmail.models import PaymentReminder
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import FASCE_SCADENZIARIO

READONLY = Actor(id=uuid7(), type="user", role="readonly")
_PREFIX = "SCAD"
_CORE = Path(__file__).resolve().parents[1] / "src" / "pigrocrm" / "core"

# One row per bucket, amounts chosen so no two sums coincide and the total is a literal
# a reader can check by hand: 100 + 200 + 300 + 400 + 500 + 600 = 2100. The paid and the
# annulled rows are in no figure at all.
_TOTALE = Decimal("2100.00")


@pytest.fixture
def corpus(db_engine: Engine) -> Iterator[tuple[Engine, date, UUID, UUID]]:
    factory = session_factory(db_engine)
    oggi = today_local()
    with factory() as session:
        leftovers = session.execute(select(func.count(Invoice.id))).scalar_one()
        assert leftovers == 0, f"{leftovers} committed invoice(s) already in the register"
        acme = Customer(ragione_sociale=f"{_PREFIX} Acme", nazione="IT", custom_fields={})
        beta = Customer(ragione_sociale=f"{_PREFIX} Beta", nazione="IT", custom_fields={})
        session.add_all([acme, beta])
        session.flush()
        rows = [
            # scaduto: strictly before today, the worst first when listed
            (acme.id, "100.00", oggi - timedelta(days=40), 1),
            # entro 30: due today counts here, not as overdue
            (acme.id, "200.00", oggi, 2),
            (beta.id, "300.00", oggi + timedelta(days=45), 3),
            (beta.id, "400.00", oggi + timedelta(days=90), 4),
            (acme.id, "500.00", oggi + timedelta(days=91), 5),
            (beta.id, "600.00", None, 6),
        ]
        for customer_id, totale, scadenza, numero in rows:
            session.add(_invoice(customer_id, totale, scadenza, numero))
        paid = _invoice(acme.id, "7000.00", oggi - timedelta(days=10), 7)
        paid.stato_pagamento = "incassato"
        paid.data_incasso = oggi
        annulled = _invoice(beta.id, "8000.00", oggi - timedelta(days=10), 8)
        annulled.stato = "annullata"
        annulled.annullata_il = oggi
        annulled.motivo_annullamento = "storno"
        session.add_all([paid, annulled])
        session.flush()
        overdue = session.execute(
            select(Invoice).where(Invoice.totale == Decimal("100.00"))
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
        ids = (db_engine, oggi, acme.id, beta.id)
    try:
        yield ids
    finally:
        with factory() as session:
            customers = select(Customer.id).where(Customer.ragione_sociale.like(f"{_PREFIX} %"))
            invoices = select(Invoice.id).where(Invoice.customer_id.in_(customers))
            session.execute(delete(PaymentReminder).where(PaymentReminder.invoice_id.in_(invoices)))
            session.execute(delete(Invoice).where(Invoice.customer_id.in_(customers)))
            session.execute(delete(Customer).where(Customer.id.in_(customers)))
            session.commit()


def _invoice(customer_id: UUID, totale: str, scadenza: date | None, numero: int) -> Invoice:
    return Invoice(
        customer_id=customer_id,
        tipo="fattura",
        stato="emessa",
        anno=2026,
        numero=numero,
        stato_pagamento="da_incassare",
        imponibile=Decimal(totale),
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=Decimal(totale),
        data_emissione=date(2026, 9, 1),
        data_scadenza=scadenza,
        tipo_documento="TD01",
        divisa="EUR",
        custom_fields={},
    )


def _dashboard(engine: Engine) -> ReceivablesDashboard:
    with session_factory(engine)() as session:
        return DashboardService(session).get_receivables_dashboard(READONLY)


def test_the_six_buckets_add_up_to_da_incassare_to_the_cent(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, oggi, _, _ = corpus
    page = _dashboard(engine)
    assert [f.codice for f in page.fasce] == list(FASCE_SCADENZIARIO)
    assert sum(f.importo for f in page.fasce) == page.totale == _TOTALE
    assert {f.codice: str(f.importo) for f in page.fasce} == {
        "scaduto": "100.00",
        "entro_30": "200.00",
        "da_31_a_60": "300.00",
        "da_61_a_90": "400.00",
        "oltre_90": "500.00",
        "senza_scadenza": "600.00",
    }
    assert all(f.numero == 1 for f in page.fasce)
    assert page.oggi == oggi
    # Only the overdue bucket drills through, to the list's own overdue filter.
    assert [f.collegamento for f in page.fasce] == ["/app/invoices?scadute=true"] + [None] * 5
    # The largest bucket scales to 1, the others to their share.
    assert max(f.quota for f in page.fasce) == 1.0


def test_a_row_without_a_due_date_is_in_no_dated_bucket(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, _, _, _ = corpus
    page = _dashboard(engine)
    dated = [f for f in page.fasce if f.codice != "senza_scadenza"]
    assert sum(f.importo for f in dated) == Decimal("1500.00")
    # And no month holds it either: the month view is only ever of dated rows.
    assert sum(m.importo for m in page.per_mese) == Decimal("1500.00")
    assert all(m.mese.day == 1 for m in page.per_mese)
    assert [m.mese for m in page.per_mese] == sorted(m.mese for m in page.per_mese)


def test_exposure_per_customer_names_the_overdue_share(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, _, acme, beta = corpus
    page = _dashboard(engine)
    by_id = {row.customer_id: row for row in page.per_cliente}
    assert by_id[beta].importo == Decimal("1300.00")  # 300 + 400 + 600, largest first
    assert page.per_cliente[0].customer_id == beta
    assert by_id[acme].importo == Decimal("800.00")  # 100 + 200 + 500
    assert by_id[acme].scaduto == Decimal("100.00")
    assert by_id[beta].scaduto == Decimal("0.00")
    assert by_id[acme].numero == 3


def test_the_overdue_rows_count_the_reminders_that_left(
    corpus: tuple[Engine, date, UUID, UUID],
) -> None:
    engine, oggi, acme, _ = corpus
    page = _dashboard(engine)
    assert page.scadute_totale == 1
    [row] = page.scadute
    assert row.customer_id == acme
    assert row.numero == "2026/1"
    assert row.importo == Decimal("100.00")
    assert row.giorni_di_ritardo == 40
    assert row.data_scadenza == oggi - timedelta(days=40)
    assert row.solleciti_inviati == 1
    assert row.ultimo_sollecito_il == date(2026, 9, 15)


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
    none writes `stato_pagamento == "da_incassare"` by hand."""
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
        assert 'stato_pagamento == "da_incassare"' not in body, name
