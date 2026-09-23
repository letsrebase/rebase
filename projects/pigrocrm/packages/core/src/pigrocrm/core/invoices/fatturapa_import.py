"""The FatturaPA FPR12 import adapter (REB-363): the first concrete
`InvoiceFormatAdapter`, mirroring mastro's
`formats/fattura-pa/{adapter,map,xml}.ts` collapsed into one module -- lxml's own
namespace-agnostic tree walk (below) replaces the `fast-xml-parser` + `zod`
two-step mastro needed to get a validated raw shape before mapping it, so there is
no separate "raw XML shape" module here.

`FORMAT_ID` is `"FPR12"`, the FatturaPA transmission-format code for an invoice
addressed to a private party (as opposed to `FPA12`, addressed to a Public
Administration) -- a consultant billing private clients always transmits under
this code. `fatturapa.py`, next to this file, is the export direction: `InvoiceForExport`
in, `bytes` out. Nothing in that module reads a FatturaPA XML file, and nothing here
writes one; the two are deliberately independent translations, not a round trip.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TypedDict

from lxml import etree

from pigrocrm.core.invoices.import_adapter import InvoiceFormatAdapter
from pigrocrm.core.invoices.import_schemas import (
    ParsedInvoice,
    ParsedInvoiceDocumentType,
    ParsedInvoiceLine,
    ParsedInvoiceParty,
    ParsedInvoicePaymentInstallment,
    ParsedInvoicePaymentTerms,
    ParsedInvoiceSocialCharge,
    ParsedInvoiceTaxSummary,
    ParsedInvoiceTransmission,
)
from pigrocrm.core.invoices.scadenza import scadenza_da_termini

FORMAT_ID = "FPR12"

_ROOT_LOCAL_NAME = "FatturaElettronica"

# Named after what the FatturaPA `TipoDocumento` code means, mirroring mastro's own
# `DOCUMENT_TYPE_BY_CODE` (`map.ts:32-39`) translated into PigroCRM's vocabulary.
_DOCUMENT_TYPE_BY_CODE: dict[str, ParsedInvoiceDocumentType] = {
    "TD01": "fattura",
    "TD02": "acconto_fattura",
    "TD03": "acconto_parcella",
    "TD04": "nota_credito",
    "TD05": "nota_debito",
    "TD06": "parcella",
}


class FatturaPaFormatError(ValueError):
    """A document that passed `detect` (well-formed XML, a `FatturaElettronica`
    root claiming `versione="FPR12"`) but is missing or misusing a field this
    adapter needs to produce a complete `ParsedInvoice`. There is no human in this
    path to notice a wrong field, so this is raised rather than returning a
    partial or guessed result -- mirroring mastro's own plain `Error` throw in
    `map.ts` for the equivalent cases.
    """


# --- tree helpers: matched by local name only, never by namespace URI. A prefixed
# root (`p:FatturaElettronica`, what this project's own exporter emits), a
# default-namespaced one (`xmlns="..."` with no prefix, common among real senders),
# and one with no namespace declared at all are all read the same way. Real
# FatturaPA files vary on this in practice; the FPR12 XSD itself declares
# `elementFormDefault="unqualified"`, so a byte-correct sender's children carry no
# namespace regardless of what the root declares. ---


def _local(tag: str) -> str:
    return etree.QName(tag).localname


def _children(parent: etree._Element, tag: str) -> list[etree._Element]:
    return [el for el in parent if _local(el.tag) == tag]


def _child(parent: etree._Element, tag: str) -> etree._Element | None:
    for el in parent:
        if _local(el.tag) == tag:
            return el
    return None


def _required_child(parent: etree._Element, tag: str, *, context: str) -> etree._Element:
    el = _child(parent, tag)
    if el is None:
        raise FatturaPaFormatError(f"{context} is missing required element {tag!r}")
    return el


def _text(parent: etree._Element, tag: str) -> str | None:
    el = _child(parent, tag)
    if el is None or el.text is None:
        return None
    value = el.text.strip()
    return value or None


def _required_text(parent: etree._Element, tag: str, *, context: str) -> str:
    value = _text(parent, tag)
    if value is None:
        raise FatturaPaFormatError(f"{context} is missing required element {tag!r}")
    return value


def _decimal(value: str, *, context: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise FatturaPaFormatError(f"{context}: {value!r} is not a valid decimal") from exc
    # `Decimal("NaN")`/`Decimal("Infinity")` parse without raising `InvalidOperation` --
    # Pydantic's own `finite_number` check on `ParsedInvoice`'s `Decimal` fields would
    # catch these too, but as a bare `pydantic_core.ValidationError`, not the
    # `FatturaPaFormatError` this module's every other malformed-value path raises.
    if not result.is_finite():
        raise FatturaPaFormatError(f"{context}: {value!r} is not a finite decimal")
    return result


def _date(value: str, *, context: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FatturaPaFormatError(f"{context}: {value!r} is not an ISO date") from exc


def _int(value: str, *, context: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise FatturaPaFormatError(f"{context}: {value!r} is not an integer") from exc


def _root_element(content: bytes) -> etree._Element | None:
    """The well-formed document's `FatturaElettronica` root, or `None` for
    anything else -- malformed XML, or a document rooted in something else.
    `None` is the one signal `detect` needs to decide it does not claim a file.

    The parser is configured against untrusted bytes -- no DTD loading, no
    network, no entity resolution -- so a hostile document gets neither a
    billion-laughs expansion nor a chance to read a local file or reach the
    network through an external entity, a concern this project has not needed to
    reason about until an adapter started reading files nobody here produced.
    """
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )
    try:
        root = etree.fromstring(content, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        # `ValueError` covers lxml's own refusal of an empty byte string, which
        # raises before it ever gets to XMLSyntaxError.
        return None
    if _local(root.tag) != _ROOT_LOCAL_NAME:
        return None
    return root


def detect(content: bytes) -> bool:
    """Whether `content` is a well-formed FPR12 `FatturaElettronica` document.

    Wrapped in a blanket `except`, not just around the XML parsing: `detect`
    must be total (the adapter's own contract), so a bug reachable only from
    unexpected bytes anywhere in this function must still come back `False`
    rather than crash a scan of many candidate files.
    """
    try:
        root = _root_element(content)
        if root is None:
            return False
        if root.get("versione") != FORMAT_ID:
            return False
        header = _child(root, "FatturaElettronicaHeader")
        if header is None:
            return False
        trasmissione = _child(header, "DatiTrasmissione")
        if trasmissione is None:
            return False
        return _text(trasmissione, "FormatoTrasmissione") == FORMAT_ID
    except Exception:
        return False


def _fiscal_id(el: etree._Element, *, context: str) -> str:
    """`IdPaese` + `IdCodice`, concatenated -- the one identifier every party a
    FatturaPA document names is guaranteed to carry when it carries this element
    at all, mirroring mastro's `fiscalIdString` (`map.ts:41-43`).
    """
    id_paese = _required_text(el, "IdPaese", context=context)
    id_codice = _required_text(el, "IdCodice", context=context)
    return f"{id_paese}{id_codice}"


def _party_name(anagrafica: etree._Element, *, context: str) -> str:
    denominazione = _text(anagrafica, "Denominazione")
    if denominazione is not None:
        return denominazione
    nome = _text(anagrafica, "Nome")
    cognome = _text(anagrafica, "Cognome")
    if nome is not None and cognome is not None:
        return f"{nome} {cognome}"
    raise FatturaPaFormatError(f"{context}: Anagrafica has neither Denominazione nor Nome/Cognome")


class _PartyAddress(TypedDict):
    indirizzo: str
    comune: str
    cap: str
    provincia: str | None
    nazione: str


def _party_address(sede: etree._Element) -> _PartyAddress:
    indirizzo = _required_text(sede, "Indirizzo", context="Sede")
    numero_civico = _text(sede, "NumeroCivico")
    return {
        "indirizzo": f"{indirizzo} {numero_civico}" if numero_civico else indirizzo,
        "comune": _required_text(sede, "Comune", context="Sede"),
        "cap": _required_text(sede, "CAP", context="Sede"),
        "provincia": _text(sede, "Provincia"),
        # The schema declares `Nazione` defaulting to "IT" for a document that
        # omits it; lxml has no notion of XSD defaults, so the fallback is
        # applied here, mirroring mastro's `partyAddress` (`map.ts:57-68`).
        "nazione": _text(sede, "Nazione") or "IT",
    }


def _map_fornitore(cedente: etree._Element) -> ParsedInvoiceParty:
    anagrafici = _required_child(cedente, "DatiAnagrafici", context="CedentePrestatore")
    id_fiscale_iva = _required_child(
        anagrafici, "IdFiscaleIVA", context="CedentePrestatore.DatiAnagrafici"
    )
    anagrafica = _required_child(
        anagrafici, "Anagrafica", context="CedentePrestatore.DatiAnagrafici"
    )
    sede = _required_child(cedente, "Sede", context="CedentePrestatore")
    return ParsedInvoiceParty(
        ragione_sociale=_party_name(
            anagrafica, context="CedentePrestatore.DatiAnagrafici.Anagrafica"
        ),
        # `IdFiscaleIVA` is mandatory for `CedentePrestatore`, `CodiceFiscale` is
        # not -- a later direction-detection issue needs a field every supplier
        # carries, so this reads the one the schema guarantees, mirroring
        # mastro's `mapSupplier` (`map.ts:70-81`).
        partita_iva=_fiscal_id(
            id_fiscale_iva, context="CedentePrestatore.DatiAnagrafici.IdFiscaleIVA"
        ),
        codice_fiscale=_text(anagrafici, "CodiceFiscale"),
        **_party_address(sede),
    )


def _map_cliente(cessionario: etree._Element) -> ParsedInvoiceParty:
    """Unlike the supplier, `IdFiscaleIVA` is optional for the customer (an
    individual buyer may carry only a `CodiceFiscale`); mirrors mastro's
    `mapCustomer` (`map.ts:83-106`), including its refusal when the document
    carries neither identifier.
    """
    anagrafici = _required_child(cessionario, "DatiAnagrafici", context="CessionarioCommittente")
    anagrafica = _required_child(
        anagrafici, "Anagrafica", context="CessionarioCommittente.DatiAnagrafici"
    )
    sede = _required_child(cessionario, "Sede", context="CessionarioCommittente")
    ragione_sociale = _party_name(
        anagrafica, context="CessionarioCommittente.DatiAnagrafici.Anagrafica"
    )
    id_fiscale_iva = _child(anagrafici, "IdFiscaleIVA")
    codice_fiscale = _text(anagrafici, "CodiceFiscale")
    if id_fiscale_iva is not None:
        return ParsedInvoiceParty(
            ragione_sociale=ragione_sociale,
            partita_iva=_fiscal_id(
                id_fiscale_iva, context="CessionarioCommittente.DatiAnagrafici.IdFiscaleIVA"
            ),
            codice_fiscale=codice_fiscale,
            **_party_address(sede),
        )
    if codice_fiscale is None:
        raise FatturaPaFormatError(
            "CessionarioCommittente has neither IdFiscaleIVA nor CodiceFiscale"
        )
    return ParsedInvoiceParty(
        ragione_sociale=ragione_sociale,
        partita_iva=None,
        codice_fiscale=codice_fiscale,
        **_party_address(sede),
    )


def _map_linea(linea: etree._Element) -> ParsedInvoiceLine:
    quantita = _text(linea, "Quantita")
    return ParsedInvoiceLine(
        descrizione=_required_text(linea, "Descrizione", context="DettaglioLinee"),
        # `Quantita` is optional in the schema; a document that omits it means
        # exactly one unit, mirroring mastro's `mapLine` (`map.ts:108-116`).
        quantita=_decimal(quantita, context="DettaglioLinee.Quantita")
        if quantita is not None
        else Decimal("1"),
        prezzo_unitario=_decimal(
            _required_text(linea, "PrezzoUnitario", context="DettaglioLinee"),
            context="DettaglioLinee.PrezzoUnitario",
        ),
        prezzo_totale=_decimal(
            _required_text(linea, "PrezzoTotale", context="DettaglioLinee"),
            context="DettaglioLinee.PrezzoTotale",
        ),
        aliquota_iva=_decimal(
            _required_text(linea, "AliquotaIVA", context="DettaglioLinee"),
            context="DettaglioLinee.AliquotaIVA",
        ),
    )


def _map_riepilogo(riepilogo: etree._Element) -> ParsedInvoiceTaxSummary:
    return ParsedInvoiceTaxSummary(
        aliquota_iva=_decimal(
            _required_text(riepilogo, "AliquotaIVA", context="DatiRiepilogo"),
            context="DatiRiepilogo.AliquotaIVA",
        ),
        natura=_text(riepilogo, "Natura"),
        riferimento_normativo=_text(riepilogo, "RiferimentoNormativo"),
        imponibile=_decimal(
            _required_text(riepilogo, "ImponibileImporto", context="DatiRiepilogo"),
            context="DatiRiepilogo.ImponibileImporto",
        ),
        imposta=_decimal(
            _required_text(riepilogo, "Imposta", context="DatiRiepilogo"),
            context="DatiRiepilogo.Imposta",
        ),
    )


def _map_cassa(cassa: etree._Element) -> ParsedInvoiceSocialCharge:
    imponibile_cassa = _text(cassa, "ImponibileCassa")
    return ParsedInvoiceSocialCharge(
        tipo_cassa=_required_text(cassa, "TipoCassa", context="DatiCassaPrevidenziale"),
        aliquota_cassa=_decimal(
            _required_text(cassa, "AlCassa", context="DatiCassaPrevidenziale"),
            context="DatiCassaPrevidenziale.AlCassa",
        ),
        importo_contributo_cassa=_decimal(
            _required_text(cassa, "ImportoContributoCassa", context="DatiCassaPrevidenziale"),
            context="DatiCassaPrevidenziale.ImportoContributoCassa",
        ),
        imponibile_cassa=(
            _decimal(imponibile_cassa, context="DatiCassaPrevidenziale.ImponibileCassa")
            if imponibile_cassa is not None
            else None
        ),
        aliquota_iva=_decimal(
            _required_text(cassa, "AliquotaIVA", context="DatiCassaPrevidenziale"),
            context="DatiCassaPrevidenziale.AliquotaIVA",
        ),
    )


def _map_rata(dettaglio: etree._Element) -> ParsedInvoicePaymentInstallment:
    importo = _decimal(
        _required_text(dettaglio, "ImportoPagamento", context="DettaglioPagamento"),
        context="DettaglioPagamento.ImportoPagamento",
    )
    modalita = _required_text(dettaglio, "ModalitaPagamento", context="DettaglioPagamento")
    iban = _text(dettaglio, "IBAN")

    scadenza = _text(dettaglio, "DataScadenzaPagamento")
    if scadenza is not None:
        return ParsedInvoicePaymentInstallment(
            data_scadenza=_date(scadenza, context="DettaglioPagamento.DataScadenzaPagamento"),
            origine_scadenza="documento",
            importo=importo,
            modalita_pagamento=modalita,
            iban=iban,
        )

    riferimento = _text(dettaglio, "DataRiferimentoTerminiPagamento")
    giorni = _text(dettaglio, "GiorniTerminiPagamento")
    if riferimento is not None and giorni is not None:
        # The schema also allows expressing a due date as a relative term
        # (`GiorniTerminiPagamento` days after `DataRiferimentoTerminiPagamento`)
        # instead of an explicit `DataScadenzaPagamento`. The due date still
        # comes from the document, never invented -- it is computed here from
        # the document's own reference date and day count, reusing
        # `scadenza_da_termini`, the same "net N days" arithmetic every other due
        # date in this product already goes through, applied to the document's
        # own terms instead of a contract's (mirrors mastro's `mapPaymentTerms`,
        # `map.ts:147-194`).
        due = scadenza_da_termini(
            _date(riferimento, context="DettaglioPagamento.DataRiferimentoTerminiPagamento"),
            _int(giorni, context="DettaglioPagamento.GiorniTerminiPagamento"),
            fine_mese=False,
        )
        return ParsedInvoicePaymentInstallment(
            data_scadenza=due,
            origine_scadenza="calcolata",
            importo=importo,
            modalita_pagamento=modalita,
            iban=iban,
        )

    raise FatturaPaFormatError(
        "DettaglioPagamento has neither DataScadenzaPagamento nor "
        "DataRiferimentoTerminiPagamento/GiorniTerminiPagamento to compute a due date from"
    )


def _map_pagamento(pagamento: etree._Element) -> ParsedInvoicePaymentTerms:
    return ParsedInvoicePaymentTerms(
        condizioni_pagamento=_required_text(
            pagamento, "CondizioniPagamento", context="DatiPagamento"
        ),
        rate=[_map_rata(dettaglio) for dettaglio in _children(pagamento, "DettaglioPagamento")],
    )


def _map_body(header: etree._Element, body: etree._Element) -> ParsedInvoice:
    """Maps a single `FatturaElettronicaBody` plus the `FatturaElettronicaHeader`
    it shares with every other body in the same file onto a `ParsedInvoice`.
    Raises on a well-formed body this adapter does not support -- a document
    omitting `ImportoTotaleDocumento`, most concretely -- rather than silently
    parsing part of it, mirroring mastro's `mapBody` (`map.ts:201-247`).
    """
    dati_generali = _required_child(body, "DatiGenerali", context="FatturaElettronicaBody")
    documento = _required_child(dati_generali, "DatiGeneraliDocumento", context="DatiGenerali")

    tipo_documento_code = _required_text(
        documento, "TipoDocumento", context="DatiGeneraliDocumento"
    )
    tipo_documento = _DOCUMENT_TYPE_BY_CODE.get(tipo_documento_code)
    if tipo_documento is None:
        raise FatturaPaFormatError(f"unrecognised TipoDocumento: {tipo_documento_code!r}")

    totale_raw = _text(documento, "ImportoTotaleDocumento")
    if totale_raw is None:
        raise FatturaPaFormatError("DatiGeneraliDocumento is missing ImportoTotaleDocumento")

    dati_beni_servizi = _required_child(body, "DatiBeniServizi", context="FatturaElettronicaBody")
    righe = [_map_linea(linea) for linea in _children(dati_beni_servizi, "DettaglioLinee")]
    if not righe:
        # Both elements have no `minOccurs` of their own in the schema's
        # `DatiBeniServiziType` sequence, so the XSD default of 1 applies: a
        # real document always carries at least one of each. `detect` stays
        # cheap and total on purpose (mirrors mastro's own `detect`, which
        # checks the same handful of fields); this is the completeness check
        # that belongs here instead, for the same reason every other required
        # field in this function is checked here rather than in `detect`.
        raise FatturaPaFormatError("DatiBeniServizi has no DettaglioLinee")
    riepiloghi = [_map_riepilogo(r) for r in _children(dati_beni_servizi, "DatiRiepilogo")]
    if not riepiloghi:
        raise FatturaPaFormatError("DatiBeniServizi has no DatiRiepilogo")

    dati_bollo = _child(documento, "DatiBollo")
    cedente = _required_child(header, "CedentePrestatore", context="FatturaElettronicaHeader")
    cessionario = _required_child(
        header, "CessionarioCommittente", context="FatturaElettronicaHeader"
    )
    dati_trasmissione = _required_child(
        header, "DatiTrasmissione", context="FatturaElettronicaHeader"
    )
    id_trasmittente = _required_child(
        dati_trasmissione, "IdTrasmittente", context="DatiTrasmissione"
    )

    return ParsedInvoice(
        numero=_required_text(documento, "Numero", context="DatiGeneraliDocumento"),
        data_emissione=_date(
            _required_text(documento, "Data", context="DatiGeneraliDocumento"),
            context="DatiGeneraliDocumento.Data",
        ),
        tipo_documento=tipo_documento,
        divisa=_required_text(documento, "Divisa", context="DatiGeneraliDocumento"),
        fornitore=_map_fornitore(cedente),
        cliente=_map_cliente(cessionario),
        righe=righe,
        riepiloghi=riepiloghi,
        # Sums of the tax-summary blocks, not a separately declared document
        # field -- FatturaPA has none at this level -- mirroring mastro's own
        # `taxableAmount`/`taxAmount` (`map.ts:229-230`).
        imponibile=sum((r.imponibile for r in riepiloghi), start=Decimal("0")),
        imposta=sum((r.imposta for r in riepiloghi), start=Decimal("0")),
        totale=_decimal(totale_raw, context="DatiGeneraliDocumento.ImportoTotaleDocumento"),
        bollo=(
            _decimal(
                _required_text(dati_bollo, "ImportoBollo", context="DatiBollo"),
                context="DatiBollo.ImportoBollo",
            )
            if dati_bollo is not None
            else None
        ),
        cassa_previdenziale=[
            _map_cassa(cassa) for cassa in _children(documento, "DatiCassaPrevidenziale")
        ],
        termini_pagamento=[
            _map_pagamento(pagamento) for pagamento in _children(body, "DatiPagamento")
        ],
        trasmissione=ParsedInvoiceTransmission(
            id_trasmittente=_fiscal_id(id_trasmittente, context="DatiTrasmissione.IdTrasmittente"),
            progressivo_invio=_required_text(
                dati_trasmissione, "ProgressivoInvio", context="DatiTrasmissione"
            ),
        ),
    )


def parse(content: bytes) -> list[ParsedInvoice]:
    """Maps `content` to one `ParsedInvoice` per `FatturaElettronicaBody` it
    carries, in document order. The XSD allows a root to carry more than one body
    (`maxOccurs="unbounded"`) -- a `lotto` batch of several invoices transmitted
    together in one file, all sharing the same `FatturaElettronicaHeader` -- so
    every one of them comes back, not just the first, mirroring mastro's
    `mapFatturaPaToInvoices` (`map.ts:256-258`).

    Only ever called after `detect` has returned `True` for the same bytes, per
    the `InvoiceFormatAdapter` contract; this does not re-check the format code.
    """
    root = _root_element(content)
    if root is None:
        raise FatturaPaFormatError("content is not a well-formed FatturaElettronica document")
    header = _required_child(root, "FatturaElettronicaHeader", context="FatturaElettronica")
    bodies = _children(root, "FatturaElettronicaBody")
    if not bodies:
        # The XSD requires at least one; a document with none is exactly the
        # "well-formed but semantically incomplete" case this adapter refuses to
        # guess through, matching the reasoning behind every other raise above.
        raise FatturaPaFormatError("FatturaElettronica has no FatturaElettronicaBody")
    return [_map_body(header, body) for body in bodies]


class FatturaPaFpr12Adapter:
    """The concrete `InvoiceFormatAdapter` for FatturaPA FPR12."""

    id = FORMAT_ID

    def detect(self, content: bytes) -> bool:
        return detect(content)

    def parse(self, content: bytes) -> list[ParsedInvoice]:
        return parse(content)


fattura_pa_fpr12_adapter: InvoiceFormatAdapter = FatturaPaFpr12Adapter()
