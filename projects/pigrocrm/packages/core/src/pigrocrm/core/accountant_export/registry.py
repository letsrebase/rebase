"""The parts of the bundle, in the order the index lists them.

A project that ships a source the export design names (spec accountant export § 1) adds its
`PartDefinition` to `PARTS` and removes its row from `UNAVAILABLE`; the service, the ZIP and
the index need no other change. `test_accountant_export.py` holds the two lists apart, so a
part is never both built and declared missing.
"""

from pigrocrm.core.accountant_export.due_items import build_open_items, build_settlements
from pigrocrm.core.accountant_export.invoice_files import build_invoice_files
from pigrocrm.core.accountant_export.parts import PartDefinition, UnavailablePart
from pigrocrm.core.accountant_export.purchases import (
    build_receipts_and_expenses,
    build_received_invoice_files,
)
from pigrocrm.core.accountant_export.sales_register import build_sales_register
from pigrocrm.core.fiscal.pack import PACK_NON_IT

PARTS: tuple[PartDefinition, ...] = (
    PartDefinition(
        key="sales_register",
        folder="vendite",
        label="Registro delle vendite",
        date_rule="per data di emissione",
        contents="Elenco CSV",
        build=build_sales_register,
        # No IVA register for a company outside Italy (§ 1, « Which parts exist »).
        not_for_packs=(PACK_NON_IT,),
    ),
    PartDefinition(
        key="issued_invoice_files",
        folder="fatture-emesse",
        label="Fatture emesse: file originali",
        date_rule="per data del documento",
        contents="File originali",
        build=build_invoice_files,
    ),
    PartDefinition(
        key="received_invoice_files",
        folder="fatture-ricevute",
        label="Fatture ricevute: file originali",
        date_rule="per data del documento",
        contents="File originali",
        build=build_received_invoice_files,
        scopes=("entity", "shared"),
        requires_modules=("purchases",),
    ),
    PartDefinition(
        key="receipts_and_expenses",
        folder="ricevute-e-spese",
        label="Ricevute e spese: elenco e file originali",
        date_rule="per data del documento",
        contents="Elenco CSV e file originali",
        build=build_receipts_and_expenses,
        scopes=("entity", "shared"),
        requires_modules=("purchases",),
    ),
    PartDefinition(
        key="open_due_items",
        folder="scadenze",
        label="Scadenze aperte a fine periodo",
        date_rule="aperte all'ultimo giorno del periodo",
        contents="Elenco CSV",
        build=build_open_items,
    ),
    PartDefinition(
        key="settlements",
        folder="pagamenti",
        label="Pagamenti registrati",
        date_rule="per data di pagamento",
        contents="Elenco CSV",
        build=build_settlements,
    ),
)

# What the design lists and no source feeds yet. Each is listed in the index with its reason
# rather than left out of it, so the accountant never wonders whether a part was forgotten.
_NOT_BUILT = "la fonte non è ancora disponibile in PigroCRM"
UNAVAILABLE: tuple[UnavailablePart, ...] = (
    UnavailablePart(
        "purchase_register", "Registro degli acquisti", _NOT_BUILT, scopes=("entity", "shared")
    ),
    UnavailablePart("iva_settlements", "Liquidazioni IVA del periodo", _NOT_BUILT),
    UnavailablePart("integration_documents", "Documenti di integrazione", _NOT_BUILT),
    UnavailablePart("bank_movements", "Movimenti di banca con ciò che hanno saldato", _NOT_BUILT),
    UnavailablePart("taxes_and_f24", "Imposte pagate e F24", _NOT_BUILT),
    UnavailablePart("withholdings", "Ritenute versate per conto di terzi", _NOT_BUILT),
    UnavailablePart("payroll", "Paghe (a richiesta)", _NOT_BUILT),
)
