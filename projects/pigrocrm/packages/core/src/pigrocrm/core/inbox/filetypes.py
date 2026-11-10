"""What the inbox accepts, and how it tells (design § 2.1).

The inbox has **its own allowlist and signature checks**: the documents allowlist
(`documents/schemas.py`) refuses p7m, ZIP, CSV, XLS and CAMT, and `documents/uploads.py` sniffs
magic bytes for only five types. A file is accepted when its name's extension is in
`ACCEPTED` **and** its bytes carry the signature of that type where the format has one, so a
renamed executable is never an item. The type is never taken from the client's content type.

Ten types: `xml` (a FatturaPA, or any XML), `camt053` (a bank statement in ISO 20022, told
from `xml` by its namespace and stored and tagged by type only; reading it is a later card),
`p7m`, `zip`, `pdf`, `png`, `jpeg`, `csv`, `xls`, `xlsx`. `xlsx` and `zip` are both ZIP
containers, which is why the extension decides between them and the container is then checked
to be the one it says.
"""

import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Literal, cast, get_args

from pigrocrm.core.inbox.p7m import envelope_bytes

FileType = Literal["xml", "camt053", "p7m", "zip", "pdf", "png", "jpeg", "csv", "xls", "xlsx"]
FILE_TYPES: tuple[str, ...] = get_args(FileType)


@dataclass(frozen=True, slots=True)
class TypeInfo:
    extension: str
    """What the stored key ends with: drawn from here, never from the uploaded name."""
    content_type: str
    """What a download answers with: chosen here, never echoed from the request."""


ACCEPTED: dict[str, TypeInfo] = {
    "xml": TypeInfo(".xml", "application/xml"),
    "camt053": TypeInfo(".xml", "application/xml"),
    "p7m": TypeInfo(".p7m", "application/pkcs7-mime"),
    "zip": TypeInfo(".zip", "application/zip"),
    "pdf": TypeInfo(".pdf", "application/pdf"),
    "png": TypeInfo(".png", "image/png"),
    "jpeg": TypeInfo(".jpg", "image/jpeg"),
    "csv": TypeInfo(".csv", "text/csv"),
    "xls": TypeInfo(".xls", "application/vnd.ms-excel"),
    "xlsx": TypeInfo(".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}

_BY_EXTENSION: dict[str, str] = {
    ".xml": "xml",
    ".camt": "xml",
    ".p7m": "p7m",
    ".zip": "zip",
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".csv": "csv",
    ".xls": "xls",
    ".xlsx": "xlsx",
}

ACCEPTED_DESCRIPTION = "XML, p7m, ZIP, PDF, PNG, JPEG, CSV, XLS, XLSX o CAMT.053"

ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PDF_MAGIC = b"%PDF-"
_PDF_HEADER_WINDOW = 1024
"""A PDF may carry junk before its header, which the format tolerates within the first KiB."""
_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_CAMT_NAMESPACE = b"urn:iso:std:iso:20022:tech:xsd:camt.053"
_SNIFF_WINDOW = 4096
_CSV_WINDOW = 64 * 1024


class UnsupportedFile(ValueError):
    """The file is not one the inbox takes. The message is Italian and names why."""


def basename(name: str) -> str:
    """The last path segment of a name, whichever separator it was written with."""
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def is_accepted_name(name: str) -> bool:
    """Whether the extension of `name` is one of the inbox's: what a mail reader asks before it
    fetches an attachment, so that no part of another kind is ever downloaded."""
    return extension_of(name) in _BY_EXTENSION


def extension_of(name: str) -> str:
    segment = basename(name).lower()
    dot = segment.rfind(".")
    return segment[dot:] if dot > 0 else ""


def _is_xml(content: bytes) -> bool:
    return content[:_SNIFF_WINDOW].removeprefix(_UTF8_BOM).lstrip()[:1] == b"<"


def _is_xlsx(content: bytes) -> bool:
    if not content.startswith(ZIP_MAGIC):
        return False
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)


def _is_text(content: bytes) -> bool:
    head = content[:_CSV_WINDOW]
    if head.startswith(_UTF16_BOMS):
        return True  # UTF-16, which has NUL bytes by construction
    return b"\x00" not in head


def detect_file_type(name: str, content: bytes) -> FileType:
    """The type of an upload, or `UnsupportedFile` saying why it is not one.

    Order of the two checks matters to the sentence: an extension nobody accepts is refused
    first, a signature that disagrees with the extension second."""
    extension = extension_of(name)
    declared = _BY_EXTENSION.get(extension)
    label = basename(name)
    if declared is None:
        raise UnsupportedFile(
            f"{label}: tipo di file non accettato (accettati: {ACCEPTED_DESCRIPTION})"
        )
    if not content:
        raise UnsupportedFile(f"{label}: il file e' vuoto")
    mismatch = UnsupportedFile(f"{label}: il contenuto non e' quello di un file {extension}")
    match declared:
        case "xml":
            if not _is_xml(content):
                raise mismatch
            return "camt053" if _CAMT_NAMESPACE in content[:_SNIFF_WINDOW] else "xml"
        case "p7m":
            if envelope_bytes(content) is None:
                raise mismatch
        case "zip":
            if not content.startswith(ZIP_MAGIC):
                raise mismatch
        case "xlsx":
            if not _is_xlsx(content):
                raise mismatch
        case "xls":
            if not content.startswith(_OLE2_MAGIC):
                raise mismatch
        case "pdf":
            if _PDF_MAGIC not in content[:_PDF_HEADER_WINDOW]:
                raise mismatch
        case "png":
            if not content.startswith(_PNG_MAGIC):
                raise mismatch
        case "jpeg":
            if not content.startswith(_JPEG_MAGIC):
                raise mismatch
        case "csv":
            if not _is_text(content):
                raise mismatch
    return cast(FileType, declared)
