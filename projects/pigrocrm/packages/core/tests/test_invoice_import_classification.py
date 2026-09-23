"""Direction detection and the register duplicate check for a parsed invoice
(REB-364): `import_direction`, `import_dedup`, `import_classification`, and the
new `InvoiceRepository.existing_by_number` lookup they build on.

Proven two ways, like REB-363's own adapter tests: small hand-built parties for
the comparison-logic edge cases (no XML, no database), and the real FPR12 fixture
REB-363 already built for an end-to-end "does a genuinely parsed document
classify correctly" check. `check_invoice_duplicate`/`classify_parsed_invoice`
need no database either -- both take whatever the repository already found as a
plain argument, so a hand-built `Invoice` row (never flushed) is the row they
compare against. Only `existing_by_number` itself, the register lookup, needs one.
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa_import import parse
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_dedup import check_invoice_duplicate
from pigrocrm.core.invoices.import_direction import classify_direction, classify_invoice_direction
from pigrocrm.core.invoices.import_schemas import ParsedInvoiceParty
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULENZA = "fpr12-consulenza-marzo.xml"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _emitter(**overrides: object) -> EmitterProfile:
    base: dict[str, object] = {
        "ragione_sociale": "Studio Rossi",
        "partita_iva": "01234567890",
        "codice_fiscale": "HMCRFT00A01H501K",
        "nazione": "IT",
    }
    base.update(overrides)
    return EmitterProfile(**base)


def _party(**overrides: object) -> ParsedInvoiceParty:
    base: dict[str, object] = {
        "ragione_sociale": "Chiara Bianchi",
        "partita_iva": "IT01234567890",
        "codice_fiscale": "BNCCHR85M41H501Z",
        "indirizzo": "Via delle Officine 10",
        "cap": "20121",
        "comune": "Milano",
        "provincia": "MI",
        "nazione": "IT",
    }
    base.update(overrides)
    return ParsedInvoiceParty(**base)


# --- classify_direction: the low-level party-vs-emitter comparison ----------------


def test_a_matching_partita_iva_is_outgoing_case_and_prefix_insensitive() -> None:
    fornitore = _party(partita_iva="it 01234567890")
    emitter = _emitter(partita_iva="01234567890")
    assert classify_direction(fornitore, emitter) == "outgoing"


def test_a_matching_codice_fiscale_is_outgoing_when_the_partita_iva_channel_misses() -> None:
    fornitore = _party(partita_iva="IT99999999999", codice_fiscale="hmcrft00a01h501k")
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="HMCRFT00A01H501K")
    assert classify_direction(fornitore, emitter) == "outgoing"


def test_neither_identifier_matching_is_incoming() -> None:
    fornitore = _party(partita_iva="IT99999999999", codice_fiscale="ZZZZZZ00A01H501Z")
    emitter = _emitter()
    assert classify_direction(fornitore, emitter) == "incoming"


def test_a_supplier_with_no_recognisable_identifier_is_incoming_not_a_crash() -> None:
    fornitore = _party(partita_iva=None, codice_fiscale=None)
    emitter = _emitter()
    assert classify_direction(fornitore, emitter) == "incoming"


def test_an_unconfigured_emitter_profile_refuses_to_classify_at_all() -> None:
    fornitore = _party()
    emitter = _emitter(partita_iva=None, codice_fiscale=None)
    with pytest.raises(ValidationFailed) as caught:
        classify_direction(fornitore, emitter)
    assert caught.value.details["entity"] == "emitter_profile"


# --- classify_invoice_direction: the same check against a real parsed document ----


def test_the_fixtures_own_issuer_classifies_as_outgoing_against_her_own_profile() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="BNCCHR85M41H501Z")
    assert classify_invoice_direction(invoice, emitter) == "outgoing"


def test_the_same_document_is_incoming_against_a_different_accounts_profile() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="09876543210", codice_fiscale="RSSMRA80A01H501U")
    assert classify_invoice_direction(invoice, emitter) == "incoming"


# --- check_invoice_duplicate: the hash comparison, including the NULL-hash rule ---


def test_no_invoice_on_record_is_new() -> None:
    assert check_invoice_duplicate(None, b"whatever bytes") == "new"


def test_a_matching_stored_hash_is_already_present() -> None:
    content = b"the exact bytes of the document"
    existing = Invoice(xml_hash_sha256=_digest(content))
    assert check_invoice_duplicate(existing, content) == "already_present"


def test_a_different_stored_hash_is_conflict_not_already_present() -> None:
    existing = Invoice(xml_hash_sha256=_digest(b"a previous, different document"))
    assert check_invoice_duplicate(existing, b"today's bytes") == "conflict"


def test_a_numbered_invoice_with_no_stored_hash_is_always_conflict_never_already_present() -> None:
    """The rule the issue names explicitly: an `esterno`-imported (or lotto-batch)
    row has `xml_hash_sha256 = NULL` by construction (spec 2026-09-04 §3.1), and a
    re-import of the same number must never be waved through as identical to
    something nobody ever hashed, however innocuous the incoming bytes look."""
    existing = Invoice(xml_hash_sha256=None)
    assert check_invoice_duplicate(existing, b"any bytes at all") == "conflict"


# --- InvoiceRepository.existing_by_number: the natural-key lookup the check needs -


def _customer(session: Session) -> Customer:
    customer = Customer(
        ragione_sociale=f"Acme {uuid4()}",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def test_existing_by_number_finds_the_row_at_that_natural_key_and_nothing_else(
    db_session: Session,
) -> None:
    row = Invoice(
        customer_id=_customer(db_session).id,
        tipo="fattura",
        stato="emessa",
        anno=2026,
        numero=6,
        data_emissione=date(2026, 3, 15),
        importata_da="fatturapa",
        xml_hash_sha256="deadbeef",
        imponibile=Decimal("1000.00"),
        imposta=Decimal("0.00"),
        bollo=Decimal("2.00"),
        totale=Decimal("1000.00"),
    )
    db_session.add(row)
    db_session.flush()

    repo = InvoiceRepository(db_session)
    found = repo.existing_by_number(2026, 6)
    assert found is not None
    assert found.id == row.id
    assert found.xml_hash_sha256 == "deadbeef"
    assert repo.existing_by_number(2026, 7) is None
    assert repo.existing_by_number(2025, 6) is None


# --- classify_parsed_invoice: the four outcomes the issue's Done-when names -------


def test_incoming_reports_incoming_skipped_whatever_the_register_holds() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="09876543210", codice_fiscale="RSSMRA80A01H501U")
    outcome = classify_parsed_invoice(
        invoice,
        emitter,
        existing=Invoice(xml_hash_sha256="irrelevant, never reached"),
        content=b"irrelevant",
    )
    assert outcome == "incoming_skipped"


def test_an_outgoing_invoice_not_on_record_is_ready() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="BNCCHR85M41H501Z")
    outcome = classify_parsed_invoice(invoice, emitter, existing=None, content=_fixture(CONSULENZA))
    assert outcome == "ready"


def test_an_outgoing_invoice_with_a_matching_stored_hash_is_already_present() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="BNCCHR85M41H501Z")
    content = _fixture(CONSULENZA)
    existing = Invoice(xml_hash_sha256=_digest(content))
    outcome = classify_parsed_invoice(invoice, emitter, existing=existing, content=content)
    assert outcome == "already_present"


def test_an_outgoing_invoice_with_a_different_stored_hash_is_conflict() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="BNCCHR85M41H501Z")
    existing = Invoice(xml_hash_sha256=_digest(b"a different document entirely"))
    outcome = classify_parsed_invoice(
        invoice, emitter, existing=existing, content=_fixture(CONSULENZA)
    )
    assert outcome == "conflict"


def test_an_outgoing_invoice_with_no_stored_hash_is_conflict_not_already_present() -> None:
    [invoice] = parse(_fixture(CONSULENZA))
    emitter = _emitter(partita_iva="01234567890", codice_fiscale="BNCCHR85M41H501Z")
    existing = Invoice(xml_hash_sha256=None)
    outcome = classify_parsed_invoice(
        invoice, emitter, existing=existing, content=_fixture(CONSULENZA)
    )
    assert outcome == "conflict"
