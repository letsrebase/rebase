"""The CSV of one azienda's year, for the accountant (REB-757).

Mastro's year export, ported: Italian spreadsheet format, a UTF-8 byte-order mark, the
semicolon as delimiter and the comma as decimal mark, because that combination is what
Excel under an Italian locale opens correctly. A comma-delimited, dot-decimal file parses
every money column as text, or reads `1.234,56` as a thousand times too much.

Two tables in one file, separated by one blank line, so a single download answers both
questions: **the lines** (one row per invoice line, with the rate, the `Natura` and the
legal reference it printed) and **the register** (one row per invoice, with the rivalsa
and the stamp duty apart and the declaration the document carries). Each ends in a total
row, and the two totals are the same figure: `Σ lines = Σ imponibile`, which is what
`YearOverview.totali.imponibile` says and what the page shows.

Pure functions over what `AnalyticsService` already read: no query here, so the file and
the page cannot read a different year.
"""

import re
import unicodedata
from decimal import Decimal
from uuid import UUID

from pigrocrm.core.analytics.schemas import YearOverview
from pigrocrm.core.invoices.models import InvoiceLine

BOM = "\ufeff"
DELIMITER = ";"
NEEDS_QUOTING = re.compile(r'["\r\n;]')
# A cell a spreadsheet reads as a formula: the customer's name and the line description
# are typed by people, and the accountant opens this file in Excel.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")

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
PAGAMENTO = {"incassato": "Incassata", "da_incassare": "Da incassare"}


def _text(value: str | None) -> str:
    """A text cell: defused against formula injection, quoted when it holds the delimiter,
    a quote or a line break (RFC 4180, an embedded quote doubled)."""
    if not value:
        return ""
    if value.startswith(FORMULA_TRIGGERS):
        value = "'" + value
    return f'"{value.replace(chr(34), chr(34) * 2)}"' if NEEDS_QUOTING.search(value) else value


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
        _row([_text(f"Righe delle fatture emesse {overview.anno} - {overview.azienda_nome}")]),
    ]
    out.append(_row([_text(column) for column in LINE_COLUMNS]))
    total_lines = Decimal("0.00")
    for invoice in overview.registro:
        numero = f"{invoice.anno}/{invoice.numero}"
        for line in lines.get(invoice.invoice_id, []):
            total_lines += line.prezzo_totale
            out.append(
                _row(
                    [
                        numero,
                        invoice.data_emissione.isoformat(),
                        _text(invoice.ragione_sociale),
                        _text(invoice.partita_iva),
                        invoice.tipo_documento,
                        str(line.numero_linea),
                        _text(line.descrizione),
                        _factor(line.quantita),
                        _text(line.unita_misura),
                        _price(line.prezzo_unitario),
                        _amount(line.prezzo_totale),
                        _amount(line.aliquota_iva),
                        _text(line.natura),
                        _text(line.riferimento_normativo),
                        PAGAMENTO.get(invoice.stato_pagamento, invoice.stato_pagamento),
                        invoice.data_incasso.isoformat() if invoice.data_incasso else "",
                    ]
                )
            )
    out.append(_row(["Totale righe"] + [""] * 9 + [_amount(total_lines)] + [""] * 5))
    out.append("\r\n")

    out.append(
        _row([_text(f"Registro delle fatture emesse {overview.anno} - {overview.azienda_nome}")])
    )
    out.append(_row([_text(column) for column in REGISTER_COLUMNS]))
    for invoice in overview.registro:
        out.append(
            _row(
                [
                    f"{invoice.anno}/{invoice.numero}",
                    invoice.data_emissione.isoformat(),
                    _text(invoice.ragione_sociale),
                    _text(invoice.partita_iva),
                    invoice.tipo_documento,
                    _amount(invoice.imponibile),
                    _amount(invoice.rivalsa),
                    _amount(invoice.bollo),
                    _amount(invoice.totale),
                    _text(invoice.dichiarazione_bollo),
                    PAGAMENTO.get(invoice.stato_pagamento, invoice.stato_pagamento),
                    invoice.data_incasso.isoformat() if invoice.data_incasso else "",
                ]
            )
        )
    totals = overview.totali
    out.append(
        _row(
            ["Totale registro"]
            + [""] * 4
            + [
                _amount(totals.imponibile),
                _amount(totals.rivalsa),
                _amount(totals.bollo),
                _amount(totals.totale),
            ]
            + [""] * 3
        )
    )
    return "".join(out)


def year_csv_filename(overview: YearOverview) -> str:
    """`registro-2026-studio-rossi.csv`: the year and the azienda's short name, reduced
    to what any file system and any `Content-Disposition` header accept."""
    ascii_name = (
        unicodedata.normalize("NFKD", overview.azienda_nome).encode("ascii", "ignore").decode()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "azienda"
    return f"registro-{overview.anno}-{slug}.csv"


__all__ = ["render_year_csv", "year_csv_filename"]
