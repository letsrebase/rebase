"""What the inbox accepts and what it reads of a file (REB-971): the allowlist and its
signature checks, the `.p7m` reader and the keys deduplication uses. No database."""

import base64
import io
import re
import zipfile
from pathlib import Path

import pytest

from pigrocrm.core.inbox.filetypes import UnsupportedFile, detect_file_type
from pigrocrm.core.inbox.p7m import MAX_ENVELOPES, P7mError, open_p7m
from pigrocrm.core.inbox.reading import (
    METADATA_REASON,
    RECEIPT_REASON,
    fatturapa_key,
    read_file,
    sha256_of,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fatturapa"
INVOICE = (FIXTURES / "fpr-12-march-consulting.xml").read_bytes()
LOTTO = (FIXTURES / "fpr-12-batch-due-invoices.xml").read_bytes()
SIGNED = (FIXTURES / "fpr-12-march-consulting.xml.p7m").read_bytes()
SIGNED_BASE64 = (FIXTURES / "fpr-12-march-consulting-base64.xml.p7m").read_bytes()

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
XLS = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 16


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


def _id(value: object) -> str:
    """A readable, stable id for a parameter: bytes would put the file in the test's name, and
    a ZIP's timestamps make that name differ between xdist workers."""
    return f"{len(value)}b" if isinstance(value, bytes) else str(value)


def _tlv(tag: int, body: bytes) -> bytes:
    """Tag, DER length (long form past 127 bytes) and body."""
    if len(body) < 0x80:
        return bytes([tag, len(body)]) + body
    length = len(body).to_bytes((len(body).bit_length() + 7) // 8)
    return bytes([tag, 0x80 | len(length)]) + length + body


def _indefinite_envelope(document: bytes) -> bytes:
    """A `SignedData` envelope the way the SdI's tools write it: BER, with indefinite lengths
    and the content as a constructed OCTET STRING in two chunks. `cryptography` only writes
    DER, so the bytes are laid out by hand."""
    half = len(document) // 2
    octets = b"\x24\x80" + _tlv(4, document[:half]) + _tlv(4, document[half:]) + b"\x00\x00"
    encap = (
        b"\x30\x80"
        + bytes.fromhex("06092a864886f70d010701")
        + b"\xa0\x80"
        + octets
        + b"\x00\x00\x00\x00"
    )
    signed = b"\x30\x80" + bytes.fromhex("020101") + bytes.fromhex("3100") + encap
    signed += bytes.fromhex("3100") + b"\x00\x00"
    return (
        b"\x30\x80"
        + bytes.fromhex("06092a864886f70d010702")
        + b"\xa0\x80"
        + signed
        + b"\x00\x00\x00\x00"
    )


# ---- the allowlist and the signatures -------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("fattura.xml", INVOICE, "xml"),
        ("FATTURA.XML", INVOICE, "xml"),
        ("fattura.xml", b"\xef\xbb\xbf  <?xml version='1.0'?><a/>", "xml"),
        (
            "estratto.xml",
            b"<Document xmlns='urn:iso:std:iso:20022:tech:xsd:camt.053.001.02'/>",
            "camt053",
        ),
        ("IT0123_1.xml.p7m", SIGNED, "p7m"),
        ("IT0123_1.xml.p7m", SIGNED_BASE64, "p7m"),
        ("cassetto.zip", _zip({"a.xml": INVOICE}), "zip"),
        ("estratto.pdf", PDF, "pdf"),
        ("scontrino.png", PNG, "png"),
        ("scontrino.jpg", JPEG, "jpeg"),
        ("scontrino.JPEG", JPEG, "jpeg"),
        ("movimenti.csv", b"data;importo\n2026-01-01;10,00\n", "csv"),
        ("movimenti.csv", "\ufeffdata;importo\n".encode("utf-16"), "csv"),
        ("movimenti.xls", XLS, "xls"),
        (
            "movimenti.xlsx",
            _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"}),
            "xlsx",
        ),
        ("a/b\\c/fattura.xml", INVOICE, "xml"),
    ],
    ids=_id,
)
def test_a_file_of_an_accepted_type_with_its_signature_is_accepted(
    name: str, content: bytes, expected: str
) -> None:
    assert detect_file_type(name, content) == expected


@pytest.mark.parametrize(
    "name",
    [
        "virus.exe",
        "note.txt",
        "document.docx",
        "photo.heic",
        "archive.tar.gz",
        "noextension",
        ".xml",
    ],
)
def test_a_type_the_inbox_does_not_take_is_refused_by_its_extension(name: str) -> None:
    with pytest.raises(UnsupportedFile, match="tipo di file non accettato"):
        detect_file_type(name, b"anything")


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("fattura.xml", b"MZ\x90\x00 an executable called xml"),
        ("firmato.p7m", PDF),
        ("cassetto.zip", PDF),
        ("estratto.pdf", INVOICE),
        ("scontrino.png", JPEG),
        ("scontrino.jpg", PNG),
        ("movimenti.xls", b"just text"),
        ("movimenti.xlsx", _zip({"a.xml": b"<a/>"})),
        ("movimenti.xlsx", b"PK\x03\x04 not really a zip"),
        ("movimenti.csv", b"MZ\x00\x00\x00 binary"),
    ],
    ids=_id,
)
def test_a_signature_that_disagrees_with_the_extension_is_refused(
    name: str, content: bytes
) -> None:
    with pytest.raises(UnsupportedFile, match="il contenuto non e' quello"):
        detect_file_type(name, content)


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(UnsupportedFile, match="vuoto"):
        detect_file_type("fattura.xml", b"")


def test_an_xlsx_is_not_taken_for_a_zip_and_a_zip_not_for_an_xlsx() -> None:
    workbook = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<w/>"})
    assert detect_file_type("a.xlsx", workbook) == "xlsx"
    assert detect_file_type("a.zip", workbook) == "zip"


# ---- the p7m reader --------------------------------------------------------------------


def test_a_der_envelope_gives_back_the_signed_xml() -> None:
    assert open_p7m(SIGNED) == INVOICE


def test_a_base64_envelope_gives_back_the_signed_xml() -> None:
    assert open_p7m(SIGNED_BASE64) == INVOICE
    assert (
        open_p7m(b"-----BEGIN PKCS7-----\n" + SIGNED_BASE64 + b"-----END PKCS7-----\n") == INVOICE
    )


def test_a_ber_envelope_with_indefinite_lengths_gives_back_the_signed_xml() -> None:
    assert open_p7m(_indefinite_envelope(INVOICE)) == INVOICE


def test_a_counter_signed_file_is_opened_down_to_the_document() -> None:
    assert open_p7m(_indefinite_envelope(SIGNED)) == INVOICE


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (PDF, "ne' DER ne' Base64"),
        (b"\x30\x82garbage", "non si legge"),
        (base64.b64encode(b"\x30\x03\x02\x01\x01"), "non si legge"),
    ],
    ids=_id,
)
def test_a_file_that_is_not_an_envelope_says_why(data: bytes, reason: str) -> None:
    with pytest.raises(P7mError, match=reason):
        open_p7m(data)


def test_envelopes_nested_beyond_the_ceiling_are_refused() -> None:
    wrapped = INVOICE
    for _ in range(MAX_ENVELOPES + 1):
        wrapped = _indefinite_envelope(wrapped)
    with pytest.raises(P7mError, match="una dentro l'altra"):
        open_p7m(wrapped)


# ---- what is read of a file ------------------------------------------------------------


def test_the_same_invoice_has_one_natural_key_whatever_carries_it() -> None:
    key = fatturapa_key(INVOICE)
    assert key is not None
    assert read_file("a.xml", "xml", INVOICE).natural_key == key
    assert read_file("a.xml.p7m", "p7m", SIGNED).natural_key == key
    assert read_file("b.xml.p7m", "p7m", SIGNED_BASE64).natural_key == key


def test_a_signed_copy_has_the_content_hash_of_the_xml_it_signs() -> None:
    assert read_file("a.xml.p7m", "p7m", SIGNED).content_sha256 == sha256_of(INVOICE)
    assert read_file("a.xml", "xml", INVOICE).content_sha256 == sha256_of(INVOICE)
    assert sha256_of(SIGNED) != sha256_of(INVOICE)


def test_two_invoices_have_two_keys_and_a_lotto_has_its_own() -> None:
    assert fatturapa_key(INVOICE) != fatturapa_key(LOTTO)


def test_a_key_does_not_depend_on_the_order_of_the_invoices_of_a_lotto() -> None:
    blocks = re.findall(
        rb"\s*<FatturaElettronicaBody>.*?</FatturaElettronicaBody>", LOTTO, flags=re.DOTALL
    )
    assert len(blocks) == 2
    swapped = LOTTO.replace(blocks[0] + blocks[1], blocks[1] + blocks[0])
    assert swapped != LOTTO
    assert fatturapa_key(swapped) == fatturapa_key(LOTTO)


def test_a_document_that_is_not_a_fatturapa_has_no_key() -> None:
    assert fatturapa_key(b"<a>not an invoice</a>") is None
    assert read_file("a.pdf", "pdf", PDF).natural_key is None


def test_an_unreadable_envelope_is_kept_as_other_with_the_reason() -> None:
    reading = read_file("rotto.xml.p7m", "p7m", b"\x30\x82garbage")
    assert reading.kind == "other"
    assert reading.note is not None and "non si legge" in reading.note
    assert reading.discard_reason is None
    assert reading.content_sha256 == sha256_of(b"\x30\x82garbage")


def test_the_sdi_metadata_files_and_receipts_are_discarded_with_a_reason() -> None:
    assert read_file("IT01234567890_00001_MT_001.xml", "xml", b"<a/>").discard_reason == (
        METADATA_REASON
    )
    receipt = b"<?xml version='1.0'?><ns2:RicevutaConsegna xmlns:ns2='urn:x'/>"
    assert read_file("IT01234567890_00001_RC_001.xml", "xml", receipt).discard_reason == (
        RECEIPT_REASON
    )
    notice = b"<NotificaScarto/>"
    assert read_file("anything.xml", "xml", notice).discard_reason == RECEIPT_REASON
    assert read_file("fattura.xml", "xml", INVOICE).discard_reason is None
