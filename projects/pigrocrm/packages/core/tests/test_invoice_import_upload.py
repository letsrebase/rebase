"""FatturaPA files imported from bytes, not from an archived document (REB-758):
`expand_uploads`, `InvoiceService.review_upload` and `InvoiceService.confirm_upload`,
on the FPR12 fixtures `test_invoice_import_confirm.py` already uses and on a synthetic
year of them.

What each scenario serves is the card's own "Done when": a year of XML goes in from the
page and the register holds every invoice with no gap declared by hand, and the review
before it writes nothing.
"""

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_invoice_import_confirm import (
    ADMIN,
    CLIENTE_PIVA,
    CONSULENZA,
    LOTTO,
    _customer,
    _fixture,
    _invoice_count,
    _svc,
)

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents.models import Document
from pigrocrm.core.errors import PermissionDenied, ValidationFailed
from pigrocrm.core.invoices import import_upload
from pigrocrm.core.invoices.import_upload import (
    UploadedPartyDecision,
    expand_uploads,
    parse_decisions,
)
from pigrocrm.core.invoices.models import Invoice, InvoiceRegisterGap
from pigrocrm.core.invoices.repository import InvoiceRepository
from pigrocrm.core.storage import LocalFileStorage

COLLABORATORE = Actor(id=None, type="user", role="collaboratore")


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return out.getvalue()


def _year(count: int) -> dict[str, bytes]:
    """`count` synthetic invoices numbered 1..count, one per day from 2026-01-01, each
    the consulenza fixture with its number and date changed: the same counterparty, the
    same amounts, a register with no hole in it."""
    base = _fixture(CONSULENZA).decode()
    files: dict[str, bytes] = {}
    for numero in range(1, count + 1):
        day = date(2026, 1, 1) + timedelta(days=numero - 1)
        xml = base.replace("<Numero>2026/6</Numero>", f"<Numero>2026/{numero}</Numero>").replace(
            "<Data>2026-03-15</Data>", f"<Data>{day.isoformat()}</Data>"
        )
        files[f"IT01234567890_{numero:05d}.xml"] = xml.encode()
    return files


def _document_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Document)).scalar_one())


def _customer_count(session: Session) -> int:
    return int(session.execute(select(func.count()).select_from(Customer)).scalar_one())


def _items(files: dict[str, bytes]) -> Sequence[tuple[str, bytes]]:
    return list(files.items())


# --- expand_uploads ---------------------------------------------------------------


def test_a_plain_file_is_one_upload_named_and_hashed() -> None:
    content = _fixture(CONSULENZA)

    [file] = expand_uploads([("fattura.xml", content)])

    assert (file.name, file.content) == ("fattura.xml", content)
    assert file.sha256 == hashlib.sha256(content).hexdigest()


def test_a_zip_is_opened_and_each_entry_named_after_it_and_its_junk_is_not_a_file() -> None:
    archive = _zip(
        {
            "2026/uno.xml": b"<a/>",
            "2026/due.xml": b"<b/>",
            "__MACOSX/2026/._uno.xml": b"junk",
            "2026/.DS_Store": b"junk",
            "Thumbs.db": b"junk",
        }
    )

    files = expand_uploads([("anno.zip", archive)])

    assert [f.name for f in files] == ["anno.zip/2026/uno.xml", "anno.zip/2026/due.xml"]


def test_a_zip_is_recognised_by_its_bytes_not_its_name() -> None:
    [file] = expand_uploads([("rinominato.xml", _zip({"a.xml": b"<a/>"}))])

    assert file.name == "rinominato.xml/a.xml"


def test_a_zip_inside_a_zip_is_one_file_nothing_recognises_not_opened() -> None:
    inner = _zip({"a.xml": b"<a/>"})

    [file] = expand_uploads([("fuori.zip", _zip({"dentro.zip": inner}))])

    assert file.name == "fuori.zip/dentro.zip"
    assert file.content == inner


def test_a_request_with_nothing_to_read_is_refused_not_answered_with_an_empty_list() -> None:
    with pytest.raises(ValidationFailed):
        expand_uploads([])
    with pytest.raises(ValidationFailed):
        expand_uploads([("vuoto.zip", _zip({"__MACOSX/._a": b"junk"}))])


def test_a_broken_zip_is_refused_with_its_name() -> None:
    with pytest.raises(ValidationFailed, match="rotto.zip"):
        expand_uploads([("rotto.zip", b"PK\x03\x04 and then nothing a zip has")])


def test_a_zip_bomb_is_refused_by_what_it_decompresses_to_not_by_what_it_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILE_BYTES", 1024)
    honest = _zip({"grande.xml": b"a" * 5000})
    with pytest.raises(ValidationFailed, match="supera"):
        expand_uploads([("b.zip", honest)])

    # The directory is written by whoever made the archive: lower both copies of the
    # size to a harmless 100 bytes and the read must still stop at the ceiling.
    lying = bytearray(honest)
    real = (5000).to_bytes(4, "little")
    fake = (100).to_bytes(4, "little")
    index = 0
    while (index := lying.find(real, index)) != -1:
        lying[index : index + 4] = fake
        index += 4
    with pytest.raises(ValidationFailed, match="grande.xml"):
        expand_uploads([("b.zip", bytes(lying))])


def test_the_total_of_a_zips_entries_counts_against_the_requests_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValidationFailed, match="in tutto"):
        expand_uploads([("a.zip", _zip({"1.xml": b"123456", "2.xml": b"123456"}))])


def test_the_number_of_files_and_the_total_size_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILES", 2)
    with pytest.raises(ValidationFailed):
        expand_uploads([("a.xml", b"<a/>"), ("b.xml", b"<b/>"), ("c.xml", b"<c/>")])
    with pytest.raises(ValidationFailed):
        expand_uploads([("a.zip", _zip({"a": b"1", "b": b"2", "c": b"3"}))])

    monkeypatch.setattr(import_upload, "UPLOAD_MAX_FILES", 2000)
    monkeypatch.setattr(import_upload, "UPLOAD_MAX_TOTAL_BYTES", 10)
    with pytest.raises(ValidationFailed):
        expand_uploads([("a.xml", b"123456"), ("b.xml", b"123456")])


def test_decisions_are_json_keyed_by_party_and_anything_else_is_a_sentence() -> None:
    parsed = parse_decisions(json.dumps({"clienti": {"09876543210": {"create_customer": True}}}))

    assert parsed == {"09876543210": UploadedPartyDecision(create_customer=True)}
    assert parse_decisions("{}") == {}
    for bad in (
        "not json",
        json.dumps({"decisioni": {"abc": {}}}),
        json.dumps({"clienti": {"09876543210": {"x": 1}}}),
    ):
        with pytest.raises(ValidationFailed):
            parse_decisions(bad)


# --- review_upload ----------------------------------------------------------------


def test_a_review_reads_every_verdict_and_writes_nothing(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    known = _customer(db_session, partita_iva=CLIENTE_PIVA)
    invoices, documents, customers = (
        _invoice_count(db_session),
        _document_count(db_session),
        _customer_count(db_session),
    )

    [ready] = service.review_upload([("fattura.xml", _fixture(CONSULENZA))], ADMIN)
    [unclaimed] = service.review_upload([("note.txt", b"not a fattura")], ADMIN)

    assert ready.outcome == "ready"
    assert ready.file == "fattura.xml"
    assert ready.sha256 == hashlib.sha256(_fixture(CONSULENZA)).hexdigest()
    assert ready.matched_customer_id == known.id
    assert ready.invoice is not None and ready.invoice.numero == "2026/6"
    assert unclaimed.outcome == "unclaimed" and unclaimed.invoice is None
    assert (_invoice_count(db_session), _document_count(db_session)) == (invoices, documents)
    assert _customer_count(db_session) == customers


def test_a_review_tells_a_new_counterparty_from_an_invoice_already_on_the_register(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)

    [new] = service.review_upload([("fattura.xml", content)], ADMIN)
    assert new.outcome == "needs_customer_confirmation"

    service.confirm_upload(
        [("fattura.xml", content)],
        {new.party_key or "": UploadedPartyDecision(create_customer=True)},
        ADMIN,
    )
    [again] = service.review_upload([("fattura.xml", content)], ADMIN)
    assert again.outcome == "already_present"


def test_a_lotto_reviews_as_one_row_per_invoice_under_the_one_file(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)

    rows = service.review_upload([("lotto.xml", _fixture(LOTTO))], ADMIN)

    assert len(rows) == 2
    assert {row.file for row in rows} == {"lotto.xml"}
    assert len({row.sha256 for row in rows}) == 1


def test_reviewing_and_confirming_are_admin_only(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)

    with pytest.raises(PermissionDenied):
        service.review_upload([("a.xml", b"<a/>")], COLLABORATORE)
    with pytest.raises(PermissionDenied):
        service.confirm_upload([("a.xml", b"<a/>")], {}, COLLABORATORE)


# --- confirm_upload ---------------------------------------------------------------


def test_a_single_invoice_file_is_written_and_its_xml_filed_under_the_customer(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    customer = _customer(db_session, partita_iva=CLIENTE_PIVA)
    content = _fixture(CONSULENZA)
    documents = _document_count(db_session)

    [row] = service.confirm_upload([("fattura.xml", content)], {}, ADMIN)

    assert row.outcome == "imported"
    assert row.file == "fattura.xml"
    fattura = row.fattura
    assert fattura is not None
    assert (fattura.anno, fattura.numero, fattura.importata_da) == (2026, 6, "fatturapa")
    assert fattura.customer_id == customer.id
    assert fattura.xml_hash_sha256 == hashlib.sha256(content).hexdigest()
    # One document more: the XML, filed as the customer's, and served back byte for byte.
    assert _document_count(db_session) == documents + 1
    assert fattura.xml_document_id is not None
    document = db_session.get(Document, fattura.xml_document_id)
    assert document is not None
    assert (document.customer_id, document.tipo) == (customer.id, "fattura_xml")
    artefact = service.download(fattura.id, "xml", ADMIN)
    assert artefact[0] == content


def test_the_same_file_confirmed_twice_is_written_once_and_files_one_xml(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    _customer(db_session, partita_iva=CLIENTE_PIVA)
    content = _fixture(CONSULENZA)

    [first] = service.confirm_upload([("fattura.xml", content)], {}, ADMIN)
    invoices, documents = _invoice_count(db_session), _document_count(db_session)
    [second] = service.confirm_upload([("copia.xml", content)], {}, ADMIN)

    assert second.outcome == "already_present"
    assert second.fattura is not None and first.fattura is not None
    assert second.fattura.id == first.fattura.id
    assert (_invoice_count(db_session), _document_count(db_session)) == (invoices, documents)


def test_a_new_counterparty_is_created_only_when_the_decision_for_its_customer_says_so(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    content = _fixture(CONSULENZA)
    customers, documents = _customer_count(db_session), _document_count(db_session)

    [withheld] = service.confirm_upload([("fattura.xml", content)], {}, ADMIN)
    assert withheld.outcome == "needs_customer_confirmation"
    assert withheld.fattura is None
    assert (_customer_count(db_session), _document_count(db_session)) == (customers, documents)

    [created] = service.confirm_upload(
        [("fattura.xml", content)],
        {"09876543210": UploadedPartyDecision(create_customer=True)},
        ADMIN,
    )
    assert created.outcome == "imported"
    assert _customer_count(db_session) == customers + 1
    assert created.fattura is not None and created.fattura.xml_document_id is not None


def test_a_decision_names_the_customer_to_attach_by_the_partys_tax_id(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    chosen = _customer(db_session)
    content = _fixture(CONSULENZA)

    [row] = service.confirm_upload(
        [("fattura.xml", content)],
        {"09876543210": UploadedPartyDecision(customer_id=chosen.id)},
        ADMIN,
    )

    assert row.fattura is not None and row.fattura.customer_id == chosen.id


def test_a_lotto_is_written_per_invoice_and_archives_no_xml(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    documents = _document_count(db_session)

    rows = service.confirm_upload(
        [("lotto.xml", _fixture(LOTTO))],
        {"09876543210": UploadedPartyDecision(create_customer=True)},
        ADMIN,
    )

    assert [row.outcome for row in rows] == ["imported", "imported"]
    assert all(row.fattura is not None and row.fattura.xml_document_id is None for row in rows)
    assert _document_count(db_session) == documents


def test_a_refused_invoice_leaves_neither_its_customer_nor_its_xml(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    InvoiceRepository(db_session).add_gap(
        InvoiceRegisterGap(anno=2026, numero=6, motivo="annullata prima della trasmissione")
    )
    content = _fixture(CONSULENZA)
    invoices, documents, customers = (
        _invoice_count(db_session),
        _document_count(db_session),
        _customer_count(db_session),
    )

    [row] = service.confirm_upload(
        [("fattura.xml", content)],
        {"09876543210": UploadedPartyDecision(create_customer=True)},
        ADMIN,
    )

    assert row.outcome == "conflict"
    assert (
        _invoice_count(db_session),
        _document_count(db_session),
        _customer_count(db_session),
    ) == (invoices, documents, customers)


def test_a_year_of_xml_in_a_zip_fills_the_register_with_no_gap_declared_by_hand(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    archive = [("fatture-2026.zip", _zip(_year(24)))]

    reviewed = service.review_upload(archive, ADMIN)
    assert [row.outcome for row in reviewed] == ["needs_customer_confirmation"] * 24
    decisions = {"09876543210": UploadedPartyDecision(create_customer=True)}

    confirmed = service.confirm_upload(archive, decisions, ADMIN)

    assert [row.outcome for row in confirmed] == ["imported"] * 24
    assert sorted(row.fattura.numero for row in confirmed if row.fattura) == list(range(1, 25))
    # One counterparty, created once, for all of them.
    assert len({row.fattura.customer_id for row in confirmed if row.fattura}) == 1
    # What the register reports as missing: nothing, and no gap row was declared.
    assert service.undeclared_gaps(2026) == []
    assert db_session.execute(select(func.count()).select_from(InvoiceRegisterGap)).scalar() == 0
    assert db_session.execute(select(func.count()).select_from(Invoice)).scalar() == 24
    # Every file was written, so the same archive reviews as nothing left to do.
    again = service.review_upload(archive, ADMIN)
    assert {row.outcome for row in again} == {"already_present"}


def test_a_year_with_a_missing_number_reports_the_gap_it_leaves(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    files = _year(5)
    del files["IT01234567890_00003.xml"]
    archive = [("fatture.zip", _zip(files))]
    decisions = {"09876543210": UploadedPartyDecision(create_customer=True)}

    confirmed = service.confirm_upload(archive, decisions, ADMIN)

    assert [row.outcome for row in confirmed] == ["imported"] * 4
    assert confirmed[-1].buchi_non_dichiarati == [3]


def test_a_foreign_counterparty_with_no_italian_tax_id_is_created_once_across_the_request(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """`match_customer` cannot find a customer without an Italian VAT number or tax code, so
    every file of a foreign customer would create a new one; the counterparty named by the
    decision is created for the first and reused for the rest."""
    service = _svc(db_session, local_storage)
    italian = "<IdPaese>IT</IdPaese>\n          <IdCodice>09876543210</IdCodice>"
    german = "<IdPaese>DE</IdPaese>\n          <IdCodice>123456789</IdCodice>"
    files = {
        name: content.decode().replace(italian, german).encode()
        for name, content in _year(3).items()
    }
    archive = [("estero.zip", _zip(files))]
    [first, *_] = service.review_upload(archive, ADMIN)
    assert first.party_key == "estero:DE123456789"
    customers = _customer_count(db_session)

    confirmed = service.confirm_upload(
        archive, {first.party_key or "": UploadedPartyDecision(create_customer=True)}, ADMIN
    )

    assert [row.outcome for row in confirmed] == ["imported"] * 3
    assert len({row.fattura.customer_id for row in confirmed if row.fattura}) == 1
    assert _customer_count(db_session) == customers + 1


def test_two_foreign_customers_with_one_name_and_different_vat_numbers_stay_two(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    italian = "<IdPaese>IT</IdPaese>\n          <IdCodice>09876543210</IdCodice>"
    files = [
        (
            f"{vat}.xml",
            content.decode()
            .replace(italian, f"<IdPaese>DE</IdPaese>\n          <IdCodice>{vat}</IdCodice>")
            .encode(),
        )
        for vat, content in zip(("123456789", "987654321"), _year(2).values(), strict=True)
    ]
    first, second = service.review_upload(files, ADMIN)
    assert (first.party_key, second.party_key) == ("estero:DE123456789", "estero:DE987654321")
    customers = _customer_count(db_session)

    confirmed = service.confirm_upload(
        files,
        {
            first.party_key or "": UploadedPartyDecision(create_customer=True),
            second.party_key or "": UploadedPartyDecision(create_customer=True),
        },
        ADMIN,
    )

    assert [row.outcome for row in confirmed] == ["imported"] * 2
    assert len({row.fattura.customer_id for row in confirmed if row.fattura}) == 2
    assert _customer_count(db_session) == customers + 2


def test_a_file_that_looks_like_a_fattura_and_does_not_parse_is_unclaimed_with_its_reason(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    service = _svc(db_session, local_storage)
    broken = _fixture(CONSULENZA).replace(b"TD06", b"TD99")
    files = [("rotta.xml", broken), ("buona.xml", _fixture(CONSULENZA))]

    bad, good = service.review_upload(files, ADMIN)
    assert (bad.outcome, good.outcome) == ("unclaimed", "needs_customer_confirmation")
    assert bad.motivo is not None and "TD99" in bad.motivo

    bad_confirmed, good_confirmed = service.confirm_upload(
        files, {good.party_key or "": UploadedPartyDecision(create_customer=True)}, ADMIN
    )
    assert (bad_confirmed.outcome, good_confirmed.outcome) == ("unclaimed", "imported")
    assert bad_confirmed.motivo is not None and "TD99" in bad_confirmed.motivo


def test_an_invoice_the_register_schema_refuses_is_a_conflict_and_does_not_stop_the_request(
    db_session: Session, local_storage: LocalFileStorage
) -> None:
    """201 lines pass the parser and review, and `InvoiceImport` allows 200: the invoice is
    refused as a `conflict` row instead of a 500, and nothing is left behind for it."""
    service = _svc(db_session, local_storage)
    base = _fixture(CONSULENZA).decode()
    start = base.index("<DettaglioLinee>")
    end = base.index("</DettaglioLinee>") + len("</DettaglioLinee>")
    many = base[:start] + base[start:end] * 201 + base[end:]
    # The long one last: `rollback()` in this fixture's savepoint also takes back the azienda
    # `_svc` created, so a file after it could not be classified, which production's
    # committed azienda never suffers.
    files = [("normale.xml", _year(2)["IT01234567890_00002.xml"]), ("lunga.xml", many.encode())]
    create = {"09876543210": UploadedPartyDecision(create_customer=True)}
    customers = _customer_count(db_session)

    normal, long = service.confirm_upload(files, create, ADMIN)

    assert (normal.outcome, long.outcome) == ("imported", "conflict")
    assert long.fattura is None and normal.fattura is not None
    # The customer the long invoice would have shared is the one the first file created:
    # nothing extra was left behind by the refused one.
    assert _customer_count(db_session) == customers + 1
