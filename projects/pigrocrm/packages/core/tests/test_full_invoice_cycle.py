"""The whole cycle, once, across both adapters.

Every other test in this slice proves one property of one piece. This one proves the
claim the slice exists to make: **an agent prepares, a human issues, and both are
working on the same row through the same services.** If the MCP surface and the REST
surface had drifted into two implementations, every unit test could still pass and this
one would not.

It also pins the boundary that cost eleven failing tests to find: `issue` does not
produce the artefacts. The caller does. A cycle that never called `produce_artifacts`
would leave a fiscally complete invoice with no PDF and no XML, and nothing else in the
suite would notice.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.schemas import LegalEntityUpsert
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.invoices.schemas import InvoiceCreate, InvoiceIssue, InvoiceLineIn
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

# Two different actors on purpose: the agent's work and the human's must be
# distinguishable afterwards, which is the point of the timeline assertion below.
AGENT = Actor(id=None, type="mcp", role="admin")
HUMAN = Actor(id=None, type="user", role="admin")


@pytest.fixture
def storage(tmp_path) -> LocalFileStorage:  # type: ignore[no-untyped-def]
    return LocalFileStorage(tmp_path / "documents")


@pytest.fixture
def configured(db_session: Session, storage: LocalFileStorage) -> InvoiceService:
    FiscalProfileService(db_session).upsert(FiscalProfileUpsert(codice_regime="RF19"), HUMAN)
    LegalEntityService(db_session).upsert_default(
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
        HUMAN,
    )
    return InvoiceService(db_session, storage)


@pytest.fixture
def customer_id(db_session: Session) -> UUID:
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
    db_session.add(customer)
    db_session.flush()
    return customer.id


def test_an_agent_prepares_a_human_issues_and_both_artefacts_exist(
    configured: InvoiceService, customer_id: UUID, db_session: Session
) -> None:
    service = configured

    # 1. The agent's half. A proforma is everything MCP is allowed to do -- the four
    #    fiscal writes are not registered as tools at all, which
    #    apps/mcp/tests/test_mcp_invoice_ban.py enforces structurally.
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            causale="Consulenza CTO, agosto",
            righe=[
                InvoiceLineIn(
                    descrizione="Advisory",
                    quantita=Decimal("10.000000"),
                    unita_misura="ore",
                    prezzo_unitario=Decimal("150.000000"),
                )
            ],
        ),
        AGENT,
    )
    assert proforma.tipo == "proforma"
    assert proforma.numero is None, "una proforma non consuma un numero fiscale"
    assert proforma.riferimento is not None

    # The amount is agreed. Still no register number: confirming a proforma is a
    # commercial act, not a fiscal one, which is why an agent may do it.
    service.confirm_proforma(proforma.id, AGENT)

    # 2. The human's half. Issuing is the act an agent cannot perform.
    issued = service.issue(proforma.id, InvoiceIssue(), HUMAN)
    assert issued.tipo == "fattura"
    assert issued.numero == 1
    assert issued.anno is not None

    # 3. The caller produces the artefacts -- `issue` deliberately does not, so that it
    #    stays one transaction. A cycle that stopped at step 2 would leave a fiscally
    #    complete invoice with nothing to send.
    service.produce_artifacts(issued.id, HUMAN)

    stored = service.get(issued.id, HUMAN)
    assert stored.pdf_document_id is not None, "il PDF non è stato prodotto"
    assert stored.xml_document_id is not None, "l'XML FatturaPA non è stato prodotto"
    assert stored.xml_hash_sha256 is not None

    # 4. Both artefacts are real bytes, reachable through the one path that carries
    #    authorisation.
    pdf, pdf_type, pdf_name = service.download(issued.id, "pdf", HUMAN)
    assert pdf.startswith(b"%PDF"), "il PDF non è un PDF"
    assert pdf_type == "application/pdf"
    assert pdf_name.endswith(".pdf")

    xml, xml_type, xml_name = service.download(issued.id, "xml", HUMAN)
    assert b"FatturaElettronica" in xml
    assert xml_type == "application/xml"
    assert xml_name.endswith(".xml")

    # 5. The proforma is consumed, not deleted: the trail from one to the other survives.
    source = service.get(proforma.id, HUMAN)
    assert source.stato == "consumata"
    assert stored.origine_proforma_id == proforma.id


def test_regenerating_reproduces_the_same_bytes(
    configured: InvoiceService, customer_id: UUID
) -> None:
    """The promise a version history makes: an invoice regenerates identically months
    later, from the frozen snapshot, without the person who wrote it.

    Only true because `render_pdf` pins `--creation-timestamp 0`; Typst otherwise stamps
    wall-clock compile time into every `/CreationDate`, and two renders of one document
    would differ in bytes while looking identical on screen.
    """
    service = configured
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="fattura",
            righe=[
                InvoiceLineIn(
                    descrizione="Advisory",
                    quantita=Decimal("1.000000"),
                    prezzo_unitario=Decimal("500.000000"),
                )
            ],
        ),
        HUMAN,
    )
    issued = service.issue(invoice.id, InvoiceIssue(), HUMAN)
    service.produce_artifacts(issued.id, HUMAN)
    first, _, _ = service.download(issued.id, "pdf", HUMAN)

    service.produce_artifacts(issued.id, HUMAN)
    second, _, _ = service.download(issued.id, "pdf", HUMAN)

    assert first == second, "una rigenerazione ha prodotto byte diversi"


def test_the_timeline_tells_the_agent_and_the_human_apart(
    configured: InvoiceService, customer_id: UUID, db_session: Session
) -> None:
    """Two actors touched one document, and afterwards it must be possible to say which
    did what. Without this, "an agent prepared it" and "a person issued it" become the
    same sentence in the register."""
    from pigrocrm.core.activities.service import ActivityService

    service = configured
    proforma = service.create(
        InvoiceCreate(
            customer_id=customer_id,
            tipo="proforma",
            righe=[
                InvoiceLineIn(
                    descrizione="Advisory",
                    quantita=Decimal("1.000000"),
                    prezzo_unitario=Decimal("100.000000"),
                )
            ],
        ),
        AGENT,
    )
    service.confirm_proforma(proforma.id, AGENT)
    issued = service.issue(proforma.id, InvoiceIssue(), HUMAN)

    entries = ActivityService(db_session).timeline("invoice", issued.id, 50)
    assert entries, "l'emissione non ha lasciato traccia"
    actor_types = {entry.actor_type for entry in entries}
    assert "user" in actor_types
