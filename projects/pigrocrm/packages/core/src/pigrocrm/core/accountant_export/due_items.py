"""The due items of the period (spec accountant export § 1): what was still open at its end,
and the payments booked in it.

Open items are read as of the last day of the period, not as of today: an item paid the day
after is open here, one that did not exist yet is not. The settlements are the cash facts,
placed by the day they were booked; a reversed one is not a payment and is left out. Both
speak of the amounts as the schedule does (`DueItem`): a positive amount is owed in the
direction the item says, a negative one is a credit.
"""

from pigrocrm.core.accountant_export.parts import (
    BundleFile,
    IndexEntry,
    PartContext,
    PartOutput,
)
from pigrocrm.core.accountant_export.repository import ExportRepository
from pigrocrm.core.csv_cells import (
    BOM,
    amount_cell,
    row_line,
    text_cell,
)
from pigrocrm.core.due_items.models import DueItem
from pigrocrm.core.money import ZERO_MONEY

OPEN_FILE = "scadenze-aperte.csv"
SETTLEMENTS_FILE = "pagamenti.csv"

DIRECTION = {"in": "Da incassare", "out": "Da pagare"}
# The words the accountant reads for where an item comes from; a kind a later project adds
# is written as it is until it has its words here.
SOURCE = {
    "sales_invoice": "Fattura emessa",
    "purchase": "Acquisto",
    "expense_note": "Nota spese",
    "integration_vat": "IVA da integrazione",
    "iva_settlement": "Liquidazione IVA",
    "withholding": "Ritenuta",
    "tax_instalment": "Imposta",
    "tax_balance": "Saldo d'imposta",
    "tax_advance": "Acconto d'imposta",
    "chamber_fee": "Diritto camerale",
    "payroll": "Paghe",
    "contract_occurrence": "Contratto",
    "recurring_expense": "Spesa ricorrente",
    "cash_basis_vat": "IVA per cassa",
    "engagement": "Incarico",
}

OPEN_COLUMNS = (
    "Direzione",
    "Controparte",
    "Origine",
    "Descrizione",
    "Scadenza",
    "Scadenza stimata",
    "Importo",
    "Già saldato",
    "Residuo",
    "Valuta",
)
SETTLEMENT_COLUMNS = (
    "Data",
    "Data valuta",
    "Direzione",
    "Controparte",
    "Origine",
    "Descrizione",
    "Importo",
    "Valuta",
    "Importo sul conto",
    "Valuta del conto",
    "Nota",
)


_PAYROLL_TAX = {
    "payroll_withholding": "Ritenute IRPEF",
    "payroll_inps": "INPS",
    "payroll_inail": "INAIL",
}


def _counterparty(item: DueItem, organization_name: str | None) -> str:
    if item.counterparty_type == "staff_member":
        # Never the person's name: pay is an administrator's, and this part has no consent for it.
        return text_cell("Dipendente")
    if organization_name is None and item.tax is not None:
        return text_cell(_PAYROLL_TAX.get(item.tax, item.tax))
    return text_cell(organization_name)


def _origin(item: DueItem) -> str:
    return text_cell(SOURCE.get(item.source_kind, item.source_kind))


def build_open_items(ctx: PartContext) -> PartOutput:
    legal_entity = ctx.legal_entity
    if legal_entity is None:  # pragma: no cover - the registry marks the part as an entity's
        raise RuntimeError("due items belong to one legal entity")
    out = [
        BOM,
        row_line(
            [
                text_cell(
                    f"Scadenze aperte al {ctx.period.date_to.isoformat()} - {legal_entity.name}"
                )
            ]
        ),
        row_line([text_cell(column) for column in OPEN_COLUMNS]),
    ]
    rows = 0
    for item, name, settled in ExportRepository(ctx.session).open_due_items(
        legal_entity.id, ctx.period.date_to
    ):
        remaining = item.amount - settled
        if remaining == ZERO_MONEY:
            continue
        out.append(
            row_line(
                [
                    DIRECTION[item.direction],
                    _counterparty(item, name),
                    _origin(item),
                    text_cell(item.description),
                    item.due_date.isoformat() if item.due_date else "",
                    "Sì" if item.due_date_estimated else "",
                    amount_cell(item.amount),
                    amount_cell(settled),
                    amount_cell(remaining),
                    item.currency,
                ]
            )
        )
        rows += 1
    output = PartOutput()
    output.files.append(BundleFile(OPEN_FILE, "".join(out).encode("utf-8")))
    output.entries.append(IndexEntry(OPEN_FILE, rows, "rows"))
    return output


def build_settlements(ctx: PartContext) -> PartOutput:
    legal_entity = ctx.legal_entity
    if legal_entity is None:  # pragma: no cover - the registry marks the part as an entity's
        raise RuntimeError("settlements belong to one legal entity")
    out = [
        BOM,
        row_line(
            [
                text_cell(
                    f"Pagamenti {ctx.period.date_from.isoformat()} - "
                    f"{ctx.period.date_to.isoformat()} - {legal_entity.name}"
                )
            ]
        ),
        row_line([text_cell(column) for column in SETTLEMENT_COLUMNS]),
    ]
    rows = 0
    for settlement, item, name in ExportRepository(ctx.session).settlements(
        legal_entity.id, ctx.period.date_from, ctx.period.date_to
    ):
        out.append(
            row_line(
                [
                    settlement.settled_on.isoformat(),
                    settlement.value_date.isoformat() if settlement.value_date else "",
                    DIRECTION[item.direction],
                    _counterparty(item, name),
                    _origin(item),
                    text_cell(item.description),
                    amount_cell(settlement.amount),
                    item.currency,
                    amount_cell(settlement.account_amount)
                    if settlement.account_amount is not None
                    else "",
                    settlement.account_currency or "",
                    text_cell(settlement.note),
                ]
            )
        )
        rows += 1
    output = PartOutput()
    output.files.append(BundleFile(SETTLEMENTS_FILE, "".join(out).encode("utf-8")))
    output.entries.append(IndexEntry(SETTLEMENTS_FILE, rows, "rows"))
    return output
