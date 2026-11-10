"""The CSV of one legal entity's year, for the accountant (REB-757).

Mastro's year export, ported: Italian spreadsheet format, a UTF-8 byte-order mark, the
semicolon as delimiter and the comma as decimal mark, because that combination is what
Excel under an Italian locale opens correctly. A comma-delimited, dot-decimal file parses
every money column as text, or reads `1.234,56` as a thousand times too much.

Two tables in one file, separated by one blank line, so a single download answers both
questions: **the lines** (one row per invoice line, with the rate, the `Natura` and the
legal reference it printed) and **the register** (one row per invoice, with the social security
surcharge
and the stamp duty apart and the declaration the document carries). Each ends in a total
row, and the two totals are the same figure: `Σ lines = Σ imponibile`, which is what
`YearOverview.totali.taxable_amount` says and what the page shows.

The invoice number and the partita IVA are written as text cells (`identifier_cell`): left
bare, an Italian Excel reads `2026/1` as a date and drops the leading zero of a partita IVA.

Pure functions over what `AnalyticsService` already read: no query here, so the file and
the page cannot read a different year.
"""

import re
import unicodedata
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.analytics.schemas import YearOverview
from pigrocrm.core.csv_cells import (
    BOM,
    amount_cell,
    factor_cell,
    identifier_cell,
    price_cell,
    row_line,
    text_cell,
)
from pigrocrm.core.invoices.models import InvoiceLine

LINE_COLUMNS = (
    "Numero",
    "Data emissione",
    "Cliente",
    "Partita IVA",
    "Tipo documento",
    "Riga",
    "Descrizione",
    "Quantità",
    "Unità di misura",
    "Prezzo unitario",
    "Importo",
    "Aliquota IVA",
    "Natura",
    "Riferimento normativo",
    "Pagamento",
    "Data incasso",
)
REGISTER_COLUMNS = (
    "Numero",
    "Data emissione",
    "Cliente",
    "Partita IVA",
    "Tipo documento",
    "Imponibile",
    "di cui rivalsa INPS",
    "Bollo",
    "Totale",
    "Dichiarazione bollo",
    "Pagamento",
    "Data incasso",
)
PAYMENT = {"paid": "Incassata", "unpaid": "Da incassare"}


def render_year_csv(overview: YearOverview, lines: dict[UUID, list[InvoiceLine]]) -> str:
    """`overview`'s register and the lines of each invoice, as the file's text."""
    out = [
        BOM,
        row_line(
            [
                text_cell(
                    f"Righe delle fatture emesse {overview.year} - {overview.legal_entity_name}"
                )
            ]
        ),
    ]
    out.append(row_line([text_cell(column) for column in LINE_COLUMNS]))
    total_lines = Decimal("0.00")
    for invoice in overview.register:
        number = identifier_cell(f"{invoice.year}/{invoice.number}")
        for line in lines.get(invoice.invoice_id, []):
            total_lines += line.total_price
            out.append(
                row_line(
                    [
                        number,
                        invoice.issue_date.isoformat(),
                        text_cell(invoice.legal_name),
                        identifier_cell(invoice.vat_number),
                        invoice.sdi_document_type,
                        str(line.line_number),
                        text_cell(line.description),
                        factor_cell(line.quantity),
                        text_cell(line.unit_of_measure),
                        price_cell(line.unit_price),
                        amount_cell(line.total_price),
                        amount_cell(line.vat_rate),
                        text_cell(line.vat_nature),
                        text_cell(line.legal_reference),
                        PAYMENT.get(invoice.payment_status, invoice.payment_status),
                        invoice.payment_date.isoformat() if invoice.payment_date else "",
                    ]
                )
            )
    out.append(row_line(["Totale righe"] + [""] * 9 + [amount_cell(total_lines)] + [""] * 5))
    out.append("\r\n")

    out.append(
        row_line(
            [
                text_cell(
                    f"Registro delle fatture emesse {overview.year} - {overview.legal_entity_name}"
                )
            ]
        )
    )
    out.append(row_line([text_cell(column) for column in REGISTER_COLUMNS]))
    for invoice in overview.register:
        out.append(
            row_line(
                [
                    identifier_cell(f"{invoice.year}/{invoice.number}"),
                    invoice.issue_date.isoformat(),
                    text_cell(invoice.legal_name),
                    identifier_cell(invoice.vat_number),
                    invoice.sdi_document_type,
                    amount_cell(invoice.taxable_amount),
                    amount_cell(invoice.social_security_surcharge),
                    amount_cell(invoice.stamp_duty),
                    amount_cell(invoice.total),
                    text_cell(invoice.stamp_duty_declaration),
                    PAYMENT.get(invoice.payment_status, invoice.payment_status),
                    invoice.payment_date.isoformat() if invoice.payment_date else "",
                ]
            )
        )
    totals = overview.total
    out.append(
        row_line(
            ["Totale registro"]
            + [""] * 4
            + [
                amount_cell(totals.taxable_amount),
                amount_cell(totals.social_security_surcharge),
                amount_cell(totals.stamp_duty),
                amount_cell(totals.total),
            ]
            + [""] * 3
        )
    )
    return "".join(out)


def year_csv_filename(overview: YearOverview) -> str:
    """`registro-2026-studio-rossi.csv`: the year and the legal entity's short name, reduced
    to what any file system and any `Content-Disposition` header accept."""
    ascii_name = (
        unicodedata.normalize("NFKD", overview.legal_entity_name).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "azienda"
    return f"registro-{overview.year}-{slug}.csv"


__all__ = ["render_year_csv", "year_csv_filename"]
