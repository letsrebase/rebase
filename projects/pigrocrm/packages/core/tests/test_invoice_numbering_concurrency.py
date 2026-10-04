"""Spec 14.3, run for real.

The shared `db_session` fixture binds one connection and wraps every test in a
savepoint that is rolled back, which is exactly right for the other suites and
useless here: a single connection cannot contend with itself, so a "concurrency" test
written on it would pass no matter what the code did. Every session below is opened
from `db_engine` and commits for real, and the fixture cleans up after itself.
"""

import threading
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, text

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
EMISSIONS = 20
FAILURES = 10
# `oggi_in_italia()`, not `date.today()`: this must be the same "today" `issue()`
# itself computes (see `clock.py`), or this file's own assertions would drift from
# the service's output on a host that is not running in Europe/Rome.
ANNO = oggi_in_italia().year


@pytest.fixture
def world(db_engine: Engine, tmp_path):  # type: ignore[no-untyped-def]
    """A committed customer, emitter profile and fiscal profile, plus a teardown that
    physically removes everything this test wrote.

    Physical `DELETE`s, not a soft delete: `ck_invoices_no_delete_once_consumed`
    refuses `deleted_at` on a numbered row, which is the point of the constraint, and
    a test must not be the reason it gets weakened.
    """
    factory = session_factory(db_engine)
    with factory() as setup:
        customer = Customer(
            ragione_sociale="Acme S.r.l.",
            partita_iva="12345678901",
            codice_sdi="ABCDEFG",
            indirizzo="Corso Italia 5",
            cap="00100",
            comune="Roma",
            provincia="RM",
            nazione="IT",
        )
        setup.add(customer)
        setup.flush()
        customer_id = customer.id
        AziendaService(setup).upsert_default(
            AziendaUpsert(
                ragione_sociale="Studio Rossi",
                partita_iva="01234567890",
                codice_fiscale="HMCRFT00A01H501K",
                indirizzo="Via Vittorio Veneto 12",
                cap="20124",
                comune="Milano",
                provincia="MI",
                nazione="IT",
                email="mario@example.com",
            ),
            ADMIN,
        )
        # After the azienda, which the profile belongs to since REB-615.
        FiscalProfileService(setup).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
        setup.commit()
    try:
        yield factory, customer_id, tmp_path
    finally:
        with factory() as cleanup:
            cleanup.execute(text("DELETE FROM invoice_lines"))
            cleanup.execute(text("DELETE FROM activities WHERE entity_type = 'invoice'"))
            cleanup.execute(text("UPDATE invoices SET origine_proforma_id = NULL"))
            cleanup.execute(text("DELETE FROM invoices"))
            cleanup.execute(text("DELETE FROM invoice_counters"))
            cleanup.execute(text("DELETE FROM fiscal_profile"))
            cleanup.execute(text("DELETE FROM emitter_profile WHERE NOT predefinita"))
            cleanup.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer_id})
            cleanup.commit()


def _make_draft(factory, storage_root, customer_id: UUID) -> UUID:  # type: ignore[no-untyped-def]
    with factory() as session:
        return (
            InvoiceService(session, LocalFileStorage(storage_root))
            .create(
                InvoiceCreate(
                    customer_id=customer_id,
                    righe=[
                        InvoiceLineIn(descrizione="Consulenza", prezzo_unitario=Decimal("100.00"))
                    ],
                ),
                ADMIN,
            )
            .id
        )


def test_twenty_concurrent_emissions_produce_one_to_twenty_with_no_gap(world) -> None:  # type: ignore[no-untyped-def]
    """Spec 14.3, first half. Twenty threads, each on its own session and its own
    transaction, all issuing at once: twenty invoices, numbers 1 to 20, no duplicate
    and no gap, verified with a `SELECT` against the database rather than against what
    the service returned."""
    factory, customer_id, tmp_path = world
    drafts = [_make_draft(factory, tmp_path / "documents", customer_id) for _ in range(EMISSIONS)]
    start = threading.Barrier(EMISSIONS)
    results: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def emit(draft_id: UUID) -> None:
        try:
            start.wait(timeout=30)
            with factory() as session:
                issued = InvoiceService(session, LocalFileStorage(tmp_path / "documents")).issue(
                    draft_id, InvoiceIssue(), ADMIN
                )
            with lock:
                results.append(issued.numero or 0)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=emit, args=(d,)) for d in drafts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"emissioni fallite: {errors}"
    assert sorted(results) == list(range(1, EMISSIONS + 1))

    with factory() as check:
        rows = (
            check.execute(
                text("SELECT numero FROM invoices WHERE anno = :anno ORDER BY numero"),
                {"anno": ANNO},
            )
            .scalars()
            .all()
        )
        counter = check.execute(
            text("SELECT ultimo_numero FROM invoice_counters WHERE anno = :anno"),
            {"anno": ANNO},
        ).scalar_one()
    assert list(rows) == list(range(1, EMISSIONS + 1))
    assert counter == EMISSIONS


def test_a_failure_between_the_counter_update_and_the_commit_consumes_nothing(
    world, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Spec 14.3, second half -- the property a `SEQUENCE` cannot provide.

    The error is injected **after** the counter has been incremented in the
    transaction and **before** the commit, which is the only window where a
    sequence-based design would already have burnt the number. `ActivityService.record`
    is the last statement `issue` runs before committing, so patching it puts the
    failure exactly there without inventing a seam in production code for a test to
    pull.
    """
    factory, customer_id, tmp_path = world
    storage_root = tmp_path / "documents"

    # Phase 1: twenty successful emissions, so there is a real register to protect.
    for _ in range(EMISSIONS):
        with factory() as session:
            InvoiceService(session, LocalFileStorage(storage_root)).issue(
                _make_draft(factory, storage_root, customer_id), InvoiceIssue(), ADMIN
            )

    # Phase 2: ten emissions that die between the UPDATE and the COMMIT.
    original = ActivityService.record

    def explode(self, entity_type, entity_id, kind, actor, payload=None):  # type: ignore[no-untyped-def]
        if kind == "issued":
            raise RuntimeError("iniezione fra l'UPDATE del contatore e il COMMIT")
        return original(self, entity_type, entity_id, kind, actor, payload)

    monkeypatch.setattr(ActivityService, "record", explode)
    for _ in range(FAILURES):
        draft_id = _make_draft(factory, storage_root, customer_id)
        with factory() as session, pytest.raises(RuntimeError):
            InvoiceService(session, LocalFileStorage(storage_root)).issue(
                draft_id, InvoiceIssue(), ADMIN
            )
    monkeypatch.undo()

    with factory() as check:
        counter = check.execute(
            text("SELECT ultimo_numero FROM invoice_counters WHERE anno = :anno"),
            {"anno": ANNO},
        ).scalar_one()
        rows = (
            check.execute(
                text("SELECT numero FROM invoices WHERE anno = :anno ORDER BY numero"),
                {"anno": ANNO},
            )
            .scalars()
            .all()
        )
    assert counter == EMISSIONS, "un'emissione fallita ha bruciato un numero"
    assert list(rows) == list(range(1, EMISSIONS + 1))

    # Phase 3: the next successful emission takes 21, not 31.
    with factory() as session:
        issued = InvoiceService(session, LocalFileStorage(storage_root)).issue(
            _make_draft(factory, storage_root, customer_id), InvoiceIssue(), ADMIN
        )
    assert issued.numero == EMISSIONS + 1


def test_two_first_invoices_of_a_year_do_not_collide_on_the_counter_insert(
    world,
) -> None:  # type: ignore[no-untyped-def]
    """`INSERT ... ON CONFLICT (anno) DO NOTHING` before the `SELECT ... FOR UPDATE`:
    two concurrent first-invoices-of-the-year both proceed, one having inserted the
    row and the other having done nothing. Without `ON CONFLICT` the loser would take
    a `UniqueViolation` the caller would have to retry."""
    factory, customer_id, tmp_path = world
    drafts = [_make_draft(factory, tmp_path / "documents", customer_id) for _ in range(2)]
    start = threading.Barrier(2)
    numbers: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def emit(draft_id: UUID) -> None:
        try:
            start.wait(timeout=30)
            with factory() as session:
                issued = InvoiceService(session, LocalFileStorage(tmp_path / "documents")).issue(
                    draft_id, InvoiceIssue(), ADMIN
                )
            with lock:
                numbers.append(issued.numero or 0)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=emit, args=(d,)) for d in drafts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert sorted(numbers) == [1, 2]
