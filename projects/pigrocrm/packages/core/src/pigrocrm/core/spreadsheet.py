"""Writing people's words into a spreadsheet without letting them become formulas.

openpyxl turns a string that starts with `=` into a formula cell. A customer's name, an
offer title or an hour's description is typed by a person (or written by an agent over MCP
from an email), and the file is opened by a client, so a value like `=HYPERLINK(...)` must
land as the text it is. The one place a sheet is built writes every free-text cell through
`text_cell`; the only formula a sheet may carry is one its own code writes.
"""

from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

# What a spreadsheet reads as the start of a formula when it opens a text file (a CSV): a
# CSV has no cell types, so there the defence is a prefix, not a cell type.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def text_cell(sheet: Worksheet, row: int, column: int, value: str) -> Cell:
    """Writes `value` at `(row, column)` as a string cell, whatever it starts with.

    Setting `data_type` after the value is what stops openpyxl's own `=` rule: the cell is
    saved as a string and read back as one."""
    cell = sheet.cell(row=row, column=column, value=value)
    cell.data_type = "s"
    return cell
