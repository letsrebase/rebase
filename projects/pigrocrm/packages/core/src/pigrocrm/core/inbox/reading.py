"""What is read of a file when it arrives: only what deduplication and the automatic discards
need (REB-971). Everything beyond that, the legal entity, the kind, the proposal, is the
classifier's (design § 3), a later card.

Three things, all certain and none of them a guess:

* the **content hash**: the SHA-256 of the document the file carries, which for a `.p7m` is the
  XML inside the envelope and for every other type the file itself, so a signed copy and the
  bare XML it signs are the same content;
* the **natural key** (design § 3.3) of a FatturaPA: issuer tax id, number, date and document
  type of each invoice it carries, so the same invoice arriving as XML, as `.p7m` and inside a
  ZIP is one item whatever the bytes around it. A sales invoice's key is the importer's own
  (legal entity, year, number), which needs the legal entity: that comes with classification
  and, for the same supplier, number and date, the two keys name the same invoice;
* the **automatic discards**: the SdI's metadata files and receipts, which are no document.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import quote

from lxml import etree

from pigrocrm.core.inbox.filetypes import FileType, basename
from pigrocrm.core.inbox.p7m import P7mError, open_p7m
from pigrocrm.core.invoices import fatturapa_import
from pigrocrm.core.invoices.import_upload import party_key

SDI_METADATA_MARKER = "_MT_"
SDI_ROOTS = frozenset(
    {
        "FileMetadati",
        "RicevutaConsegna",
        "NotificaScarto",
        "NotificaMancataConsegna",
        "NotificaEsito",
        "NotificaDecorrenzaTermini",
        "AttestazioneTrasmissioneFattura",
    }
)
"""The roots of the SdI's own messages about an invoice: the metadata file, the delivery
receipt, the rejection, the failed delivery, the outcome, the lapse of terms and the
attestation. None is a document anyone files."""

METADATA_REASON = "File di metadati dello SdI: non e' un documento"
RECEIPT_REASON = "Ricevuta o notifica dello SdI: non e' un documento"


@dataclass(frozen=True, slots=True)
class Reading:
    content_sha256: str
    natural_key: str | None = None
    kind: str | None = None
    """`other` for a `.p7m` whose envelope cannot be opened, nothing otherwise: the kind is
    the classifier's to decide."""
    note: str | None = None
    """Why the kind is `other`."""
    discard_reason: str | None = None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _xml_root(content: bytes) -> str | None:
    """The local name of the root element, read without building the tree and with no DTD,
    entity or network (the parser `fatturapa_import` uses on the same untrusted bytes)."""
    try:
        for _, element in etree.iterparse(
            BytesIO(content),
            events=("start",),
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        ):
            return etree.QName(element).localname
    except (etree.XMLSyntaxError, ValueError):
        return None
    return None


def fatturapa_key(content: bytes) -> str | None:
    """The natural key of a FatturaPA: the SHA-256 of one line per invoice it carries (a
    `lotto` carries several), sorted so the order of the bodies does not matter. A digest and
    not the text, because a lotto of a year of invoices is a long line and the key is
    indexed; `None` when `content` is not an FPR12 document or does not parse as one."""
    if not fatturapa_import.detect(content):
        return None
    try:
        invoices = fatturapa_import.parse(content)
    except ValueError:  # InvoicePaFormatError and pydantic's ValidationError are both
        return None
    return sha256_of(
        "\n".join(
            sorted(
                "|".join(
                    (
                        quote(party_key(invoice.supplier), safe=""),
                        quote(invoice.number.strip().casefold(), safe=""),
                        invoice.issue_date.isoformat(),
                        invoice.sdi_document_type,
                    )
                )
                for invoice in invoices
            )
        ).encode()
    )


def read_file(name: str, file_type: FileType, content: bytes) -> Reading:
    """What arrives with `content` (the file's bytes, a ZIP entry's included)."""
    document = content
    if file_type == "p7m":
        try:
            document = open_p7m(content)
        except P7mError as exc:
            return Reading(content_sha256=sha256_of(content), kind="other", note=str(exc))
    digest = sha256_of(document)
    if file_type in ("xml", "p7m") and SDI_METADATA_MARKER in basename(name).upper():
        return Reading(content_sha256=digest, discard_reason=METADATA_REASON)
    if file_type in ("xml", "p7m"):
        root = _xml_root(document)
        if root in SDI_ROOTS:
            return Reading(content_sha256=digest, discard_reason=RECEIPT_REASON)
        if root is not None:
            return Reading(content_sha256=digest, natural_key=fatturapa_key(document))
    return Reading(content_sha256=digest)
