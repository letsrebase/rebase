"""FatturaPA files that arrive as bytes instead of as an archived document (REB-758).

`review_invoice_import` and `confirm_invoice_import` take the `document_id` of a file
that is already filed, which is the right door for an agent (MCP never carries bytes)
and the wrong one for a person at the invoices page. A `documents` row must have an
owner (`ck_documents_customer_xor_deal`), and the owner of an invoice's XML is the
customer the invoice is billed to: a customer that, for a counterparty the register has
never seen, does not exist until the confirmation creates it. There is nothing to
stage the upload on before the review has run, so the page sends the files twice
instead, once to be reviewed and once to be written, and nothing is stored between the
two calls. The server never trusts the first for the second: `InvoiceService.
confirm_upload` re-parses and re-classifies exactly as `confirm_import` does, because
it is the same function (`InvoiceService._confirm_content`), and a single-invoice file
is archived only then, under the customer the invoice was written for, in the same
transaction as the invoice.

This module is the part that needs no database: what an upload is (a file, or the
entries of a ZIP), the bounds that keep a ZIP from being a bomb, and the shapes the two
calls answer with. A file is named in the answer by its SHA-256 (two files with the same
name in two folders are two files, and two copies of the same bytes are one), and the
one decision the page makes, whether to create a new customer, is per counterparty, named
by the `party_key` the review gives: the same customer holds for every invoice of it in
every file and every batch, and a `lotto` that bills two customers is two decisions.
"""

import hashlib
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa import normalise_fiscal_id, normalise_foreign_fiscal_id
from pigrocrm.core.invoices.import_confirm import InvoiceConfirmOutcome
from pigrocrm.core.invoices.import_review import InvoiceReviewOutcome
from pigrocrm.core.invoices.import_schemas import ParsedInvoice, ParsedInvoiceParty
from pigrocrm.core.invoices.schemas import InvoiceRead
from pigrocrm.core.work_units.day_mapping import WorkUnitDayMappingProposal

ENTITY = "invoice"

ZIP_MAGIC = b"PK\x03\x04"
"""What a ZIP starts with. Read off the bytes, never the name: a `.zip` that is not one
is reviewed as the unclaimed file it is, and an archive renamed `.xml` is still opened."""

UPLOAD_MAX_FILES = 2000
UPLOAD_MAX_FILE_BYTES = 10 * 1024 * 1024
UPLOAD_MAX_TOTAL_BYTES = 100 * 1024 * 1024
"""One request's ceilings, after the ZIPs are opened. A FatturaPA file is kilobytes and a
lotto of a few hundred invoices is a few megabytes; these leave a year of a busy space
room and stop an archive that decompresses into the whole disk. The total is the same
100 MiB `documents.DIMENSIONE_MAX` and the nginx gate in front of the route use."""


@dataclass(frozen=True, slots=True)
class UploadedFile:
    """One file to review or confirm: an upload as it was, or one entry of a ZIP, named
    `archive.zip/entry.xml` so the page can say where it came from."""

    name: str
    content: bytes
    sha256: str


def _refuse(reason: str, *, expected: str | None = None) -> ValidationFailed:
    return ValidationFailed(ENTITY, "files", reason, expected=expected)


def _is_junk(entry: str) -> bool:
    """What a ZIP made on a Mac or Windows carries beside the files: not a file anyone
    chose, and a row of `unclaimed` noise for every one of them otherwise."""
    parts = entry.replace("\\", "/").split("/")
    return parts[0] == "__MACOSX" or parts[-1].startswith(".") or parts[-1] == "Thumbs.db"


def _unzip(name: str, content: bytes, room: int) -> list[tuple[str, bytes]]:
    """The regular files of a ZIP, every one read through a ceiling: `file_size` in a
    ZIP's own directory is written by whoever made the archive and is only the cheap
    first refusal, never what the read relies on. `room` is what is left of the
    request's total before this archive."""
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise _refuse(f"{name}: l'archivio ZIP non si legge") from exc
    entries: list[tuple[str, bytes]] = []
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir() and not _is_junk(i.filename)]
        if len(infos) > UPLOAD_MAX_FILES:
            raise _refuse(
                f"{name}: l'archivio contiene {len(infos)} file",
                expected=f"al massimo {UPLOAD_MAX_FILES} file",
            )
        used = 0
        for info in infos:
            label = f"{name}/{info.filename}"
            if info.flag_bits & 0x1:
                raise _refuse(f"{label}: l'archivio e' protetto da password")
            if info.file_size > UPLOAD_MAX_FILE_BYTES:
                raise _refuse(
                    f"{label}: il file supera {UPLOAD_MAX_FILE_BYTES} byte",
                    expected=f"al massimo {UPLOAD_MAX_FILE_BYTES} byte per file",
                )
            try:
                with archive.open(info) as handle:
                    data = handle.read(UPLOAD_MAX_FILE_BYTES + 1)
            except (
                zipfile.BadZipFile,
                NotImplementedError,
                RuntimeError,
                zlib.error,
                EOFError,
            ) as exc:
                raise _refuse(f"{label}: il file dell'archivio non si legge") from exc
            if len(data) > UPLOAD_MAX_FILE_BYTES:
                raise _refuse(
                    f"{label}: il file supera {UPLOAD_MAX_FILE_BYTES} byte",
                    expected=f"al massimo {UPLOAD_MAX_FILE_BYTES} byte per file",
                )
            used += len(data)
            if used > room:
                raise _refuse(
                    f"{name}: il contenuto supera {UPLOAD_MAX_TOTAL_BYTES} byte",
                    expected=f"al massimo {UPLOAD_MAX_TOTAL_BYTES} byte in tutto",
                )
            entries.append((label, data))
    return entries


def expand_uploads(files: Sequence[tuple[str, bytes]]) -> list[UploadedFile]:
    """Every file of the request, each ZIP replaced by its entries, in the order given.

    A ZIP inside a ZIP is not opened: it comes back as a file nothing recognises, which
    is the honest answer and keeps the depth of the read at one. Nothing is dropped
    silently except what `_is_junk` names; an empty request is refused, since a review
    of nothing would answer an empty list and read as "all clear"."""
    expanded: list[tuple[str, bytes]] = []
    total = 0
    for name, content in files:
        if len(content) > UPLOAD_MAX_TOTAL_BYTES - total:
            raise _refuse(
                f"{name}: i file superano {UPLOAD_MAX_TOTAL_BYTES} byte in tutto",
                expected=f"al massimo {UPLOAD_MAX_TOTAL_BYTES} byte in tutto",
            )
        if content.startswith(ZIP_MAGIC):
            entries = _unzip(name, content, UPLOAD_MAX_TOTAL_BYTES - total)
        else:
            if len(content) > UPLOAD_MAX_FILE_BYTES:
                raise _refuse(
                    f"{name}: il file supera {UPLOAD_MAX_FILE_BYTES} byte",
                    expected=f"al massimo {UPLOAD_MAX_FILE_BYTES} byte per file",
                )
            entries = [(name, content)]
        total += sum(len(data) for _, data in entries)
        expanded.extend(entries)
        if len(expanded) > UPLOAD_MAX_FILES:
            raise _refuse(
                f"i file sono piu' di {UPLOAD_MAX_FILES}",
                expected=f"al massimo {UPLOAD_MAX_FILES} file per richiesta",
            )
    if not expanded:
        raise _refuse("nessun file da leggere", expected="almeno un file XML o un archivio ZIP")
    return [
        UploadedFile(name=name, content=data, sha256=hashlib.sha256(data).hexdigest())
        for name, data in expanded
    ]


def party_key(party: ParsedInvoiceParty) -> str:
    """What names a counterparty in a decision: its VAT number or tax code as the
    register would store it (`normalise_fiscal_id`, the same cleaning `match_customer`
    matches on, so `IT01234567890` and `01234567890` are one party). A party the register
    cannot match by tax id, which is a foreign one in practice, is named by its
    identifier with the country as the document states it (`estero:DE123456789`), so two
    foreign customers with one name stay two, and only a party with no identifier at all
    falls back to its name. The server defines the key and the review hands it to the
    page: the page never computes one."""
    italian = normalise_fiscal_id(party.partita_iva) or normalise_fiscal_id(party.codice_fiscale)
    if italian is not None:
        return italian
    foreign = normalise_foreign_fiscal_id(party.partita_iva) or normalise_foreign_fiscal_id(
        party.codice_fiscale
    )
    if foreign is not None:
        return f"estero:{foreign}"
    return f"nome:{party.ragione_sociale.casefold()}"


class UploadedInvoiceReviewRead(BaseModel):
    """One row of the review of an uploaded file: `ReviewedInvoiceRead`'s own fields,
    named by the file (and its SHA-256) instead of a `document_id`, since nothing was
    archived. One per invoice the file parses into, or exactly one `"unclaimed"` for a
    file no registered format reads: `motivo` then says why when the file looked like a
    FatturaPA and could not be read as one. `party_key` names the invoice's customer in
    the decisions of the confirmation."""

    model_config = ConfigDict(extra="forbid")

    file: str
    sha256: str
    outcome: InvoiceReviewOutcome
    invoice: ParsedInvoice | None = None
    party_key: str | None = None
    motivo: str | None = None
    azienda_id: UUID | None = None
    matched_customer_id: UUID | None = None
    mappature_giorni: list[WorkUnitDayMappingProposal | None] | None = None


class UploadedInvoiceReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    righe: list[UploadedInvoiceReviewRead]


class UploadedInvoiceConfirmRead(BaseModel):
    """One row of the confirmation of an uploaded file, `ConfirmedInvoiceRead`'s fields
    named by the file, with the `party_key` of the invoice's customer and the `motivo`
    of an unreadable file, as on the review row."""

    model_config = ConfigDict(extra="forbid")

    file: str
    sha256: str
    outcome: InvoiceConfirmOutcome
    party_key: str | None = None
    motivo: str | None = None
    azienda_id: UUID | None = None
    fattura: InvoiceRead | None = None
    buchi_non_dichiarati: list[int] | None = None


class UploadedInvoiceConfirmResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    righe: list[UploadedInvoiceConfirmRead]


class UploadedPartyDecision(BaseModel):
    """The human decisions `InvoiceConfirmRequest` carries for one document, for one
    counterparty: which customer to attach, whether to create the counterparty when none
    matches, and `skip`, to leave its invoices out whatever the register would match.
    Absent, an invoice is written only when its customer matches on its own, and otherwise
    comes back `needs_customer_confirmation`, written nowhere."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    create_customer: bool = False
    skip: bool = False


class UploadedInvoiceDecisions(BaseModel):
    """`clienti`: one decision per counterparty, keyed by the `party_key` the review
    named, so it holds for every invoice of that customer in every file of every batch
    of the import, a `lotto` with several customers included."""

    model_config = ConfigDict(extra="forbid")

    clienti: dict[str, UploadedPartyDecision] = Field(default_factory=dict)


def parse_decisions(raw: str) -> dict[str, UploadedPartyDecision]:
    """The decisions field of the multipart body, a JSON object. A malformed one is a
    422 with a sentence, not a stack: it reaches here as a string."""
    try:
        parsed = UploadedInvoiceDecisions.model_validate_json(raw)
    except ValidationError as exc:
        raise ValidationFailed(
            ENTITY,
            "decisioni",
            "le decisioni non sono un JSON valido",
            expected='{"clienti": {"<party_key>": {"create_customer": true}}}',
        ) from exc
    return parsed.clienti
