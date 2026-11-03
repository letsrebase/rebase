"""The invoice PDF follows the layout of the register it inherited (ORB-102).

Ivan's issued invoices are the previous system's own PDFs, kept through `import_issued`;
a proforma rendered here sat next to them looking like another product. These tests pin
the scope `build_scope` hands the template -- every value a string already formatted the
way that layout prints it -- and, where Pandoc and Typst are installed, the words the
rendered document actually carries.
"""

import shutil
import subprocess
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.pdf import PROFORMA_DECLARATION, build_scope, render_invoice_pdf
from pigrocrm.core.invoices.schemas import (
    InvoiceForExport,
    InvoiceLineRead,
    InvoiceSnapshot,
    PartySnapshot,
)

SETTINGS = Settings(jwt_secret="x" * 32)

LEGAL_ENTITY = PartySnapshot(
    legal_name="Studio Rossi",
    vat_number="01234567890",
    tax_code="RSSMRA80A01H501U",
    sdi_code="1234567",
    certified_email="studiorossi@pec.it",
    address="Via Roma 1",
    postal_code="20053",
    city="Milano",
    province="MI",
    country="IT",
    email="mario@example.com",
    phone="+39 02 1234567",
    website="www.example.com",
)

CUSTOMER = PartySnapshot(
    legal_name="Acme S.r.l.",
    vat_number="12345678901",
    tax_code="12345678901",
    sdi_code="ABCDEFG",
    certified_email="acme@pec.it",
    address="Corso Italia 5",
    postal_code="00100",
    city="Roma",
    province="RM",
    country="IT",
    email=None,
    phone=None,
    website=None,
)

FLAT_RATE = FiscalSnapshot(
    regime_code="RF19",
    default_vat_rate=Decimal("0.00"),
    default_vat_nature="N2.2",
    legal_reference="Operazione senza applicazione dell'IVA, art. 1 c. 54-89 L. 190/2014",
    apply_stamp_duty=True,
    stamp_duty_threshold=Decimal("77.47"),
    stamp_duty_amount=Decimal("2.00"),
    payment_terms="TP02",
    payment_method="MP05",
    due_days=30,
    iban="IT60X0542811101000000123456",
)


def _line(
    description: str,
    price: str,
    *,
    rate: str = "0.00",
    vat_nature: str | None = "N2.2",
    number: int = 1,
) -> InvoiceLineRead:
    return InvoiceLineRead(
        id=uuid4(),
        invoice_id=uuid4(),
        line_number=number,
        description=description,
        quantity=Decimal("1.000000"),
        unit_of_measure=None,
        unit_price=Decimal(price),
        discount_percentage=None,
        discount_amount=None,
        total_price=Decimal(price),
        vat_rate=Decimal(rate),
        vat_nature=vat_nature,
        legal_reference=FLAT_RATE.legal_reference if vat_nature else None,
    )


def _export(
    *,
    customer: PartySnapshot = CUSTOMER,
    tax: FiscalSnapshot = FLAT_RATE,
    lines: tuple[InvoiceLineRead, ...] | None = None,
    vat_amount: str = "0.00",
    stamp_duty: str = "0.00",
    accrual: tuple[date, date] | None = None,
    order: dict[str, object] | None = None,
) -> InvoiceForExport:
    lines = lines or (_line("Consulenza Remote Console", "1760.00"),)
    taxable_amount = sum((r.total_price for r in lines), Decimal("0.00"))
    return InvoiceForExport(
        year=2026,
        number=7,
        issue_date=date(2026, 8, 11),
        due_date=date(2026, 9, 30),
        sdi_document_type="TD01",
        currency="EUR",
        taxable_amount=taxable_amount,
        vat_amount=Decimal(vat_amount),
        stamp_duty=Decimal(stamp_duty),
        total=taxable_amount + Decimal(vat_amount),
        memo="Consulenza",
        accrual_from=accrual[0] if accrual else None,
        accrual_to=accrual[1] if accrual else None,
        **(order or {}),
        snapshot=InvoiceSnapshot(version=1, legal_entity=LEGAL_ENTITY, customer=customer, tax=tax),
        lines=lines,
    )


# --- the scope: every value already formatted the way the inherited layout prints it ---


def test_amounts_read_the_italian_way_with_the_currency() -> None:
    scope = build_scope(_export(), reference=None)
    assert scope["fattura"]["totale"] == "1.760,00 €"
    assert scope["righe"][0]["prezzo_totale"] == "1.760,00 €"


def test_dates_read_dd_mm_yyyy_throughout_the_document() -> None:
    scope = build_scope(_export(accrual=(date(2026, 8, 1), date(2026, 8, 31))), reference=None)
    assert scope["fattura"]["data"] == "11-08-2026"
    assert scope["fattura"]["data_scadenza"] == "30-09-2026"
    assert scope["fattura"]["periodo_competenza"] == "dal 01-08-2026 al 31-08-2026"


def test_the_first_line_names_the_fiscal_document_type_and_a_proforma_names_none() -> None:
    """`TD01 fattura` is what the inherited layout prints; a proforma is not a fiscal
    document, so it gets no TD code, and it is the only one that carries the
    declaration the template wraps in a condition."""
    invoice = build_scope(_export(), reference=None)["fattura"]
    assert invoice["etichetta"] == "TD01 fattura"
    assert invoice["numero"] == "2026/7"
    assert invoice["dichiarazione_proforma"] == ""

    proforma = build_scope(_export(), reference="PROV-2026-0001")["fattura"]
    assert proforma["etichetta"] == "Fattura proforma"
    assert proforma["numero"] == "PROV-2026-0001"
    assert proforma["dichiarazione_proforma"] == PROFORMA_DECLARATION


def test_the_customer_reads_on_two_lines_separated_by_bars() -> None:
    customer = build_scope(_export(), reference=None)["cliente"]
    assert customer["riga_identita"] == "Acme S.r.l. | IVA: IT12345678901 | CF: 12345678901"
    assert (
        customer["riga_recapiti"]
        == "Corso Italia 5, 00100 Roma (RM) IT | PEC: acme@pec.it | Codice destinatario: ABCDEFG"
    )


def test_a_customer_missing_a_field_loses_the_segment_not_the_label() -> None:
    """A private customer has no VAT id and a foreign one has no SDI code: the segment
    goes, and no `IVA:` is left standing in front of nothing. The VAT id prefix is the
    party's own country, the way `IdPaese` precedes `IdCodice` in the XML, where the
    previous system printed `IT` for a London customer."""
    foreign = CUSTOMER.model_copy(
        update={
            "vat_number": "123456789",
            "tax_code": None,
            "sdi_code": None,
            "certified_email": None,
            "address": "1 Old Street",
            "postal_code": "EC1V 9HL",
            "city": "London",
            "province": "",
            "country": "GB",
        }
    )
    customer = build_scope(_export(customer=foreign), reference=None)["cliente"]
    assert customer["riga_identita"] == "Acme S.r.l. | IVA: GB123456789"
    assert customer["riga_recapiti"] == "1 Old Street, EC1V 9HL London GB"


def test_the_legal_entity_block_carries_what_the_inherited_header_printed() -> None:
    scope = build_scope(_export(), reference=None)
    assert scope["emittente"]["identificativo_iva"] == "IT01234567890"
    # An emitter row with no country yet reads `IT`, the same fallback the XML applies.
    without_country = LEGAL_ENTITY.model_copy(update={"country": ""})
    export = _export().model_copy(
        update={
            "snapshot": InvoiceSnapshot(
                version=1, legal_entity=without_country, customer=CUSTOMER, tax=FLAT_RATE
            )
        }
    )
    assert build_scope(export, reference=None)["emittente"]["identificativo_iva"] == "IT01234567890"
    assert scope["emittente"]["indirizzo_display"] == "Via Roma 1, 20053 Milano (MI) IT"
    assert scope["fiscale"]["regime_display"] == "RF19 Regime forfettario"


def test_the_vat_column_shows_the_vat_nature_when_there_is_no_rate_and_the_rate_otherwise() -> None:
    lines = (
        _line("Consulenza", "100.00"),
        _line("Licenza", "200.00", rate="22.00", vat_nature=None, number=2),
    )
    scope = build_scope(_export(lines=lines, vat_amount="44.00"), reference=None)
    assert [r["iva"] for r in scope["righe"]] == ["N2.2", "22%"]


def test_the_payment_row_carries_the_code_and_its_words() -> None:
    payment = build_scope(_export(), reference=None)["pagamento"]
    assert payment["codice"] == "MP05"
    assert payment["dettagli"] == "Bonifico IBAN IT60X0542811101000000123456"


def test_the_totals_block_appears_only_when_there_is_vat() -> None:
    """A flat-rate-regime invoice has no VAT, and the inherited layout printed no totals
    block at all: the payment table already carries the amount. An ordinary-regime invoice
    must still show its VAT. The stamp never brings the block back: nearly every
    flat-rate invoice above 77,47 EUR carries one, its declaration under the rule
    already states it, and a `2,00 €` row under the VAT row would read as charged to the
    customer when the total does not include it."""
    assert build_scope(_export(), reference=None)["fattura"]["mostra_totali"] is False
    assert (
        build_scope(_export(stamp_duty="2.00"), reference=None)["fattura"]["mostra_totali"] is False
    )
    assert build_scope(_export(vat_amount="22.00"), reference=None)["fattura"]["mostra_totali"]


# --- the rendered document ----------------------------------------------------------

needs_toolchain = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in ("pandoc", "typst", "pdftotext")),
    reason="pandoc, typst and pdftotext live in the API image (Dockerfile.api)",
)


def _text(pdf: bytes) -> str:
    out = subprocess.run(
        ["pdftotext", "-layout", "-", "-"], input=pdf, capture_output=True, check=True
    )
    return " ".join(out.stdout.decode("utf-8", errors="replace").split())


@needs_toolchain
def test_the_invoice_pdf_reads_like_the_inherited_register() -> None:
    # With the stamp a real forfettario invoice above 77,47 EUR carries: the register
    # printed no totals block on those either, and the stamp is stated under the rule.
    _, pdf = render_invoice_pdf(_export(stamp_duty="2.00"), reference=None, settings=SETTINGS)
    text = _text(pdf)
    # The header the invoice owns, not the one offers and time reports share.
    assert "Identificativo fiscale ai fini IVA: IT01234567890" in text
    assert "Codice fiscale: RSSMRA80A01H501U" in text
    assert "Regime fiscale: RF19 Regime forfettario" in text
    assert "Codice destinatario: 1234567" in text
    assert "contatti:" not in text
    # The body, section by section.
    assert "TD01 fattura | Numero: 2026/7 | Data: 11-08-2026" in text
    assert "Acme S.r.l. | IVA: IT12345678901 | CF: 12345678901" in text
    assert "Consulenza Remote Console N2.2 1.760,00 €" in text
    assert "MP05 Bonifico IBAN IT60X0542811101000000123456 30-09-2026 1.760,00 €" in text
    assert "Thank you!" in text
    assert "mario@example.com" in text
    assert "Imponibile" not in text
    assert PROFORMA_DECLARATION not in text
    # The declarations the law wants on a forfettario invoice stay, small, under the rule.
    assert "art. 1 c. 54-89 L. 190/2014" in text
    assert "Imposta di bollo di 2,00 € assolta in modo virtuale" in text


@needs_toolchain
def test_the_proforma_pdf_is_the_same_document_with_its_declaration() -> None:
    _, pdf = render_invoice_pdf(_export(), reference="PROV-2026-0001", settings=SETTINGS)
    text = _text(pdf)
    assert PROFORMA_DECLARATION in text
    assert "Fattura proforma | Numero: PROV-2026-0001 | Data: 11-08-2026" in text
    assert "TD01" not in text
    assert "Thank you!" in text


@needs_toolchain
def test_an_ordinary_invoice_still_shows_its_vat() -> None:
    lines = (_line("Licenza", "200.00", rate="22.00", vat_nature=None),)
    _, pdf = render_invoice_pdf(
        _export(
            lines=lines,
            vat_amount="44.00",
            tax=FLAT_RATE.model_copy(update={"regime_code": "RF01"}),
        ),
        reference=None,
        settings=SETTINGS,
    )
    text = _text(pdf)
    assert "Licenza 22% 200,00 €" in text
    assert "Imponibile 200,00 €" in text
    assert "Imposta 44,00 €" in text
    assert "Totale documento 244,00 €" in text
    assert "Regime fiscale: RF01 Regime ordinario" in text


def test_the_order_reference_reads_as_one_line_with_the_parts_it_has() -> None:
    """REB-693: the number, then each part the order has, the date spelled like every
    other date on the document; nothing at all without a number, so the template's
    `{{#if}}` prints no label with an empty value after it."""
    whole = build_scope(
        _export(
            order={
                "purchase_order_number": "ODA-14",
                "purchase_order_date": date(2026, 10, 9),
                "purchase_order_tender_code": "Z0A1B2C3D4",
                "purchase_order_project_code": "J41E22000110005",
            }
        ),
        reference=None,
    )
    assert whole["fattura"]["riferimento_ordine"] == (
        "n. ODA-14 del 09-10-2026, CIG Z0A1B2C3D4, CUP J41E22000110005"
    )
    number = build_scope(_export(order={"purchase_order_number": "8"}), reference=None)
    assert number["fattura"]["riferimento_ordine"] == "n. 8"
    tender_code = build_scope(
        _export(order={"purchase_order_number": "8", "purchase_order_tender_code": "Z0A1B2C3D4"}),
        reference=None,
    )
    assert tender_code["fattura"]["riferimento_ordine"] == "n. 8, CIG Z0A1B2C3D4"
    assert build_scope(_export(), reference=None)["fattura"]["riferimento_ordine"] == ""
