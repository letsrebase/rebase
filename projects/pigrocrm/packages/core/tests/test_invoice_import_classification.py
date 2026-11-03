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
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa_import import parse
from pigrocrm.core.invoices.import_classification import classify_parsed_invoice
from pigrocrm.core.invoices.import_dedup import check_invoice_duplicate
from pigrocrm.core.invoices.import_direction import (
    classify_direction,
    match_legal_entity,
)
from pigrocrm.core.invoices.import_schemas import ParsedInvoiceParty
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.legal_entities.models import LegalEntity

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"
CONSULTING = "fpr-12-march-consulting.xml"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _legal_entity(**overrides: object) -> LegalEntity:
    base: dict[str, object] = {
        "legal_name": "Studio Rossi",
        "vat_number": "01234567890",
        "tax_code": "HMCRFT00A01H501K",
        "country": "IT",
    }
    base.update(overrides)
    return LegalEntity(**base)


def _party(**overrides: object) -> ParsedInvoiceParty:
    base: dict[str, object] = {
        "legal_name": "Chiara Bianchi",
        "vat_number": "IT01234567890",
        "tax_code": "BNCCHR85M41H501Z",
        "address": "Via delle Officine 10",
        "cap": "20121",
        "city": "Milano",
        "province": "MI",
        "country": "IT",
    }
    base.update(overrides)
    return ParsedInvoiceParty(**base)


# --- classify_direction: the low-level party-vs-emitter comparison ----------------


def test_to_matching_vat_number_is_outgoing_case_and_prefix_insensitive() -> None:
    supplier = _party(vat_number="it 01234567890")
    legal_entity = _legal_entity(vat_number="01234567890")
    assert classify_direction(supplier, [legal_entity]) == "outgoing"


def test_to_matching_tax_code_is_outgoing_when_the_vat_number_channel_misses() -> None:
    supplier = _party(vat_number="IT99999999999", tax_code="hmcrft00a01h501k")
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="HMCRFT00A01H501K")
    assert classify_direction(supplier, [legal_entity]) == "outgoing"


def test_neither_identifier_matching_is_incoming() -> None:
    supplier = _party(vat_number="IT99999999999", tax_code="ZZZZZZ00A01H501Z")
    legal_entity = _legal_entity()
    assert classify_direction(supplier, [legal_entity]) == "incoming"


def test_a_supplier_with_no_recognisable_identifier_is_incoming_not_a_crash() -> None:
    supplier = _party(vat_number=None, tax_code=None)
    legal_entity = _legal_entity()
    assert classify_direction(supplier, [legal_entity]) == "incoming"


def test_an_unconfigured_legal_entities_refuses_to_classify_at_all() -> None:
    supplier = _party()
    legal_entity = _legal_entity(vat_number=None, tax_code=None)
    with pytest.raises(ValidationFailed) as caught:
        classify_direction(supplier, [legal_entity])
    assert caught.value.details["entity"] == "legal_entities"


def test_the_file_lands_on_the_legal_entity_whose_identifier_matches_among_several() -> None:
    """REB-619, spec §1.5: a space with two aziende, the supplier names the second."""
    humancraft = _legal_entity(vat_number="01234567890", tax_code="HMCRFT00A01H501K")
    rebase = _legal_entity(legal_name="Rebase S.r.l.", vat_number="09876543210", tax_code=None)
    supplier = _party(vat_number="IT09876543210", tax_code=None)
    assert match_legal_entity(supplier, [humancraft, rebase]) is rebase
    assert classify_direction(supplier, [humancraft, rebase]) == "outgoing"
    stranger = _party(vat_number="IT11111111111", tax_code=None)
    assert match_legal_entity(stranger, [humancraft, rebase]) is None


def test_to_supplier_matching_two_legal_entities_by_different_channels_is_refused() -> None:
    """The one ambiguity the two unique indexes cannot rule out (spec §1.5): X by
    P.IVA, Y by codice fiscale. Nothing is classified, the configuration is named."""
    by_vat_number = _legal_entity(vat_number="01234567890", tax_code=None)
    by_cf = _legal_entity(vat_number="09876543210", tax_code="HMCRFT00A01H501K")
    supplier = _party(vat_number="IT01234567890", tax_code="HMCRFT00A01H501K")
    with pytest.raises(ValidationFailed) as caught:
        match_legal_entity(supplier, [by_vat_number, by_cf])
    assert caught.value.details["entity"] == "legal_entities"
    assert caught.value.details["field"] == "tax_code"


def test_only_an_legal_entity_with_an_identifier_counts_as_configured() -> None:
    """One azienda with ids beside one without: there is a fact to compare against."""
    supplier = _party(vat_number="IT01234567890")
    configured = _legal_entity(vat_number="01234567890")
    bare = _legal_entity(vat_number=None, tax_code=None)
    assert match_legal_entity(supplier, [bare, configured]) is configured


# --- classify_direction: the same check against a real parsed document's supplier ---


def test_the_fixtures_own_issuer_classifies_as_outgoing_against_her_own_profile() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="BNCCHR85M41H501Z")
    assert classify_direction(invoice.supplier, [legal_entity]) == "outgoing"


def test_the_same_document_is_incoming_against_a_different_accounts_profile() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="09876543210", tax_code="RSSMRA80A01H501U")
    assert classify_direction(invoice.supplier, [legal_entity]) == "incoming"


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
    """The rule the issue names explicitly: an `external`-imported (or lotto-batch)
    row has `xml_hash_sha256 = NULL` by construction (spec 2026-09-04 §3.1), and a
    re-import of the same number must never be waved through as identical to
    something nobody ever hashed, however innocuous the incoming bytes look."""
    existing = Invoice(xml_hash_sha256=None)
    assert check_invoice_duplicate(existing, b"any bytes at all") == "conflict"


# --- InvoiceRepository.existing_by_number: the natural-key lookup the check needs -


def _customer(session: Session) -> Customer:
    customer = Customer(
        legal_name=f"Acme {uuid4()}",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def test_existing_by_number_finds_the_row_at_that_natural_key_and_nothing_else(
    db_session: Session,
) -> None:
    row = Invoice(
        customer_id=_customer(db_session).id,
        type="invoice",
        status="issued",
        year=2026,
        number=6,
        issue_date=date(2026, 3, 15),
        imported_from="fatturapa",
        xml_hash_sha256="deadbeef",
        taxable_amount=Decimal("1000.00"),
        vat_amount=Decimal("0.00"),
        stamp_duty=Decimal("2.00"),
        total=Decimal("1000.00"),
    )
    db_session.add(row)
    db_session.flush()

    repo = InvoiceRepository(db_session)
    legal_entity_id = row.legal_entity_id  # the default azienda, by the column's own default
    found = repo.existing_by_number(legal_entity_id, 2026, 6)
    assert found is not None
    assert found.id == row.id
    assert found.xml_hash_sha256 == "deadbeef"
    assert repo.existing_by_number(legal_entity_id, 2026, 7) is None
    assert repo.existing_by_number(legal_entity_id, 2025, 6) is None
    # Another azienda's register does not carry it (REB-619).
    assert repo.existing_by_number(uuid4(), 2026, 6) is None


# --- classify_parsed_invoice: the four outcomes the issue's Done-when names -------


def test_incoming_reports_incoming_skipped_whatever_the_register_holds() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="09876543210", tax_code="RSSMRA80A01H501U")
    outcome, azienda = classify_parsed_invoice(
        invoice,
        [legal_entity],
        existing_for=lambda _a: Invoice(xml_hash_sha256="irrelevant, never reached"),
        content=b"irrelevant",
    )
    assert (outcome, azienda) == ("incoming_skipped", None)


def test_an_outgoing_invoice_not_on_record_is_ready() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="BNCCHR85M41H501Z")
    outcome, azienda = classify_parsed_invoice(
        invoice, [legal_entity], existing_for=lambda _a: None, content=_fixture(CONSULTING)
    )
    assert (outcome, azienda) == ("ready", legal_entity)


def test_an_outgoing_invoice_with_a_matching_stored_hash_is_already_present() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="BNCCHR85M41H501Z")
    content = _fixture(CONSULTING)
    existing = Invoice(xml_hash_sha256=_digest(content))
    outcome, azienda = classify_parsed_invoice(
        invoice, [legal_entity], existing_for=lambda _a: existing, content=content
    )
    assert (outcome, azienda) == ("already_present", legal_entity)


def test_an_outgoing_invoice_with_a_different_stored_hash_is_conflict() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="BNCCHR85M41H501Z")
    existing = Invoice(xml_hash_sha256=_digest(b"a different document entirely"))
    outcome, _ = classify_parsed_invoice(
        invoice, [legal_entity], existing_for=lambda _a: existing, content=_fixture(CONSULTING)
    )
    assert outcome == "conflict"


def test_an_outgoing_invoice_with_no_stored_hash_is_conflict_not_already_present() -> None:
    [invoice] = parse(_fixture(CONSULTING))
    legal_entity = _legal_entity(vat_number="01234567890", tax_code="BNCCHR85M41H501Z")
    existing = Invoice(xml_hash_sha256=None)
    outcome, _ = classify_parsed_invoice(
        invoice, [legal_entity], existing_for=lambda _a: existing, content=_fixture(CONSULTING)
    )
    assert outcome == "conflict"
