"""The FatturaPA FPR12 generator.

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
from pigrocrm.core.invoices.fatturapa.checks import (
    check_party_exportable,
    check_recipient_identity,
    check_text,
)
from pigrocrm.core.invoices.fatturapa.constants import (
    BASIC_LATIN_RE,
    BOLLO_VIRTUALE,
    CAP_ESTERO,
    CAP_RE,
    CAUSALE_MAX,
    CODICE_CIG_MAX,
    CODICE_CUP_MAX,
    CODICE_DESTINATARIO_ESTERO,
    CODICE_DESTINATARIO_FALLBACK,
    CODICE_DESTINATARIO_RE,
    COMUNE_MAX,
    DENOMINAZIONE_MAX,
    DESCRIZIONE_MAX,
    EMAIL_CONTATTI_RE,
    EMAIL_MAX,
    EMAIL_MIN,
    EMAIL_PATTERN_EXPECTED,
    EMAIL_RE,
    ESIGIBILITA_IVA,
    FORMATO_TRASMISSIONE,
    FPR12_NAMESPACE,
    IBAN_RE,
    ID_CODICE_MAX,
    ID_DOCUMENTO_MAX,
    INDIRIZZO_MAX,
    ISSUER_ENTITY,
    NAZIONE_RE,
    NSMAP,
    PROVINCIA_RE,
    RECIPIENT_ENTITY,
    RIFERIMENTO_NORMATIVO_MAX,
    TELEFONO_MAX,
    TIPO_PAGAMENTO_RE,
    UNITA_MISURA_MAX,
)
from pigrocrm.core.invoices.fatturapa.normalise import (
    indirizzo_xml,
    is_italian,
    normalise_fiscal_id,
    normalise_foreign_fiscal_id,
)
from pigrocrm.core.invoices.naming import numero_completo, progressivo_invio
from pigrocrm.core.invoices.schemas import InvoiceForExport, InvoiceLineRead, PartySnapshot
from pigrocrm.core.invoices.totals import (
    ComputedLine,
    RiepilogoGroup,
    build_riepilogo,
    format_amount_2,
    format_amount_8,
    format_rate,
)


class FatturaPAExporter:
    """`InvoiceForExport` in, `bytes` out. No database, no profile lookup, no clock."""

    def to_bytes(self, invoice: InvoiceForExport) -> bytes:
        if not invoice.righe:
            raise ValidationFailed(
                "invoice",
                "righe",
                "una fattura senza righe non e' esportabile",
                expected="almeno una riga",
            )
        emittente = invoice.snapshot.emittente
        cliente = invoice.snapshot.cliente
        check_party_exportable(emittente, ISSUER_ENTITY)
        check_party_exportable(cliente, RECIPIENT_ENTITY)
        check_recipient_identity(cliente)

        # `lxml-stubs` types `nsmap` as `Mapping[str, str]` here even though the
        # *property* it defines a few lines above is `Dict[Optional[str], str]` --
        # the `None` key (default namespace) is real and readable at runtime, the
        # stub is simply incomplete for the write side. `NSMAP` has no `None` key in
        # this module, so this is a stub gap, not a real type mismatch.
        root = etree.Element(f"{{{FPR12_NAMESPACE}}}FatturaElettronica", nsmap=NSMAP)  # type: ignore[arg-type]
        root.set("versione", FORMATO_TRASMISSIONE)

        header = etree.SubElement(root, "FatturaElettronicaHeader")
        self._dati_trasmissione(header, invoice)
        self._cedente(header, invoice)
        self._cessionario(header, cliente)

        body = etree.SubElement(root, "FatturaElettronicaBody")
        self._dati_generali(body, invoice)
        self._dati_beni_servizi(body, invoice)
        self._dati_pagamento(body, invoice)

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

    def _anagrafica(self, parent: etree._Element, party: PartySnapshot, entity: str) -> None:
        """`Anagrafica/Denominazione`, even for a natural person with only a fiscal
        code: FPR12 allows `Nome`/`Cognome` instead, but `customers` has a single
        `ragione_sociale`, so there is no choice to make."""
        anagrafica = etree.SubElement(parent, "Anagrafica")
        self._text(
            anagrafica,
            "Denominazione",
            party.ragione_sociale,
            entity=entity,
            field="ragione_sociale",
            max_length=DENOMINAZIONE_MAX,
        )

    def _sede(self, parent: etree._Element, party: PartySnapshot, entity: str) -> None:
        """`Indirizzo`, `CAP`, `Comune`, `Provincia?`, `Nazione`, the way the technical
        specifications want them for the party's country.

        An Italian address is written as stored. A foreign one is written the way the
        SdI describes it: `CAP` is the `00000` placeholder whatever the record holds,
        `Provincia` is left out, and the real postcode rides at the end of `Indirizzo`
        (see `indirizzo_xml`, shared with the pre-check). Applying the five-digit
        pattern to a real foreign postcode here is what ORB-38 was: `issue` had already
        spent the number by the time this method refused.
        """
        italiano = is_italian(party)
        sede = etree.SubElement(parent, "Sede")
        self._text(
            sede,
            "Indirizzo",
            indirizzo_xml(party),
            entity=entity,
            field="indirizzo",
            max_length=INDIRIZZO_MAX,
        )
        self._text(
            sede,
            "CAP",
            party.cap.strip() if italiano else CAP_ESTERO,
            entity=entity,
            field="cap",
            pattern=CAP_RE,
        )
        self._text(
            sede, "Comune", party.comune, entity=entity, field="comune", max_length=COMUNE_MAX
        )
        # Omitted rather than emitted empty when there is none. `Provincia` is optional
        # in FPR12 for exactly this reason -- a London address has no two-letter Italian
        # province and inventing one would be a false statement about where the customer
        # is. Writing it as `""` would be worse still: `check_party_exportable` keeps it
        # mandatory for an Italian address, so a blank one here can only mean a party
        # that is legitimately without. Outside Italy it is omitted whatever the record
        # holds, since the element is the code of an Italian province and nothing else.
        provincia = (party.provincia or "").strip().upper()
        if italiano and provincia:
            self._text(
                sede,
                "Provincia",
                provincia,
                entity=entity,
                field="provincia",
                pattern=PROVINCIA_RE,
            )
        self._text(
            sede,
            "Nazione",
            party.nazione.strip().upper(),
            entity=entity,
            field="nazione",
            pattern=NAZIONE_RE,
        )

    def _id_fiscale(
        self,
        parent: etree._Element,
        tag: str,
        id_codice: str,
        entity: str,
        field: str,
        paese: str = "IT",
    ) -> None:
        """`IdPaese` says which country's register `IdCodice` belongs to.

        It was hard-coded to `IT`, which was true of every party this exporter could
        reach while a foreign customer was refused outright — and became a lie the
        moment one was allowed through: a British VAT number announced as an Italian
        one. The emitter is Italian by definition of this product and keeps the default;
        the customer's is read from the customer.
        """
        block = etree.SubElement(parent, tag)
        elemento = etree.SubElement(block, "IdPaese")
        elemento.text = paese.strip().upper()
        self._text(
            block,
            "IdCodice",
            id_codice,
            entity=entity,
            field=field,
            max_length=ID_CODICE_MAX,
        )

    # ---- header ----------------------------------------------------------------

    def _dati_trasmissione(self, header: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: IdTrasmittente, ProgressivoInvio, FormatoTrasmissione,
        CodiceDestinatario, ContattiTrasmittente?, PECDestinatario?."""
        emittente = invoice.snapshot.emittente
        cliente = invoice.snapshot.cliente
        block = etree.SubElement(header, "DatiTrasmissione")

        # `IdTrasmittente/IdCodice` may be the issuer's *fiscal code* rather than the
        # VAT number, which is not obvious from the schema. Fiscal code first because
        # that is what the working generator sent.
        trasmittente = normalise_fiscal_id(emittente.codice_fiscale) or normalise_fiscal_id(
            emittente.partita_iva
        )
        if trasmittente is None:
            raise ValidationFailed(
                "emitter_profile",
                "codice_fiscale",
                "serve un codice fiscale o una partita IVA dell'emittente per il "
                "blocco IdTrasmittente",
                expected="11 cifre oppure 16 caratteri",
            )
        self._id_fiscale(block, "IdTrasmittente", trasmittente, "emitter_profile", "codice_fiscale")

        progressivo = etree.SubElement(block, "ProgressivoInvio")
        progressivo.text = progressivo_invio(invoice.anno, invoice.numero)

        formato = etree.SubElement(block, "FormatoTrasmissione")
        # Not redundant with the root's own `versione` attribute: the SdI reads it here.
        formato.text = FORMATO_TRASMISSIONE

        codice_sdi = (cliente.codice_sdi or "").strip().upper()
        if (cliente.nazione or "").strip().upper() != "IT":
            # Checked before the SDI code, not after: a foreign customer that happens to
            # carry one — copied in by hand, or left behind by a country change — is
            # still a foreign customer, and routing the file to an Italian recipient's
            # code would send it to somebody else entirely.
            destinatario = etree.SubElement(block, "CodiceDestinatario")
            destinatario.text = CODICE_DESTINATARIO_ESTERO
        elif codice_sdi:
            self._text(
                block,
                "CodiceDestinatario",
                codice_sdi,
                entity="customer",
                field="codice_sdi",
                pattern=CODICE_DESTINATARIO_RE,
            )
        elif (cliente.pec or "").strip():
            destinatario = etree.SubElement(block, "CodiceDestinatario")
            destinatario.text = CODICE_DESTINATARIO_FALLBACK
        else:
            # The previous system emitted an empty element here: an invalid file, produced with no
            # error at all.
            raise ValidationFailed(
                "customer",
                "codice_sdi",
                "serve un codice destinatario (SDI) oppure una PEC per emettere la fattura",
                expected="codice_sdi di 7 caratteri oppure pec",
            )

        if (emittente.email or "").strip():
            contatti = etree.SubElement(block, "ContattiTrasmittente")
            self._text(
                contatti,
                "Email",
                (emittente.email or "").strip(),
                entity="emitter_profile",
                field="email",
                max_length=EMAIL_MAX,
                min_length=EMAIL_MIN,
                pattern=EMAIL_CONTATTI_RE,
                pattern_expected=EMAIL_PATTERN_EXPECTED,
            )
        if not codice_sdi and (cliente.pec or "").strip():
            self._text(
                block,
                "PECDestinatario",
                (cliente.pec or "").strip(),
                entity="customer",
                field="pec",
                max_length=EMAIL_MAX,
                pattern=EMAIL_RE,
                pattern_expected=EMAIL_PATTERN_EXPECTED,
            )

    def _cedente(self, header: etree._Element, invoice: InvoiceForExport) -> None:
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
        emittente = invoice.snapshot.emittente
        cedente = etree.SubElement(header, "CedentePrestatore")
        anagrafici = etree.SubElement(cedente, "DatiAnagrafici")

        piva = normalise_fiscal_id(emittente.partita_iva)
        if piva is None:
            raise ValidationFailed(
                "emitter_profile",
                "partita_iva",
                "l'emittente deve avere una partita IVA valida: IdFiscaleIVA e' "
                "obbligatorio per il cedente/prestatore",
                expected="11 cifre",
            )
        self._id_fiscale(anagrafici, "IdFiscaleIVA", piva, "emitter_profile", "partita_iva")
        codice_fiscale = normalise_fiscal_id(emittente.codice_fiscale)
        if codice_fiscale is not None:
            self._text(
                anagrafici,
                "CodiceFiscale",
                codice_fiscale,
                entity="emitter_profile",
                field="codice_fiscale",
            )
        self._anagrafica(anagrafici, emittente, "emitter_profile")
        regime = etree.SubElement(anagrafici, "RegimeFiscale")
        # From `fiscal_profile.codice_regime`, never a constant in the source: the
        # whole point of the profile. `None` is a foreign azienda's profile (REB-619),
        # which `InvoiceService.export_xml` refuses before this writer runs; refused
        # again here, since a writer that emitted an empty `RegimeFiscale` would hand
        # the SdI a file it rejects with a message nobody can act on.
        if invoice.snapshot.fiscale.codice_regime is None:
            raise ValidationFailed(
                "fiscal_profile",
                "codice_regime",
                "questa azienda non emette fatture elettroniche: il suo profilo fiscale "
                "non ha un codice regime",
                expected="un codice da RF01 a RF19",
            )
        regime.text = invoice.snapshot.fiscale.codice_regime

        self._sede(cedente, emittente, "emitter_profile")

        telefono = (emittente.telefono or "").strip()
        email = (emittente.email or "").strip()
        if telefono or email:
            contatti = etree.SubElement(cedente, "Contatti")
            if telefono:
                # FPR12's Telefono is 5-12 characters with no spaces or plus sign
                # allowed by the pattern; strip the presentation characters a user
                # typed rather than refusing a perfectly good number.
                digits = re.sub(r"[^0-9]", "", telefono)[:TELEFONO_MAX]
                if len(digits) >= 5:
                    node = etree.SubElement(contatti, "Telefono")
                    node.text = digits
            if email:
                self._text(
                    contatti,
                    "Email",
                    email,
                    entity="emitter_profile",
                    field="email",
                    max_length=EMAIL_MAX,
                    min_length=EMAIL_MIN,
                    pattern=EMAIL_CONTATTI_RE,
                    pattern_expected=EMAIL_PATTERN_EXPECTED,
                )

    def _cessionario(self, header: etree._Element, cliente: PartySnapshot) -> None:
        """Sequence: DatiAnagrafici(IdFiscaleIVA?, CodiceFiscale?, Anagrafica), Sede, ...

        Both identifiers are optional in the schema and at least one of them is not
        optional to the SdI, so `check_recipient_identity` is what stands between a
        recipient with neither and a rejection after the number is spent. It runs in
        `to_bytes` as well, and is called again here because this method is the one that
        decides not to write an element: a check one call away from the decision it
        guards is a check a later edit can walk past.
        """
        check_recipient_identity(cliente)
        cessionario = etree.SubElement(header, "CessionarioCommittente")
        anagrafici = etree.SubElement(cessionario, "DatiAnagrafici")
        paese = (cliente.nazione or "IT").strip().upper()
        # Italy's two shapes are checked; every other country's is not, because there is
        # no shape to check. See `normalise_foreign_fiscal_id`.
        piva = (
            normalise_fiscal_id(cliente.partita_iva)
            if paese == "IT"
            else normalise_foreign_fiscal_id(cliente.partita_iva)
        )
        if piva is not None:
            self._id_fiscale(
                anagrafici,
                "IdFiscaleIVA",
                piva,
                "customer",
                "partita_iva",
                paese=paese,
            )
        codice_fiscale = normalise_fiscal_id(cliente.codice_fiscale)
        if codice_fiscale is not None:
            self._text(
                anagrafici,
                "CodiceFiscale",
                codice_fiscale,
                entity="customer",
                field="codice_fiscale",
            )
        self._anagrafica(anagrafici, cliente, "customer")
        self._sede(cessionario, cliente, "customer")

    # ---- body ------------------------------------------------------------------

    def _dati_generali(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: TipoDocumento, Divisa, Data, Numero, DatiRitenuta*, DatiBollo?,
        DatiCassaPrevidenziale*, ScontoMaggiorazione*, ImportoTotaleDocumento?,
        Arrotondamento?, Causale*, Art73?.

        Then `DatiOrdineAcquisto`, the next element of `DatiGenerali`, when the document
        has an order."""
        generali = etree.SubElement(body, "DatiGenerali")
        documento = etree.SubElement(generali, "DatiGeneraliDocumento")

        tipo = etree.SubElement(documento, "TipoDocumento")
        tipo.text = invoice.tipo_documento
        divisa = etree.SubElement(documento, "Divisa")
        divisa.text = invoice.divisa
        data = etree.SubElement(documento, "Data")
        # A `date`, formatted with `isoformat()`. Never `toISOString()` on an instant:
        # that is what put an invoice issued on 31 December at 23:30 CET into the next
        # fiscal year.
        data.text = self._iso(invoice.data_emissione)
        numero = etree.SubElement(documento, "Numero")
        numero.text = numero_completo(invoice.anno, invoice.numero)

        if invoice.bollo > Decimal("0.00"):
            bollo = etree.SubElement(documento, "DatiBollo")
            virtuale = etree.SubElement(bollo, "BolloVirtuale")
            virtuale.text = BOLLO_VIRTUALE
            importo = etree.SubElement(bollo, "ImportoBollo")
            importo.text = format_amount_2(invoice.bollo)

        totale = etree.SubElement(documento, "ImportoTotaleDocumento")
        # The stamp duty is not part of the total: `DatiBollo` declares that the
        # issuer settled it virtually, and charging it back would need a line with
        # `Natura N1` -- explicitly out of scope.
        totale.text = format_amount_2(invoice.totale)

        if (invoice.causale or "").strip():
            self._text(
                documento,
                "Causale",
                invoice.causale or "",
                entity="invoice",
                field="causale",
                max_length=CAUSALE_MAX,
            )

        self._dati_ordine_acquisto(generali, invoice)

    def _dati_ordine_acquisto(self, generali: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence (`DatiDocumentiCorrelatiType`): RiferimentoNumeroLinea*, IdDocumento,
        Data?, NumItem?, CodiceCommessaConvenzione?, CodiceCUP?, CodiceCIG?.

        One block, after `DatiGeneraliDocumento`, only when the document has an order
        (REB-693). No `RiferimentoNumeroLinea`: the order covers every line, which is
        what the element's absence means. CUP before CIG is the schema's order.
        """
        if not (invoice.ordine_numero or "").strip():
            return
        ordine = etree.SubElement(generali, "DatiOrdineAcquisto")
        self._text(
            ordine,
            "IdDocumento",
            invoice.ordine_numero or "",
            entity="invoice",
            field="ordine_numero",
            max_length=ID_DOCUMENTO_MAX,
            pattern=BASIC_LATIN_RE,
        )
        if invoice.ordine_data is not None:
            data = etree.SubElement(ordine, "Data")
            data.text = self._iso(invoice.ordine_data)
        if invoice.ordine_cup:
            self._text(
                ordine,
                "CodiceCUP",
                invoice.ordine_cup,
                entity="invoice",
                field="ordine_cup",
                max_length=CODICE_CUP_MAX,
                pattern=BASIC_LATIN_RE,
            )
        if invoice.ordine_cig:
            self._text(
                ordine,
                "CodiceCIG",
                invoice.ordine_cig,
                entity="invoice",
                field="ordine_cig",
                max_length=CODICE_CIG_MAX,
                pattern=BASIC_LATIN_RE,
            )

    def _dati_beni_servizi(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        beni = etree.SubElement(body, "DatiBeniServizi")
        for riga in invoice.righe:
            self._dettaglio_linea(beni, riga, invoice)
        for group in build_riepilogo(
            [
                ComputedLine(
                    numero_linea=r.numero_linea,
                    descrizione=r.descrizione,
                    quantita=r.quantita,
                    unita_misura=r.unita_misura,
                    prezzo_unitario=r.prezzo_unitario,
                    sconto_percentuale=r.sconto_percentuale,
                    sconto_importo=r.sconto_importo,
                    prezzo_totale=r.prezzo_totale,
                    aliquota_iva=r.aliquota_iva,
                    natura=r.natura,
                    riferimento_normativo=r.riferimento_normativo,
                )
                for r in invoice.righe
            ]
        ):
            self._dati_riepilogo(beni, group)

    def _dettaglio_linea(
        self, parent: etree._Element, riga: InvoiceLineRead, invoice: InvoiceForExport
    ) -> None:
        """Sequence: NumeroLinea, TipoCessionePrestazione?, CodiceArticolo*,
        Descrizione, Quantita?, UnitaMisura?, DataInizioPeriodo?, DataFinePeriodo?,
        PrezzoUnitario, ScontoMaggiorazione*, PrezzoTotale, AliquotaIVA, Ritenuta?,
        Natura?, RiferimentoAmministrazione?, AltriDatiGestionali*.

        `UnitaMisura` comes *after* `Quantita` and *before* `PrezzoUnitario`: getting
        that wrong is a schema-invalid file with every value correct. The two period
        dates sit between the two and are the invoice's, not the line's (ORB-61): the
        period is header level on the row, and every line repeats it because the schema
        has no place for it on the document.
        """
        linea = etree.SubElement(parent, "DettaglioLinee")
        numero = etree.SubElement(linea, "NumeroLinea")
        # A real line number per real line. The previous system hardcoded 1, quantity 1 and the
        # whole total as the unit price, so the detail of the work never reached the
        # customer.
        numero.text = str(riga.numero_linea)
        self._text(
            linea,
            "Descrizione",
            riga.descrizione,
            entity="invoice_line",
            field="descrizione",
            max_length=DESCRIZIONE_MAX,
        )
        quantita = etree.SubElement(linea, "Quantita")
        quantita.text = format_amount_8(riga.quantita)
        if (riga.unita_misura or "").strip():
            self._text(
                linea,
                "UnitaMisura",
                riga.unita_misura or "",
                entity="invoice_line",
                field="unita_misura",
                max_length=UNITA_MISURA_MAX,
                pattern=BASIC_LATIN_RE,
            )
        if invoice.competenza_da is not None and invoice.competenza_a is not None:
            # Both or neither, which the row's own CHECK already guarantees: an empty
            # `<DataInizioPeriodo/>` would be a schema error, and a period invented from
            # the emission date would be a fact nobody stated.
            inizio = etree.SubElement(linea, "DataInizioPeriodo")
            inizio.text = self._iso(invoice.competenza_da)
            fine = etree.SubElement(linea, "DataFinePeriodo")
            fine.text = self._iso(invoice.competenza_a)
        prezzo = etree.SubElement(linea, "PrezzoUnitario")
        prezzo.text = format_amount_8(riga.prezzo_unitario)
        # `ScontoMaggiorazione` is deliberately not emitted: the discount is already
        # inside `prezzo_totale` (totals.py::line_total), and declaring it twice would
        # make the SdI's own check on PrezzoTotale fail.
        totale = etree.SubElement(linea, "PrezzoTotale")
        totale.text = format_amount_2(riga.prezzo_totale)
        aliquota = etree.SubElement(linea, "AliquotaIVA")
        aliquota.text = format_rate(riga.aliquota_iva)
        if riga.natura:
            natura = etree.SubElement(linea, "Natura")
            natura.text = riga.natura

    def _dati_riepilogo(self, parent: etree._Element, group: RiepilogoGroup) -> None:
        """Sequence: AliquotaIVA, Natura?, SpeseAccessorie?, Arrotondamento?,
        ImponibileImporto, Imposta, EsigibilitaIVA?, RiferimentoNormativo?."""
        riepilogo = etree.SubElement(parent, "DatiRiepilogo")
        aliquota = etree.SubElement(riepilogo, "AliquotaIVA")
        aliquota.text = format_rate(group.aliquota_iva)
        if group.natura:
            natura = etree.SubElement(riepilogo, "Natura")
            natura.text = group.natura
        imponibile = etree.SubElement(riepilogo, "ImponibileImporto")
        imponibile.text = format_amount_2(group.imponibile)
        imposta = etree.SubElement(riepilogo, "Imposta")
        imposta.text = format_amount_2(group.imposta)
        esigibilita = etree.SubElement(riepilogo, "EsigibilitaIVA")
        esigibilita.text = ESIGIBILITA_IVA
        if group.natura and group.riferimento_normativo:
            # A real normative reference, from the profile. The previous system sent the
            # *description of the code* ("N2.2 (non soggette - altri casi)") in this field.
            self._text(
                riepilogo,
                "RiferimentoNormativo",
                group.riferimento_normativo,
                entity="fiscal_profile",
                field="riferimento_normativo",
                max_length=RIFERIMENTO_NORMATIVO_MAX,
            )

    def _dati_pagamento(self, body: etree._Element, invoice: InvoiceForExport) -> None:
        """Sequence: CondizioniPagamento, DettaglioPagamento+; and inside it
        Beneficiario?, ModalitaPagamento, DataRiferimentoTerminiPagamento?,
        GiorniTerminiPagamento?, DataScadenzaPagamento?, ImportoPagamento, ..., IBAN?."""
        fiscale = invoice.snapshot.fiscale
        pagamento = etree.SubElement(body, "DatiPagamento")
        self._text(
            pagamento,
            "CondizioniPagamento",
            fiscale.condizioni_pagamento,
            entity="fiscal_profile",
            field="condizioni_pagamento",
            pattern=TIPO_PAGAMENTO_RE,
        )
        dettaglio = etree.SubElement(pagamento, "DettaglioPagamento")
        self._text(
            dettaglio,
            "ModalitaPagamento",
            fiscale.modalita_pagamento,
            entity="fiscal_profile",
            field="modalita_pagamento",
            pattern=TIPO_PAGAMENTO_RE,
        )
        if invoice.data_scadenza is not None:
            scadenza = etree.SubElement(dettaglio, "DataScadenzaPagamento")
            scadenza.text = self._iso(invoice.data_scadenza)
        importo = etree.SubElement(dettaglio, "ImportoPagamento")
        importo.text = format_amount_2(invoice.totale)
        if (fiscale.iban or "").strip():
            self._text(
                dettaglio,
                "IBAN",
                (fiscale.iban or "").strip().replace(" ", ""),
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
