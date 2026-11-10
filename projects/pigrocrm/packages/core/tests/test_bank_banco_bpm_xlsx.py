"""The Banco BPM YouWeb XLSX reader (REB-1017), on a workbook built from made-up rows in the
shape of the real export: one sheet `movimentiConto`, text dates, signed amounts, everything
else in the description."""

import datetime as dt
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.bank import readers
from pigrocrm.core.bank.banco_bpm_xlsx import end_to_end_id, read_banco_bpm_xlsx
from pigrocrm.core.bank.models import BankAccount, BankMovement
from pigrocrm.core.bank.service import BankStatementService
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.legal_entities.repository import LegalEntityRepository

D = dt.date
HEADER = [
    "Data Contabile",
    "Data Valuta",
    "Importo",
    "Divisa",
    "Causale / Descrizione",
    "Stato ",
    "Canale",
]
SEPA_IN = (
    "BON.DA ACME SRL FATTURA 12/2026 NR. BONIFICO SEPA: IT12A34567890123 "
    "RIF. BANCA ORDINANTE: 123 END TO END ID: INV-2026-12"
)
SEPA_NOT_PROVIDED = (
    "BON.DA FORNITORE SPA NR. BONIFICO SEPA: IT99Z00000000001 "
    "RIF. BANCA ORDINANTE:  XY END TO END ID: NOTPROVIDED"
)
SEPA_EMPTY = (
    "BON.DA ALTRO SRL NR. BONIFICO SEPA: IT99Z00000000002 "
    "RIF. BANCA ORDINANTE: 7 END TO END ID:  CAUSALE: saldo ordine"
)
CARD = "pagamento con carta - carta **1234-shop milano ita"
ROWS = [
    ["10/11/2026", "10/11/2026", 1220.0, "EUR", SEPA_IN, "Cont.", ""],
    ["09/11/2026", "09/11/2026", 300.5, "EUR", SEPA_NOT_PROVIDED, "Cont.", ""],
    ["09/11/2026", "08/11/2026", 40.0, "EUR", SEPA_EMPTY, "Cont.", "Web"],
    ["08/11/2026", "08/11/2026", -12.3, "EUR", CARD, "Cont.", ""],
    ["08/11/2026", "08/11/2026", -12.3, "EUR", CARD, "Cont.", ""],
    ["07/11/2026", "07/11/2026", -0.1, "EUR", CARD + "   -da contab", "Cont.", "App"],
    [None, None, None, None, None, None, None],
]


def _workbook(rows: list[list[object]] = ROWS, header: list[str] = HEADER) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "movimentiConto"
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def test_rows_become_movements_with_signed_two_decimal_amounts() -> None:
    parsed = read_banco_bpm_xlsx(_workbook())
    assert parsed.format == "banco_bpm_xlsx"
    assert (parsed.account_iban, parsed.period_from, parsed.opening_balance) == (None, None, None)
    first = parsed.movements[0]
    assert first.booking_date == D(2026, 11, 10)
    assert first.amount == Decimal("1220.00") and first.currency == "EUR"
    assert [m.amount for m in parsed.movements] == [
        Decimal("1220.00"),
        Decimal("300.50"),
        Decimal("40.00"),
        Decimal("-12.30"),
        Decimal("-12.30"),
    ]
    assert parsed.movements[2].value_date == D(2026, 11, 8)
    assert parsed.movements[0].bank_reference is None


def test_a_sepa_row_gets_its_end_to_end_id_unless_the_payer_gave_none() -> None:
    movements = read_banco_bpm_xlsx(_workbook()).movements
    assert [m.end_to_end_id for m in movements[:3]] == ["INV-2026-12", None, None]
    assert end_to_end_id("x END TO END ID: ABC-1 NR. BONIFICO SEPA: 1") == "ABC-1"
    assert end_to_end_id("no sepa here") is None
    assert end_to_end_id("END TO END ID: " + "X" * 36) is None


def test_a_card_payment_not_yet_booked_is_left_out() -> None:
    descriptions = [m.description for m in read_banco_bpm_xlsx(_workbook()).movements]
    assert not any("da contab" in (d or "") for d in descriptions)
    assert len(descriptions) == 5


def test_importing_the_same_export_twice_adds_its_movements_once(db_session: Session) -> None:
    entity = LegalEntityRepository(db_session).default()
    assert entity is not None
    account = BankAccount(
        legal_entity_id=entity.id, name="Main", bank="Banco BPM", iban="IT60X0542811101000000123456"
    )
    db_session.add(account)
    db_session.flush()
    service = BankStatementService(db_session)
    period = {"period_from": D(2026, 8, 12), "period_to": D(2026, 11, 10)}
    first = service.import_statement(account.id, read_banco_bpm_xlsx(_workbook()), **period)
    # A later export overlaps and adds one row: the two identical card payments stay two.
    more = [[*ROWS[0][:2], 5.0, *ROWS[0][3:4], "extra", "Cont.", ""], *ROWS]
    second = service.import_statement(account.id, read_banco_bpm_xlsx(_workbook(more)), **period)
    assert (first.added, first.duplicates) == (5, 0)
    assert (second.added, second.duplicates) == (1, 5)
    assert db_session.scalar(select(func.count()).select_from(BankMovement)) == 6
    stored = db_session.scalars(
        select(BankMovement.end_to_end_id).where(BankMovement.end_to_end_id.is_not(None))
    ).all()
    assert stored == ["INV-2026-12"]


def test_the_reader_is_registered_for_the_seam() -> None:
    assert readers.reader_for("banco_bpm_xlsx") is read_banco_bpm_xlsx


def test_a_file_that_is_not_the_export_is_refused() -> None:
    with pytest.raises(ValidationFailed):
        read_banco_bpm_xlsx(b"not a workbook")
    with pytest.raises(ValidationFailed):
        read_banco_bpm_xlsx(_workbook(header=["a", "b", "c", "d", "e", "f", "g"]))
    with pytest.raises(ValidationFailed):
        read_banco_bpm_xlsx(_workbook([["31/02/2026", "", 1.0, "EUR", "x", "Cont.", ""]]))
    with pytest.raises(ValidationFailed):
        read_banco_bpm_xlsx(_workbook([["01/02/2026", "", "abc", "EUR", "x", "Cont.", ""]]))
    with pytest.raises(ValidationFailed):
        read_banco_bpm_xlsx(_workbook([["01/02/2026", "", None, "EUR", "x", "Cont.", ""]]))
