"""Two people press «Emetti» on the same draft at the same moment.

Found by review, not by the plan. Every other emission test drives one transaction, and
under one transaction this cannot happen: the state check and the counter increment
appear adjacent. They are not. The check reads the row *before* the year's counter is
locked, so two calls on the same `invoice_id` can both pass it, and then both take a
number and both write it to the same row.

The loser's number is the damage, and it is worse than a lost update. It has been
consumed from `invoice_counters` and is carried by no invoice, so the register skips it
for ever. `uq_invoices_anno_numero` cannot see the problem -- there is no duplicate, the
row simply holds the other number -- and no other test in this suite notices, because
every one of them looks at the invoice rather than at the counter.

That property is the reason `lock_counter` is a locked row and deliberately not a
`SEQUENCE`: `nextval()` is non-transactional and leaves a hole on every rollback. A
design chosen to make gaps impossible must not have a path that produces one anyway.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import Conflict
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice, InvoiceCounter, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

HUMAN = Actor(id=None, type="user", role="admin")


def _configure(session: Session) -> None:
    AziendaService(session).upsert_default(
        AziendaUpsert(
            ragione_sociale="Studio Rossi",
            partita_iva="12345678901",
            codice_fiscale="RSSMRA80A01H501U",
            indirizzo="Via Roma 1",
            cap="20100",
            comune="Milano",
            provincia="MI",
            nazione="IT",
            pec="studio@pec.it",
        ),
        HUMAN,
    )
    # After the azienda, which the profile belongs to since REB-615.
    FiscalProfileService(session).upsert(FiscalProfileUpsert(codice_regime="RF19"), HUMAN)


def _customer(session: Session) -> Customer:
    customer = Customer(
        ragione_sociale="Acme S.r.l.",
        partita_iva="98765432109",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def _cleanup(factory, invoice_id: UUID | None, customer_id: UUID | None) -> None:  # type: ignore[no-untyped-def]
    """Unwind everything these two tests committed, **including the year's counter**.

    The counter is the part that is easy to forget and expensive to forget. These tests
    issue real invoices, so `invoice_counters` is left one or two higher than it was, and
    the row survives the test because there is no transaction to roll back. That is not a
    local mess: `test_invoice_numbering_concurrency.py` asserts its twenty emissions come
    out as exactly 1..20, and `test_invoice_models.py` inserts its own counter row for
    2026 -- both of which fail if this file ran first and left the register moved. It cost
    a full-suite run to find, and the failure names those files rather than this one.

    Deleting rather than restoring the previous value, which is what the neighbouring
    concurrency suite already does in its own teardown: the counter is per-year state that
    every emitting test expects to start empty, so putting it back to zero and removing it
    are the same thing here, and removing it is the one that leaves no row for a later
    test to trip over.

    Dependency order, because these are real foreign keys and nothing is rolling back:
    lines, then activities pointing at the invoice, then the invoice, then the profiles
    and the customer.
    """
    with factory() as cleaner:
        if invoice_id is not None:
            cleaner.execute(
                InvoiceLine.__table__.delete().where(
                    InvoiceLine.__table__.c.invoice_id == invoice_id
                )
            )
            cleaner.execute(
                text("DELETE FROM activities WHERE entity_type = 'invoice' AND entity_id = :id"),
                {"id": invoice_id},
            )
            cleaner.execute(Invoice.__table__.delete().where(Invoice.__table__.c.id == invoice_id))
        cleaner.execute(text("DELETE FROM invoice_counters"))
        cleaner.execute(text("DELETE FROM fiscal_profile"))
        cleaner.execute(text("DELETE FROM emitter_profile"))
        if customer_id is not None:
            cleaner.execute(
                Customer.__table__.delete().where(Customer.__table__.c.id == customer_id)
            )
        cleaner.commit()


def test_two_concurrent_issues_of_one_draft_consume_exactly_one_number(
    db_engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """Exactly one call may win, and the counter may move exactly once.

    Both assertions are needed and they fail differently. Without the re-read after the
    lock, *both* calls succeed -- so the outcome list catches it. But a fix that merely
    made the second call fail *after* it had already incremented would still leave the
    gap, and only the counter assertion sees that. The register is the thing under test,
    not the error message.

    A committed fixture and two real connections, because the transactional `db_session`
    cannot host a race: both threads would share one transaction and one snapshot, and
    the interleaving this test exists to produce could not occur.
    """
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    invoice_id: UUID | None = None
    customer_id: UUID | None = None
    anno: int | None = None

    try:
        with factory() as setup:
            _configure(setup)
            customer = _customer(setup)
            customer_id = customer.id
            draft = InvoiceService(setup, storage).create(
                InvoiceCreate(
                    customer_id=customer.id,
                    tipo="fattura",
                    causale="Gara di emissione",
                    righe=[
                        InvoiceLineIn(
                            descrizione="Advisory",
                            quantita=Decimal("1.000000"),
                            prezzo_unitario=Decimal("100.000000"),
                        )
                    ],
                ),
                HUMAN,
            )
            invoice_id = draft.id
            setup.commit()

        both_ready = Barrier(2)

        def issue_once() -> bool:
            with factory() as session:
                service = InvoiceService(session, storage)
                # The rendezvous is before the call, not inside it: each thread must
                # reach `issue` with its own fresh session and no knowledge of the
                # other, which is exactly the situation two browser tabs create.
                both_ready.wait(timeout=30)
                try:
                    service.issue(invoice_id, InvoiceIssue(), HUMAN)  # type: ignore[arg-type]
                except Exception:
                    return False
                return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(
                future.result(timeout=120) for future in [pool.submit(issue_once) for _ in range(2)]
            )

        assert outcomes == [False, True], "the same draft was issued twice"

        with factory() as reader:
            stored = reader.get(Invoice, invoice_id)
            assert stored is not None
            assert stored.stato == "emessa"
            assert stored.numero is not None
            anno = stored.anno
            counter = reader.execute(
                select(InvoiceCounter).where(InvoiceCounter.anno == anno)
            ).scalar_one()
            # The heart of it. The number the register handed out and the number the
            # invoice carries must be the same one; a counter ahead of the document is
            # a hole nothing later can fill.
            assert counter.ultimo_numero == stored.numero, (
                "the counter moved further than the register did: "
                f"consumed {counter.ultimo_numero}, invoice carries {stored.numero}"
            )
    finally:
        _cleanup(factory, invoice_id, customer_id)


def test_a_draft_already_issued_is_refused_without_touching_the_counter(
    db_engine: Engine, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """The sequential half of the same guarantee, and the control for the test above.

    Without it, a fix that simply refused *every* second `issue` — including ones that
    never raced — would pass the race test while breaking nothing visible, and the
    counter assertion there would be satisfied by a service that had stopped issuing
    altogether.
    """
    factory = session_factory(db_engine)
    storage = LocalFileStorage(tmp_path / "documents")
    invoice_id: UUID | None = None
    customer_id: UUID | None = None

    try:
        with factory() as session:
            _configure(session)
            customer = _customer(session)
            customer_id = customer.id
            service = InvoiceService(session, storage)
            draft = service.create(
                InvoiceCreate(
                    customer_id=customer.id,
                    tipo="fattura",
                    righe=[
                        InvoiceLineIn(
                            descrizione="Advisory",
                            quantita=Decimal("1.000000"),
                            prezzo_unitario=Decimal("50.000000"),
                        )
                    ],
                ),
                HUMAN,
            )
            invoice_id = draft.id
            issued = service.issue(draft.id, InvoiceIssue(), HUMAN)
            session.commit()

            counter_before = session.execute(
                select(InvoiceCounter.ultimo_numero).where(InvoiceCounter.anno == issued.anno)
            ).scalar_one()

            with pytest.raises(Conflict):
                service.issue(draft.id, InvoiceIssue(), HUMAN)
            session.rollback()

            counter_after = session.execute(
                select(InvoiceCounter.ultimo_numero).where(InvoiceCounter.anno == issued.anno)
            ).scalar_one()
            assert counter_after == counter_before, "a refused emission still spent a number"
    finally:
        _cleanup(factory, invoice_id, customer_id)
