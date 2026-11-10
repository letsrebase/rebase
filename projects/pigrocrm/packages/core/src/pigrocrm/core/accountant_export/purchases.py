"""The documents of what the space bought (spec accountant export § 1): the supplier invoices
and credit notes received, and the receipts and expenses, with their original files.

Files and rows are placed by the document's date. A legal entity's export carries its own
purchases; the shared ones (no legal entity) go in the section of their own an «all
entities» export has, through the same builders. In a space with a single legal entity there
is no other to share with (`PurchaseService` reads «shared» as that entity there), so its
export carries them. A purchase with no document attached is a
missing file in the index, by its number or its date and description, not a silent gap.
"""

import re
import unicodedata

from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    MissingFile,
    PartContext,
    PartOutput,
)
from pigrocrm.core.accountant_export.repository import ExportRepository
from pigrocrm.core.csv_cells import BOM, amount_cell, factor_cell, row_line, text_cell
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.purchases.models import Purchase

RECEIVED_KINDS = ("invoice", "credit_note")
LIST_KINDS = ("receipt", "expense")
LIST_FILE = "elenco.csv"

KIND = {
    "invoice": "Fattura",
    "credit_note": "Nota di credito",
    "receipt": "Ricevuta",
    "expense": "Spesa",
}
LIST_COLUMNS = (
    "Data",
    "Tipo",
    "Fornitore o esercente",
    "Numero",
    "Descrizione",
    "Categoria",
    "Importo",
    "Valuta",
    "Importo in valuta",
    "Cambio",
    "File",
)


def _safe(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip("-.") or "documento"


def _short_id(purchase: Purchase) -> str:
    """The random end of the id: the start of a UUID v7 is a timestamp, equal for two rows
    written in the same millisecond."""
    return purchase.id.hex[-8:]


def _label(purchase: Purchase) -> str:
    """How the index names a purchase: its number, else its date and the start of its id."""
    return purchase.number or f"{purchase.date.isoformat()} {_short_id(purchase)}"


def _file(
    ctx: PartContext, purchase: Purchase, output: PartOutput, documents: DocumentService
) -> str | None:
    """Attaches the purchase's original file and answers its name in the bundle, or records
    why it could not and answers `None`."""
    if purchase.document_id is None:
        output.missing.append(
            MissingFile(_label(purchase), KIND[purchase.kind], "nessun documento allegato")
        )
        return None
    try:
        data, _, filename = documents.download(purchase.document_id, None, ctx.actor)
    except NotFound:
        output.missing.append(
            MissingFile(_label(purchase), KIND[purchase.kind], "il file non è archiviato")
        )
        return None
    except (Conflict, ValidationFailed) as refusal:
        output.missing.append(MissingFile(_label(purchase), KIND[purchase.kind], refusal.message))
        return None
    # The start of the id keeps two documents with the same number and title apart.
    short_id = _short_id(purchase)
    reference = f"{_safe(purchase.number)}-{short_id}" if purchase.number else short_id
    name = f"{purchase.date.isoformat()}-{reference}-{_safe(filename)}"
    output.files.append(BundleFile(name, data))
    return name


def build_received_invoice_files(ctx: PartContext) -> PartOutput:
    output = PartOutput()
    documents = DocumentService(ctx.session, ctx.storage, ctx.settings)
    legal_entity_id = ctx.legal_entity.id if ctx.legal_entity is not None else None
    for purchase, _, _ in ExportRepository(ctx.session).purchases(
        legal_entity_id,
        RECEIVED_KINDS,
        ctx.period.date_from,
        ctx.period.date_to,
        with_shared=ctx.owns_shared_purchases,
    ):
        _file(ctx, purchase, output, documents)
    output.entries.append(IndexEntry("", len(output.files), "files"))
    return output


def build_receipts_and_expenses(ctx: PartContext) -> PartOutput:
    output = PartOutput()
    documents = DocumentService(ctx.session, ctx.storage, ctx.settings)
    legal_entity_id = ctx.legal_entity.id if ctx.legal_entity is not None else None
    out = [BOM, row_line([text_cell(column) for column in LIST_COLUMNS])]
    rows = 0
    for purchase, supplier, category in ExportRepository(ctx.session).purchases(
        legal_entity_id,
        LIST_KINDS,
        ctx.period.date_from,
        ctx.period.date_to,
        with_shared=ctx.owns_shared_purchases,
    ):
        name = _file(ctx, purchase, output, documents)
        out.append(
            row_line(
                [
                    purchase.date.isoformat(),
                    KIND[purchase.kind],
                    text_cell(supplier or purchase.merchant_name),
                    text_cell(purchase.number),
                    text_cell(purchase.description),
                    text_cell(category),
                    amount_cell(purchase.amount),
                    purchase.currency,
                    amount_cell(purchase.currency_amount)
                    if purchase.currency_amount is not None
                    else "",
                    factor_cell(purchase.exchange_rate)
                    if purchase.exchange_rate is not None
                    else "",
                    text_cell(name),
                ]
            )
        )
        rows += 1
    output.files.append(BundleFile(LIST_FILE, "".join(out).encode("utf-8")))
    output.entries.append(IndexEntry(LIST_FILE, rows, "rows"))
    output.entries.append(IndexEntry("", len(output.files) - 1, "files"))
    return output
