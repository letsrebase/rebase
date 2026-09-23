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
    FatturaPaFormatError,
    detect,
    fattura_pa_fpr12_adapter,
    parse,
)
from pigrocrm.core.invoices.import_adapter import InvoiceFormatAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "fatturapa"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


CONSULENZA = "fpr12-consulenza-marzo.xml"
LOTTO = "fpr12-lotto-due-fatture.xml"
UNRELATED = "unrelated-well-formed.xml"


# --- the adapter object itself conforms to the ported protocol --------------------


def test_the_concrete_adapter_conforms_to_the_protocol() -> None:
    assert isinstance(fattura_pa_fpr12_adapter, InvoiceFormatAdapter)
    assert fattura_pa_fpr12_adapter.id == "FPR12"


# --- detect() is total: never raises, whatever the bytes --------------------------


def test_detect_claims_a_real_fpr12_document() -> None:
    assert detect(_fixture(CONSULENZA)) is True


def test_detect_claims_the_lotto_batch_too() -> None:
    assert detect(_fixture(LOTTO)) is True


def test_detect_rejects_an_unrelated_well_formed_xml_document() -> None:
    assert detect(_fixture(UNRELATED)) is False


def test_detect_rejects_a_document_transmitted_under_a_different_format_code() -> None:
    """Otherwise-identical, fully valid document with both occurrences of the
    format code changed -- proves `detect` rejects on the format code
    specifically, not on some incidental structural difference."""
    original = _fixture(CONSULENZA).decode("utf-8")
    as_fpa12 = original.replace("FPR12", "FPA12").encode("utf-8")
    assert detect(as_fpa12) is False


def test_detect_rejects_non_xml_bytes_without_raising() -> None:
    assert detect(b"not xml at all, just garbage \x00\x01\x02") is False


def test_detect_rejects_empty_bytes_without_raising() -> None:
    assert detect(b"") is False


def test_detect_rejects_a_root_with_no_namespace_or_prefix_that_is_not_fattura_pa() -> None:
    assert detect(b"<Ordine><Numero>1</Numero></Ordine>") is False


# --- parse() maps the real fixture into a complete invoice, no human input --------


def test_parse_maps_the_real_fixture_into_a_complete_parsed_invoice() -> None:
    [invoice] = parse(_fixture(CONSULENZA))

    assert invoice.numero == "2026/6"
    assert invoice.data_emissione.isoformat() == "2026-03-15"
    assert invoice.tipo_documento == "parcella"  # TD06
    assert invoice.divisa == "EUR"

    assert invoice.fornitore.ragione_sociale == "Chiara Bianchi"
    assert invoice.fornitore.partita_iva == "IT01234567890"
    assert invoice.fornitore.codice_fiscale == "BNCCHR85M41H501Z"
    assert invoice.fornitore.comune == "Milano"
    assert invoice.fornitore.nazione == "IT"

    assert invoice.cliente.ragione_sociale == "Esempio Servizi S.r.l."
    assert invoice.cliente.partita_iva == "IT09876543210"
    assert invoice.cliente.comune == "Roma"

    assert len(invoice.righe) == 1
    riga = invoice.righe[0]
    assert riga.descrizione == "Consulenza professionale - marzo 2026"
    assert riga.quantita == Decimal("5.000000")
    assert riga.prezzo_unitario == Decimal("200.000000")
    assert riga.prezzo_totale == Decimal("1000.00")
    assert riga.aliquota_iva == Decimal("0.00")

    assert len(invoice.riepiloghi) == 1
    riepilogo = invoice.riepiloghi[0]
    assert riepilogo.natura == "N2.2"
    assert riepilogo.imponibile == Decimal("1000.00")
    assert riepilogo.imposta == Decimal("0.00")
    assert "regime forfettario" in (riepilogo.riferimento_normativo or "")

    # Sums of the tax-summary blocks, matching the sole block here exactly.
    assert invoice.imponibile == Decimal("1000.00")
    assert invoice.imposta == Decimal("0.00")
    assert invoice.totale == Decimal("1000.00")

    # Stamp duty survives; this fixture's fiscal profile has no social-security
    # fund, so the list is empty rather than defaulted to some placeholder.
    assert invoice.bollo == Decimal("2.00")
    assert invoice.cassa_previdenziale == []

    # The due date comes straight from the document, for every instalment.
    assert len(invoice.termini_pagamento) == 1
    termini = invoice.termini_pagamento[0]
    assert termini.condizioni_pagamento == "TP02"
    assert len(termini.rate) == 1
    rata = termini.rate[0]
    assert rata.data_scadenza.isoformat() == "2026-04-14"
    assert rata.origine_scadenza == "documento"
    assert rata.importo == Decimal("1000.00")
    assert rata.modalita_pagamento == "MP05"
    assert rata.iban == "IT60X0542811101000000123456"

    assert invoice.trasmissione.progressivo_invio


# --- the lotto batch: one file, several invoices, none dropped --------------------


def test_parse_handles_a_lotto_batch_two_bodies_produce_two_distinct_invoices() -> None:
    invoices = parse(_fixture(LOTTO))
    assert len(invoices) == 2
    assert [invoice.numero for invoice in invoices] == ["2026/6", "2026/7"]
    assert [invoice.totale for invoice in invoices] == [Decimal("1000.00"), Decimal("600.00")]

    # A lotto batch shares one FatturaElettronicaHeader: every invoice in it
    # carries the same supplier, customer and transmission -- exactly the
    # design record's own reason a batch-sourced invoice cannot claim a single
    # xml_document_id for itself (§5 item 3).
    first, second = invoices
    assert first.fornitore == second.fornitore
    assert first.cliente == second.cliente
    assert first.trasmissione == second.trasmissione

    # The second invoice's fiscal profile carried no stamp duty (`bollo=0` at
    # generation), so it is genuinely absent, not a copy of the first's.
    assert first.bollo == Decimal("2.00")
    assert second.bollo is None


def test_the_adapter_object_parses_the_lotto_batch_identically_to_the_module_function() -> None:
    assert fattura_pa_fpr12_adapter.detect(_fixture(LOTTO)) is True
    invoices = fattura_pa_fpr12_adapter.parse(_fixture(LOTTO))
    assert [invoice.numero for invoice in invoices] == ["2026/6", "2026/7"]


# --- parse() raises rather than guessing, on content detect never claims ----------


def test_parse_raises_on_content_that_is_not_a_well_formed_fattura_pa_document() -> None:
    with pytest.raises(FatturaPaFormatError):
        parse(_fixture(UNRELATED))


def test_parse_raises_on_garbage_bytes() -> None:
    with pytest.raises(FatturaPaFormatError):
        parse(b"not xml at all")


# --- synthetic minimal documents: the mapping edge cases one real fixture cannot
# exercise without becoming several near-duplicate multi-hundred-line files. Each
# builds only the elements this adapter reads, which is exactly as valid an input
# to it as one that came from a full real invoice. ---------------------------------

_NAMESPACE = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"

_DEFAULT_CESSIONARIO_ID = (
    "<IdFiscaleIVA><IdPaese>IT</IdPaese><IdCodice>09876543210</IdCodice></IdFiscaleIVA>"
)
_DEFAULT_RIEPILOGO = (
    "<DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA>"
    "<ImponibileImporto>100.00</ImponibileImporto><Imposta>22.00</Imposta></DatiRiepilogo>"
)
_DEFAULT_RIGA = (
    "<DettaglioLinee><Descrizione>Consulenza</Descrizione>"
    "<PrezzoUnitario>100.00</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
    "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
)
_DEFAULT_PAGAMENTO = (
    "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
    "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
    "<DataScadenzaPagamento>2026-04-14</DataScadenzaPagamento>"
    "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
)


def _fattura_xml(
    *,
    cessionario_identificativi: str = _DEFAULT_CESSIONARIO_ID,
    tipo_documento: str = "TD01",
    importo_totale_documento: str | None = "122.00",
    righe: str = _DEFAULT_RIGA,
    riepiloghi: str = _DEFAULT_RIEPILOGO,
    dati_bollo: str = "",
    dati_cassa: str = "",
    dati_pagamento: str = _DEFAULT_PAGAMENTO,
) -> bytes:
    """A minimal, well-formed `FatturaElettronica` document carrying only the
    elements this adapter reads. Uses a default (unprefixed) namespace on the
    root, deliberately different from the `p:`-prefixed style the project's own
    exporter emits, to exercise the same namespace-agnostic tree walk against
    the other style real senders use.
    """
    totale = (
        f"<ImportoTotaleDocumento>{importo_totale_documento}</ImportoTotaleDocumento>"
        if importo_totale_documento is not None
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
        {cessionario_identificativi}
        <Anagrafica><Denominazione>Cliente Test S.p.A.</Denominazione></Anagrafica>
      </DatiAnagrafici>
      <Sede><Indirizzo>Via Cliente 2</Indirizzo><CAP>00200</CAP><Comune>Milano</Comune></Sede>
    </CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <TipoDocumento>{tipo_documento}</TipoDocumento>
        <Divisa>EUR</Divisa>
        <Data>2026-03-15</Data>
        <Numero>1/2026</Numero>
        {dati_bollo}
        {dati_cassa}
        {totale}
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      {righe}
      {riepiloghi}
    </DatiBeniServizi>
    {dati_pagamento}
  </FatturaElettronicaBody>
</FatturaElettronica>
"""
    return xml.encode("utf-8")


def test_the_common_case_a_company_customer_identified_by_id_fiscale_iva() -> None:
    [invoice] = parse(_fattura_xml())
    assert invoice.cliente.partita_iva == "IT09876543210"
    assert invoice.cliente.codice_fiscale is None


def test_an_individual_customer_identified_only_by_codice_fiscale() -> None:
    xml = _fattura_xml(cessionario_identificativi="<CodiceFiscale>RSSMRA80A01H501U</CodiceFiscale>")
    [invoice] = parse(xml)
    assert invoice.cliente.partita_iva is None
    assert invoice.cliente.codice_fiscale == "RSSMRA80A01H501U"


def test_a_customer_with_neither_id_fiscale_iva_nor_codice_fiscale_is_rejected() -> None:
    xml = _fattura_xml(cessionario_identificativi="")
    with pytest.raises(FatturaPaFormatError, match="neither IdFiscaleIVA nor CodiceFiscale"):
        parse(xml)


def test_an_unrecognised_tipo_documento_is_rejected_rather_than_guessed() -> None:
    xml = _fattura_xml(tipo_documento="TD99")
    with pytest.raises(FatturaPaFormatError, match="TipoDocumento"):
        parse(xml)


def test_a_document_missing_importo_totale_documento_is_rejected() -> None:
    xml = _fattura_xml(importo_totale_documento=None)
    with pytest.raises(FatturaPaFormatError, match="ImportoTotaleDocumento"):
        parse(xml)


def test_a_document_with_no_dettaglio_linee_is_rejected_rather_than_producing_no_lines() -> None:
    with pytest.raises(FatturaPaFormatError, match="DettaglioLinee"):
        parse(_fattura_xml(righe=""))


def test_a_document_with_no_dati_riepilogo_is_rejected_rather_than_producing_zero_totals() -> None:
    with pytest.raises(FatturaPaFormatError, match="DatiRiepilogo"):
        parse(_fattura_xml(riepiloghi=""))


def test_a_non_iso_issue_date_is_rejected_as_fatturapa_format_error_not_a_bare_value_error() -> (
    None
):
    """A document detect() still claims (the format-code fields are untouched) but
    whose `Data` is not an ISO date must fail with this module's own exception,
    not leak `ValueError` straight from `date.fromisoformat` -- a caller matching
    on `FatturaPaFormatError` to turn a malformed document into a review outcome
    would otherwise see an unhandled exception instead.
    """
    xml = (
        _fixture(CONSULENZA)
        .decode("utf-8")
        .replace("<Data>2026-03-15</Data>", "<Data>15/03/2026</Data>")
    )
    with pytest.raises(FatturaPaFormatError, match="ISO date"):
        parse(xml.encode("utf-8"))


def test_a_non_integer_giorni_termini_pagamento_is_rejected_as_fatturapa_format_error() -> None:
    xml = _fattura_xml(
        dati_pagamento=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataRiferimentoTerminiPagamento>2026-03-15</DataRiferimentoTerminiPagamento>"
            "<GiorniTerminiPagamento>trenta</GiorniTerminiPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    with pytest.raises(FatturaPaFormatError, match="not an integer"):
        parse(xml)


def test_a_non_finite_decimal_is_rejected_as_fatturapa_format_error_not_a_pydantic_error() -> None:
    """`Decimal("NaN")`/`Decimal("Infinity")` parse without raising `InvalidOperation`;
    without an explicit finiteness check, only Pydantic's own `finite_number`
    validator would catch this, as a `pydantic_core.ValidationError` rather than
    this module's own exception."""
    riga = (
        "<DettaglioLinee><Descrizione>Consulenza</Descrizione>"
        "<PrezzoUnitario>NaN</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
        "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
    )
    with pytest.raises(FatturaPaFormatError, match="not a finite decimal"):
        parse(_fattura_xml(righe=riga))


def test_an_instalment_with_neither_explicit_nor_computable_relative_terms_is_rejected() -> None:
    xml = _fattura_xml(
        dati_pagamento=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    with pytest.raises(FatturaPaFormatError, match="due date"):
        parse(xml)


def test_an_instalment_expressed_as_relative_terms_computes_a_due_date() -> None:
    xml = _fattura_xml(
        dati_pagamento=(
            "<DatiPagamento><CondizioniPagamento>TP02</CondizioniPagamento>"
            "<DettaglioPagamento><ModalitaPagamento>MP05</ModalitaPagamento>"
            "<DataRiferimentoTerminiPagamento>2026-03-15</DataRiferimentoTerminiPagamento>"
            "<GiorniTerminiPagamento>30</GiorniTerminiPagamento>"
            "<ImportoPagamento>122.00</ImportoPagamento></DettaglioPagamento></DatiPagamento>"
        )
    )
    [invoice] = parse(xml)
    [termini] = invoice.termini_pagamento
    [rata] = termini.rate
    assert rata.data_scadenza.isoformat() == "2026-04-14"
    assert rata.origine_scadenza == "calcolata"


def test_every_payment_terms_block_keeps_its_own_condition_code_and_instalments() -> None:
    xml = _fattura_xml(
        dati_pagamento=(
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
    assert [t.condizioni_pagamento for t in invoice.termini_pagamento] == ["TP01", "TP02"]
    assert [t.rate[0].modalita_pagamento for t in invoice.termini_pagamento] == ["MP05", "MP08"]


def test_stamp_duty_and_social_charge_are_absent_not_defaulted_when_the_document_carries_none() -> (
    None
):
    [invoice] = parse(_fattura_xml())
    assert invoice.bollo is None
    assert invoice.cassa_previdenziale == []


def test_stamp_duty_and_the_social_charge_survive_when_the_document_carries_them() -> None:
    xml = _fattura_xml(
        dati_bollo="<DatiBollo><ImportoBollo>2.00</ImportoBollo></DatiBollo>",
        dati_cassa=(
            "<DatiCassaPrevidenziale><TipoCassa>TC22</TipoCassa><AlCassa>4.00</AlCassa>"
            "<ImportoContributoCassa>4.00</ImportoContributoCassa>"
            "<ImponibileCassa>100.00</ImponibileCassa><AliquotaIVA>22.00</AliquotaIVA>"
            "</DatiCassaPrevidenziale>"
        ),
    )
    [invoice] = parse(xml)
    assert invoice.bollo == Decimal("2.00")
    [cassa] = invoice.cassa_previdenziale
    assert cassa.tipo_cassa == "TC22"
    assert cassa.aliquota_cassa == Decimal("4.00")
    assert cassa.importo_contributo_cassa == Decimal("4.00")
    assert cassa.imponibile_cassa == Decimal("100.00")


def test_imponibile_and_imposta_sum_every_tax_summary_block_for_a_mixed_rate_invoice() -> None:
    riepiloghi = (
        "<DatiRiepilogo><AliquotaIVA>22.00</AliquotaIVA>"
        "<ImponibileImporto>100.00</ImponibileImporto><Imposta>22.00</Imposta></DatiRiepilogo>"
        "<DatiRiepilogo><AliquotaIVA>10.00</AliquotaIVA>"
        "<ImponibileImporto>50.00</ImponibileImporto><Imposta>5.00</Imposta></DatiRiepilogo>"
    )
    xml = _fattura_xml(riepiloghi=riepiloghi, importo_totale_documento="177.00")
    [invoice] = parse(xml)
    assert invoice.imponibile == Decimal("150.00")
    assert invoice.imposta == Decimal("27.00")
    assert invoice.totale == Decimal("177.00")
    assert len(invoice.riepiloghi) == 2


def test_quantita_defaults_to_one_when_the_document_omits_it() -> None:
    riga = (
        "<DettaglioLinee><Descrizione>Consulenza a corpo</Descrizione>"
        "<PrezzoUnitario>100.00</PrezzoUnitario><PrezzoTotale>100.00</PrezzoTotale>"
        "<AliquotaIVA>22.00</AliquotaIVA></DettaglioLinee>"
    )
    [invoice] = parse(_fattura_xml(righe=riga))
    assert invoice.righe[0].quantita == Decimal("1")


def test_a_batch_where_one_body_is_malformed_raises_rather_than_returning_partial_results() -> None:
    """A caller must not get one good invoice and one silently dropped -- the
    same reasoning `parse`'s own docstring states for a single-invoice document.
    """
    good = _fattura_xml().decode("utf-8")
    body_start = good.index("<FatturaElettronicaBody>")
    body_end = good.index("</FatturaElettronicaBody>") + len("</FatturaElettronicaBody>")
    second_body = good[body_start:body_end].replace("TD01", "TD99")
    batch = good[:body_end] + second_body + good[body_end:]
    with pytest.raises(FatturaPaFormatError, match="TipoDocumento"):
        parse(batch.encode("utf-8"))
