"""The FatturaPA FPR12 and FPA12 generator.

Built as an `lxml` element tree and serialised exactly once. There is no point in this
module where a fragment of XML exists as a string, which is what makes markup
injection impossible *by structure* rather than by every interpolation site
remembering to call an escaper -- the property the previous system's string-concatenated generator
could only ever approximate.

Three consequences, spelled out because each replaces a specific defect:

1. A value enters as the `.text` of a node, in its domain form, and the serialiser
   escapes it once. `escape_xml` is applied first and substitutes nothing; it only
   refuses code points XML 1.0 cannot represent. No value in this module has been
   through another escaper -- the previous system's `normalizeSingleLine` ran `escapeTypstText`
   before `escapeXml`, so "Rossi & C. #1" reached the Agenzia delle Entrate as
   "Rossi &amp; C. \\#1", with a literal backslash inside a fiscal record.
2. `lxml`, not `xml.etree.ElementTree`: an explicit `nsmap` on the root keeps the
   `ds:` and `xsi:` declarations this document does not reference. ElementTree prunes
   them. The prologue is reproduced identically because it is one the SdI and the
   intermediaries' own validators have already accepted, and there is no upside to
   changing it.
3. Nothing here reads the database. The input is an `InvoiceForExport`, whose
   `snapshot` was frozen at emission, so a re-export is reproducible and the whole
   generator is testable with no session at all.

Order is not negotiable: every FPR12 complex type is an `xs:sequence`, so an element
in the wrong position is an invalid document even when every value is right.
Reconstructing that order from the schema is the bulk of the work, and it is carried
over from a generator whose output the Sistema di Interscambio accepted.
"""

import re
from datetime import date
from decimal import Decimal

from lxml import etree

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.pack import current_pack
from pigrocrm.core.invoices.charges import PUBLIC_BODY, italian
from pigrocrm.core.invoices.fatturapa.checks import (
    check_party_exportable,
    check_recipient_identity,
    check_text,
)
from pigrocrm.core.invoices.fatturapa.constants import (
    ADDRESS_MAX,
    BASIC_LATIN_RE,
    CITY_MAX,
    CODE_PROJECT_CODE_MAX,
    CODE_RECIPIENT_FALLBACK,
    CODE_RECIPIENT_FOREIGN,
    CODE_RECIPIENT_IPA_RE,
    CODE_RECIPIENT_RE,
    CODE_TENDER_CODE_MAX,
    COUNTRY_RE,
    DENOMINATION_MAX,
    DESCRIPTION_MAX,
    EMAIL_CONTACTS_RE,
    EMAIL_MAX,
    EMAIL_MIN,
    EMAIL_PATTERN_EXPECTED,
    EMAIL_RE,
    FORMAT_TRANSMISSION,
    FORMAT_TRANSMISSION_PA,
    FPR12_NAMESPACE,
    IBAN_RE,
    ID_CODE_MAX,
    ID_DOCUMENT_MAX,
    ISSUER_ENTITY,
    LEGAL_REFERENCE_MAX,
    MEMO_MAX,
    NSMAP,
    PHONE_MAX,
    POSTAL_CODE_FOREIGN,
    POSTAL_CODE_RE,
    PROVINCE_RE,
    RECIPIENT_ENTITY,
    STAMP_DUTY_VIRTUAL,
    TYPE_PAYMENT_RE,
    UNIT_OF_MEASURE_MAX,
    VAT_COLLECTABILITY_SPLIT,
    VAT_COLLECTABILITY_VAT,
    WITHHOLDING_SUBJECT,
)
from pigrocrm.core.invoices.fatturapa.normalise import (
    address_xml,
    is_italian,
    normalise_fiscal_id,
    normalise_foreign_fiscal_id,
)
from pigrocrm.core.invoices.naming import number_complete, send_sequential
from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceLineRead, PartySnapshot
from pigrocrm.core.invoices.totals import (
    ComputedLine,
    RecapGroup,
    build_recap,
    format_amount_2,
    format_amount_8,
    format_rate,
)


class InvoicePaExporter:
    """`InvoiceForExport` in, `bytes` out. No database, no profile lookup, no clock."""

    @staticmethod
    def format_of(invoice: InvoiceForExport) -> str:
        """`FPA12` for an invoice to a public body in Italy, `FPR12` for every other
        recipient (Specifiche tecniche v1.4, § 3.2, where `FormatoTrasmissione` names the
        two). The body's own kind, frozen with the customer."""
        customer = invoice.snapshot.customer
        if customer.customer_kind == PUBLIC_BODY and italian(customer.country):
            return FORMAT_TRANSMISSION_PA
        return FORMAT_TRANSMISSION

    def to_bytes(self, invoice: InvoiceForExport) -> bytes:
        if not invoice.lines:
            raise ValidationFailed(
                "invoice",
                "lines",
                "una fattura senza righe non e' esportabile",
                expected="almeno una riga",
                in_words=True,
            )
        legal_entity = invoice.snapshot.legal_entity
        customer = invoice.snapshot.customer
        check_party_exportable(legal_entity, ISSUER_ENTITY)
        check_party_exportable(customer, RECIPIENT_ENTITY)
        check_recipient_identity(customer)

        # `lxml-stubs` types `nsmap` as `Mapping[str, str]` here even though the
        # *property* it defines a few lines above is `Dict[Optional[str], str]` --
        # the `None` key (default namespace) is real and readable at runtime, the
        # stub is simply incomplete for the write side. `NSMAP` has no `None` key in
        # this module, so this is a stub gap, not a real type mismatch.
        root = etree.Element(f"{{{FPR12_NAMESPACE}}}FatturaElettronica", nsmap=NSMAP)  # type: ignore[arg-type]
        root.set("versione", self.format_of(invoice))

        header = etree.SubElement(root, "FatturaElettronicaHeader")
        self._transmission_data(header, invoice)
        self._supplier(header, invoice)
        self._buyer(header, customer)

        body = etree.SubElement(root, "FatturaElettronicaBody")
        self._general_data(body, invoice)
        self._goods_services_data(body, invoice)
        self._payment_data(body, invoice)

        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # ---- value writers ---------------------------------------------------------

    def _text(
        self,
        parent: etree._Element,
        tag: str,
        value: str,
        *,
        entity: str,
        field: str,
        max_length: int | None = None,
        min_length: int | None = None,
        pattern: re.Pattern[str] | None = None,
        pattern_expected: str | None = None,
    ) -> etree._Element:
        """Append `<tag>value</tag>`, with `value` as the node's text and nothing else.

        `check_text` is the whole rule: it refuses what XML 1.0 cannot represent,
        spells the typographic punctuation and applies the width, the length floor
        where the type carries one, the Latin range and the pattern, the same call
        the pre-checks make before a number is spent. The serialiser is the single
        escaping pass, which is why no escaper is applied here and why one applied
        earlier would be a defect rather than extra safety.
        """
        value = check_text(
            value,
            tag,
            entity=entity,
            field=field,
            max_length=max_length,
            min_length=min_length,
            pattern=pattern,
            pattern_expected=pattern_expected,
        )
        element = etree.SubElement(parent, tag)
        element.text = value
        return element

    def _directory(self, parent: etree._Element, party: PartySnapshot, entity: str) -> None:
        """`Anagrafica/Denominazione`, even for a natural person with only a fiscal
        code: FPR12 allows `Nome`/`Cognome` instead, but `organizations` has a single
        `legal_name`, so there is no choice to make."""
        directory = etree.SubElement(parent, "Anagrafica")
        self._text(
            directory,
            "Denominazione",
            party.legal_name,
            entity=entity,
            field="legal_name",
            max_length=DENOMINATION_MAX,
        )

    def _headquarters(self, parent: etree._Element, party: PartySnapshot, entity: str) -> None:
        """`Indirizzo`, `CAP`, `Comune`, `Provincia?`, `Nazione`, the way the technical
        specifications want them for the party's country.

        An Italian address is written as stored. A foreign one is written the way the
        SdI describes it: `CAP` is the `00000` placeholder whatever the record holds,
        `Provincia` is left out, and the real postcode rides at the end of `Indirizzo`
        (see `address_xml`, shared with the pre-check). Applying the five-digit
        pattern to a real foreign postcode here is what ORB-38 was: `issue` had already
        spent the number by the time this method refused.
        """
        italian = is_italian(party)
        headquarters = etree.SubElement(parent, "Sede")
        self._text(
            headquarters,
            "Indirizzo",
            address_xml(party),
            entity=entity,
            field="address",
            max_length=ADDRESS_MAX,
        )
        self._text(
            headquarters,
            "CAP",
            party.postal_code.strip() if italian else POSTAL_CODE_FOREIGN,
            entity=entity,
            field="postal_code",
            pattern=POSTAL_CODE_RE,
        )
        self._text(
            headquarters, "Comune", party.city, entity=entity, field="city", max_length=CITY_MAX
        )
        # Omitted rather than emitted empty when there is none. `Provincia` is optional
        # in FPR12 for exactly this reason -- a London address has no two-letter Italian
        # province and inventing one would be a false statement about where the customer
        # is. Writing it as `""` would be worse still: `check_party_exportable` keeps it
        # mandatory for an Italian address, so a blank one here can only mean a party
        # that is legitimately without. Outside Italy it is omitted whatever the record
        # holds, since the element is the code of an Italian province and nothing else.
        province = (party.province or "").strip().upper()
        if italian and province:
            self._text(
                headquarters,
                "Provincia",
                province,
                entity=entity,
                field="province",
                pattern=PROVINCE_RE,
            )
        self._text(
            headquarters,
            "Nazione",
            party.country.strip().upper(),
            entity=entity,
            field="country",
            pattern=COUNTRY_RE,
        )

    def _id_tax(
        self,
        parent: etree._Element,
        tag: str,
        id_code: str,
        entity: str,
        field: str,
        country: str = "IT",
    ) -> None:
        """`IdPaese` says which country's register `IdCodice` belongs to.

        It was hard-coded to `IT`, which was true of every party this exporter could
        reach while a foreign customer was refused outright — and became a lie the
        moment one was allowed through: a British VAT number announced as an Italian
        one. The emitter is Italian by definition of this product and keeps the default;
        the customer's is read from the customer.
        """
        block = etree.SubElement(parent, tag)
        element = etree.SubElement(block, "IdPaese")
        element.text = country.strip().upper()
        self._text(
            block,
            "IdCodice",
            id_code,
            entity=entity,
            field=field,
            max_length=ID_CODE_MAX,
        )

    # ---- header ----------------------------------------------------------------

    def _transmission_data(self, header: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: IdTrasmittente, ProgressivoInvio, FormatoTrasmissione,
        CodiceDestinatario, ContattiTrasmittente?, PECDestinatario?."""
        legal_entity = invoice.snapshot.legal_entity
        customer = invoice.snapshot.customer
        block = etree.SubElement(header, "DatiTrasmissione")

        # `IdTrasmittente/IdCodice` may be the issuer's *fiscal code* rather than the
        # VAT number, which is not obvious from the schema. Fiscal code first because
        # that is what the working generator sent.
        transmitter = normalise_fiscal_id(legal_entity.tax_code) or normalise_fiscal_id(
            legal_entity.vat_number
        )
        if transmitter is None:
            raise ValidationFailed(
                "legal_entity",
                "tax_code",
                "serve un codice fiscale o una partita IVA dell'emittente per il "
                "blocco IdTrasmittente",
                expected="11 cifre oppure 16 caratteri",
                in_words=True,
            )
        self._id_tax(block, "IdTrasmittente", transmitter, "legal_entity", "tax_code")

        sequential = etree.SubElement(block, "ProgressivoInvio")
        sequential.text = send_sequential(invoice.year, invoice.number)

        format = etree.SubElement(block, "FormatoTrasmissione")
        # Not redundant with the root's own `versione` attribute: the SdI reads it here.
        format.text = self.format_of(invoice)

        sdi_code = (customer.sdi_code or "").strip().upper()
        if self.format_of(invoice) == FORMAT_TRANSMISSION_PA:
            # A public body is reached by its billing office's IPA code and by nothing else:
            # no PEC, and no fall-back to `0000000` (`PECDestinatario` is for a recipient other
            # than a public body, Specifiche tecniche v1.4, § 1.1).
            self._text(
                block,
                "CodiceDestinatario",
                sdi_code,
                entity="customer",
                field="sdi_code",
                pattern=CODE_RECIPIENT_IPA_RE,
                pattern_expected="il codice IPA dell'ufficio, di 6 caratteri",
            )
        elif (customer.country or "").strip().upper() != "IT":
            # Checked before the SDI code, not after: a foreign customer that happens to
            # carry one — copied in by hand, or left behind by a country change — is
            # still a foreign customer, and routing the file to an Italian recipient's
            # code would send it to somebody else entirely.
            recipient = etree.SubElement(block, "CodiceDestinatario")
            recipient.text = CODE_RECIPIENT_FOREIGN
        elif sdi_code:
            self._text(
                block,
                "CodiceDestinatario",
                sdi_code,
                entity="customer",
                field="sdi_code",
                pattern=CODE_RECIPIENT_RE,
            )
        elif (customer.certified_email or "").strip():
            recipient = etree.SubElement(block, "CodiceDestinatario")
            recipient.text = CODE_RECIPIENT_FALLBACK
        else:
            # The previous system emitted an empty element here: an invalid file, produced with no
            # error at all.
            raise ValidationFailed(
                "customer",
                "sdi_code",
                "serve un codice destinatario (SDI) oppure una PEC per emettere la fattura",
                expected="un codice destinatario di 7 caratteri oppure una PEC",
                in_words=True,
            )

        if (legal_entity.email or "").strip():
            contacts = etree.SubElement(block, "ContattiTrasmittente")
            self._text(
                contacts,
                "Email",
                (legal_entity.email or "").strip(),
                entity="legal_entity",
                field="email",
                max_length=EMAIL_MAX,
                min_length=EMAIL_MIN,
                pattern=EMAIL_CONTACTS_RE,
                pattern_expected=EMAIL_PATTERN_EXPECTED,
            )
        if (
            self.format_of(invoice) == FORMAT_TRANSMISSION
            and not sdi_code
            and (customer.certified_email or "").strip()
        ):
            self._text(
                block,
                "PECDestinatario",
                (customer.certified_email or "").strip(),
                entity="customer",
                field="certified_email",
                max_length=EMAIL_MAX,
                pattern=EMAIL_RE,
                pattern_expected=EMAIL_PATTERN_EXPECTED,
            )

    def _supplier(self, header: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: DatiAnagrafici(IdFiscaleIVA, CodiceFiscale?, Anagrafica,
        ..., RegimeFiscale), Sede, ..., Contatti?.

        `IdFiscaleIVA` is **mandatory** in the vendored schema's own
        `DatiAnagraficiCedenteType` -- unlike `DatiAnagraficiCessionarioType`, where it
        is `minOccurs="0"` -- so, unlike the customer side, a `CedentePrestatore` with
        only a fiscal code and no VAT number would validate the element order but not
        the content: `IdFiscaleIVA` would simply be missing from a required position.
        This also matches Italian practice: an entity with no partita IVA is not a VAT
        subject and cannot be a `CedentePrestatore` on a FatturaPA document at all.
        """
        legal_entity = invoice.snapshot.legal_entity
        supplier = etree.SubElement(header, "CedentePrestatore")
        master_data = etree.SubElement(supplier, "DatiAnagrafici")

        vat_number = normalise_fiscal_id(legal_entity.vat_number)
        if vat_number is None:
            raise ValidationFailed(
                "legal_entity",
                "vat_number",
                "l'emittente deve avere una partita IVA valida: IdFiscaleIVA e' "
                "obbligatorio per il cedente/prestatore",
                expected="11 cifre",
                in_words=True,
            )
        self._id_tax(master_data, "IdFiscaleIVA", vat_number, "legal_entity", "vat_number")
        tax_code = normalise_fiscal_id(legal_entity.tax_code)
        if tax_code is not None:
            self._text(
                master_data,
                "CodiceFiscale",
                tax_code,
                entity="legal_entity",
                field="tax_code",
            )
        self._directory(master_data, legal_entity, "legal_entity")
        regime = etree.SubElement(master_data, "RegimeFiscale")
        # From `fiscal_profile.regime_code`, never a constant in the source: the
        # whole point of the profile. `None` is a foreign legal entity's profile (REB-619),
        # which `InvoiceService.export_xml` refuses before this writer runs; refused
        # again here, since a writer that emitted an empty `RegimeFiscale` would hand
        # the SdI a file it rejects with a message nobody can act on.
        if invoice.snapshot.tax.regime_code is None:
            raise ValidationFailed(
                "fiscal_profile",
                "regime_code",
                "questa azienda non emette fatture elettroniche: il suo profilo fiscale "
                "non ha un codice regime",
                expected="un codice da RF01 a RF19",
                in_words=True,
            )
        regime.text = invoice.snapshot.tax.regime_code

        self._headquarters(supplier, legal_entity, "legal_entity")

        phone = (legal_entity.phone or "").strip()
        email = (legal_entity.email or "").strip()
        if phone or email:
            contacts = etree.SubElement(supplier, "Contatti")
            if phone:
                # FPR12's Telefono is 5-12 characters with no spaces or plus sign
                # allowed by the pattern; strip the presentation characters a user
                # typed rather than refusing a perfectly good number.
                digits = re.sub(r"[^0-9]", "", phone)[:PHONE_MAX]
                if len(digits) >= 5:
                    node = etree.SubElement(contacts, "Telefono")
                    node.text = digits
            if email:
                self._text(
                    contacts,
                    "Email",
                    email,
                    entity="legal_entity",
                    field="email",
                    max_length=EMAIL_MAX,
                    min_length=EMAIL_MIN,
                    pattern=EMAIL_CONTACTS_RE,
                    pattern_expected=EMAIL_PATTERN_EXPECTED,
                )

    def _buyer(self, header: etree._Element, customer: PartySnapshot) -> None:
        """Sequence: DatiAnagrafici(IdFiscaleIVA?, CodiceFiscale?, Anagrafica), Sede, ...

        Both identifiers are optional in the schema and at least one of them is not
        optional to the SdI, so `check_recipient_identity` is what stands between a
        recipient with neither and a rejection after the number is spent. It runs in
        `to_bytes` as well, and is called again here because this method is the one that
        decides not to write an element: a check one call away from the decision it
        guards is a check a later edit can walk past.
        """
        check_recipient_identity(customer)
        buyer = etree.SubElement(header, "CessionarioCommittente")
        master_data = etree.SubElement(buyer, "DatiAnagrafici")
        country = (customer.country or "IT").strip().upper()
        # Italy's two shapes are checked; every other country's is not, because there is
        # no shape to check. See `normalise_foreign_fiscal_id`.
        vat_number = (
            normalise_fiscal_id(customer.vat_number)
            if country == "IT"
            else normalise_foreign_fiscal_id(customer.vat_number)
        )
        if vat_number is not None:
            self._id_tax(
                master_data,
                "IdFiscaleIVA",
                vat_number,
                "customer",
                "vat_number",
                country=country,
            )
        tax_code = normalise_fiscal_id(customer.tax_code)
        if tax_code is not None:
            self._text(
                master_data,
                "CodiceFiscale",
                tax_code,
                entity="customer",
                field="tax_code",
            )
        self._directory(master_data, customer, "customer")
        self._headquarters(buyer, customer, "customer")

    # ---- body ------------------------------------------------------------------

    def _general_data(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: TipoDocumento, Divisa, Data, Numero, DatiRitenuta*, DatiBollo?,
        DatiCassaPrevidenziale*, ScontoMaggiorazione*, ImportoTotaleDocumento?,
        Arrotondamento?, Causale*, Art73?.

        Then `DatiOrdineAcquisto`, the next element of `DatiGenerali`, when the document
        has an order."""
        general = etree.SubElement(body, "DatiGenerali")
        document = etree.SubElement(general, "DatiGeneraliDocumento")

        type = etree.SubElement(document, "TipoDocumento")
        type.text = invoice.sdi_document_type
        currency = etree.SubElement(document, "Divisa")
        currency.text = invoice.currency
        data = etree.SubElement(document, "Data")
        # A `date`, formatted with `isoformat()`. Never `toISOString()` on an instant:
        # that is what put an invoice issued on 31 December at 23:30 CET into the next
        # fiscal year.
        data.text = self._iso(invoice.issue_date)
        number = etree.SubElement(document, "Numero")
        number.text = number_complete(invoice.year, invoice.number)

        self._withholding_data(document, invoice)

        if invoice.stamp_duty > Decimal("0.00"):
            stamp_duty = etree.SubElement(document, "DatiBollo")
            virtual = etree.SubElement(stamp_duty, "BolloVirtuale")
            virtual.text = STAMP_DUTY_VIRTUAL
            amount = etree.SubElement(stamp_duty, "ImportoBollo")
            amount.text = format_amount_2(invoice.stamp_duty)

        total = etree.SubElement(document, "ImportoTotaleDocumento")
        # The stamp duty is not part of the total: `DatiBollo` declares that the
        # issuer settled it virtually, and charging it back would need a line with
        # `Natura N1` -- explicitly out of scope. The withholding is not taken off it
        # either, nor the VAT of a split payment: the total is what the document prints,
        # and what the customer pays is `ImportoPagamento`.
        total.text = format_amount_2(invoice.total)

        if (invoice.memo or "").strip():
            self._text(
                document,
                "Causale",
                invoice.memo or "",
                entity="invoice",
                field="memo",
                max_length=MEMO_MAX,
            )

        self._purchase_order_data(general, invoice)

    def _withholding_data(self, document: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence (`DatiRitenutaType`): TipoRitenuta, ImportoRitenuta, AliquotaRitenuta,
        CausalePagamento. One block, only when the invoice carries a withholding
        (REB-994). The type and the reason are the pack's own, the amount and the rate the
        row's, frozen at issue."""
        if invoice.withholding_amount <= Decimal("0.00"):
            return
        law = current_pack(invoice.snapshot.tax.pack_id).sales_withholding
        if law is None or invoice.withholding_rate is None:
            raise ValidationFailed(
                "invoice",
                "withholding_amount",
                "la fattura porta una ritenuta ma non ne dichiara l'aliquota: "
                "non si esporta una ritenuta che il regime non conosce",
                expected="una ritenuta con la sua aliquota",
                in_words=True,
            )
        block = etree.SubElement(document, "DatiRitenuta")
        withholding_type = etree.SubElement(block, "TipoRitenuta")
        withholding_type.text = law.taxpayer_type
        amount = etree.SubElement(block, "ImportoRitenuta")
        amount.text = format_amount_2(invoice.withholding_amount)
        rate = etree.SubElement(block, "AliquotaRitenuta")
        rate.text = format_rate(invoice.withholding_rate)
        reason = etree.SubElement(block, "CausalePagamento")
        reason.text = law.payment_reason

    @staticmethod
    def _computed(invoice: InvoiceForExport) -> list[ComputedLine]:
        return [
            ComputedLine(
                line_number=r.line_number,
                description=r.description,
                quantity=r.quantity,
                unit_of_measure=r.unit_of_measure,
                unit_price=r.unit_price,
                discount_percentage=r.discount_percentage,
                discount_amount=r.discount_amount,
                total_price=r.total_price,
                vat_rate=r.vat_rate,
                vat_nature=r.vat_nature,
                legal_reference=r.legal_reference,
            )
            for r in invoice.lines
        ]

    def _purchase_order_data(self, general: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence (`DatiDocumentiCorrelatiType`): RiferimentoNumeroLinea*, IdDocumento,
        Data?, NumItem?, CodiceCommessaConvenzione?, CodiceCUP?, CodiceCIG?.

        One block, after `DatiGeneraliDocumento`, only when the document has an order
        (REB-693). No `RiferimentoNumeroLinea`: the order covers every line, which is
        what the element's absence means. CUP before CIG is the schema's order.
        """
        if not (invoice.purchase_order_number or "").strip():
            return
        order = etree.SubElement(general, "DatiOrdineAcquisto")
        self._text(
            order,
            "IdDocumento",
            invoice.purchase_order_number or "",
            entity="invoice",
            field="purchase_order_number",
            max_length=ID_DOCUMENT_MAX,
            pattern=BASIC_LATIN_RE,
        )
        if invoice.purchase_order_date is not None:
            data = etree.SubElement(order, "Data")
            data.text = self._iso(invoice.purchase_order_date)
        if invoice.purchase_order_project_code:
            self._text(
                order,
                "CodiceCUP",
                invoice.purchase_order_project_code,
                entity="invoice",
                field="purchase_order_project_code",
                max_length=CODE_PROJECT_CODE_MAX,
                pattern=BASIC_LATIN_RE,
            )
        if invoice.purchase_order_tender_code:
            self._text(
                order,
                "CodiceCIG",
                invoice.purchase_order_tender_code,
                entity="invoice",
                field="purchase_order_tender_code",
                max_length=CODE_TENDER_CODE_MAX,
                pattern=BASIC_LATIN_RE,
            )

    def _goods_services_data(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        goods = etree.SubElement(body, "DatiBeniServizi")
        for invoice_line in invoice.lines:
            self._detail_line(goods, invoice_line, invoice)
        for group in build_recap(self._computed(invoice)):
            self._summary_data(goods, group, split_payment=invoice.split_payment)

    def _detail_line(
        self, parent: etree._Element, invoice_line: InvoiceLineRead, invoice: InvoiceForExport
    ) -> None:
        """Sequence: NumeroLinea, TipoCessionePrestazione?, CodiceArticolo*,
        Descrizione, Quantita?, UnitaMisura?, DataInizioPeriodo?, DataFinePeriodo?,
        PrezzoUnitario, ScontoMaggiorazione*, PrezzoTotale, AliquotaIVA, Ritenuta?,
        VAT nature?, RiferimentoAmministrazione?, AltriDatiGestionali*.

        `UnitaMisura` comes *after* `Quantita` and *before* `PrezzoUnitario`: getting
        that wrong is a schema-invalid file with every value correct. The two period
        dates sit between the two and are the invoice's, not the line's (ORB-61): the
        period is header level on the row, and every line repeats it because the schema
        has no place for it on the document.
        """
        line = etree.SubElement(parent, "DettaglioLinee")
        number = etree.SubElement(line, "NumeroLinea")
        # A real line number per real line. The previous system hardcoded 1, quantity 1 and the
        # whole total as the unit price, so the detail of the work never reached the
        # customer.
        number.text = str(invoice_line.line_number)
        self._text(
            line,
            "Descrizione",
            invoice_line.description,
            entity="invoice_line",
            field="description",
            max_length=DESCRIPTION_MAX,
        )
        quantity = etree.SubElement(line, "Quantita")
        quantity.text = format_amount_8(invoice_line.quantity)
        if (invoice_line.unit_of_measure or "").strip():
            self._text(
                line,
                "UnitaMisura",
                invoice_line.unit_of_measure or "",
                entity="invoice_line",
                field="unit_of_measure",
                max_length=UNIT_OF_MEASURE_MAX,
                pattern=BASIC_LATIN_RE,
            )
        if invoice.accrual_from is not None and invoice.accrual_to is not None:
            # Both or neither, which the row's own CHECK already guarantees: an empty
            # `<DataInizioPeriodo/>` would be a schema error, and a period invented from
            # the emission date would be a fact nobody stated.
            start = etree.SubElement(line, "DataInizioPeriodo")
            start.text = self._iso(invoice.accrual_from)
            end = etree.SubElement(line, "DataFinePeriodo")
            end.text = self._iso(invoice.accrual_to)
        price = etree.SubElement(line, "PrezzoUnitario")
        price.text = format_amount_8(invoice_line.unit_price)
        # `ScontoMaggiorazione` is deliberately not emitted: the discount is already
        # inside `total_price` (totals.py::line_total), and declaring it twice would
        # make the SdI's own check on PrezzoTotale fail.
        total = etree.SubElement(line, "PrezzoTotale")
        total.text = format_amount_2(invoice_line.total_price)
        rate = etree.SubElement(line, "AliquotaIVA")
        rate.text = format_rate(invoice_line.vat_rate)
        if invoice.withholding_amount > Decimal("0.00"):
            # Every line of an invoice with a withholding is part of its base: the fee and
            # the expenses refunded at cost are (A5), and the ordinary regime writes no line
            # that is not one of the two.
            withheld = etree.SubElement(line, "Ritenuta")
            withheld.text = WITHHOLDING_SUBJECT
        if invoice_line.vat_nature:
            vat_nature = etree.SubElement(line, "Natura")
            vat_nature.text = invoice_line.vat_nature

    def _summary_data(
        self, parent: etree._Element, group: RecapGroup, *, split_payment: bool
    ) -> None:
        """Sequence: AliquotaIVA, VAT nature?, SpeseAccessorie?, Arrotondamento?,
        ImponibileImporto, Imposta, EsigibilitaIVA?, RiferimentoNormativo?."""
        recap = etree.SubElement(parent, "DatiRiepilogo")
        rate = etree.SubElement(recap, "AliquotaIVA")
        rate.text = format_rate(group.vat_rate)
        if group.vat_nature:
            vat_nature = etree.SubElement(recap, "Natura")
            vat_nature.text = group.vat_nature
        taxable_amount = etree.SubElement(recap, "ImponibileImporto")
        taxable_amount.text = format_amount_2(group.taxable_amount)
        vat_amount = etree.SubElement(recap, "Imposta")
        vat_amount.text = format_amount_2(group.vat_amount)
        vat_collectability = etree.SubElement(recap, "EsigibilitaIVA")
        # `S` only where there is VAT for the customer to pay: a group with no VAT
        # (a service to a business abroad) stays `I`, which is what the lines of a split
        # payment invoice that carry none say.
        vat_collectability.text = (
            VAT_COLLECTABILITY_SPLIT
            if split_payment and group.vat_rate > Decimal("0.00")
            else VAT_COLLECTABILITY_VAT
        )
        if group.vat_nature and group.legal_reference:
            # A real normative reference, from the profile. The previous system sent the
            # *description of the code* ("N2.2 (non soggette - altri casi)") in this field.
            self._text(
                recap,
                "RiferimentoNormativo",
                group.legal_reference,
                entity="fiscal_profile",
                field="legal_reference",
                max_length=LEGAL_REFERENCE_MAX,
            )

    def _payment_data(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: CondizioniPagamento, DettaglioPagamento+; and inside it
        Beneficiario?, ModalitaPagamento, DataRiferimentoTerminiPagamento?,
        GiorniTerminiPagamento?, DataScadenzaPagamento?, ImportoPagamento, ..., IBAN?."""
        tax = invoice.snapshot.tax
        payment = etree.SubElement(body, "DatiPagamento")
        self._text(
            payment,
            "CondizioniPagamento",
            tax.payment_terms,
            entity="fiscal_profile",
            field="payment_terms",
            pattern=TYPE_PAYMENT_RE,
        )
        detail = etree.SubElement(payment, "DettaglioPagamento")
        self._text(
            detail,
            "ModalitaPagamento",
            tax.payment_method,
            entity="fiscal_profile",
            field="payment_method",
            pattern=TYPE_PAYMENT_RE,
        )
        if invoice.due_date is not None:
            due_date = etree.SubElement(detail, "DataScadenzaPagamento")
            due_date.text = self._iso(invoice.due_date)
        amount = etree.SubElement(detail, "ImportoPagamento")
        # What the customer pays: the total less the withholding it keeps and, on a split
        # payment, the VAT it pays to the tax office itself (`net_receivable`).
        amount.text = format_amount_2(invoice.net_receivable)
        if (tax.iban or "").strip():
            self._text(
                detail,
                "IBAN",
                (tax.iban or "").strip().replace(" ", ""),
                entity="fiscal_profile",
                field="iban",
                pattern=IBAN_RE,
            )

    @staticmethod
    def _iso(value: date) -> str:
        """A `date`'s own ISO form.

        Deliberately not a timestamp conversion. The previous system's `formatIsoDate` called
        `toISOString()`, i.e. projected an instant through UTC: an invoice created on
        31 December at 23:30 CET came out dated 1 January, so its fiscal year was
        wrong on an immutable document. There is no instant here to get wrong.
        """
        return value.isoformat()
