"""Pre-export checks, run before a register number is spent and again by the writer."""

import re
from collections.abc import Iterable
from typing import NamedTuple, Protocol

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.invoices.fatturapa.constants import (
    BASIC_LATIN_RE,
    CAP_RE,
    CAUSALE_MAX,
    CODICE_CIG_MAX,
    CODICE_CUP_MAX,
    COMUNE_MAX,
    DENOMINAZIONE_MAX,
    DESCRIZIONE_MAX,
    EMAIL_CONTATTI_RE,
    EMAIL_MAX,
    EMAIL_MIN,
    EMAIL_PATTERN_EXPECTED,
    EMAIL_RE,
    ID_DOCUMENTO_MAX,
    INDIRIZZO_MAX,
    ISSUER_ENTITY,
    LATIN_RE,
    NAZIONE_RE,
    PROVINCIA_RE,
    RECIPIENT_ENTITY,
    RIFERIMENTO_NORMATIVO_MAX,
    UNITA_MISURA_MAX,
)
from pigrocrm.core.invoices.fatturapa.normalise import (
    indirizzo_xml,
    latinise,
    normalise_fiscal_id,
    normalise_foreign_fiscal_id,
)
from pigrocrm.core.invoices.schemas import PartySnapshot
from pigrocrm.core.templates.escaping import escape_xml


def check_party_exportable(party: PartySnapshot, entity: str) -> None:
    """Refuse, naming the field on the record the user can go and fix (spec 14.9).

    A module-level function rather than a method, because it has **two** callers and
    they must not drift: this exporter, and `InvoiceService.issue`, which runs it
    *before* consuming a number. Checking only here would be too late -- the export
    happens after the emission transaction has committed (spec 3), so a customer
    missing a CAP would already own a register number that can never produce a valid
    file, and the only remaining remedy would be an annulment.

    The previous system guessed `indirizzo`, `cap`, `comune` and `provincia` out of one free-text
    field with a regex over Italian street prefixes. They are four real columns on
    `customers`; nothing is guessed, and a missing one refuses.
    """
    nazione = (party.nazione or "").strip().upper()
    if not NAZIONE_RE.fullmatch(nazione):
        raise ValidationFailed(
            entity, "nazione", "codice paese non valido", expected="due lettere ISO 3166-1"
        )
    italiano = nazione == "IT"

    # `provincia` is required for an Italian address and meaningless outside one: FPR12
    # makes `Provincia` optional precisely so that a London address is not forced to
    # invent one. Requiring it of everybody is what made a foreign customer
    # unrepresentable -- along with the outright refusal that used to stand here, which
    # this replaces. `cap` is not required outside Italy either, since `CAP` carries the
    # `00000` placeholder there. `comune` is required of everybody: the schema's
    # `Comune` is one to sixty characters with no `minOccurs="0"`, and leaving it out
    # of this list let an emission through that the writer could never serialise.
    obbligatori = (
        ("indirizzo", "cap", "comune", "provincia") if italiano else ("indirizzo", "comune")
    )
    for field in obbligatori:
        if not (getattr(party, field) or "").strip():
            raise ValidationFailed(
                entity,
                field,
                "campo obbligatorio per la fattura elettronica",
                expected="un valore non vuoto",
            )
    if not party.ragione_sociale.strip():
        raise ValidationFailed(
            entity, "ragione_sociale", "campo obbligatorio", expected="un valore non vuoto"
        )

    # The formats, and not merely the presence — checked *here* because this function's
    # whole reason for existing is that `InvoiceService.issue` calls it before consuming
    # a register number. Presence alone was not enough: `customers.ragione_sociale` is
    # `String(255)` against FPR12's 80, `indirizzo` and `comune` are `String(255)` and
    # `String(120)` against 60, and `cap` is `String(10)` where the schema wants exactly
    # five digits. So an ordinary customer — a long consortium name, a mistyped CAP —
    # passed this check, the emission committed, and every later `export_xml` raised
    # forever, leaving annulment as the only remedy. A refusal before the number is spent
    # costs the user one correction; a refusal after costs them a hole in the register.
    check_widths(party, entity, italiano)
    _check_latin(party, entity)


def check_widths(party: PartySnapshot, entity: str, italiano: bool) -> None:
    """The FPR12 patterns that a database column is too loose to enforce.

    The widths of the free-text fields used to be measured here, with a message of
    their own; they are `check_text`'s now, through `_check_latin`, so a value that
    is too long is refused with the writer's own words and measured as the writer
    will spell it (ORB-140). What is left here are the two coded fields.
    """
    # Outside Italy the stored postcode has no shape to check: the writer puts the
    # `00000` placeholder in `CAP` and the real one at the end of `Indirizzo`, measured
    # above. Inside Italy the schema's five digits are the customer's own CAP and a
    # mistyped one is refused before it costs a register number.
    cap = (party.cap or "").strip()
    if italiano and not CAP_RE.fullmatch(cap):
        raise ValidationFailed(entity, "cap", "CAP non valido", expected="esattamente 5 cifre")

    # `Provincia` is written only for an Italian address (the specifications fill it only
    # when `Nazione` is `IT`), so a county or a state stored there for a foreign customer
    # is neither emitted nor a reason to refuse.
    provincia = (party.provincia or "").strip().upper()
    if italiano and provincia and not PROVINCIA_RE.fullmatch(provincia):
        raise ValidationFailed(entity, "provincia", "sigla non valida", expected="due lettere")


def _latin_message(tag: str) -> str:
    """The refusal `_text` produces for a character outside the schema's Latin set.

    One function, called by the writer and by the pre-check, for the same reason
    `indirizzo_xml` is one function: a message the two spell separately is a message
    they can come to disagree about, and a user who is told two different things about
    one field has to guess which one is the real rule.
    """
    return f"FPR12 ammette in {tag} solo caratteri latini di base o Latin-1"


def check_text(
    value: str,
    tag: str,
    *,
    entity: str,
    field: str,
    max_length: int | None = None,
    min_length: int | None = None,
    pattern: re.Pattern[str] | None = None,
    pattern_expected: str | None = None,
) -> str:
    """The text rules of the schema's string types, applied once for everybody.

    Returns the value as the writer will emit it: representable in XML 1.0, spelled by
    `latinise`, within `max_length` and at least `min_length` where the type carries
    one, inside the Latin range, and matching `pattern` where the type carries one
    narrower than the range. `_text` calls it on the way into the document;
    `_check_latin` and `check_document_text_exportable` call it before a register
    number is spent. One function, so a pre-check cannot be laxer than the writer
    and a refusal reads the same wherever it comes from (ORB-56, ORB-140).

    `escape_xml` substitutes nothing (see its docstring): it refuses a code point XML
    1.0 cannot represent, which `LATIN_RE` alone would let through, since U+0001 is
    inside the range. Its `ValueError` becomes a `ValidationFailed` naming the field
    here, so a control character pasted into a causale is a correction before the
    number and not a crash after it.
    """
    try:
        escape_xml(value)
    except ValueError as exc:
        raise ValidationFailed(
            entity, field, str(exc), expected="solo caratteri rappresentabili in XML 1.0"
        ) from exc
    value = latinise(value)
    if max_length is not None and len(value) > max_length:
        raise ValidationFailed(
            entity,
            field,
            f"il valore supera i {max_length} caratteri ammessi da FPR12 per {tag}",
            expected=f"al massimo {max_length} caratteri",
        )
    if min_length is not None and len(value) < min_length:
        raise ValidationFailed(
            entity,
            field,
            f"il valore ha meno dei {min_length} caratteri richiesti da FPR12 per {tag}",
            expected=f"almeno {min_length} caratteri",
        )
    if not LATIN_RE.fullmatch(value):
        raise ValidationFailed(entity, field, _latin_message(tag), expected="solo caratteri latini")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValidationFailed(
            entity,
            field,
            f"il valore non ha la forma richiesta da FPR12 per {tag}: {value!r}",
            expected=pattern_expected or pattern.pattern,
        )
    return value


class _Campo(NamedTuple):
    """One `_check_latin` row: a value, the writer's own bound for it, and -- for the
    two contact fields -- the pattern that applies only once the writer would emit it."""

    field: str
    tag: str
    value: str
    max_length: int | None = None
    pattern: re.Pattern[str] | None = None
    min_length: int | None = None
    pattern_expected: str | None = None


def _check_latin(party: PartySnapshot, entity: str) -> None:
    """The character set, checked before a number is spent (ORB-56).

    `_text` refuses a code point outside `String*LatinType` when it writes, and this
    check had no counterpart: a `ragione_sociale` with a Cyrillic letter, or a PEC with
    a euro sign in it, passed `check_party_exportable`, `issue` consumed a register
    number, and every later `export_xml` refused forever. Same failure as ORB-38 and the
    same remedy, on the other constraint the writer applies.

    Each value is the one the writer will pass to `_text`, with the writer's own bound,
    and each goes through the same `check_text`, so the pre-check cannot be laxer than
    the writer or describe the refusal differently. `indirizzo` is measured as `_sede`
    will write it: for a foreign party that is the address with the postcode appended
    (ORB-38), so a London address one character too long is refused here, by the field
    the user can shorten, rather than by the writer after the number is spent.
    """
    campi: list[_Campo] = [
        _Campo("ragione_sociale", "Denominazione", party.ragione_sociale, DENOMINAZIONE_MAX),
        _Campo("indirizzo", "Indirizzo", indirizzo_xml(party), INDIRIZZO_MAX),
        _Campo("comune", "Comune", party.comune, COMUNE_MAX),
    ]
    # `Nazione`, `CAP`, `Provincia` and `CodiceDestinatario` carry patterns narrower than
    # the Latin set and are already checked above; `IdCodice` and `CodiceFiscale` come out
    # of the normalisers as alphanumerics. What is left is the one free-text contact each
    # role contributes, and `RECIPIENT_ENTITY`/`ISSUER_ENTITY` say which (REB-227).
    # Stripped once here, so the pre-check, the writer and the emitted text all measure
    # and match the same value -- `EmailType`'s base is `xs:token`, which the SdI
    # whitespace-collapses before the pattern applies, so surrounding whitespace the
    # writer would otherwise carry into the document is not this rule's problem.
    if entity == RECIPIENT_ENTITY:
        pec = (party.pec or "").strip()
        # `_dati_trasmissione` writes `PECDestinatario` only for a customer with no SDI
        # code; one with an SDI code keeps `pec` as an ordinary, unchecked contact field
        # the writer never touches, so its shape is not this rule's problem either.
        emesso = bool(pec) and not (party.codice_sdi or "").strip()
        campi.append(
            _Campo(
                "pec",
                "PECDestinatario",
                pec,
                EMAIL_MAX,
                EMAIL_RE if emesso else None,
                None,
                EMAIL_PATTERN_EXPECTED if emesso else None,
            )
        )
    elif entity == ISSUER_ENTITY:
        email = (party.email or "").strip()
        campi.append(
            _Campo(
                "email",
                "Email",
                email,
                EMAIL_MAX,
                EMAIL_CONTATTI_RE if email else None,
                EMAIL_MIN if email else None,
                EMAIL_PATTERN_EXPECTED if email else None,
            )
        )
    for campo in campi:
        check_text(
            campo.value,
            campo.tag,
            entity=entity,
            field=campo.field,
            max_length=campo.max_length,
            min_length=campo.min_length,
            pattern=campo.pattern,
            pattern_expected=campo.pattern_expected,
        )


class _LineText(Protocol):
    """What `check_document_text_exportable` reads off a line. The stored
    `InvoiceLine` row at `issue` time has these, and so does `InvoiceLineRead`
    wherever a caller holds the frozen view."""

    @property
    def descrizione(self) -> str: ...

    @property
    def unita_misura(self) -> str | None: ...

    @property
    def riferimento_normativo(self) -> str | None: ...


def check_document_text_exportable(
    causale: str | None,
    righe: Iterable[_LineText],
    *,
    ordine_numero: str | None = None,
    ordine_cig: str | None = None,
    ordine_cup: str | None = None,
) -> None:
    """The document's own free text, checked before a number is spent (ORB-140).

    `_check_latin` covered the two parties and nothing covered the invoice: a causale
    typed with an em dash passed `issue`, spent number 18 of 2026, and every export after
    it refused by `invoice.causale`. Every value here is the one the writer will pass to
    `_text`, with the same tag, entity, field and bound, through the same `check_text`:
    `Causale`, and per line `Descrizione`, `UnitaMisura` and the `RiferimentoNormativo`
    the line carries into `DatiRiepilogo`. Since REB-693 also the purchase order's
    `IdDocumento`, `CodiceCUP` and `CodiceCIG`, which the service checks when it stores
    them but which another path could have written. `issue` calls this before it touches the
    counter, so a value the mapping cannot spell costs one correction and not a hole in
    the register.
    """
    if (causale or "").strip():
        check_text(
            causale or "", "Causale", entity="invoice", field="causale", max_length=CAUSALE_MAX
        )
    for riga in righe:
        check_text(
            riga.descrizione,
            "Descrizione",
            entity="invoice_line",
            field="descrizione",
            max_length=DESCRIZIONE_MAX,
        )
        if (riga.unita_misura or "").strip():
            check_text(
                riga.unita_misura or "",
                "UnitaMisura",
                entity="invoice_line",
                field="unita_misura",
                max_length=UNITA_MISURA_MAX,
                pattern=BASIC_LATIN_RE,
            )
        if riga.riferimento_normativo:
            check_text(
                riga.riferimento_normativo,
                "RiferimentoNormativo",
                entity="fiscal_profile",
                field="riferimento_normativo",
                max_length=RIFERIMENTO_NORMATIVO_MAX,
            )
    for value, tag, field, width in (
        (ordine_numero, "IdDocumento", "ordine_numero", ID_DOCUMENTO_MAX),
        (ordine_cup, "CodiceCUP", "ordine_cup", CODICE_CUP_MAX),
        (ordine_cig, "CodiceCIG", "ordine_cig", CODICE_CIG_MAX),
    ):
        if value:
            check_text(
                value,
                tag,
                entity="invoice",
                field=field,
                max_length=width,
                pattern=BASIC_LATIN_RE,
            )


def check_recipient_identity(party: PartySnapshot) -> None:
    """A recipient needs `IdFiscaleIVA` or `CodiceFiscale`, and the schema does not say so.

    Both are `minOccurs="0"` in `DatiAnagraficiCessionarioType` (the vendored FPR12 1.2.3
    XSD, unlike `DatiAnagraficiCedenteType` where `IdFiscaleIVA` is mandatory), so a
    document with neither is schema-valid and the SdI refuses it anyway: control 00417 of
    the Elenco dei controlli, "almeno uno dei campi 1.4.1.1 IdFiscaleIVA e 1.4.1.2
    CodiceFiscale del Cessionario/Committente deve essere valorizzato". Spec 3 of slice 3
    is the reason it has to be checked here: the export runs after the emission
    transaction has committed, so a recipient the SdI will refuse would already own a
    register number, and annulment would be the only remedy.

    The hole it closes is a foreign customer with no VAT number whose `codice_fiscale`
    holds a foreign identifier: `normalise_fiscal_id` answers `None` to anything that is
    not one of Italy's two shapes -- correctly, since `CodiceFiscale` is the Italian
    register's own code -- so the writer emitted neither element and nothing noticed. A
    foreign register's number belongs in `partita_iva`, where `IdFiscaleIVA` carries it
    with the customer's own `IdPaese`, and that is the field the refusal names.

    Recipient-only, and a function of its own for the same reason `check_recipient_routing`
    is one: the issuer's `IdFiscaleIVA` is mandatory in the schema and is already refused
    by `_cedente`, with a message about being a VAT subject that would be wrong here.
    """
    paese = (party.nazione or "IT").strip().upper()
    piva = (
        normalise_fiscal_id(party.partita_iva)
        if paese == "IT"
        else normalise_foreign_fiscal_id(party.partita_iva)
    )
    if piva is not None or normalise_fiscal_id(party.codice_fiscale) is not None:
        return
    raise ValidationFailed(
        RECIPIENT_ENTITY,
        "partita_iva",
        "serve un identificativo fiscale del cliente: lo SdI rifiuta una fattura senza "
        "partita IVA e senza codice fiscale del cessionario",
        expected="una partita IVA, anche estera, oppure un codice fiscale italiano",
    )


def check_recipient_routing(party: PartySnapshot) -> None:
    """A customer must have an SDI code or a PEC, or there is no `CodiceDestinatario`.

    Split from `check_party_exportable` because it applies only to the *recipient*,
    and shared with `InvoiceService.issue` for the same reason: the previous system emitted an empty
    `CodiceDestinatario` here, producing an invalid file with no error at all.
    """
    if (party.nazione or "").strip().upper() != "IT":
        # A foreign customer has no SDI code and no PEC, and is not supposed to: the SdI
        # routes nothing to them. `CODICE_DESTINATARIO_ESTERO` is the placeholder the
        # specification reserves for exactly this, and `_dati_trasmissione` writes it.
        # Demanding a code here would make a foreign invoice impossible to issue, which
        # is what used to happen one function up.
        return
    if not (party.codice_sdi or "").strip() and not (party.pec or "").strip():
        raise ValidationFailed(
            "customer",
            "codice_sdi",
            "serve un codice destinatario (SDI) oppure una PEC per emettere la fattura",
            expected="codice_sdi di 7 caratteri oppure pec",
        )
