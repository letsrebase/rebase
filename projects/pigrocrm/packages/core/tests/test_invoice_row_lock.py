"""The writers that decide on an invoice's state lock its row first (PCORE-A-03).

Until then only `issue` serialised, through the year's counter. `update`, `replace_lines`,
`annul` and the rest read the row unlocked and checked its state on that read, so a commit
landing in between let them write a frozen document, or let `annul` and
`mark_transmitted_externally` both pass checks that are meant to exclude each other.

Committed fixtures and real connections, like `test_invoice_issue_race.py`: the
transactional `db_session` holds one transaction and cannot host a race.
"""

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import ImmutableField
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceTransmitted,
    InvoiceUpdate,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="user", role="admin")


@pytest.fixture
def committed_draft(db_engine: Engine) -> Iterator[tuple[sessionmaker[Session], UUID]]:
    """A draft that is really committed, and the teardown of everything it leaves."""
    factory = session_factory(db_engine)
    invoice_id: UUID | None = None
    customer_id: UUID | None = None
    # The default azienda is committed state shared with the other files: its identity is
    # put back, or a later test about "no identity configured" finds this one's.
    identity_columns = (
        "ragione_sociale",
        "partita_iva",
        "codice_fiscale",
        "indirizzo",
        "cap",
        "comune",
        "provincia",
        "nazione",
        "pec",
    )
    with factory() as probe:
        original = (
            probe.execute(
                text(f"SELECT {', '.join(identity_columns)} FROM emitter_profile WHERE predefinita")
            )
            .mappings()
            .one()
        )
    try:
        with factory() as setup:
            LegalEntityService(setup).upsert_default(
                LegalEntityUpsert(
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
                ADMIN,
            )
            FiscalProfileService(setup).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
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
            setup.add(customer)
            setup.flush()
            customer_id = customer.id
            draft = InvoiceService(setup, LocalFileStorage("/nonexistent")).create(
                InvoiceCreate(
                    customer_id=customer.id,
                    causale="Prima",
                    righe=[
                        InvoiceLineIn(descrizione="Advisory", prezzo_unitario=Decimal("100.00"))
                    ],
                ),
                ADMIN,
            )
            invoice_id = draft.id
            setup.commit()
        yield factory, draft.id
    finally:
        with factory() as cleaner:
            if invoice_id is not None:
                cleaner.execute(
                    text("DELETE FROM invoice_lines WHERE invoice_id = :id"), {"id": invoice_id}
                )
                cleaner.execute(
                    text(
                        "DELETE FROM activities WHERE entity_type = 'invoice' AND entity_id = :id"
                    ),
                    {"id": invoice_id},
                )
                cleaner.execute(text("DELETE FROM invoices WHERE id = :id"), {"id": invoice_id})
            cleaner.execute(text("DELETE FROM invoice_counters"))
            cleaner.execute(text("DELETE FROM fiscal_profile"))
            cleaner.execute(text("DELETE FROM emitter_profile WHERE NOT predefinita"))
            cleaner.execute(
                text(
                    "UPDATE emitter_profile SET "
                    + ", ".join(f"{name} = :{name}" for name in identity_columns)
                    + " WHERE predefinita"
                ),
                dict(original),
            )
            if customer_id is not None:
                cleaner.execute(text("DELETE FROM customers WHERE id = :id"), {"id": customer_id})
            cleaner.commit()


def _service(session: Session, tmp_path) -> InvoiceService:  # type: ignore[no-untyped-def]
    return InvoiceService(session, LocalFileStorage(tmp_path / "documents"))


def test_an_update_sees_the_emission_another_session_committed_after_it_loaded_the_row(
    committed_draft: tuple[sessionmaker[Session], UUID], tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """The identity map still holds the draft as `bozza` when the other session has already
    issued it. `update` has to re-read under the lock, not trust that copy, or it writes
    the causale of a fiscal document."""
    factory, invoice_id = committed_draft
    with factory() as stale, factory() as other:
        stale_service = _service(stale, tmp_path)
        assert stale_service.get(invoice_id, ADMIN).stato == "bozza"

        _service(other, tmp_path).issue(invoice_id, InvoiceIssue(), ADMIN)

        with pytest.raises(ImmutableField):
            stale_service.update(invoice_id, InvoiceUpdate(causale="Dopo l'emissione"), ADMIN)

    with factory() as reader:
        stored = reader.get(Invoice, invoice_id)
        assert stored is not None
        assert stored.causale == "Prima"


def _waiting_on_row_locks(engine: Engine) -> int:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND query ILIKE '%FROM invoices%'"
            )
        ).scalar_one()


def test_annul_and_mark_transmitted_racing_on_one_invoice_leave_exactly_one_winner(
    db_engine: Engine, committed_draft: tuple[sessionmaker[Session], UUID], tmp_path
) -> None:  # type: ignore[no-untyped-def]
    """An invoice that is both `annullata` and "delivered to the intermediary" is the state
    the code says must be impossible. A third session holds the row so both writers are
    provably queued on it before either decides; whichever goes first wins and the other
    is refused on what it then reads."""
    factory, invoice_id = committed_draft
    with factory() as setup:
        _service(setup, tmp_path).issue(invoice_id, InvoiceIssue(), ADMIN)

    holder = factory()
    InvoiceRepository(holder).get_for_update(invoice_id)

    def annul() -> bool:
        with factory() as session:
            try:
                _service(session, tmp_path).annul(invoice_id, InvoiceAnnul(motivo="errore"), ADMIN)
            except Exception:
                return False
            return True

    def transmit() -> bool:
        with factory() as session:
            try:
                _service(session, tmp_path).mark_transmitted_externally(
                    invoice_id,
                    InvoiceTransmitted(data=oggi_in_italia()),
                    ADMIN,
                )
            except Exception:
                return False
            return True

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(annul), pool.submit(transmit)]
            deadline = time.monotonic() + 30
            while _waiting_on_row_locks(db_engine) < 2:
                assert time.monotonic() < deadline, "the two writers never queued on the row"
                time.sleep(0.1)
            holder.rollback()
            outcomes = sorted(future.result(timeout=60) for future in futures)
    finally:
        holder.close()

    assert outcomes == [False, True], "annul and mark_transmitted_externally both succeeded"
    with factory() as reader:
        stored = reader.get(Invoice, invoice_id)
        assert stored is not None
        assert (stored.stato == "annullata") != (stored.trasmessa_esternamente_il is not None)
