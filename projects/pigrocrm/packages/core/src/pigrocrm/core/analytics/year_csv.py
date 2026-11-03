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

The invoice number and the partita IVA are written as text cells (`_identifier`): left
bare, an Italian Excel reads `2026/1` as a date and drops the leading zero of a partita IVA.

Pure functions over what `AnalyticsService` already read: no query here, so the file and
the page cannot read a different year.
"""

import re
import unicodedata
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.analytics.schemas import YearOverview
from pigrocrm.core.invoices.models import InvoiceLine
from pigrocrm.core.spreadsheet import FORMULA_TRIGGERS

BOM = "\ufeff"
DELIMITER = ";"
NEEDS_QUOTING = re.compile(r'["\r\n;]')

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


def _text(value: str | None) -> str:
    """A text cell: defused against formula injection, quoted when it holds the delimiter,
    a quote or a line break (RFC 4180, an embedded quote doubled)."""
    if not value:
        return ""
    if value.startswith(FORMULA_TRIGGERS):
        value = "'" + value
    return f'"{value.replace(chr(34), chr(34) * 2)}"' if NEEDS_QUOTING.search(value) else value


def _identifier(value: str | None) -> str:
    """A code a spreadsheet must keep as the text it is: `="01234567890"`, which Excel and
    LibreOffice read as the string and not as a number. An Italian partita IVA often starts
    with a zero that a numeric cell drops, and `2026/1` is a date (January 2026) to an
    Italian-locale Excel. Only for what is made of letters, digits and `/.-`; anything
    else is free text and goes through `_text`, whose formula guard it needs."""
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9/.\-]+", value):
        return f'"=""{value}"""'
    return _text(value)


def _amount(value: Decimal) -> str:
    """`1.234,56`: thousands with a point, two decimals with a comma. Decimal all the way,
    never a float, so a cent is never lost on the way to the file."""
    grouped = f"{value.quantize(Decimal('0.01')):,.2f}"
    return grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _factor(value: Decimal) -> str:
    """A quantity or a unit price: as many decimals as it carries up to six, at least
    two for a price, comma as the mark and no thousands separator (it is not an amount)."""
    text = f"{value:f}"
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    return f"{whole},{fraction}" if fraction else whole


def _price(value: Decimal) -> str:
    text = _factor(value)
    if "," not in text:
        return f"{text},00"
    whole, fraction = text.split(",")
    return f"{whole},{fraction.ljust(2, '0')}"


def _row(cells: list[str]) -> str:
    return DELIMITER.join(cells) + "\r\n"


def render_year_csv(overview: YearOverview, lines: dict[UUID, list[InvoiceLine]]) -> str:
    """`overview`'s register and the lines of each invoice, as the file's text."""
    out = [
        BOM,
        _row([_text(f"Righe delle fatture emesse {overview.year} - {overview.legal_entity_name}")]),
    ]
    out.append(_row([_text(column) for column in LINE_COLUMNS]))
    total_lines = Decimal("0.00")
    for invoice in overview.register:
        number = _identifier(f"{invoice.year}/{invoice.number}")
        for line in lines.get(invoice.invoice_id, []):
            total_lines += line.total_price
            out.append(
                _row(
                    [
                        number,
                        invoice.issue_date.isoformat(),
                        _text(invoice.legal_name),
                        _identifier(invoice.vat_number),
                        invoice.sdi_document_type,
                        str(line.line_number),
                        _text(line.description),
                        _factor(line.quantity),
                        _text(line.unit_of_measure),
                        _price(line.unit_price),
                        _amount(line.total_price),
                        _amount(line.vat_rate),
                        _text(line.vat_nature),
                        _text(line.legal_reference),
                        PAYMENT.get(invoice.payment_status, invoice.payment_status),
                        invoice.payment_date.isoformat() if invoice.payment_date else "",
                    ]
                )
            )
    out.append(_row(["Totale righe"] + [""] * 9 + [_amount(total_lines)] + [""] * 5))
    out.append("\r\n")

    out.append(
        _row(
            [_text(f"Registro delle fatture emesse {overview.year} - {overview.legal_entity_name}")]
        )
    )
    out.append(_row([_text(column) for column in REGISTER_COLUMNS]))
    for invoice in overview.register:
        out.append(
            _row(
                [
                    _identifier(f"{invoice.year}/{invoice.number}"),
                    invoice.issue_date.isoformat(),
                    _text(invoice.legal_name),
                    _identifier(invoice.vat_number),
                    invoice.sdi_document_type,
                    _amount(invoice.taxable_amount),
                    _amount(invoice.social_security_surcharge),
                    _amount(invoice.stamp_duty),
                    _amount(invoice.total),
                    _text(invoice.stamp_duty_declaration),
                    PAYMENT.get(invoice.payment_status, invoice.payment_status),
                    invoice.payment_date.isoformat() if invoice.payment_date else "",
                ]
            )
        )
    totals = overview.total
    out.append(
        _row(
            ["Totale registro"]
            + [""] * 4
            + [
                _amount(totals.taxable_amount),
                _amount(totals.social_security_surcharge),
                _amount(totals.stamp_duty),
                _amount(totals.total),
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
