"""Confirming a reviewed invoice import onto the register (REB-366):
`InvoiceService.confirm_import`, exercised end to end with a real
`EmitterProfile` row, a real `FiscalProfile` row, a real `documents` row and
the FPR12 fixtures REB-363/364/365 already use.

The issue's own "Done when" is what every scenario below serves: confirming an
invoice review already flagged `already_present` is a no-op, never a
duplicate insert; confirming a fresh one produces exactly the row
`import_issued` would have produced by hand, plus a hash-verified
`xml_document_id` for a single-invoice source, `NULL` for a batch-sourced one.
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import AgentForbidden, NotFound, PermissionDenied
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.invoices.import_confirm import map_parsed_invoice_to_import
from pigrocrm.core.invoices.import_schemas import (
    ParsedInvoice,
    ParsedInvoiceLine,
    ParsedInvoiceParty,
    ParsedInvoiceTaxSummary,
    ParsedInvoiceTransmission,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceRegisterGap
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage import LocalFileStorage

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULENZA = "fpr12-consulenza-marzo.xml"
LOTTO = "fpr12-lotto-due-fatture.xml"
ADMIN = Actor(id=None, type="user", role="admin")

# The FPR12 fixtures' own `CedentePrestatore` (fornitore): matching this on an
# `EmitterProfile` is what makes them "outgoing" for the account holder.
FORNITORE_PIVA = "01234567890"
FORNITORE_CF = "BNCCHR85M41H501Z"
# Their `CessionarioCommittente` (cliente).
CLIENTE_PIVA = "09876543210"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _fiscal_profile(session: Session) -> FiscalProfile:
    profile = FiscalProfile(codice_regime="RF19")
    session.add(profile)
    session.flush()
    return profile


def _emitter(session: Session, **overrides: object) -> EmitterProfile:
    base: dict[str, object] = {
        "ragione_sociale": "Chiara Bianchi",
        "partita_iva": FORNITORE_PIVA,
        "codice_fiscale": FORNITORE_CF,
        "nazione": "IT",
    }
    base.update(overrides)
    profile = EmitterProfile(**base)
    session.add(profile)
    session.flush()
    return profile


def _svc(session: Session, storage: LocalFileStorage) -> InvoiceService:
    """`InvoiceService` with the fiscal and emitter profiles `import_issued` reads
    already in place, the emitter matching the fixtures' own `CedentePrestatore`
    so a confirmed invoice classifies "outgoing" by default."""
    _fiscal_profile(session)
    _emitter(session)
    return InvoiceService(session, storage)


def _customer(session: Session, **overrides: object) -> Customer:
    base: dict[str, object] = {
        "ragione_sociale": f"Cliente {uuid4()}",
        "indirizzo": "Piazza dei Modelli 4",
        "cap": "00187",
        "comune": "Roma",
        "provincia": "RM",
        "nazione": "IT",
    }
    base.update(overrides)
    customer = Customer(**base)
    session.add(customer)
    session.flush()
    return customer


def _document(
    session: Session,
    storage: LocalFileStorage,
    content: bytes,
    *,
    content_type: str = "application/xml",
    owner: Customer | None = None,
) -> UUID:
    owner = owner if owner is not None else _customer(session)
    service = DocumentService(session, storage)
    document = service.create(
        DocumentCreate(customer_id=owner.id, titolo="Fattura ricevuta"), ADMIN
    )
    service.add_version(document.id, content, content_type, ADMIN)
    return document.id


def _seed_register_row(
    session: Session, *, anno: int, numero: int, xml_hash_sha256: str | None
) -> UUID:
    row = Invoice(
        customer_id=_customer(session).id,
        tipo="fattura",
        stato="emessa",
        anno=anno,
        numero=numero,
        data_emissione=date(2026, 3, 15),
        importata_da="fatturapa",
        xml_hash_sha256=xml_hash_sha256,
        imponibile=Decimal("1000.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("2.00"),
        totale=Decimal("1000.00"),
    )
    session.add(row)
    session.flush()
    return row.id


def _invoice_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Invoice)).scalar_one())


def test_confirming_a_fresh_invoice_produces_exactly_what_import_issued_would_by_hand(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    content = _fixture(CONSULENZA)
    document_id = _document(db_session, local_storage, content, owner=customer)

    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "imported"
    assert row.document_id == document_id
    assert row.fattura is not None
    fattura = row.fattura
    assert (fattura.anno, fattura.numero, fattura.stato, fattura.tipo) == (
        2026,
        6,
        "emessa",
        "fattura",
    )
    assert fattura.customer_id == customer.id
    assert fattura.data_emissione == date(2026, 3, 15)
    assert fattura.imponibile == Decimal("1000.00")
    assert fattura.imposta == Decimal("0.00")
    assert fattura.bollo == Decimal("2.00")
    assert fattura.totale == Decimal("1000.00")
    assert fattura.importata_da == "esterno"
    assert fattura.stato_pagamento == "da_incassare"
    lines = service.repo.lines(fattura.id)
    assert [
        (riga.descrizione, riga.quantita, riga.prezzo_unitario, riga.prezzo_totale)
        for riga in lines
    ] == [
        (
            "Consulenza professionale - marzo 2026",
            Decimal("5.000000"),
            Decimal("200.000000"),
            Decimal("1000.00"),
        )
    ]
    # The register starts empty and this invoice is number 6: 1-5 are undeclared
    # gaps, exactly as `import_issued`'s own response would report for the same
    # hand-declared numero on an empty register.
    assert row.buchi_non_dichiarati == [1, 2, 3, 4, 5]


def test_a_single_invoice_source_document_gets_a_hash_verified_xml_document_id(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)

    [row] = service.confirm_import(document_id, ADMIN)

    assert row.fattura is not None
    assert row.fattura.xml_document_id == document_id
    assert row.fattura.xml_hash_sha256 == hashlib.sha256(content).hexdigest()


def test_a_batch_sourced_document_leaves_xml_document_id_and_hash_null_on_every_invoice(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(LOTTO)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, content, owner=customer)

    rows = service.confirm_import(document_id, ADMIN)

    assert len(rows) == 2
    assert {row.outcome for row in rows} == {"imported"}
    for row in rows:
        assert row.fattura is not None
        assert row.fattura.xml_document_id is None
        assert row.fattura.xml_hash_sha256 is None
    assert {row.fattura.numero for row in rows if row.fattura is not None} == {6, 7}


def test_confirming_an_already_present_invoice_is_a_no_op_never_a_duplicate_insert(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    existing_id = _seed_register_row(
        db_session, anno=2026, numero=6, xml_hash_sha256=hashlib.sha256(content).hexdigest()
    )
    document_id = _document(
        db_session, local_storage, content, owner=_customer(db_session, partita_iva=CLIENTE_PIVA)
    )

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)
    after = _invoice_count(db_session)

    assert before == after
    assert row.outcome == "already_present"
    assert row.fattura is not None
    assert row.fattura.id == existing_id
    assert row.buchi_non_dichiarati is None


def test_a_conflicting_or_missing_hash_is_reported_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    _seed_register_row(
        db_session,
        anno=2026,
        numero=6,
        xml_hash_sha256=hashlib.sha256(b"a different document").hexdigest(),
    )
    document_id = _document(db_session, local_storage, content)

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "conflict"
    assert row.fattura is None
    assert _invoice_count(db_session) == before


def test_a_suppliers_invoice_is_incoming_skipped_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    _fiscal_profile(db_session)
    _emitter(db_session, partita_iva="99999999999", codice_fiscale="ZZZZZZ00A01H501Z")
    service = InvoiceService(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA))

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "incoming_skipped"
    assert row.fattura is None
    assert _invoice_count(db_session) == before


def test_a_document_no_adapter_recognises_is_unclaimed_and_never_written(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(
        db_session, local_storage, b"not a fattura at all", content_type="text/plain"
    )

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "unclaimed"
    assert row.fattura is None
    assert _invoice_count(db_session) == before


def test_no_matching_customer_refuses_and_never_writes(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA))

    before = _invoice_count(db_session)
    [row] = service.confirm_import(document_id, ADMIN)

    assert row.outcome == "needs_customer_confirmation"
    assert row.fattura is None
    assert _invoice_count(db_session) == before


def test_an_explicit_customer_id_overrides_the_automatic_match(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The human decision always wins over the automatic tax-id match: `customer_id`
    is exactly how a reviewer says which `Customer` this invoice attaches to, even
    when one already matches on its own."""
    service = _svc(db_session, local_storage)
    matched = _customer(db_session, partita_iva=CLIENTE_PIVA)
    chosen = _customer(db_session)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA), owner=matched)

    [row] = service.confirm_import(document_id, ADMIN, customer_id=chosen.id)

    assert row.outcome == "imported"
    assert row.fattura is not None
    assert row.fattura.customer_id == chosen.id


def test_confirming_the_same_document_twice_the_second_time_is_already_present(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The natural-key/hash rule applies to confirm's own prior write too: a second
    confirm of the same document never inserts a second row."""
    service = _svc(db_session, local_storage)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA), owner=customer)

    [first] = service.confirm_import(document_id, ADMIN)
    before = _invoice_count(db_session)
    [second] = service.confirm_import(document_id, ADMIN)

    assert first.outcome == "imported"
    assert second.outcome == "already_present"
    assert first.fattura is not None and second.fattura is not None
    assert second.fattura.id == first.fattura.id
    assert _invoice_count(db_session) == before


def test_a_readonly_actor_is_refused(db_session: Session, local_storage: LocalFileStorage) -> None:
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA))
    readonly = Actor(id=None, type="user", role="readonly")
    with pytest.raises(PermissionDenied):
        service.confirm_import(document_id, readonly)


def test_an_agent_credential_without_full_access_is_forbidden(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """The credential-level half of the ban (`actor.py::AGENT_FORBIDDEN_ACTIONS`),
    proven at the service the tool actually calls -- the tool's own absence on a
    default installation is `test_mcp_invoice_ban.py`'s guarantee, not this one."""
    service = _svc(db_session, local_storage)
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA))
    agent = Actor(id=None, type="mcp", role="admin", full_access=False)
    with pytest.raises(AgentForbidden):
        service.confirm_import(document_id, agent)


def test_no_emitter_profile_configured_refuses_rather_than_guesses(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    document_id = _document(db_session, local_storage, _fixture(CONSULENZA))
    with pytest.raises(NotFound):
        InvoiceService(db_session, local_storage).confirm_import(document_id, ADMIN)


def test_natura_is_read_from_the_line_itself_never_reconstructed_by_rate_alone() -> None:
    """Regression: an invoice can carry two `DatiRiepilogo` blocks at the *same*
    rate with a *different* `natura` each -- a mixed-exemption invoice, and
    exactly the case this codebase's own `totals.RiepilogoGroup` already keys on
    the pair `(aliquota_iva, natura)` for the export side. Reconstructing a
    line's `natura` from `riepiloghi` matched by rate alone would silently tag
    both lines with whichever riepilogo happened to be seen first; this proves
    each line keeps its own, correctly-parsed `natura`, and `riferimento_
    normativo` is looked up by the matching pair, not by rate alone."""
    cliente = ParsedInvoiceParty(
        ragione_sociale="Cliente Test",
        indirizzo="Via Test 1",
        cap="00100",
        comune="Roma",
        nazione="IT",
    )
    fornitore = ParsedInvoiceParty(
        ragione_sociale="Emittente Test",
        partita_iva=FORNITORE_PIVA,
        indirizzo="Via Test 2",
        cap="20100",
        comune="Milano",
        nazione="IT",
    )
    invoice = ParsedInvoice(
        numero="2026/6",
        data_emissione=date(2026, 3, 15),
        tipo_documento="fattura",
        divisa="EUR",
        fornitore=fornitore,
        cliente=cliente,
        righe=[
            ParsedInvoiceLine(
                descrizione="Riga non soggetta",
                quantita=Decimal("1"),
                prezzo_unitario=Decimal("500.00"),
                prezzo_totale=Decimal("500.00"),
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
            ),
            ParsedInvoiceLine(
                descrizione="Riga esente",
                quantita=Decimal("1"),
                prezzo_unitario=Decimal("300.00"),
                prezzo_totale=Decimal("300.00"),
                aliquota_iva=Decimal("0.00"),
                natura="N4",
            ),
        ],
        riepiloghi=[
            ParsedInvoiceTaxSummary(
                aliquota_iva=Decimal("0.00"),
                natura="N2.2",
                riferimento_normativo="Regime forfettario, art. 1 commi 54-89 L. 190/2014",
                imponibile=Decimal("500.00"),
                imposta=Decimal("0.00"),
            ),
            ParsedInvoiceTaxSummary(
                aliquota_iva=Decimal("0.00"),
                natura="N4",
                riferimento_normativo="Operazioni esenti, art. 10 DPR 633/1972",
                imponibile=Decimal("300.00"),
                imposta=Decimal("0.00"),
            ),
        ],
        imponibile=Decimal("800.00"),
        imposta=Decimal("0.00"),
        totale=Decimal("800.00"),
        cassa_previdenziale=[],
        termini_pagamento=[],
        trasmissione=ParsedInvoiceTransmission(id_trasmittente="X", progressivo_invio="1"),
    )

    data = map_parsed_invoice_to_import(invoice, anno=2026, numero=6, customer_id=uuid4())

    first, second = data.righe
    assert first.natura == "N2.2"
    assert first.riferimento_normativo == "Regime forfettario, art. 1 commi 54-89 L. 190/2014"
    assert second.natura == "N4"
    assert second.riferimento_normativo == "Operazioni esenti, art. 10 DPR 633/1972"


def test_a_register_rule_conflict_mid_batch_is_reported_and_the_rest_still_lands(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """`import_issued`'s own four further register rules (declared gaps here) are
    not re-implemented in `confirm_import`'s own pre-check -- they are enforced by
    `import_issued` itself, exactly as the design demands (§5 item 2: one write
    function, never a second set of the same rules). This proves a batch document
    does not lose earlier progress or silently drop the rest when one invoice in
    it hits one of those rules: the first invoice's write survives, the second is
    reported `"conflict"`, and the session stays usable after."""
    service = _svc(db_session, local_storage)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    InvoiceRepository(db_session).add_gap(
        InvoiceRegisterGap(anno=2026, numero=7, motivo="annullata prima della trasmissione")
    )
    document_id = _document(db_session, local_storage, _fixture(LOTTO), owner=customer)

    rows = service.confirm_import(document_id, ADMIN)

    assert [row.outcome for row in rows] == ["imported", "conflict"]
    assert rows[0].fattura is not None
    assert rows[0].fattura.numero == 6
    assert rows[1].fattura is None
    assert _invoice_count(db_session) == 1
    # The session must still answer a query after the mid-batch Conflict.
    assert len(db_session.execute(select(Invoice)).scalars().all()) == 1
