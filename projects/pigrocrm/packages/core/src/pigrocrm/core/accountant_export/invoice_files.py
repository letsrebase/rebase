"""The original files of the invoices issued in the period (spec accountant export § 1).

For every numbered invoice dated in the period, annulled ones included, the stored FatturaPA
file and the PDF, served the way a download serves them: the XML only if it still hashes to
what the register recorded. What cannot be attached is not hidden: it goes in the index by
invoice number, with the reason. A legal entity abroad issues the PDF alone (no XML is
expected from it), and an invoice whose file was never stored or no longer matches its hash
is a missing file, not a failed export.

Files are placed by the document date, the date printed on the invoice.
"""

from pydantic import ValidationError as PydanticValidationError

from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    MissingFile,
    PartContext,
    PartOutput,
)
from pigrocrm.core.accountant_export.repository import ExportRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.invoices.artifacts import InvoiceArtifacts
from pigrocrm.core.invoices.schemas import ArtifactKind
from pigrocrm.core.invoices.service import InvoiceService

FROZEN_UNREADABLE = "i dati congelati della fattura non si leggono"


def build_invoice_files(ctx: PartContext) -> PartOutput:
    legal_entity = ctx.legal_entity
    if legal_entity is None:  # pragma: no cover - the registry marks the part as an entity's
        raise RuntimeError("issued invoices belong to one legal entity")
    invoices = InvoiceService(ctx.session, ctx.storage, ctx.settings)
    output = PartOutput()
    for invoice in ExportRepository(ctx.session).numbered_invoices(
        legal_entity.id, ctx.period.date_from, ctx.period.date_to
    ):
        label = f"{invoice.year}/{invoice.number}"
        kinds: tuple[ArtifactKind, ...] = (
            ("pdf",) if InvoiceArtifacts.frozen_abroad(invoice) else ("xml", "pdf")
        )
        for kind in kinds:
            try:
                data, _, filename = invoices.download(invoice.id, kind, ctx.actor)
            except NotFound:
                output.missing.append(MissingFile(label, kind.upper(), "il file non è archiviato"))
            except (Conflict, ValidationFailed) as refusal:
                output.missing.append(MissingFile(label, kind.upper(), refusal.message))
            except PydanticValidationError:
                output.missing.append(
                    MissingFile(
                        label, kind.upper(), "i dati congelati della fattura non si leggono"
                    )
                )
            else:
                output.files.append(BundleFile(filename, data))
    output.entries.append(IndexEntry("", len(output.files), "files"))
    return output
