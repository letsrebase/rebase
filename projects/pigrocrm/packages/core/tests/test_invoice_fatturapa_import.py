"""The FatturaPA FPR12 import adapter (REB-363), proven two ways: against real,
schema-valid documents (this project's own exporter, cross-checked against the
vendored FPR12 XSD) for the end-to-end shape, and against small hand-built
documents for the mapping edge cases a single fixture cannot exercise without
becoming several near-duplicate multi-hundred-line files.

No database, no session, no container anywhere below: `detect`/`parse` read bytes
and return a `ParsedInvoice`, which is exactly what the design record
(`docs/superpowers/specs/2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md`
§5 item 1) asked this issue to prove.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from pigrocrm.core.invoices.fatturapa_import import (
    InvoicePaFormatError,
    detect,
    invoice_pa_fpr_12_adapter,
    parse,
)
from pigrocrm.core.invoices.import_adapter import InvoiceFormatAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


CONSULTING = "fpr-12-march-consulting.xml"
LOTTO = "fpr-12-batch-due-invoices.xml"
UNRELATED = "unrelated-well-formed.xml"


# --- the adapter object itself conforms to the ported protocol --------------------


def test_the_concrete_adapter_conforms_to_the_protocol() -> None:
    assert isinstance(invoice_pa_fpr_12_adapter, InvoiceFormatAdapter)
    assert invoice_pa_fpr_12_adapter.id == "FPR12"


# --- detect() is total: never raises, whatever the bytes --------------------------


def test_detect_claims_a_real_fpr12_document() -> None:
    assert detect(_fixture(CONSULTING)) is True


def test_detect_claims_the_lotto_batch_too() -> None:
    assert detect(_fixture(LOTTO)) is True


def test_detect_rejects_an_unrelated_well_formed_xml_document() -> None:
    assert detect(_fixture(UNRELATED)) is False


def test_detect_rejects_a_document_transmitted_under_a_different_format_code() -> None:
    """Otherwise-identical, fully valid document with both occurrences of the
    format code changed -- proves `detect` rejects on the format code
    specifically, not on some incidental structural difference."""
    original = _fixture(CONSULTING).decode("utf-8")
    as_fpa12 = original.replace("FPR12", "FPA12").encode("utf-8")
    assert detect(as_fpa12) is False


def test_detect_rejects_non_xml_bytes_without_raising() -> None:
    assert detect(b"not xml at all, just garbage \x00\x01\x02") is False


def test_detect_rejects_empty_bytes_without_raising() -> None:
    assert detect(b"") is False


def test_detect_rejects_to_root_with_no_namespace_or_prefix_that_is_not_invoice_pa() -> None:
    assert detect(b"<Ordine><Numero>1</Numero></Ordine>") is False


# --- parse() maps the real fixture into a complete invoice, no human input --------


def test_parse_maps_the_real_fixture_into_a_complete_parsed_invoice() -> None:
    [invoice] = parse(_fixture(CONSULTING))

    assert invoice.number == "2026/6"
    assert invoice.issue_date.isoformat() == "2026-03-15"
    assert invoice.sdi_document_type == "fee_note"  # TD06
    assert invoice.currency == "EUR"

    assert invoice.supplier.legal_name == "Chiara Bianchi"
    assert invoice.supplier.vat_number == "IT01234567890"
    assert invoice.supplier.tax_code == "BNCCHR85M41H501Z"
    assert invoice.supplier.city == "Milano"
    assert invoice.supplier.country == "IT"

    assert invoice.customer.legal_name == "Esempio Servizi S.r.l."
    assert invoice.customer.vat_number == "IT09876543210"
    assert invoice.customer.city == "Roma"

    assert len(invoice.lines) == 1
    line = invoice.lines[0]
    assert line.description == "Consulenza professionale - marzo 2026"
    assert line.quantity == Decimal("5.000000")
    assert line.unit_price == Decimal("200.000000")
    assert line.total_price == Decimal("1000.00")
    assert line.vat_rate == Decimal("0.00")

    assert len(invoice.recaps) == 1
    recap = invoice.recaps[0]
    assert recap.vat_nature == "N2.2"
    assert recap.taxable_amount == Decimal("1000.00")
    assert recap.vat_amount == Decimal("0.00")
    assert "regime forfettario" in (recap.legal_reference or "")

    # Sums of the tax-summary blocks, matching the sole block here exactly.
    assert invoice.taxable_amount == Decimal("1000.00")
    assert invoice.vat_amount == Decimal("0.00")
    assert invoice.total == Decimal("1000.00")

    # Stamp duty survives; this fixture's fiscal profile has no social-security
    # fund, so the list is empty rather than defaulted to some placeholder.
    assert invoice.stamp_duty == Decimal("2.00")
    assert invoice.social_security_pension_fund == []

    # The due date comes straight from the document, for every instalment.
    assert len(invoice.payment_terms) == 1
    payment_block = invoice.payment_terms[0]
    assert payment_block.payment_terms == "TP02"
    assert len(payment_block.rate) == 1
    installment = payment_block.rate[0]
    assert installment.due_date.isoformat() == "2026-04-14"
    assert installment.due_date_origin == "document"
    assert installment.amount == Decimal("1000.00")
    assert installment.payment_method == "MP05"
    assert installment.iban == "IT60X0542811101000000123456"

    assert invoice.transmission.send_sequential


# --- the lotto batch: one file, several invoices, none dropped --------------------


def test_parse_handles_a_lotto_batch_two_bodies_produce_two_distinct_invoices() -> None:
    invoices = parse(_fixture(LOTTO))
    assert len(invoices) == 2
    assert [invoice.number for invoice in invoices] == ["2026/6", "2026/7"]
    assert [invoice.total for invoice in invoices] == [Decimal("1000.00"), Decimal("600.00")]

    # A lotto batch shares one FatturaElettronicaHeader: every invoice in it
    # carries the same supplier, customer and transmission -- exactly the
    # design record's own reason a batch-sourced invoice cannot claim a single
    # xml_document_id for itself (§5 item 3).
    first, second = invoices
    assert first.supplier == second.supplier
    assert first.customer == second.customer
    assert first.transmission == second.transmission

    # The second invoice's fiscal profile carried no stamp duty (`stamp_duty=0` at
    # generation), so it is genuinely absent, not a copy of the first's.
    assert first.stamp_duty == Decimal("2.00")
    assert second.stamp_duty is None


def test_the_adapter_object_parses_the_lotto_batch_identically_to_the_module_function() -> None:
    assert invoice_pa_fpr_12_adapter.detect(_fixture(LOTTO)) is True
    invoices = invoice_pa_fpr_12_adapter.parse(_fixture(LOTTO))
    assert [invoice.number for invoice in invoices] == ["2026/6", "2026/7"]


# --- parse() raises rather than guessing, on content detect never claims ----------


def test_parse_raises_on_content_that_is_not_to_well_formed_invoice_pa_document() -> None:
    with pytest.raises(InvoicePaFormatError):
        parse(_fixture(UNRELATED))


def test_parse_raises_on_garbage_bytes() -> None:
    with pytest.raises(InvoicePaFormatError):
        parse(b"not xml at all")


# --- synthetic minimal documents: the mapping edge cases one real fixture cannot
# exercise without becoming several near-duplicate multi-hundred-line files. Each
# builds only the elements this adapter reads, which is exactly as valid an input
# to it as one that came from a full real invoice. ---------------------------------

_NAMESPACE = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"

_DEFAULT_BUYER_ID = (
    "<IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>09876543210</IdCodice></IdFiscaleIVA>"
)
_DEFAULT_RECAP = (
    "<DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA>"
    "<ImponibileImporto>100.00</ImponibileImporto><Imposta>22.00</Imposta></DatiRiepilogo>"
)
_DEFAULT_LINE = (
    "<DettaglioLinee><Descrizione>Consulenza</Descrizione>"
    "<PrezzoUnitario>100.00</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
    "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
)
_DEFAULT_PAYMENT = (
    "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
    "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
    "<DataScadenzaPagamento>2026-04-14</DataScadenzaPagamento>"
    "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
)


def _invoice_xml(
    *,
    buyer_identifiers: str = _DEFAULT_BUYER_ID,
    sdi_document_type: str = "TD01",
    amount_total_document: str | None = "122.00",
    lines: str = _DEFAULT_LINE,
    recaps: str = _DEFAULT_RECAP,
    data_stamp_duty: str = "",
    data_pension_fund: str = "",
    data_payment: str = _DEFAULT_PAYMENT,
) -> bytes:
    """A minimal, well-formed `FatturaElettronica` document carrying only the
    elements this adapter reads. Uses a default (unprefixed) namespace on the
    root, deliberately different from the `p:`-prefixed style the project's own
    exporter emits, to exercise the same namespace-agnostic tree walk against
    the other style real senders use.
    """
    total = (
        f"<ImportoTotaleDocumento>{amount_total_document}</ImportoTotaleDocumento>"
        if amount_total_document is not None
        else ""
    )
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<FatturaElettronica xmlns="{_NAMESPACE}" versione="FPR12">
  <FatturaElettronicaHeader>
    <DatiTrasmissione>
      <IdTrasmittente><IdPaese>IT</IdPaese><IdCodice>11122233344</IdCodice></IdTrasmittente>
      <ProgressivoInvio>00001</ProgressivoInvio>
      <FormatoTrasmissione>FPR12</FormatoTrasmissione>
    </DatiTrasmissione>
    <CedentePrestatore>
      <DatiAnagrafici>
        <IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>01234567890</IdCodice></IdFiscaleIVA>
        <Anagrafica><Denominazione>Fornitore Test S.r.l.</Denominazione></Anagrafica>
      </DatiAnagrafici>
      <Sede><Indirizzo>Via Test 1</Indirizzo><CAP>00100</CAP><Comune>Roma</Comune></Sede>
    </CedentePrestatore>
    <CessionarioCommittente>
      <DatiAnagrafici>
        {buyer_identifiers}
        <Anagrafica><Denominazione>Cliente Test S.p.A.</Denominazione></Anagrafica>
      </DatiAnagrafici>
      <Sede><Indirizzo>Via Cliente 2</Indirizzo><CAP>00200</CAP><Comune>Milano</Comune></Sede>
    </CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <TipoDocumento>{sdi_document_type}</TipoDocumento>
        <Divisa>EUR</Divisa>
        <Data>2026-03-15</Data>
        <Numero>1/2026</Numero>
        {data_stamp_duty}
        {data_pension_fund}
        {total}
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      {lines}
      {recaps}
    </DatiBeniServizi>
    {data_payment}
  </FatturaElettronicaBody>
</FatturaElettronica>
"""
    return xml.encode("utf-8")


def test_the_common_case_to_company_customer_identified_by_id_tax_vat() -> None:
    [invoice] = parse(_invoice_xml())
    assert invoice.customer.vat_number == "IT09876543210"
    assert invoice.customer.tax_code is None


def test_an_individual_customer_identified_only_by_tax_code() -> None:
    xml = _invoice_xml(buyer_identifiers="<CodiceFiscale>RSSMRA80A01H501U</CodiceFiscale>")
    [invoice] = parse(xml)
    assert invoice.customer.vat_number is None
    assert invoice.customer.tax_code == "RSSMRA80A01H501U"


def test_to_customer_with_neither_id_tax_vat_nor_tax_code_is_rejected() -> None:
    xml = _invoice_xml(buyer_identifiers="")
    with pytest.raises(InvoicePaFormatError, match="neither IdFiscaleIVA nor CodiceFiscale"):
        parse(xml)


def test_an_unrecognised_sdi_document_type_is_rejected_rather_than_guessed() -> None:
    xml = _invoice_xml(sdi_document_type="TD99")
    with pytest.raises(InvoicePaFormatError, match="TipoDocumento"):
        parse(xml)


def test_to_document_missing_amount_total_document_is_rejected() -> None:
    xml = _invoice_xml(amount_total_document=None)
    with pytest.raises(InvoicePaFormatError, match="ImportoTotaleDocumento"):
        parse(xml)


def test_to_document_with_no_detail_lines_is_rejected_rather_than_producing_no_lines() -> None:
    with pytest.raises(InvoicePaFormatError, match="DettaglioLinee"):
        parse(_invoice_xml(lines=""))


def test_to_document_with_no_data_recap_is_rejected_rather_than_producing_zero_totals() -> None:
    with pytest.raises(InvoicePaFormatError, match="DatiRiepilogo"):
        parse(_invoice_xml(recaps=""))


def test_a_non_iso_issue_date_is_rejected_as_fatturapa_format_error_not_a_bare_value_error() -> (
    None
):
    """A document detect() still claims (the format-code fields are untouched) but
    whose `Data` is not an ISO date must fail with this module's own exception,
    not leak `ValueError` straight from `date.fromisoformat` -- a caller matching
    on `InvoicePaFormatError` to turn a malformed document into a review outcome
    would otherwise see an unhandled exception instead.
    """
    xml = (
        _fixture(CONSULTING)
        .decode("utf-8")
        .replace("<Data>2026-03-15</Data>", "<Data>15/03/2026</Data>")
    )
    with pytest.raises(InvoicePaFormatError, match="ISO date"):
        parse(xml.encode("utf-8"))


def test_to_not_integer_days_payment_terms_is_rejected_as_fatturapa_format_error() -> None:
    xml = _invoice_xml(
        data_payment=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataRiferimentoTerminiPagamento>2026-03-15</DataRiferimentoTerminiPagamento>"
            "<GiorniTerminiPagamento>trenta</GiorniTerminiPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    with pytest.raises(InvoicePaFormatError, match="not an integer"):
        parse(xml)


@pytest.mark.parametrize(
    "malformed",
    ["NaN", "Infinity", "1E999999999", "1e3", "1_000", "\u0661\u0662.00", "", ".5", "1,5"],
)
def test_a_malformed_decimal_is_rejected_as_fatturapa_format_error(malformed: str) -> None:
    """`Decimal()` accepts `NaN`, an exponent form, underscores and non-ASCII digits;
    `1E999999999` then raises `InvalidOperation` out of `quantize`, a bare 500 for a
    caller that only handles `InvoicePaFormatError`. The lexical form is checked first."""
    line_xml = (
        "<DettaglioLinee><Descrizione>Consulenza</Descrizione>"
        f"<PrezzoUnitario>{malformed}</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
        "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
    )
    with pytest.raises(InvoicePaFormatError):
        parse(_invoice_xml(lines=line_xml))


@pytest.mark.parametrize("malformed", ["1_0", "\u0663\u0660", "1E2", "99999999999"])
def test_to_malformed_days_payment_terms_is_rejected_as_fatturapa_format_error(
    malformed: str,
) -> None:
    xml = _invoice_xml(
        data_payment=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataRiferimentoTerminiPagamento>2026-03-15</DataRiferimentoTerminiPagamento>"
            f"<GiorniTerminiPagamento>{malformed}</GiorniTerminiPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    with pytest.raises(InvoicePaFormatError):
        parse(xml)


def test_an_instalment_with_neither_explicit_nor_computable_relative_terms_is_rejected() -> None:
    xml = _invoice_xml(
        data_payment=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    with pytest.raises(InvoicePaFormatError, match="due date"):
        parse(xml)


def test_an_instalment_expressed_as_relative_terms_computes_a_due_date() -> None:
    xml = _invoice_xml(
        data_payment=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataRiferimentoTerminiPagamento>2026-03-15</DataRiferimentoTerminiPagamento>"
            "<GiorniTerminiPagamento>30</GiorniTerminiPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    [invoice] = parse(xml)
    [payment_block] = invoice.payment_terms
    [installment] = payment_block.rate
    assert installment.due_date.isoformat() == "2026-04-14"
    assert installment.due_date_origin == "calculated"


def test_every_payment_terms_block_keeps_its_own_condition_code_and_instalments() -> None:
    xml = _invoice_xml(
        data_payment=(
            "<DatiPagamento><CondizioniPagamento>TP01</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataScadenzaPagamento>2026-04-01</DataScadenzaPagamento>"
            "<ImportoPagamento>61.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP08</ModalitaPagamento>"
            "<DataScadenzaPagamento>2026-05-01</DataScadenzaPagamento>"
            "<ImportoPagamento>61.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    [invoice] = parse(xml)
    assert [t.payment_terms for t in invoice.payment_terms] == ["TP01", "TP02"]
    assert [t.rate[0].payment_method for t in invoice.payment_terms] == ["MP05", "MP08"]


def test_stamp_duty_and_social_charge_are_absent_not_defaulted_when_the_document_carries_none() -> (
    None
):
    [invoice] = parse(_invoice_xml())
    assert invoice.stamp_duty is None
    assert invoice.social_security_pension_fund == []


def test_stamp_duty_and_the_social_charge_survive_when_the_document_carries_them() -> None:
    xml = _invoice_xml(
        data_stamp_duty="<DatiBollo><ImportoBollo>2.00</ImportoBollo></DatiBollo>",
        data_pension_fund=(
            "<DatiCassaPrevidenziale><TipoCassa>TC22</TipoCassa><AlCassa>4.00</AlCassa>"
            "<ImportoContributoCassa>4.00</ImportoContributoCassa>"
            "<ImponibileCassa>100.00</ImponibileCassa><AliquotaIVA>22.00</AliquotaIVA>"
            "</DatiCassaPrevidenziale>"
        ),
    )
    [invoice] = parse(xml)
    assert invoice.stamp_duty == Decimal("2.00")
    [pension_fund] = invoice.social_security_pension_fund
    assert pension_fund.pension_fund_type == "TC22"
    assert pension_fund.pension_fund_rate == Decimal("4.00")
    assert pension_fund.pension_fund_contribution_amount == Decimal("4.00")
    assert pension_fund.pension_fund_taxable_amount == Decimal("100.00")


def test_taxable_amount_and_vat_amount_sum_every_tax_summary_block_for_to_mixed_rate_invoice() -> (
    None
):
    recaps = (
        "<DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA>"
        "<ImponibileImporto>100.00</ImponibileImporto><Imposta>22.00</Imposta></DatiRiepilogo>"
        "<DatiRiepilogo><AliquotaIVA>10.00</AliquotaIVA>"
        "<ImponibileImporto>50.00</ImponibileImporto><Imposta>5.00</Imposta></DatiRiepilogo>"
    )
    xml = _invoice_xml(recaps=recaps, amount_total_document="177.00")
    [invoice] = parse(xml)
    assert invoice.taxable_amount == Decimal("150.00")
    assert invoice.vat_amount == Decimal("27.00")
    assert invoice.total == Decimal("177.00")
    assert len(invoice.recaps) == 2


def test_quantity_defaults_to_one_when_the_document_omits_it() -> None:
    line_xml = (
        "<DettaglioLinee><Descrizione>Consulenza a corpo</Descrizione>"
        "<PrezzoUnitario>100.00</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
        "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
    )
    [invoice] = parse(_invoice_xml(lines=line_xml))
    assert invoice.lines[0].quantity == Decimal("1")


def test_a_batch_where_one_body_is_malformed_raises_rather_than_returning_partial_results() -> None:
    """A caller must not get one good invoice and one silently dropped -- the
    same reasoning `parse`'s own docstring states for a single-invoice document.
    """
    good = _invoice_xml().decode("utf-8")
    body_start = good.index("<FatturaElettronicaBody>")
    body_end = good.index("</FatturaElettronicaBody>") + len("</FatturaElettronicaBody>")
    second_body = good[body_start:body_end].replace("TD01", "TD99")
    batch = good[:body_end] + second_body + good[body_end:]
    with pytest.raises(InvoicePaFormatError, match="TipoDocumento"):
        parse(batch.encode("utf-8"))


def test_the_withholding_and_the_split_payment_of_a_document_are_read() -> None:
    """REB-994: what the customer keeps or pays to the tax office itself is part of what an
    invoice we issued is owed, so the parser reads the `DatiRitenuta` amounts and the `S` of
    `EsigibilitaIVA`; a document with neither reads as none."""
    plain = parse(_fixture(CONSULTING))[0]
    assert (plain.withholding_amount, plain.split_payment) == (Decimal("0.00"), False)

    xml = _fixture(CONSULTING).decode("utf-8")
    with_withholding = xml.replace(
        "<Numero>2026/6</Numero>",
        "<Numero>2026/6</Numero><DatiRitenuta><TipoRitenuta>RT01</TipoRitenuta>"
        "<ImportoRitenuta>200.00</ImportoRitenuta><AliquotaRitenuta>20.00</AliquotaRitenuta>"
        "<CausalePagamento>A</CausalePagamento></DatiRitenuta>"
        "<DatiRitenuta><TipoRitenuta>RT04</TipoRitenuta><ImportoRitenuta>10.50</ImportoRitenuta>"
        "<AliquotaRitenuta>8.50</AliquotaRitenuta><CausalePagamento>ZO</CausalePagamento>"
        "</DatiRitenuta>",
    )
    assert with_withholding != xml
    assert parse(with_withholding.encode("utf-8"))[0].withholding_amount == Decimal("210.50")

    split = xml.replace("<EsigibilitaIVA>I</EsigibilitaIVA>", "<EsigibilitaIVA>S</EsigibilitaIVA>")
    assert split != xml
    assert parse(split.encode("utf-8"))[0].split_payment is True
