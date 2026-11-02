"""An issued, unpaid invoice, built as a row rather than through an emission.

`conftest.py` already owns the fixtures that go through `InvoiceService.issue`, and
those are the right ones for anything that tests the *register*: they consume a real
number, render a PDF and freeze a snapshot. Nothing in `solleciti` cares about any of
that. What a reminder needs from an invoice is its state -- issued, unpaid, past its due
date -- so building the row directly keeps these tests to the thing under test and off
the Typst renderer.

A module under `fakes/` and not a `conftest.py` helper, for the reason
`gmail_fixtures.py` states at length: this repository has three test roots, none with an
`__init__.py`, so `conftest` is an ambiguous top-level module name while
`fakes.invoice_fixtures` is unambiguous however the roots are combined.

Every constraint on `invoices` is respected here rather than worked around, because a
fixture that reached the database by relaxing a `CHECK` would be testing a shape
production cannot hold: `anno` and `numero` travel together, `numero` implies an issued
`fattura`, and `(anno, numero)` is unique. The year is derived from the caller's own
`due` date -- which comes from `today_local()` -- and never from a literal, so this
file does not expire.
"""

from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.invoices.models import Invoice

# Per-process, so two invoices in one test never collide on `uq_invoices_anno_numero`.
# It only has to be unique within a test's own transaction, but a shared counter costs
# nothing and removes the question.
_NUMERI = count(1)


def unpaid_invoice(
    session: Session,
    *,
    due: date,
    numero: int | None = None,
    anno: int | None = None,
    totale: Decimal = Decimal("1220.00"),
    customer: Customer | None = None,
    email: str | None = "info@acme.it",
) -> Invoice:
    """An `emessa` `fattura` with a number, a due date and nothing collected against it.

    `totale` is the invoice's own frozen figure and it is a `Decimal`: a reminder that
    names an amount must name what was issued, never a sum recomputed at reminder time.
    """
    if customer is None:
        customer = Customer(ragione_sociale=f"Acme {uuid4().hex[:8]}", email=email)
        session.add(customer)
        session.flush()

    # Thirty days is `fiscal_profile.giorni_scadenza`'s own default, so an invoice built
    # here has the same shape as one this system would issue: emitted first, due later.
    emissione = due - timedelta(days=30)
    anno = anno if anno is not None else emissione.year
    if numero is None:
        numero = next(_NUMERI)
        # The counter is per process and the schema is shared by the whole session, so a
        # previous test's committed row could already hold this pair. Cheap to ask.
        while session.execute(
            select(func.count())
            .select_from(Invoice)
            .where(Invoice.anno == anno, Invoice.numero == numero)
        ).scalar_one():
            numero = next(_NUMERI)

    invoice = Invoice(
        customer_id=customer.id,
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        data_emissione=emissione,
        data_scadenza=due,
        imponibile=totale,
        imposta=Decimal("0.00"),
        bollo=Decimal("0.00"),
        totale=totale,
        stato_pagamento="da_incassare",
    )
    session.add(invoice)
    session.flush()
    return invoice
