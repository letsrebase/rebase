"""Criterion 10, XLSX half. The **shape** of the sheet is carried over from the previous system's
`buildTimeTrackingXlsx`; the `exceljs` code that produced it is not, because it was a
JavaScript library inside a Node dev server this product no longer has.

Three things are rewritten rather than carried, and each is asserted below: hours and
amounts are written as **numbers** with a `number_format` instead of pre-formatted
strings; the date is written as a **date**; and the total is a `SUBTOTAL(109; ...)`
**formula**, not a constant -- whoever receives a timesheet filters it, and a constant
total after a filter contradicts the column above it, which is exactly §6.2's principle
applied to a spreadsheet.
"""

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from pigrocrm.core.timetracking.xlsx import build_time_report_xlsx

HOSTILE = 'Call con @mario su [fase 1] & #2 — "urgente"\nseconda riga'

ROWS = [
    (date(2026, 3, 4), Decimal("2.50"), HOSTILE),
    (date(2026, 3, 20), Decimal("1.25"), "Revisione"),
]


def _sheet(**overrides):
    payload = {
        "cliente": "Rossi & C. S.r.l.",
        "offerta": "Progetto Alfa",
        "periodo": "marzo 2026",
        "rows": ROWS,
    }
    payload.update(overrides)
    return load_workbook(BytesIO(build_time_report_xlsx(**payload))).active


def test_the_four_row_header_block_and_the_bold_grey_header_row() -> None:
    sheet = _sheet()
    assert sheet["A1"].value == "Rapporto ore"
    assert sheet["A2"].value == "Cliente" and sheet["B2"].value == "Rossi & C. S.r.l."
    assert sheet["A3"].value == "Offerta" and sheet["B3"].value == "Progetto Alfa"
    assert sheet["A4"].value == "Periodo" and sheet["B4"].value == "marzo 2026"
    assert {str(r) for r in sheet.merged_cells.ranges} >= {"A1:C1", "B2:C2", "B3:C3", "B4:C4"}

    for column, label in zip("ABC", ("DATA", "ORE", "DESCRIZIONE"), strict=True):
        cell = sheet[f"{column}5"]
        assert cell.value == label
        assert cell.font.bold is True
        assert cell.fill.fgColor.rgb == "FFD9D9D9"


def test_the_frozen_pane_and_the_carried_over_column_widths() -> None:
    """`ySplit: 5` and widths 14 / 10 / 80. Not deducible from any specification: they
    are what makes the file *usable* rather than merely correct, tuned by years of
    somebody scrolling and filtering it."""
    sheet = _sheet()
    assert sheet.freeze_panes == "A6"
    assert sheet.column_dimensions["A"].width == 14
    assert sheet.column_dimensions["B"].width == 10
    assert sheet.column_dimensions["C"].width == 80


def test_dates_are_dates_and_hours_are_numbers_with_their_format() -> None:
    sheet = _sheet()
    assert isinstance(sheet["A6"].value, (date, datetime))
    assert sheet["A6"].number_format == "dd/mm/yyyy"
    # A real number, not a string: a text cell cannot be summed, filtered numerically,
    # or charted, and that is what the previous system's pre-formatted strings cost.
    assert sheet["B6"].value == Decimal("2.50") or float(sheet["B6"].value) == 2.5
    assert not isinstance(sheet["B6"].value, str)
    assert sheet["B6"].number_format == "0.00"
    assert sheet["C6"].alignment.wrap_text is True


def test_the_description_arrives_verbatim_with_its_newline_and_no_escaping() -> None:
    """The previous system stored the description already escaped for Typst and wrote that same
    string into the cell (`vite.config.js:1245`), so a client opened the spreadsheet and read
    `Call con \\@mario su \\[fase 1\\]`. Nothing here escapes anything: a cell value is
    not markup."""
    sheet = _sheet()
    assert sheet["C6"].value == HOSTILE
    assert "\\@" not in sheet["C6"].value
    assert "\n" in sheet["C6"].value


def test_the_total_is_a_subtotal_formula_not_a_constant() -> None:
    sheet = _sheet()
    total_row = 5 + len(ROWS) + 1
    assert sheet[f"A{total_row}"].value == "Totale ore"
    assert sheet[f"A{total_row}"].font.bold is True
    assert sheet[f"B{total_row}"].value == f"=SUBTOTAL(109,B6:B{5 + len(ROWS)})"
    assert sheet[f"B{total_row}"].number_format == "0.00"
    assert sheet[f"B{total_row}"].font.bold is True


def test_an_empty_month_still_produces_a_valid_sheet() -> None:
    """A month with no hours is a real answer, not an error: the header block, the
    column headers and a zero-row total all have to survive an empty range."""
    sheet = _sheet(rows=[])
    assert sheet["A5"].value == "DATA"
    assert sheet["A6"].value == "Totale ore"
    assert sheet["B6"].value == "=SUBTOTAL(109,B6:B5)"


def test_the_autofilter_covers_the_data_rows_only() -> None:
    """The filter must not include the total row, or filtering hides the total it is
    supposed to update."""
    sheet = _sheet()
    assert sheet.auto_filter.ref == f"A5:C{5 + len(ROWS)}"


def test_free_text_that_starts_like_a_formula_is_written_as_a_string() -> None:
    """A description, a customer name and an offer title are typed by people (or written by an
    agent from an email) and the file goes to a client: `=HYPERLINK(...)` must stay text. The
    `SUBTOTAL` the sheet writes itself is the only formula in the file."""
    formula = '=HYPERLINK("http://evil.example","clicca")'
    sheet = _sheet(
        cliente=formula,
        offerta="+cmd|' /C calc'!A0",
        rows=[(date(2026, 3, 4), Decimal("1.00"), formula)],
    )

    for ref in ("B2", "B3", "C6"):
        assert sheet[ref].data_type == "s", ref
    assert sheet["C6"].value == formula
    assert sheet["B2"].value == formula
    formulas = [
        cell.coordinate for row in sheet.iter_rows() for cell in row if cell.data_type == "f"
    ]
    assert formulas == ["B7"]
