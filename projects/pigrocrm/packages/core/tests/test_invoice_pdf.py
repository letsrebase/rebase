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

EMITTENTE = PartySnapshot(
    ragione_sociale="Studio Rossi",
    partita_iva="01234567890",
    codice_fiscale="RSSMRA80A01H501U",
    codice_sdi="1234567",
    pec="studiorossi@pec.it",
    indirizzo="Via Roma 1",
    cap="20053",
    comune="Milano",
    provincia="MI",
    nazione="IT",
    email="mario@example.com",
    telefono="+39 02 1234567",
    sito_web="www.example.com",
)

CLIENTE = PartySnapshot(
    ragione_sociale="Acme S.r.l.",
    partita_iva="12345678901",
    codice_fiscale="12345678901",
    codice_sdi="ABCDEFG",
    pec="acme@pec.it",
    indirizzo="Corso Italia 5",
    cap="00100",
    comune="Roma",
    provincia="RM",
    nazione="IT",
    email=None,
    telefono=None,
    sito_web=None,
)

FORFETTARIO = FiscalSnapshot(
    codice_regime="RF19",
    aliquota_iva_default=Decimal("0.00"),
    natura_default="N2.2",
    riferimento_normativo="Operazione senza applicazione dell'IVA, art. 1 c. 54-89 L. 190/2014",
    applica_bollo=True,
    soglia_bollo=Decimal("77.47"),
    importo_bollo=Decimal("2.00"),
    condizioni_pagamento="TP02",
    modalita_pagamento="MP05",
    giorni_scadenza=30,
    iban="IT60X0542811101000000123456",
)


def _line(
    descrizione: str,
    prezzo: str,
    *,
    aliquota: str = "0.00",
    natura: str | None = "N2.2",
    numero: int = 1,
) -> InvoiceLineRead:
    return InvoiceLineRead(
        id=uuid4(),
        invoice_id=uuid4(),
        numero_linea=numero,
        descrizione=descrizione,
        quantita=Decimal("1.000000"),
        unita_misura=None,
        prezzo_unitario=Decimal(prezzo),
        sconto_percentuale=None,
        sconto_importo=None,
        prezzo_totale=Decimal(prezzo),
        aliquota_iva=Decimal(aliquota),
        natura=natura,
        riferimento_normativo=FORFETTARIO.riferimento_normativo if natura else None,
    )


def _export(
    *,
    cliente: PartySnapshot = CLIENTE,
    fiscale: FiscalSnapshot = FORFETTARIO,
    righe: tuple[InvoiceLineRead, ...] | None = None,
    imposta: str = "0.00",
    bollo: str = "0.00",
    competenza: tuple[date, date] | None = None,
    ordine: dict[str, object] | None = None,
) -> InvoiceForExport:
    righe = righe or (_line("Consulenza Remote Console", "1760.00"),)
    imponibile = sum((r.prezzo_totale for r in righe), Decimal("0.00"))
    return InvoiceForExport(
        anno=2026,
        numero=7,
        data_emissione=date(2026, 8, 11),
        data_scadenza=date(2026, 9, 30),
        tipo_documento="TD01",
        divisa="EUR",
        imponibile=imponibile,
        imposta=Decimal(imposta),
        bollo=Decimal(bollo),
        totale=imponibile + Decimal(imposta),
        causale="Consulenza",
        competenza_da=competenza[0] if competenza else None,
        competenza_a=competenza[1] if competenza else None,
        **(ordine or {}),
        snapshot=InvoiceSnapshot(versione=1, emittente=EMITTENTE, cliente=cliente, fiscale=fiscale),
        righe=righe,
    )


# --- the scope: every value already formatted the way the inherited layout prints it ---


def test_amounts_read_the_italian_way_with_the_currency() -> None:
    scope = build_scope(_export(), riferimento=None)
    assert scope["fattura"]["totale"] == "1.760,00 €"
    assert scope["righe"][0]["prezzo_totale"] == "1.760,00 €"


def test_dates_read_dd_mm_yyyy_throughout_the_document() -> None:
    scope = build_scope(_export(competenza=(date(2026, 8, 1), date(2026, 8, 31))), riferimento=None)
    assert scope["fattura"]["data"] == "11-08-2026"
    assert scope["fattura"]["data_scadenza"] == "30-09-2026"
    assert scope["fattura"]["periodo_competenza"] == "dal 01-08-2026 al 31-08-2026"


def test_the_first_line_names_the_fiscal_document_type_and_a_proforma_names_none() -> None:
    """`TD01 fattura` is what the inherited layout prints; a proforma is not a fiscal
    document, so it gets no TD code, and it is the only one that carries the
    declaration the template wraps in a condition."""
    fattura = build_scope(_export(), riferimento=None)["fattura"]
    assert fattura["etichetta"] == "TD01 fattura"
    assert fattura["numero"] == "2026/7"
    assert fattura["dichiarazione_proforma"] == ""

    proforma = build_scope(_export(), riferimento="PROV-2026-0001")["fattura"]
    assert proforma["etichetta"] == "Fattura proforma"
    assert proforma["numero"] == "PROV-2026-0001"
    assert proforma["dichiarazione_proforma"] == PROFORMA_DECLARATION


def test_the_customer_reads_on_two_lines_separated_by_bars() -> None:
    cliente = build_scope(_export(), riferimento=None)["cliente"]
    assert cliente["riga_identita"] == "Acme S.r.l. | IVA: IT12345678901 | CF: 12345678901"
    assert (
        cliente["riga_recapiti"]
        == "Corso Italia 5, 00100 Roma (RM) IT | PEC: acme@pec.it | Codice destinatario: ABCDEFG"
    )


def test_a_customer_missing_a_field_loses_the_segment_not_the_label() -> None:
    """A private customer has no VAT id and a foreign one has no SDI code: the segment
    goes, and no `IVA:` is left standing in front of nothing. The VAT id prefix is the
    party's own country, the way `IdPaese` precedes `IdCodice` in the XML, where the
    previous system printed `IT` for a London customer."""
    estero = CLIENTE.model_copy(
        update={
            "partita_iva": "123456789",
            "codice_fiscale": None,
            "codice_sdi": None,
            "pec": None,
            "indirizzo": "1 Old Street",
            "cap": "EC1V 9HL",
            "comune": "London",
            "provincia": "",
            "nazione": "GB",
        }
    )
    cliente = build_scope(_export(cliente=estero), riferimento=None)["cliente"]
    assert cliente["riga_identita"] == "Acme S.r.l. | IVA: GB123456789"
    assert cliente["riga_recapiti"] == "1 Old Street, EC1V 9HL London GB"


def test_the_emitter_block_carries_what_the_inherited_header_printed() -> None:
    scope = build_scope(_export(), riferimento=None)
    assert scope["emittente"]["identificativo_iva"] == "IT01234567890"
    # An emitter row with no country yet reads `IT`, the same fallback the XML applies.
    senza_nazione = EMITTENTE.model_copy(update={"nazione": ""})
    export = _export().model_copy(
        update={
            "snapshot": InvoiceSnapshot(
                versione=1, emittente=senza_nazione, cliente=CLIENTE, fiscale=FORFETTARIO
            )
        }
    )
    assert (
        build_scope(export, riferimento=None)["emittente"]["identificativo_iva"] == "IT01234567890"
    )
    assert scope["emittente"]["indirizzo_display"] == "Via Roma 1, 20053 Milano (MI) IT"
    assert scope["fiscale"]["regime_display"] == "RF19 Regime forfettario"


def test_the_vat_column_shows_the_natura_when_there_is_no_rate_and_the_rate_otherwise() -> None:
    righe = (
        _line("Consulenza", "100.00"),
        _line("Licenza", "200.00", aliquota="22.00", natura=None, numero=2),
    )
    scope = build_scope(_export(righe=righe, imposta="44.00"), riferimento=None)
    assert [r["iva"] for r in scope["righe"]] == ["N2.2", "22%"]


def test_the_payment_row_carries_the_code_and_its_words() -> None:
    pagamento = build_scope(_export(), riferimento=None)["pagamento"]
    assert pagamento["codice"] == "MP05"
    assert pagamento["dettagli"] == "Bonifico IBAN IT60X0542811101000000123456"


def test_the_totals_block_appears_only_when_there_is_vat() -> None:
    """A forfettario invoice has no VAT, and the inherited layout printed no totals
    block at all: the payment table already carries the amount. An ordinario invoice
    must still show its VAT. The stamp never brings the block back: nearly every
    forfettario invoice above 77,47 EUR carries one, its declaration under the rule
    already states it, and a `2,00 €` row under Imposta would read as charged to the
    customer when the total does not include it."""
    assert build_scope(_export(), riferimento=None)["fattura"]["mostra_totali"] is False
    assert build_scope(_export(bollo="2.00"), riferimento=None)["fattura"]["mostra_totali"] is False
    assert build_scope(_export(imposta="22.00"), riferimento=None)["fattura"]["mostra_totali"]


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
    _, pdf = render_invoice_pdf(_export(bollo="2.00"), riferimento=None, settings=SETTINGS)
    testo = _text(pdf)
    # The header the invoice owns, not the one offers and time reports share.
    assert "Identificativo fiscale ai fini IVA: IT01234567890" in testo
    assert "Codice fiscale: RSSMRA80A01H501U" in testo
    assert "Regime fiscale: RF19 Regime forfettario" in testo
    assert "Codice destinatario: 1234567" in testo
    assert "contatti:" not in testo
    # The body, section by section.
    assert "TD01 fattura | Numero: 2026/7 | Data: 11-08-2026" in testo
    assert "Acme S.r.l. | IVA: IT12345678901 | CF: 12345678901" in testo
    assert "Consulenza Remote Console N2.2 1.760,00 €" in testo
    assert "MP05 Bonifico IBAN IT60X0542811101000000123456 30-09-2026 1.760,00 €" in testo
    assert "Thank you!" in testo
    assert "mario@example.com" in testo
    assert "Imponibile" not in testo
    assert PROFORMA_DECLARATION not in testo
    # The declarations the law wants on a forfettario invoice stay, small, under the rule.
    assert "art. 1 c. 54-89 L. 190/2014" in testo
    assert "Imposta di bollo di 2,00 € assolta in modo virtuale" in testo


@needs_toolchain
def test_the_proforma_pdf_is_the_same_document_with_its_declaration() -> None:
    _, pdf = render_invoice_pdf(_export(), riferimento="PROV-2026-0001", settings=SETTINGS)
    testo = _text(pdf)
    assert PROFORMA_DECLARATION in testo
    assert "Fattura proforma | Numero: PROV-2026-0001 | Data: 11-08-2026" in testo
    assert "TD01" not in testo
    assert "Thank you!" in testo


@needs_toolchain
def test_an_ordinario_invoice_still_shows_its_vat() -> None:
    righe = (_line("Licenza", "200.00", aliquota="22.00", natura=None),)
    _, pdf = render_invoice_pdf(
        _export(
            righe=righe,
            imposta="44.00",
            fiscale=FORFETTARIO.model_copy(update={"codice_regime": "RF01"}),
        ),
        riferimento=None,
        settings=SETTINGS,
    )
    testo = _text(pdf)
    assert "Licenza 22% 200,00 €" in testo
    assert "Imponibile 200,00 €" in testo
    assert "Imposta 44,00 €" in testo
    assert "Totale documento 244,00 €" in testo
    assert "Regime fiscale: RF01 Regime ordinario" in testo


def test_the_order_reference_reads_as_one_line_with_the_parts_it_has() -> None:
    """REB-693: the number, then each part the order has, the date spelled like every
    other date on the document; nothing at all without a number, so the template's
    `{{#if}}` prints no label with an empty value after it."""
    whole = build_scope(
        _export(
            ordine={
                "ordine_numero": "ODA-14",
                "ordine_data": date(2026, 10, 9),
                "ordine_cig": "Z0A1B2C3D4",
                "ordine_cup": "J41E22000110005",
            }
        ),
        riferimento=None,
    )
    assert whole["fattura"]["riferimento_ordine"] == (
        "n. ODA-14 del 09-10-2026, CIG Z0A1B2C3D4, CUP J41E22000110005"
    )
    numero = build_scope(_export(ordine={"ordine_numero": "8"}), riferimento=None)
    assert numero["fattura"]["riferimento_ordine"] == "n. 8"
    cig = build_scope(
        _export(ordine={"ordine_numero": "8", "ordine_cig": "Z0A1B2C3D4"}), riferimento=None
    )
    assert cig["fattura"]["riferimento_ordine"] == "n. 8, CIG Z0A1B2C3D4"
    assert build_scope(_export(), riferimento=None)["fattura"]["riferimento_ordine"] == ""
