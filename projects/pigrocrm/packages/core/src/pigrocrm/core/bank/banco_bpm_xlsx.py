"""The Banco BPM YouWeb XLSX reader (REB-1017; spec 2026-11-09, due items and the cash schedule,
§ 2).

YouWeb's «Saldo e movimenti», «SCARICA IN EXCEL» exports one sheet, `movimentiConto`, with the
header `Data Contabile | Data Valuta | Importo | Divisa | Causale / Descrizione | Stato |
Canale`: dates as text `dd/mm/yyyy`, one signed amount with a dot decimal, the description
carrying everything else. The export states no account, no period, no balance and no bank
reference, so the parsed statement has none of them: the person sets the covered period on
import and the dedup key is the one without a reference (date, amount, description hash and the
occurrence index in the file).

A card payment not yet booked ends its description with `-da contab` and is left out: once
booked it comes back without the suffix, and importing both would count it twice. An incoming
or outgoing SEPA transfer carries `END TO END ID: <id>` in its description; the id is kept as
`end_to_end_id` unless the payer gave none (empty, or `NOTPROVIDED`, the standard's «none»).
"""

import datetime as dt
import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from pigrocrm.core.bank.models import END_TO_END_ID_LENGTH
from pigrocrm.core.bank.readers import ParsedMovement, ParsedStatement
from pigrocrm.core.errors import ValidationFailed

FORMAT = "banco_bpm_xlsx"
ENTITY = "bank_statement"
SHEET = "movimentiConto"
HEADER = (
    "data contabile",
    "data valuta",
    "importo",
    "divisa",
    "causale / descrizione",
    "stato",
    "canale",
)
DESCRIPTION_MAX_LENGTH = 1000
CENT = Decimal("0.01")

_NOT_BOOKED = re.compile(r"\s*-\s*da contab\.?\s*$", re.IGNORECASE)
# The value is the token after the label; an empty one is followed by the next label
# (`CAUSALE:`), which ends in a colon and is not a value.
_END_TO_END = re.compile(r"END TO END ID\s*:?\s*(\S+)", re.IGNORECASE)
_NO_END_TO_END = "NOTPROVIDE"


def _refuse(field: str, reason: str) -> ValidationFailed:
    return ValidationFailed(ENTITY, field, reason)


def _date(value: Any, row: int) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.datetime.strptime(str(value).strip(), "%d/%m/%Y").date()
    except ValueError:
        raise _refuse("date", f"riga {row}: data non valida") from None


def _amount(value: Any, row: int) -> Decimal:
    try:
        amount = Decimal(repr(value) if isinstance(value, float) else str(value).strip())
    except InvalidOperation:
        raise _refuse("amount", f"riga {row}: importo non valido") from None
    if not amount.is_finite():
        raise _refuse("amount", f"riga {row}: importo non valido")
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


def end_to_end_id(description: str) -> str | None:
    """The end-to-end id a transfer's description carries, or `None` when the payer gave none."""
    found = _END_TO_END.search(description)
    if found is None:
        return None
    value = found.group(1)
    if (
        value.endswith(":")
        or value.upper().startswith(_NO_END_TO_END)
        or len(value) > END_TO_END_ID_LENGTH
    ):
        return None
    return value


def read_banco_bpm_xlsx(content: bytes) -> ParsedStatement:
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, KeyError, OSError):
        raise _refuse("file", "il file non è un foglio Excel valido") from None
    try:
        sheet = workbook[SHEET] if SHEET in workbook.sheetnames else workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = next(rows, None)
        if header is None or tuple(str(c or "").strip().casefold() for c in header[:7]) != HEADER:
            raise _refuse("file", "l'intestazione non è quella di «Saldo e movimenti» di YouWeb")
        movements: list[ParsedMovement] = []
        for number, row in enumerate(rows, start=2):
            if not any(c not in (None, "") for c in row):
                continue
            if len(row) < len(HEADER) or any(row[i] in (None, "") for i in (0, 2, 3)):
                raise _refuse("file", f"riga {number}: dati mancanti")
            description = " ".join(str(row[4] or "").split())
            if _NOT_BOOKED.search(description):
                continue
            movements.append(
                ParsedMovement(
                    booking_date=_date(row[0], number),
                    value_date=_date(row[1], number) if row[1] not in (None, "") else None,
                    amount=_amount(row[2], number),
                    currency=str(row[3]).strip().upper(),
                    description=description[:DESCRIPTION_MAX_LENGTH] or None,
                    end_to_end_id=end_to_end_id(description),
                )
            )
    finally:
        workbook.close()
    return ParsedStatement(format=FORMAT, movements=movements)
