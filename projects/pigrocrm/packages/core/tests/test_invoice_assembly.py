"""`assemble_draft` (one transaction for a draft and what is bound to its lines) and
`InvoiceService.issue_and_render` (the issued row survives a render failure)."""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.assembly import assemble_draft
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")


@pytest.fixture
def service(db_session: Session, tmp_path) -> InvoiceService:  # type: ignore[no-untyped-def]
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), ADMIN)
    LegalEntityService(db_session).upsert_default(
        LegalEntityUpsert(
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
    return InvoiceService(db_session, LocalFileStorage(tmp_path / "documents"))


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
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
    db_session.add(customer)
    db_session.flush()
    return customer.id


def _draft(customer_id: UUID) -> InvoiceCreate:
    return InvoiceCreate(
        customer_id=customer_id,
        righe=[
            InvoiceLineIn(descrizione=name, prezzo_unitario=Decimal(price))
            for name, price in (("Prima", "100.00"), ("Seconda", "200.00"), ("Terza", "300.00"))
        ],
    )


def _count(session: Session, model: type) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


def test_bind_receives_the_line_ids_in_line_order_and_everything_commits_once(
    service: InvoiceService,
    db_session: Session,
    customer_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits: list[None] = []
    commit = db_session.commit

    def counting_commit() -> None:
        commits.append(None)
        commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)
    received: list[list[UUID]] = []

    draft = assemble_draft(service, _draft(customer_id), ADMIN, bind=received.append)

    assert len(received) == 1
    stored = (
        db_session.execute(
            select(InvoiceLine)
            .where(InvoiceLine.invoice_id == draft.id)
            .order_by(InvoiceLine.numero_linea)
        )
        .scalars()
        .all()
    )
    assert [line.descrizione for line in stored] == ["Prima", "Seconda", "Terza"]
    assert received[0] == [line.id for line in stored]
    assert draft.stato == "bozza"
    assert draft.totale == Decimal("600.00")
    assert len(commits) == 1


def test_a_failing_bind_leaves_no_draft_and_keeps_the_callers_other_pending_work(
    service: InvoiceService, db_session: Session, customer_id: UUID
) -> None:
    other = Customer(ragione_sociale="Pending Srl")
    db_session.add(other)
    db_session.flush()
    invoices_before = _count(db_session, Invoice)
    lines_before = _count(db_session, InvoiceLine)

    def failing_bind(line_ids: list[UUID]) -> None:
        assert len(line_ids) == 3
        raise RuntimeError("the day was billed meanwhile")

    with pytest.raises(RuntimeError, match="billed meanwhile"):
        assemble_draft(service, _draft(customer_id), ADMIN, bind=failing_bind)

    assert _count(db_session, Invoice) == invoices_before
    assert _count(db_session, InvoiceLine) == lines_before
    # The caller's own pending work survived the failure: it was never rolled back.
    assert other in db_session
    assert db_session.get(Customer, other.id) is not None


def test_a_refused_draft_leaves_nothing_behind_and_never_calls_bind(
    service: InvoiceService, db_session: Session
) -> None:
    called: list[list[UUID]] = []
    with pytest.raises(NotFound):
        assemble_draft(
            service,
            _draft(UUID(int=0)),
            ADMIN,
            bind=called.append,
        )
    assert called == []


def test_issue_and_render_answers_the_issued_row_when_the_render_fails(
    service: InvoiceService,
    db_session: Session,
    customer_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = service.create(_draft(customer_id), ADMIN)

    def broken_render(invoice_id: UUID, actor: Actor) -> list[object]:
        raise RuntimeError("typst crashed")

    monkeypatch.setattr(service, "produce_artifacts", broken_render)

    issued = service.issue_and_render(draft.id, InvoiceIssue(), ADMIN)

    assert issued.id == draft.id
    assert issued.stato == "emessa"
    assert issued.numero == 1
    assert issued.pdf_document_id is None
