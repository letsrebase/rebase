"""The FatturaPA FPR12 import adapter (REB-363): the first concrete
`InvoiceFormatAdapter`, mirroring mastro's
`formats/fattura-pa/{adapter,map,xml}.ts` collapsed into one module -- lxml's own
namespace-agnostic tree walk (below) replaces the `fast-xml-parser` + `zod`
two-step mastro needed to get a validated raw shape before mapping it, so there is
no separate "raw XML shape" module here.

`FORMAT_ID` is `"FPR12"`, the FatturaPA transmission-format code for an invoice
addressed to a private party (as opposed to `FPA12`, addressed to a Public
Administration) -- a consultant billing private clients always transmits under
this code. `fatturapa/`, next to this file, is the export direction: `InvoiceForExport`
in, `bytes` out. Nothing in that module reads a FatturaPA XML file, and nothing here
writes one; the two are deliberately independent translations, not a round trip.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import TypedDict

from lxml import etree

from pigrocrm.core.invoices.due_date import due_date_from_terms
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

FORMAT_ID = "FPR12"

_ROOT_LOCAL_NAME = "FatturaElettronica"

# Named after what the FatturaPA `TipoDocumento` code means, mirroring mastro's own
# `DOCUMENT_TYPE_BY_CODE` (`map.ts:32-39`) translated into PigroCRM's vocabulary.
_DOCUMENT_TYPE_BY_CODE: dict[str, ParsedInvoiceDocumentType] = {
    "TD01": "invoice",
    "TD02": "invoice_advance",
    "TD03": "fee_advance",
    "TD04": "note_credit",
    "TD05": "note_debit",
    "TD06": "fee_note",
}


class InvoicePaFormatError(ValueError):
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
        raise InvoicePaFormatError(f"{context} is missing required element {tag!r}")
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
        raise InvoicePaFormatError(f"{context} is missing required element {tag!r}")
    return value


# The lexical forms accepted for a number, checked before `Decimal()`/`int()` see the
# text: both of those accept things FPR12 never carries (an exponent, underscores,
# non-ASCII digits, `NaN`), and a `Decimal` with an exponent such as `1E999999999`
# raises `InvalidOperation` from `quantize` far from here. Wider than the schema's
# `Amount2/8` patterns on purpose (real senders write `122` or `0.123456789`), but
# bounded so a value always fits the default `Decimal` precision of 28 digits.
_DECIMAL_RE = re.compile(r"[+-]?[0-9]{1,15}(\.[0-9]{1,12})?", re.ASCII)
_INTEGER_RE = re.compile(r"[+-]?[0-9]{1,9}", re.ASCII)


def _decimal(value: str, *, context: str) -> Decimal:
    if not _DECIMAL_RE.fullmatch(value):
        raise InvoicePaFormatError(f"{context}: {value!r} is not a valid decimal")
    return Decimal(value)


def _date(value: str, *, context: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvoicePaFormatError(f"{context}: {value!r} is not an ISO date") from exc


def _int(value: str, *, context: str) -> int:
    if not _INTEGER_RE.fullmatch(value):
        raise InvoicePaFormatError(f"{context}: {value!r} is not an integer")
    return int(value)


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
        transmission = _child(header, "DatiTrasmissione")
        if transmission is None:
            return False
        return _text(transmission, "FormatoTrasmissione") == FORMAT_ID
    except Exception:
        return False


def _fiscal_id(el: etree._Element, *, context: str) -> str:
    """`IdPaese` + `IdCodice`, concatenated -- the one identifier every party a
    FatturaPA document names is guaranteed to carry when it carries this element
    at all, mirroring mastro's `fiscalIdString` (`map.ts:41-43`).
    """
    id_country = _required_text(el, "IdPaese", context=context)
    id_code = _required_text(el, "IdCodice", context=context)
    return f"{id_country}{id_code}"


def _party_name(directory: etree._Element, *, context: str) -> str:
    denomination = _text(directory, "Denominazione")
    if denomination is not None:
        return denomination
    first_name = _text(directory, "Nome")
    last_name = _text(directory, "Cognome")
    if first_name is not None and last_name is not None:
        return f"{first_name} {last_name}"
    raise InvoicePaFormatError(f"{context}: Anagrafica has neither Denominazione nor Nome/Cognome")


class _PartyAddress(TypedDict):
    address: str
    city: str
    postal_code: str
    province: str | None
    country: str


def _party_address(headquarters: etree._Element) -> _PartyAddress:
    address = _required_text(headquarters, "Indirizzo", context="Sede")
    street_number = _text(headquarters, "NumeroCivico")
    return {
        "address": f"{address} {street_number}" if street_number else address,
        "city": _required_text(headquarters, "Comune", context="Sede"),
        "postal_code": _required_text(headquarters, "CAP", context="Sede"),
        "province": _text(headquarters, "Provincia"),
        # The schema declares `Nazione` defaulting to "IT" for a document that
        # omits it; lxml has no notion of XSD defaults, so the fallback is
        # applied here, mirroring mastro's `partyAddress` (`map.ts:57-68`).
        "country": _text(headquarters, "Nazione") or "IT",
    }


def _map_supplier(supplier: etree._Element) -> ParsedInvoiceParty:
    master_data = _required_child(supplier, "DatiAnagrafici", context="CedentePrestatore")
    id_tax_vat = _required_child(
        master_data, "IdFiscaleIVA", context="CedentePrestatore.DatiAnagrafici"
    )
    directory = _required_child(
        master_data, "Anagrafica", context="CedentePrestatore.DatiAnagrafici"
    )
    headquarters = _required_child(supplier, "Sede", context="CedentePrestatore")
    return ParsedInvoiceParty(
        legal_name=_party_name(directory, context="CedentePrestatore.DatiAnagrafici.Anagrafica"),
        # `IdFiscaleIVA` is mandatory for `CedentePrestatore`, `CodiceFiscale` is
        # not -- a later direction-detection issue needs a field every supplier
        # carries, so this reads the one the schema guarantees, mirroring
        # mastro's `mapSupplier` (`map.ts:70-81`).
        vat_number=_fiscal_id(id_tax_vat, context="CedentePrestatore.DatiAnagrafici.IdFiscaleIVA"),
        tax_code=_text(master_data, "CodiceFiscale"),
        **_party_address(headquarters),
    )


def _map_customer(buyer: etree._Element) -> ParsedInvoiceParty:
    """Unlike the supplier, `IdFiscaleIVA` is optional for the customer (an
    individual buyer may carry only a `CodiceFiscale`); mirrors mastro's
    `mapOrganization` (`map.ts:83-106`), including its refusal when the document
    carries neither identifier.
    """
    master_data = _required_child(buyer, "DatiAnagrafici", context="CessionarioCommittente")
    directory = _required_child(
        master_data, "Anagrafica", context="CessionarioCommittente.DatiAnagrafici"
    )
    headquarters = _required_child(buyer, "Sede", context="CessionarioCommittente")
    legal_name = _party_name(directory, context="CessionarioCommittente.DatiAnagrafici.Anagrafica")
    id_tax_vat = _child(master_data, "IdFiscaleIVA")
    tax_code = _text(master_data, "CodiceFiscale")
    if id_tax_vat is not None:
        return ParsedInvoiceParty(
            legal_name=legal_name,
            vat_number=_fiscal_id(
                id_tax_vat, context="CessionarioCommittente.DatiAnagrafici.IdFiscaleIVA"
            ),
            tax_code=tax_code,
            **_party_address(headquarters),
        )
    if tax_code is None:
        raise InvoicePaFormatError(
            "CessionarioCommittente has neither IdFiscaleIVA nor CodiceFiscale"
        )
    return ParsedInvoiceParty(
        legal_name=legal_name,
        vat_number=None,
        tax_code=tax_code,
        **_party_address(headquarters),
    )


def _map_line(line: etree._Element) -> ParsedInvoiceLine:
    quantity = _text(line, "Quantita")
    return ParsedInvoiceLine(
        description=_required_text(line, "Descrizione", context="DettaglioLinee"),
        # `Quantita` is optional in the schema; a document that omits it means
        # exactly one unit, mirroring mastro's `mapLine` (`map.ts:108-116`).
        quantity=_decimal(quantity, context="DettaglioLinee.Quantita")
        if quantity is not None
        else Decimal("1"),
        unit_price=_decimal(
            _required_text(line, "PrezzoUnitario", context="DettaglioLinee"),
            context="DettaglioLinee.PrezzoUnitario",
        ),
        total_price=_decimal(
            _required_text(line, "PrezzoTotale", context="DettaglioLinee"),
            context="DettaglioLinee.PrezzoTotale",
        ),
        vat_rate=_decimal(
            _required_text(line, "AliquotaIVA", context="DettaglioLinee"),
            context="DettaglioLinee.AliquotaIVA",
        ),
        # Optional in the schema (`Natura?` in `DettaglioLinee`'s own sequence,
        # `_dettaglio_linee` in `fatturapa.py`'s export side already writes it
        # the same way): the line's own declared exemption code, read directly
        # rather than reconstructed from `DatiRiepilogo` -- see `ParsedInvoiceLine`'s
        # own docstring for why a rate-only reconstruction is unsound.
        vat_nature=_text(line, "Natura"),
    )


def _map_recap(recap: etree._Element) -> ParsedInvoiceTaxSummary:
    return ParsedInvoiceTaxSummary(
        vat_rate=_decimal(
            _required_text(recap, "AliquotaIVA", context="DatiRiepilogo"),
            context="DatiRiepilogo.AliquotaIVA",
        ),
        vat_nature=_text(recap, "Natura"),
        legal_reference=_text(recap, "RiferimentoNormativo"),
        taxable_amount=_decimal(
            _required_text(recap, "ImponibileImporto", context="DatiRiepilogo"),
            context="DatiRiepilogo.ImponibileImporto",
        ),
        vat_amount=_decimal(
            _required_text(recap, "Imposta", context="DatiRiepilogo"),
            context="DatiRiepilogo.Imposta",
        ),
    )


def _map_pension_fund(pension_fund: etree._Element) -> ParsedInvoiceSocialCharge:
    pension_fund_taxable_amount = _text(pension_fund, "ImponibileCassa")
    return ParsedInvoiceSocialCharge(
        pension_fund_type=_required_text(
            pension_fund, "TipoCassa", context="DatiCassaPrevidenziale"
        ),
        pension_fund_rate=_decimal(
            _required_text(pension_fund, "AlCassa", context="DatiCassaPrevidenziale"),
            context="DatiCassaPrevidenziale.AlCassa",
        ),
        pension_fund_contribution_amount=_decimal(
            _required_text(
                pension_fund, "ImportoContributoCassa", context="DatiCassaPrevidenziale"
            ),
            context="DatiCassaPrevidenziale.ImportoContributoCassa",
        ),
        pension_fund_taxable_amount=(
            _decimal(pension_fund_taxable_amount, context="DatiCassaPrevidenziale.ImponibileCassa")
            if pension_fund_taxable_amount is not None
            else None
        ),
        vat_rate=_decimal(
            _required_text(pension_fund, "AliquotaIVA", context="DatiCassaPrevidenziale"),
            context="DatiCassaPrevidenziale.AliquotaIVA",
        ),
    )


def _map_instalment(detail: etree._Element) -> ParsedInvoicePaymentInstallment:
    amount = _decimal(
        _required_text(detail, "ImportoPagamento", context="DettaglioPagamento"),
        context="DettaglioPagamento.ImportoPagamento",
    )
    mode = _required_text(detail, "ModalitaPagamento", context="DettaglioPagamento")
    iban = _text(detail, "IBAN")

    due_date = _text(detail, "DataScadenzaPagamento")
    if due_date is not None:
        return ParsedInvoicePaymentInstallment(
            due_date=_date(due_date, context="DettaglioPagamento.DataScadenzaPagamento"),
            due_date_origin="document",
            amount=amount,
            payment_method=mode,
            iban=iban,
        )

    reference = _text(detail, "DataRiferimentoTerminiPagamento")
    days = _text(detail, "GiorniTerminiPagamento")
    if reference is not None and days is not None:
        # The schema also allows expressing a due date as a relative term
        # (`GiorniTerminiPagamento` days after `DataRiferimentoTerminiPagamento`)
        # instead of an explicit `DataScadenzaPagamento`. The due date still
        # comes from the document, never invented -- it is computed here from
        # the document's own reference date and day count, reusing
        # `due_date_from_terms`, the same "net N days" arithmetic every other due
        # date in this product already goes through, applied to the document's
        # own terms instead of a contract's (mirrors mastro's `mapPaymentTerms`,
        # `map.ts:147-194`).
        due = due_date_from_terms(
            _date(reference, context="DettaglioPagamento.DataRiferimentoTerminiPagamento"),
            _int(days, context="DettaglioPagamento.GiorniTerminiPagamento"),
            month_end=False,
        )
        return ParsedInvoicePaymentInstallment(
            due_date=due,
            due_date_origin="calculated",
            amount=amount,
            payment_method=mode,
            iban=iban,
        )

    raise InvoicePaFormatError(
        "DettaglioPagamento has neither DataScadenzaPagamento nor "
        "DataRiferimentoTerminiPagamento/GiorniTerminiPagamento to compute a due date from"
    )


def _map_payment(payment: etree._Element) -> ParsedInvoicePaymentTerms:
    return ParsedInvoicePaymentTerms(
        payment_terms=_required_text(payment, "CondizioniPagamento", context="DatiPagamento"),
        rate=[_map_instalment(detail) for detail in _children(payment, "DettaglioPagamento")],
    )


def _map_body(header: etree._Element, body: etree._Element) -> ParsedInvoice:
    """Maps a single `FatturaElettronicaBody` plus the `FatturaElettronicaHeader`
    it shares with every other body in the same file onto a `ParsedInvoice`.
    Raises on a well-formed body this adapter does not support -- a document
    omitting `ImportoTotaleDocumento`, most concretely -- rather than silently
    parsing part of it, mirroring mastro's `mapBody` (`map.ts:201-247`).
    """
    data_general = _required_child(body, "DatiGenerali", context="FatturaElettronicaBody")
    document = _required_child(data_general, "DatiGeneraliDocumento", context="DatiGenerali")

    sdi_document_type_code = _required_text(
        document, "TipoDocumento", context="DatiGeneraliDocumento"
    )
    sdi_document_type = _DOCUMENT_TYPE_BY_CODE.get(sdi_document_type_code)
    if sdi_document_type is None:
        raise InvoicePaFormatError(f"unrecognised TipoDocumento: {sdi_document_type_code!r}")

    total_raw = _text(document, "ImportoTotaleDocumento")
    if total_raw is None:
        raise InvoicePaFormatError("DatiGeneraliDocumento is missing ImportoTotaleDocumento")

    data_goods_services = _required_child(body, "DatiBeniServizi", context="FatturaElettronicaBody")
    lines = [_map_line(line) for line in _children(data_goods_services, "DettaglioLinee")]
    if not lines:
        # Both elements have no `minOccurs` of their own in the schema's
        # `DatiBeniServiziType` sequence, so the XSD default of 1 applies: a
        # real document always carries at least one of each. `detect` stays
        # cheap and total on purpose (mirrors mastro's own `detect`, which
        # checks the same handful of fields); this is the completeness check
        # that belongs here instead, for the same reason every other required
        # field in this function is checked here rather than in `detect`.
        raise InvoicePaFormatError("DatiBeniServizi has no DettaglioLinee")
    recaps = [_map_recap(r) for r in _children(data_goods_services, "DatiRiepilogo")]
    if not recaps:
        raise InvoicePaFormatError("DatiBeniServizi has no DatiRiepilogo")

    data_stamp_duty = _child(document, "DatiBollo")
    supplier = _required_child(header, "CedentePrestatore", context="FatturaElettronicaHeader")
    buyer = _required_child(header, "CessionarioCommittente", context="FatturaElettronicaHeader")
    data_transmission = _required_child(
        header, "DatiTrasmissione", context="FatturaElettronicaHeader"
    )
    id_transmitter = _required_child(
        data_transmission, "IdTrasmittente", context="DatiTrasmissione"
    )

    return ParsedInvoice(
        number=_required_text(document, "Numero", context="DatiGeneraliDocumento"),
        issue_date=_date(
            _required_text(document, "Data", context="DatiGeneraliDocumento"),
            context="DatiGeneraliDocumento.Data",
        ),
        sdi_document_type=sdi_document_type,
        currency=_required_text(document, "Divisa", context="DatiGeneraliDocumento"),
        supplier=_map_supplier(supplier),
        customer=_map_customer(buyer),
        lines=lines,
        recaps=recaps,
        # Sums of the tax-summary blocks, not a separately declared document
        # field -- FatturaPA has none at this level -- mirroring mastro's own
        # `taxableAmount`/`taxAmount` (`map.ts:229-230`).
        taxable_amount=sum((r.taxable_amount for r in recaps), start=Decimal("0")),
        vat_amount=sum((r.vat_amount for r in recaps), start=Decimal("0")),
        total=_decimal(total_raw, context="DatiGeneraliDocumento.ImportoTotaleDocumento"),
        stamp_duty=(
            _decimal(
                _required_text(data_stamp_duty, "ImportoBollo", context="DatiBollo"),
                context="DatiBollo.ImportoBollo",
            )
            if data_stamp_duty is not None
            else None
        ),
        social_security_pension_fund=[
            _map_pension_fund(pension_fund)
            for pension_fund in _children(document, "DatiCassaPrevidenziale")
        ],
        payment_terms=[_map_payment(payment) for payment in _children(body, "DatiPagamento")],
        transmission=ParsedInvoiceTransmission(
            id_transmitter=_fiscal_id(id_transmitter, context="DatiTrasmissione.IdTrasmittente"),
            send_sequential=_required_text(
                data_transmission, "ProgressivoInvio", context="DatiTrasmissione"
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
        raise InvoicePaFormatError("content is not a well-formed FatturaElettronica document")
    header = _required_child(root, "FatturaElettronicaHeader", context="FatturaElettronica")
    bodies = _children(root, "FatturaElettronicaBody")
    if not bodies:
        # The XSD requires at least one; a document with none is exactly the
        # "well-formed but semantically incomplete" case this adapter refuses to
        # guess through, matching the reasoning behind every other raise above.
        raise InvoicePaFormatError("FatturaElettronica has no FatturaElettronicaBody")
    return [_map_body(header, body) for body in bodies]


class InvoicePaFpr12Adapter:
    """The concrete `InvoiceFormatAdapter` for FatturaPA FPR12."""

    id = FORMAT_ID

    def detect(self, content: bytes) -> bool:
        return detect(content)

    def parse(self, content: bytes) -> list[ParsedInvoice]:
        return parse(content)


invoice_pa_fpr_12_adapter: InvoiceFormatAdapter = InvoicePaFpr12Adapter()
