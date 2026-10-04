"""From a frozen `InvoiceForExport` to PDF bytes, through slice 2's pipeline unchanged.

Separate from `render/pdf.py` on purpose: that module is the generic Pandoc/Typst
runner and the only place in the codebase that spawns a process, and it must stay
ignorant of invoices. This module knows about invoices and nothing about subprocesses.

Every value in the scope is a **string already formatted for display**. Amounts are
never handed to the template as `Decimal` for the template to format, and never
re-parsed from text: the previous system applied a percentage to a total it had read
back out of a formatted string, and that is the class of defect this separation removes.

The layout is the one of the register this product inherited (ORB-102). Ivan's issued
invoices are the previous system's own PDFs, kept through `import_issued`, and a
document rendered here sits next to them in the same list: a full emitter block top
right, a `TD01 fattura | Numero | Data` line, the customer on two bar-separated lines,
a three-column detail table with the Natura where there is no rate, the payment code
with its words, a rule, a «Thank you!» with the contacts, `1.760,00 €` and `dd-mm-yyyy`.
Where this file departs from that layout it says so at the value that departs.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from pigrocrm.core.config import Settings
from pigrocrm.core.emitter.assets import logo_file_in
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.invoices.schemas import InvoiceForExport
from pigrocrm.core.money import round_money
from pigrocrm.core.render.pdf import ASSETS_DIR, logo_flags, render_pdf
from pigrocrm.core.templates.renderer import render_template

# One template for the fattura and the proforma, with the declaration under a
# condition: two files that were meant to stay identical drifted once (ORB-102), and a
# condition cannot drift.
INVOICE_TEMPLATE = ASSETS_DIR / "template-invoice.md"
# The invoice's own page header, not `render/pdf.py`'s `build_header`: offers and time
# reports keep the shared two-line header and the contacts footer, an invoice prints
# the emitter's whole fiscal identity top right and a page number below and nothing
# else, the way the inherited register does.
INVOICE_HEADER_TEMPLATE = ASSETS_DIR / "header-invoice.typ.template"

# In the body of the document, not a watermark: a watermark is a thing a print can
# lose, and this is one of four independent mechanisms keeping a proforma from being
# paid as an invoice. Asserted verbatim by the tests, so it is a constant rather than
# a string typed twice.
PROFORMA_DECLARATION = "FATTURA PROFORMA - NON COSTITUISCE FATTURA"

CODICE_DESTINATARIO_FALLBACK = "0000000"
_EMPTY = ""

# The words for FPR12's `RegimeFiscaleType`, as the header prints them after the code
# (`Regime fiscale: RF19 Regime forfettario`). A code this table does not know prints
# alone rather than failing the render: the profile validates the code, not this file.
REGIMI: dict[str, str] = {
    "RF01": "Regime ordinario",
    "RF02": "Contribuenti minimi",
    "RF04": "Agricoltura e attività connesse e pesca",
    "RF05": "Vendita sali e tabacchi",
    "RF06": "Commercio fiammiferi",
    "RF07": "Editoria",
    "RF08": "Gestione servizi telefonia pubblica",
    "RF09": "Rivendita documenti di trasporto pubblico e di sosta",
    "RF10": "Intrattenimenti, giochi e altre attività",
    "RF11": "Agenzie viaggi e turismo",
    "RF12": "Agriturismo",
    "RF13": "Vendite a domicilio",
    "RF14": "Rivendita beni usati, oggetti d'arte, d'antiquariato o da collezione",
    "RF15": "Agenzie di vendite all'asta",
    "RF16": "IVA per cassa P.A.",
    "RF17": "IVA per cassa",
    "RF18": "Altro",
    "RF19": "Regime forfettario",
}

# The word after the `TipoDocumento` code on the first line (`TD01 fattura`). The
# product issues `TD01` and nothing else today (`invoices/models.py`); a code this table
# does not know prints alone rather than as "fattura" by default.
TIPI_DOCUMENTO: dict[str, str] = {"TD01": "fattura"}

# The words for FPR12's `ModalitaPagamentoType`, for the DETTAGLI column
# (`MP05` in the first column, `Bonifico IBAN IT…` in the second). Same fallback rule.
MODALITA_PAGAMENTO: dict[str, str] = {
    "MP01": "Contanti",
    "MP02": "Assegno",
    "MP03": "Assegno circolare",
    "MP04": "Contanti presso Tesoreria",
    "MP05": "Bonifico",
    "MP06": "Vaglia cambiario",
    "MP07": "Bollettino bancario",
    "MP08": "Carta di pagamento",
    "MP09": "RID",
    "MP10": "RID utenze",
    "MP11": "RID veloce",
    "MP12": "RIBA",
    "MP13": "MAV",
    "MP14": "Quietanza erario",
    "MP15": "Giroconto su conti di contabilità speciale",
    "MP16": "Domiciliazione bancaria",
    "MP17": "Domiciliazione postale",
    "MP18": "Bollettino di c/c postale",
    "MP19": "SEPA Direct Debit",
    "MP20": "SEPA Direct Debit CORE",
    "MP21": "SEPA Direct Debit B2B",
    "MP22": "Trattenuta su somme già riscosse",
    "MP23": "PagoPA",
}


def format_euro(value: Decimal) -> str:
    """`1.760,00 €`: thousands with a point, decimals with a comma, the currency after
    a space. The display form, as distinct from `totals.format_amount_2`, which is
    FPR12's `1760.00` for the XML. Two audiences, two formatters, one stored value."""
    grouped = f"{round_money(value):,.2f}"
    return grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".") + " €"


def format_data(value: date) -> str:
    """`dd-mm-yyyy`, the inherited register's own spelling, on every date the document
    prints: emission, deadline, and the accrual period."""
    return f"{value:%d-%m-%Y}"


def format_iva(aliquota: Decimal, natura: str | None) -> str:
    """What the %IVA column shows for a line: the Natura when the line carries one
    (`N2.2` on a forfettario line, the way the register always printed it), the rate
    otherwise (`22%`, `4,5%`). A rate of zero with no Natura is not a line FPR12 lets
    through, so `0%` is printed rather than guessed at."""
    if natura:
        return natura
    return f"{aliquota.normalize():f}".replace(".", ",") + "%"


def indirizzo_display(party: dict[str, Any]) -> str:
    """The customer's address as one line, the way the country writes it (ORB-55).

    Computed here rather than assembled in the template, which is the direction this
    module already leans: every other value the template reads is a string this file
    formatted, and `template-invoice.md` hard-coded `({{cliente.provincia}})` -- so a
    London customer read `1 Old Street, EC1V 9HL London () GB` on the document, empty
    parentheses and all. A template cannot make that decision well: the province is a
    field of an Italian address and the parentheses are punctuation that belongs to it,
    and `{{#if}}` around the pair would have to be repeated in every template that ever
    prints an address.

    Italy keeps the shape it always had, `CAP Comune (PR)`. Outside it, the postcode is
    printed as the record holds it (`EC1V 9HL`, not the `00000` the XML carries -- see
    `fatturapa.CAP_ESTERO`) and the province is not printed at all. An Italian record
    with no province yet, which a proforma may well have, loses the parentheses rather
    than printing them empty: the same defect in the domestic case.
    """
    nazione = (party.get("nazione") or "").strip().upper()
    pezzi = [
        pezzo
        for pezzo in ((party.get("cap") or "").strip(), (party.get("comune") or "").strip())
        if pezzo
    ]
    provincia = (party.get("provincia") or "").strip().upper()
    if nazione == "IT" and provincia:
        pezzi.append(f"({provincia})")
    if nazione:
        pezzi.append(nazione)
    indirizzo = (party.get("indirizzo") or "").strip()
    resto = " ".join(pezzi)
    if indirizzo and resto:
        return f"{indirizzo}, {resto}"
    return indirizzo or resto


def identificativo_iva(party: dict[str, Any]) -> str:
    """`IT01234567890`: the country before the number, the way `IdPaese` precedes
    `IdCodice` in the XML, or `""` for a party with no VAT id. The previous system
    printed `IT` in front of a London customer's number; the country is the party's,
    and an empty `nazione` falls back to `IT` exactly as `fatturapa.py` does, so the
    PDF and the XML of one invoice never disagree on the prefix."""
    partita_iva = (party.get("partita_iva") or "").strip()
    if not partita_iva:
        return _EMPTY
    return f"{(party.get('nazione') or 'IT').strip().upper()}{partita_iva}"


def _bars(*segments: str) -> str:
    """Segments joined with ` | `, the empty ones dropped: a customer with no PEC loses
    the segment, never keeps a `PEC:` in front of nothing."""
    return " | ".join(segment for segment in segments if segment)


def _bars_space(*segments: str) -> str:
    """Like `_bars`, with a space: `RF19 Regime forfettario`, `Bonifico IBAN IT…`."""
    return " ".join(segment for segment in segments if segment)


def _labelled(label: str, value: str) -> str:
    return f"{label}: {value}" if value else _EMPTY


def build_scope(export: InvoiceForExport, *, riferimento: str | None) -> dict[str, Any]:
    """What the template can read.

    Built entirely from the snapshot and the stored totals. The live `emitter_profile`
    and `fiscal_profile` are not consulted, which is what makes a re-render a year
    later produce the same bytes (criterion 6) rather than a document that quietly
    reflects whatever the configuration says today.
    """
    snapshot = export.snapshot
    emittente = snapshot.emittente.model_dump(mode="json")
    cliente = snapshot.cliente.model_dump(mode="json")
    fiscale = snapshot.fiscale.model_dump(mode="json")
    codice_destinatario = (cliente.get("codice_sdi") or "").strip() or (
        CODICE_DESTINATARIO_FALLBACK if (cliente.get("pec") or "").strip() else _EMPTY
    )
    cliente_indirizzo = indirizzo_display(cliente)
    bollo = export.bollo > Decimal("0.00")
    dichiarazione_bollo = (
        f"Imposta di bollo di {format_euro(export.bollo)} assolta in modo virtuale a carico "
        "dell'emittente."
        if bollo
        else _EMPTY
    )
    proforma = riferimento is not None
    codice_regime = (fiscale.get("codice_regime") or "").strip()
    codice_pagamento = (fiscale.get("modalita_pagamento") or "").strip()
    iban = (fiscale.get("iban") or "").strip()
    return {
        "emittente": {
            **emittente,
            "identificativo_iva": identificativo_iva(emittente),
            "indirizzo_display": indirizzo_display(emittente),
        },
        "cliente": {
            **cliente,
            "codice_destinatario": codice_destinatario,
            "indirizzo_display": cliente_indirizzo,
            # Two lines, bar-separated, as the register prints its Committente block.
            "riga_identita": _bars(
                cliente.get("ragione_sociale") or _EMPTY,
                _labelled("IVA", identificativo_iva(cliente)),
                _labelled("CF", (cliente.get("codice_fiscale") or "").strip()),
            ),
            "riga_recapiti": _bars(
                cliente_indirizzo,
                _labelled("PEC", (cliente.get("pec") or "").strip()),
                _labelled("Codice destinatario", codice_destinatario),
            ),
        },
        "fiscale": {
            **fiscale,
            "regime_display": _bars_space(codice_regime, REGIMI.get(codice_regime, _EMPTY)),
        },
        "pagamento": {
            "codice": codice_pagamento,
            "dettagli": _bars_space(
                MODALITA_PAGAMENTO.get(codice_pagamento, _EMPTY),
                f"IBAN {iban}" if iban else _EMPTY,
            ),
        },
        "fattura": {
            # `TD01 fattura` for a fiscal document. A proforma is not one, so it names
            # no TD code: printing one would claim a status the row does not have.
            "etichetta": (
                "Fattura proforma"
                if proforma
                else _bars_space(
                    export.tipo_documento, TIPI_DOCUMENTO.get(export.tipo_documento, _EMPTY)
                )
            ),
            "numero": (
                riferimento
                if riferimento is not None
                else numero_completo(export.anno, export.numero)
            ),
            "data": format_data(export.data_emissione),
            "data_scadenza": (
                format_data(export.data_scadenza) if export.data_scadenza else _EMPTY
            ),
            "causale": export.causale or _EMPTY,
            # The whole line, or nothing: the template wraps it in `{{#if}}`, so a
            # document with no period prints no label with an empty value after it.
            "periodo_competenza": _periodo_competenza(export),
            "imponibile": format_euro(export.imponibile),
            "imposta": format_euro(export.imposta),
            "totale": format_euro(export.totale),
            # The register printed no totals block: the payment table carries the
            # amount. It comes back only when there is something the payment row does
            # not say, VAT to show, so a forfettario invoice matches the register and an
            # ordinario one still shows its tax. The stamp is never a row here: the total
            # does not include it (`totals.py`) and the emitter bears it, so a `2,00 €`
            # line under Imposta would read as charged to the customer; the declaration
            # under the rule is where it is stated. A `bool`, the one non-string in this
            # scope, because only `{{#if}}` reads it.
            "mostra_totali": export.imposta > Decimal("0.00"),
            "dichiarazione_bollo": dichiarazione_bollo,
            "dichiarazione_regime": _dichiarazione_regime(export),
            "dichiarazione_proforma": PROFORMA_DECLARATION if proforma else _EMPTY,
        },
        "righe": [
            {
                "numero_linea": str(riga.numero_linea),
                "descrizione": riga.descrizione,
                "iva": format_iva(riga.aliquota_iva, riga.natura),
                "prezzo_totale": format_euro(riga.prezzo_totale),
            }
            for riga in export.righe
        ],
    }


def _periodo_competenza(export: InvoiceForExport) -> str:
    """`dal dd-mm-yyyy al dd-mm-yyyy`, or `""` when the document has no period
    (ORB-61). Both ends or neither is the row's own CHECK, so a half period cannot
    reach this function. Words rather than a dash between the two dates, now that the
    dates themselves carry dashes."""
    if export.competenza_da is None or export.competenza_a is None:
        return _EMPTY
    return f"dal {format_data(export.competenza_da)} al {format_data(export.competenza_a)}"


def _dichiarazione_regime(export: InvoiceForExport) -> str:
    """The normative declaration the foot prints: the one the lines carry.

    Until ORB-32 the footer read `fiscale.riferimento_normativo` straight from the
    snapshot, which is the profile's *domestic* text; a non-resident customer's lines
    carry `N2.1` and the art. 7-ter reference instead, and a PDF that contradicted its
    own XML is what that produced. The distinct references are kept in line order and
    joined with a space, so an invoice whose lines ever carried two different ones
    shows both rather than the first. The profile text remains the fallback for lines
    with no reference at all, which is what an `RF01` invoice has.

    The register this layout follows printed no declaration at all. This one stays,
    small and under the rule: a forfettario invoice has to say why it carries no VAT.
    """
    distinct: list[str] = []
    for riga in export.righe:
        if riga.riferimento_normativo and riga.riferimento_normativo not in distinct:
            distinct.append(riga.riferimento_normativo)
    if distinct:
        return " ".join(distinct)
    return export.snapshot.fiscale.riferimento_normativo or _EMPTY


def build_invoice_header(scope: dict[str, Any], *, logo: str | None = None) -> str:
    """The invoice's page header, from the same scope the body reads, through the same
    engine and therefore the same escaping (`@` in an email is a Typst reference; see
    `render/pdf.py::build_header`, whose reasoning this shares and whose template it
    does not). `logo` is the media file the job carries, or `None` for the name in type."""
    return render_template(
        INVOICE_HEADER_TEMPLATE.read_text(encoding="utf-8"),
        {"emittente": scope["emittente"], "fiscale": scope["fiscale"], "logo": logo_flags(logo)},
    )


def render_invoice_pdf(
    export: InvoiceForExport,
    *,
    riferimento: str | None,
    settings: Settings,
    media: Mapping[str, bytes] | None = None,
) -> tuple[str, bytes]:
    """`(compiled_markdown, pdf_bytes)`.

    `riferimento is None` renders a fiscal document; a reference string renders a
    proforma, which is the same template with `PROFORMA_DECLARATION` in its body and no
    TD code on its first line. The choice is made from the row's own data, never from a
    caller's flag.
    """
    scope = build_scope(export, riferimento=riferimento)
    markdown = render_template(INVOICE_TEMPLATE.read_text(encoding="utf-8"), scope)
    # The identity in the header is the frozen snapshot's; the logo is the azienda's
    # live one (REB-627, spec §1.8), the way `regenerate` re-reads a live profile: a logo
    # is not a fiscal fact, and a re-render after a rebrand should carry the new mark.
    header = build_invoice_header(scope, logo=logo_file_in(dict(media or {})))
    return markdown, render_pdf(markdown, header_typst=header, settings=settings, media=media)
