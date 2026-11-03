"""AnalyticsService.year_overview / .build_year_csv -- REB-757.

The year of one azienda as the accountant reads it. What these hold the code to: the
register is the same universe `annual_revenue` sums (so its total is the revenue
figure), the rivalsa and the stamp duty are kept apart without leaving the total, the
regime and the treatment come off what each invoice froze, and the CSV opens in an
Italian-locale spreadsheet (BOM, semicolon, comma decimals) and totals to the register.
"""

import csv
import io
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from conftest import _invoice_service
from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.repository import AnalyticsRepository
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.fiscal.pack import SOCIAL_SECURITY_SURCHARGE_INPS
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.schemas import (
    InvoiceAnnul,
    InvoiceCreate,
    InvoiceIssue,
    InvoiceLineIn,
    PaymentState,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm.core.storage.local import LocalFileStorage

ADMIN = Actor(id=None, type="system", role="admin")
READER = Actor(id=None, type="user", role="readonly")
YEAR = 2026


def _customer(
    session: Session, name: str = "Acme S.r.l.", vat_number: str | None = "01234567890"
) -> Customer:
    customer = Customer(
        legal_name=name,
        vat_number=vat_number,
        tax_code=None if vat_number else "RSSMRA80A01H501U",
        sdi_code="ABCDEFG",
        address="Corso Italia 5",
        cap="00100",
        city="Roma",
        province="RM",
        country="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def _issue(
    service: InvoiceService,
    customer: Customer,
    *lines: tuple[str, str],
    day: date = date(YEAR, 3, 10),
):
    """One issued `invoice` of `(descrizione, prezzo)` lines, on `day`."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer.id,
            lines=[
                InvoiceLineIn(description=description, unit_price=Decimal(price))
                for description, price in lines
            ],
        ),
        ADMIN,
    )
    return service.issue(invoice.id, InvoiceIssue(issue_date=day), ADMIN)


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    service: InvoiceService = _invoice_service(db_session, local_storage)
    return service


def test_the_register_lists_the_issued_invoices_in_number_order_with_the_total(
    db_session: Session, service: InvoiceService
) -> None:
    acme, beta = _customer(db_session), _customer(db_session, "Beta SpA", None)
    _issue(service, acme, ("Consulenza marzo", "1000.00"))
    _issue(service, beta, ("Sviluppo", "2500.50"), day=date(YEAR, 4, 2))

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    assert [(r.number, r.legal_name, r.taxable_amount) for r in overview.register] == [
        (1, "Acme S.r.l.", Decimal("1000.00")),
        (2, "Beta SpA", Decimal("2500.50")),
    ]
    assert overview.register[0].vat_number == "01234567890"
    assert overview.total.invoices == 2
    assert overview.total.taxable_amount == Decimal("3500.50")
    # The year's revenue figure and the register's total are one number.
    assert overview.total.taxable_amount == AnalyticsRepository(db_session).annual_revenue(
        YEAR, overview.legal_entity_id
    )


def test_the_stamp_duty_and_the_social_security_surcharge_are_kept_apart(
    db_session: Session, service: InvoiceService
) -> None:
    """A 2,000 euro fee with its 4% rivalsa line owes the 2 euro stamp duty on the
    untaxed amount. The rivalsa is inside `taxable_amount` (it is a line like any other, and
    counts toward the ceiling); the bollo is not, and its sentence is the invoice's own."""
    _issue(
        service,
        _customer(db_session),
        ("Consulenza", "2000.00"),
        (SOCIAL_SECURITY_SURCHARGE_INPS.description_line, "80.00"),
    )

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    row = overview.register[0]
    assert (row.taxable_amount, row.social_security_surcharge, row.stamp_duty, row.total) == (
        Decimal("2080.00"),
        Decimal("80.00"),
        Decimal("2.00"),
        Decimal("2080.00"),
    )
    assert row.stamp_duty_declaration == (
        "Imposta di bollo di 2,00 € assolta in modo virtuale a carico dell'emittente."
    )
    assert overview.total.social_security_surcharge == Decimal("80.00")
    assert overview.total.stamp_duty == Decimal("2.00")


def test_revenue_is_grouped_by_the_regime_and_the_treatment_the_invoices_carry(
    db_session: Session, service: InvoiceService
) -> None:
    _issue(service, _customer(db_session), ("Consulenza", "1000.00"), ("Formazione", "500.00"))

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    assert [(r.regime_code, r.pack_id, r.invoices, r.revenue) for r in overview.regimes] == [
        ("RF19", "it-flat-rate", 1, Decimal("1500.00"))
    ]
    [trattamento] = overview.treatments
    assert (trattamento.vat_rate, trattamento.vat_nature, trattamento.lines) == (
        Decimal("0.00"),
        "N2.2",
        2,
    )
    # The legal reference as the lines print it, character for character.
    assert trattamento.legal_reference is not None
    assert "L. 190/2014" in trattamento.legal_reference
    assert trattamento.revenue == Decimal("1500.00")


def test_an_annulled_invoice_and_another_year_stay_out_of_the_figures(
    db_session: Session, service: InvoiceService
) -> None:
    customer = _customer(db_session)
    _issue(service, customer, ("Tenuta", "1000.00"))
    struck = _issue(service, customer, ("Annullata", "400.00"))
    service.annul(struck.id, InvoiceAnnul(reason="errore di battitura"), ADMIN)
    # An earlier year is closed to issuing, so the row is written the way an import would.
    db_session.add(
        Invoice(
            customer_id=customer.id,
            type="invoice",
            status="issued",
            year=YEAR - 1,
            number=1,
            issue_date=date(YEAR - 1, 12, 20),
            taxable_amount=Decimal("700.00"),
            total=Decimal("700.00"),
        )
    )
    db_session.flush()

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    assert [r.number for r in overview.register] == [1]
    assert overview.total.taxable_amount == Decimal("1000.00")
    assert overview.cancelled == 1


def test_a_year_with_nothing_issued_is_an_empty_register_not_an_error(
    db_session: Session, service: InvoiceService
) -> None:
    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    assert overview.register == []
    assert overview.total.taxable_amount == Decimal("0.00")
    assert overview.regimes == [] and overview.treatments == []


# --- the CSV --------------------------------------------------------------------------


def _rows(content: bytes) -> list[list[str]]:
    text = content.decode("utf-8")
    assert text.startswith("\ufeff"), "no BOM: Excel would read the accents as Windows-1252"
    return list(csv.reader(io.StringIO(text[1:], newline=""), delimiter=";"))


def _italian(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


def test_the_csv_is_one_row_per_line_and_its_totals_are_the_registers(
    db_session: Session, service: InvoiceService
) -> None:
    acme = _customer(db_session)
    _issue(service, acme, ("Consulenza; fase 1", "1234.50"), ("Rivalsa a parte", "10.00"))
    paid = _issue(service, _customer(db_session, "Beta SpA", None), ("Sviluppo", "2500.00"))
    service.set_payment_state(
        paid.id, PaymentState(payment_status="paid", payment_date=date(YEAR, 5, 6)), ADMIN
    )

    filename, content = AnalyticsService(db_session).build_year_csv(YEAR, READER)
    rows = _rows(content)

    assert filename.startswith(f"registro-{YEAR}-")
    assert filename.endswith(".csv")
    lines_header = rows.index(
        [
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
        ]
    )
    total_lines = next(r for r in rows if r and r[0] == "Totale righe")
    line_rows = rows[lines_header + 1 : rows.index(total_lines)]
    assert len(line_rows) == 3
    # The delimiter inside a description survives as one cell; money is Italian.
    assert line_rows[0][6] == "Consulenza; fase 1"
    assert line_rows[0][10] == "1.234,50"
    assert line_rows[2][14:] == ["Incassata", f"{YEAR}-05-06"]
    total_register = next(r for r in rows if r and r[0] == "Totale registro")

    assert sum(_italian(r[10]) for r in line_rows) == _italian(total_lines[10])
    # The two totals of the file are one figure, and it is the page's.
    assert _italian(total_lines[10]) == _italian(total_register[5]) == Decimal("3744.50")
    assert (
        _italian(total_register[5])
        == AnalyticsService(db_session).year_overview(YEAR, READER).total.taxable_amount
    )


def test_a_cell_a_spreadsheet_would_run_as_a_formula_is_defused(
    db_session: Session, service: InvoiceService
) -> None:
    _issue(
        service, _customer(db_session, '=HYPERLINK("http://x")'), ("+cmd|' /C calc'!A0", "10.00")
    )

    _, content = AnalyticsService(db_session).build_year_csv(YEAR, READER)

    text = content.decode("utf-8")
    assert "'=HYPERLINK" in text and "'+cmd" in text
    # Only the number and the partita IVA are written as formulas, `="..."`, by us.
    assert not any(
        cell.startswith(("=", "+")) and not cell.startswith('="')
        for row in _rows(content)
        for cell in row
    )


def test_the_number_and_the_vat_number_are_text_cells_to_spreadsheet_cannot_reinterpret(
    db_session: Session, service: InvoiceService
) -> None:
    """`2026/1` is January 2026 to an Italian-locale Excel and `01234567890` loses its
    zero as a number: both are written as `="..."`, which both Excel and LibreOffice read
    as the text. The csv module reads the cell back as the formula, which is the proof the
    quoting is the RFC's own."""
    _issue(service, _customer(db_session), ("Consulenza", "100.00"))

    _, content = AnalyticsService(db_session).build_year_csv(YEAR, READER)

    rows = _rows(content)
    line = next(r for r in rows if r and r[0] == f'="{YEAR}/1"')
    assert line[3] == '="01234567890"'
    assert f'"=""{YEAR}/1"""'.encode() in content


def test_the_filename_is_the_year_and_the_legal_entity_reduced_to_ascii(
    db_session: Session, service: InvoiceService
) -> None:
    from pigrocrm.core.analytics.year_csv import year_csv_filename

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)
    named = overview.model_copy(update={"legal_entity_name": "Società Rossi & Figli"})

    assert year_csv_filename(named) == f"registro-{YEAR}-societa-rossi-figli.csv"
    assert year_csv_filename(named.model_copy(update={"legal_entity_name": "***"})) == (
        f"registro-{YEAR}-azienda.csv"
    )


def test_the_register_prints_the_customer_as_the_invoice_froze_it(
    db_session: Session, service: InvoiceService
) -> None:
    """A customer who had no partita IVA at issue has none in the register, whatever the
    record says today; and an invoice does not drop out of the register because the
    customer it was issued to is no longer readable (the outer join)."""
    customer = _customer(db_session, "Privato Rossi", None)
    invoice = _issue(service, customer, ("Consulenza", "100.00"))
    customer.vat_number = "99999999999"
    db_session.flush()

    overview = AnalyticsService(db_session).year_overview(YEAR, READER)

    [row] = overview.register
    assert (row.legal_name, row.vat_number) == ("Privato Rossi", None)
    assert row.invoice_id == invoice.id
