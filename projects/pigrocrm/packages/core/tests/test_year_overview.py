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
from pigrocrm.core.fiscal.pack import RIVALSA_INPS
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
ANNO = 2026


def _customer(
    session: Session, name: str = "Acme S.r.l.", piva: str | None = "01234567890"
) -> Customer:
    customer = Customer(
        ragione_sociale=name,
        partita_iva=piva,
        codice_fiscale=None if piva else "RSSMRA80A01H501U",
        codice_sdi="ABCDEFG",
        indirizzo="Corso Italia 5",
        cap="00100",
        comune="Roma",
        provincia="RM",
        nazione="IT",
    )
    session.add(customer)
    session.flush()
    return customer


def _issue(
    service: InvoiceService,
    customer: Customer,
    *righe: tuple[str, str],
    giorno: date = date(ANNO, 3, 10),
):
    """One issued `fattura` of `(descrizione, prezzo)` lines, on `giorno`."""
    invoice = service.create(
        InvoiceCreate(
            customer_id=customer.id,
            righe=[
                InvoiceLineIn(descrizione=descrizione, prezzo_unitario=Decimal(prezzo))
                for descrizione, prezzo in righe
            ],
        ),
        ADMIN,
    )
    return service.issue(invoice.id, InvoiceIssue(data_emissione=giorno), ADMIN)


@pytest.fixture
def service(db_session: Session, local_storage: LocalFileStorage) -> InvoiceService:
    service: InvoiceService = _invoice_service(db_session, local_storage)
    return service


def test_the_register_lists_the_issued_invoices_in_number_order_with_the_total(
    db_session: Session, service: InvoiceService
) -> None:
    acme, beta = _customer(db_session), _customer(db_session, "Beta SpA", None)
    _issue(service, acme, ("Consulenza marzo", "1000.00"))
    _issue(service, beta, ("Sviluppo", "2500.50"), giorno=date(ANNO, 4, 2))

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    assert [(r.numero, r.ragione_sociale, r.imponibile) for r in overview.registro] == [
        (1, "Acme S.r.l.", Decimal("1000.00")),
        (2, "Beta SpA", Decimal("2500.50")),
    ]
    assert overview.registro[0].partita_iva == "01234567890"
    assert overview.totali.fatture == 2
    assert overview.totali.imponibile == Decimal("3500.50")
    # The year's revenue figure and the register's total are one number.
    assert overview.totali.imponibile == AnalyticsRepository(db_session).annual_revenue(
        ANNO, overview.azienda_id
    )


def test_the_stamp_duty_and_the_rivalsa_are_kept_apart(
    db_session: Session, service: InvoiceService
) -> None:
    """A 2,000 euro fee with its 4% rivalsa line owes the 2 euro stamp duty on the
    untaxed amount. The rivalsa is inside `imponibile` (it is a line like any other, and
    counts toward the ceiling); the bollo is not, and its sentence is the invoice's own."""
    _issue(
        service,
        _customer(db_session),
        ("Consulenza", "2000.00"),
        (RIVALSA_INPS.descrizione_riga, "80.00"),
    )

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    row = overview.registro[0]
    assert (row.imponibile, row.rivalsa, row.bollo, row.totale) == (
        Decimal("2080.00"),
        Decimal("80.00"),
        Decimal("2.00"),
        Decimal("2080.00"),
    )
    assert row.dichiarazione_bollo == (
        "Imposta di bollo di 2,00 € assolta in modo virtuale a carico dell'emittente."
    )
    assert overview.totali.rivalsa == Decimal("80.00")
    assert overview.totali.bollo == Decimal("2.00")


def test_revenue_is_grouped_by_the_regime_and_the_treatment_the_invoices_carry(
    db_session: Session, service: InvoiceService
) -> None:
    _issue(service, _customer(db_session), ("Consulenza", "1000.00"), ("Formazione", "500.00"))

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    assert [(r.codice_regime, r.pack_id, r.fatture, r.ricavi) for r in overview.regimi] == [
        ("RF19", "it-flat-rate", 1, Decimal("1500.00"))
    ]
    [trattamento] = overview.trattamenti
    assert (trattamento.aliquota_iva, trattamento.natura, trattamento.righe) == (
        Decimal("0.00"),
        "N2.2",
        2,
    )
    # The legal reference as the lines print it, character for character.
    assert trattamento.riferimento_normativo is not None
    assert "L. 190/2014" in trattamento.riferimento_normativo
    assert trattamento.ricavi == Decimal("1500.00")


def test_an_annulled_invoice_and_another_year_stay_out_of_the_figures(
    db_session: Session, service: InvoiceService
) -> None:
    customer = _customer(db_session)
    _issue(service, customer, ("Tenuta", "1000.00"))
    struck = _issue(service, customer, ("Annullata", "400.00"))
    service.annul(struck.id, InvoiceAnnul(motivo="errore di battitura"), ADMIN)
    # An earlier year is closed to issuing, so the row is written the way an import would.
    db_session.add(
        Invoice(
            customer_id=customer.id,
            tipo="fattura",
            stato="emessa",
            anno=ANNO - 1,
            numero=1,
            data_emissione=date(ANNO - 1, 12, 20),
            imponibile=Decimal("700.00"),
            totale=Decimal("700.00"),
        )
    )
    db_session.flush()

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    assert [r.numero for r in overview.registro] == [1]
    assert overview.totali.imponibile == Decimal("1000.00")
    assert overview.annullate == 1


def test_a_year_with_nothing_issued_is_an_empty_register_not_an_error(
    db_session: Session, service: InvoiceService
) -> None:
    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    assert overview.registro == []
    assert overview.totali.imponibile == Decimal("0.00")
    assert overview.regimi == [] and overview.trattamenti == []


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
        paid.id, PaymentState(stato_pagamento="incassato", data_incasso=date(ANNO, 5, 6)), ADMIN
    )

    filename, content = AnalyticsService(db_session).build_year_csv(ANNO, READER)
    rows = _rows(content)

    assert filename.startswith(f"registro-{ANNO}-")
    assert filename.endswith(".csv")
    righe_header = rows.index(
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
    totale_righe = next(r for r in rows if r and r[0] == "Totale righe")
    line_rows = rows[righe_header + 1 : rows.index(totale_righe)]
    assert len(line_rows) == 3
    # The delimiter inside a description survives as one cell; money is Italian.
    assert line_rows[0][6] == "Consulenza; fase 1"
    assert line_rows[0][10] == "1.234,50"
    assert line_rows[2][14:] == ["Incassata", f"{ANNO}-05-06"]
    totale_registro = next(r for r in rows if r and r[0] == "Totale registro")

    assert sum(_italian(r[10]) for r in line_rows) == _italian(totale_righe[10])
    # The two totals of the file are one figure, and it is the page's.
    assert _italian(totale_righe[10]) == _italian(totale_registro[5]) == Decimal("3744.50")
    assert (
        _italian(totale_registro[5])
        == AnalyticsService(db_session).year_overview(ANNO, READER).totali.imponibile
    )


def test_a_cell_a_spreadsheet_would_run_as_a_formula_is_defused(
    db_session: Session, service: InvoiceService
) -> None:
    _issue(
        service, _customer(db_session, '=HYPERLINK("http://x")'), ("+cmd|' /C calc'!A0", "10.00")
    )

    _, content = AnalyticsService(db_session).build_year_csv(ANNO, READER)

    text = content.decode("utf-8")
    assert "'=HYPERLINK" in text and "'+cmd" in text
    # Only the number and the partita IVA are written as formulas, `="..."`, by us.
    assert not any(
        cell.startswith(("=", "+")) and not cell.startswith('="')
        for row in _rows(content)
        for cell in row
    )


def test_the_number_and_the_partita_iva_are_text_cells_a_spreadsheet_cannot_reinterpret(
    db_session: Session, service: InvoiceService
) -> None:
    """`2026/1` is January 2026 to an Italian-locale Excel and `01234567890` loses its
    zero as a number: both are written as `="..."`, which both Excel and LibreOffice read
    as the text. The csv module reads the cell back as the formula, which is the proof the
    quoting is the RFC's own."""
    _issue(service, _customer(db_session), ("Consulenza", "100.00"))

    _, content = AnalyticsService(db_session).build_year_csv(ANNO, READER)

    rows = _rows(content)
    line = next(r for r in rows if r and r[0] == f'="{ANNO}/1"')
    assert line[3] == '="01234567890"'
    assert f'"=""{ANNO}/1"""'.encode() in content


def test_the_filename_is_the_year_and_the_azienda_reduced_to_ascii(
    db_session: Session, service: InvoiceService
) -> None:
    from pigrocrm.core.analytics.year_csv import year_csv_filename

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)
    named = overview.model_copy(update={"azienda_nome": "Società Rossi & Figli"})

    assert year_csv_filename(named) == f"registro-{ANNO}-societa-rossi-figli.csv"
    assert year_csv_filename(named.model_copy(update={"azienda_nome": "***"})) == (
        f"registro-{ANNO}-azienda.csv"
    )


def test_the_register_prints_the_customer_as_the_invoice_froze_it(
    db_session: Session, service: InvoiceService
) -> None:
    """A customer who had no partita IVA at issue has none in the register, whatever the
    record says today; and an invoice does not drop out of the register because the
    customer it was issued to is no longer readable (the outer join)."""
    customer = _customer(db_session, "Privato Rossi", None)
    invoice = _issue(service, customer, ("Consulenza", "100.00"))
    customer.partita_iva = "99999999999"
    db_session.flush()

    overview = AnalyticsService(db_session).year_overview(ANNO, READER)

    [row] = overview.registro
    assert (row.ragione_sociale, row.partita_iva) == ("Privato Rossi", None)
    assert row.invoice_id == invoice.id
