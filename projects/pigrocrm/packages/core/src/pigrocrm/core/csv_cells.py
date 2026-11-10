"""The cells of the CSV files an accountant opens in a spreadsheet.

Born in the year register (REB-757) and shared with the period bundle (REB-1011), so every
file the accountant receives reads the same way: a UTF-8 byte-order mark, the semicolon as
delimiter, the comma as decimal mark and CRLF line ends, because that combination is what
Excel under an Italian locale opens correctly. A comma-delimited, dot-decimal file parses
every money column as text, or reads `1.234,56` as a thousand times too much.

Pure functions over strings and `Decimal`, no query here.
"""

import re
from decimal import Decimal

from pigrocrm.core.spreadsheet import FORMULA_TRIGGERS

BOM = "\ufeff"
DELIMITER = ";"
NEEDS_QUOTING = re.compile(r'["\r\n;]')


def text_cell(value: str | None) -> str:
    """A text cell: defused against formula injection, quoted when it holds the delimiter,
    a quote or a line break (RFC 4180, an embedded quote doubled)."""
    if not value:
        return ""
    if value.startswith(FORMULA_TRIGGERS):
        value = "'" + value
    return f'"{value.replace(chr(34), chr(34) * 2)}"' if NEEDS_QUOTING.search(value) else value


def identifier_cell(value: str | None) -> str:
    """A code a spreadsheet must keep as the text it is: `="01234567890"`, which Excel and
    LibreOffice read as the string and not as a number. An Italian partita IVA often starts
    with a zero that a numeric cell drops, and `2026/1` is a date (January 2026) to an
    Italian-locale Excel. Only for what is made of letters, digits and `/.-`; anything
    else is free text and goes through `text_cell`, whose formula guard it needs."""
    if not value:
        return ""
    if re.fullmatch(r"[A-Za-z0-9/.\-]+", value):
        return f'"=""{value}"""'
    return text_cell(value)


def amount_cell(value: Decimal) -> str:
    """`1.234,56`: thousands with a point, two decimals with a comma. Decimal all the way,
    never a float, so a cent is never lost on the way to the file."""
    grouped = f"{value.quantize(Decimal('0.01')):,.2f}"
    return grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def factor_cell(value: Decimal) -> str:
    """A quantity or a unit price: as many decimals as it carries up to six, at least
    two for a price, comma as the mark and no thousands separator (it is not an amount)."""
    text = f"{value:f}"
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    return f"{whole},{fraction}" if fraction else whole


def price_cell(value: Decimal) -> str:
    text = factor_cell(value)
    if "," not in text:
        return f"{text},00"
    whole, fraction = text.split(",")
    return f"{whole},{fraction.ljust(2, '0')}"


def row_line(cells: list[str]) -> str:
    return DELIMITER.join(cells) + "\r\n"


__all__ = [
    "BOM",
    "DELIMITER",
    "amount_cell",
    "factor_cell",
    "identifier_cell",
    "price_cell",
    "row_line",
    "text_cell",
]
