"""What the matches and contracts pages send and read (REB-387, phase 2).

Kept out of `schemas.py`, already the size of a chapter: everything here is one flow,
the admin's «Crea match» and «Match e contratti», and the MCP tools that read the same
rows. A letter's field names are the Markdown's own keys with underscores for hyphens
(`data_inizio` is `{{data-inizio}}`), so `LetteraFields.to_fields` is a rename and a
formatting step, nothing else. The company's `budget_giornaliero` has no field anywhere
here on purpose: what rebase agrees with the client never reaches a freelancer's
document (spec § 1h), and `test_contract_schemas.py` holds every model to that.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError, ValidationError

from rebase_core.contracts.fields import DAYS_LIMIT, DAYS_LIMIT_MONTH_END, Value, italian_date
from rebase_core.match_words import Action
from rebase_core.models import (
    AZIENDA_MAX_LENGTH,
    CLIENTE_PIVA_MAX_LENGTH,
    DOMICILIO_MAX_LENGTH,
    POSIZIONE_MAX_LENGTH,
    SEDE_MAX_LENGTH,
)
from rebase_core.schemas import PROGETTO_MAX_LENGTH, TARIFFA_MAX, TARIFFA_MIN, clean_multiline
from rebase_core.validation import SafeStr

# A person's codice fiscale is sixteen letters and digits; a ditta's is its eleven digits.
_CODICE_FISCALE = re.compile(r"[A-Z0-9]{16}|[0-9]{11}")
_PARTITA_IVA = re.compile(r"[0-9]{11}")
# Wide enough for the typed value with its spaces, which are dropped before the check.
_IDENTIFIER_INPUT_MAX_LENGTH = 40
LETTERA_TEXT_MAX_LENGTH = PROGETTO_MAX_LENGTH
PREAVVISO_MAX_DAYS = 365

# The letter's text fields an admin writes on «Condizioni», in the Markdown's order.
LETTERA_TEXT_FIELDS = (
    "ruolo",
    "attivita",
    "risultati",
    "accettazione",
    "impegno",
    "periodo_verifica",
    "luogo",
    "coordinamento",
    "referente_cliente",
    "referente_rebase",
    "modalita",
    "unita",
    "lavoro_extra",
    "spese",
    "scadenze_fatturazione",
    "dati_personali",
    "dati_finalita",
    "dati_categorie",
    "dati_interessati",
    "dati_autorizzazione",
    "esclusiva",
    "portfolio",
    "assicurazione",
    "altre_condizioni",
    "rapporti_precedenti",
)

# The letter's fields the hub fills itself, never the admin on «Condizioni»: the number,
# the framework's date, the two parties, and the four signing fields.
LETTER_AUTO_FIELDS = frozenset(
    {
        "numero",
        "data-contratto-quadro",
        "rebase-ragione-sociale",
        "rebase-rappresentante",
        "professionista-nome",
        "professionista-piva",
        "cliente-ragione-sociale",
        "cliente-piva",
        "cliente-sede",
        "luogo-firma",
        "data-firma",
        "firma-rebase",
        "firma-professionista",
    }
)


def _one_line(value: str, what: str) -> str:
    cleaned = clean_multiline(value, what=what)
    if any(character in cleaned for character in "\n\r\t"):
        raise ValueError(f"{what} sta su una riga sola")
    return cleaned


def _compact(value: str) -> str:
    """An identifier as it was typed, its spaces gone and in capitals."""
    return "".join(value.split()).upper()


class FiscalData(BaseModel):
    """What «Chi e per chi», step 1 of «Crea match», and «Match e contratti» save for a
    freelancer."""

    model_config = ConfigDict(extra="forbid")

    codice_fiscale: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    partita_iva: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    domicilio: SafeStr = Field(min_length=1, max_length=DOMICILIO_MAX_LENGTH)
    pec: EmailStr | None = None

    @field_validator("codice_fiscale", mode="after")
    @classmethod
    def _codice_fiscale(cls, value: str) -> str:
        compact = _compact(value)
        if not _CODICE_FISCALE.fullmatch(compact):
            raise ValueError("il codice fiscale ha 16 caratteri, o 11 cifre per una ditta")
        return compact

    @field_validator("partita_iva", mode="after")
    @classmethod
    def _partita_iva(cls, value: str) -> str:
        compact = _compact(value).removeprefix("IT")
        if not _PARTITA_IVA.fullmatch(compact):
            raise ValueError("la partita IVA ha 11 cifre")
        return compact

    @field_validator("domicilio", mode="after")
    @classmethod
    def _domicilio(cls, value: str) -> str:
        return _one_line(value, "il domicilio")


class FiscalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    freelancer_id: UUID
    codice_fiscale: str
    partita_iva: str
    domicilio: str
    pec: str | None
    updated_by: UUID
    updated_at: datetime


class ClienteData(BaseModel):
    """The client as the letter prints it, asked on «Chi e per chi»."""

    model_config = ConfigDict(extra="forbid")

    cliente_ragione_sociale: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    cliente_piva: SafeStr = Field(min_length=1, max_length=CLIENTE_PIVA_MAX_LENGTH)
    cliente_sede: SafeStr = Field(min_length=1, max_length=SEDE_MAX_LENGTH)

    @field_validator("cliente_ragione_sociale", "cliente_piva", "cliente_sede", mode="after")
    @classmethod
    def _line(cls, value: str) -> str:
        return _one_line(value, "un valore")


class ClienteDraft(BaseModel):
    """What the prefill suggests on «Chi e per chi»: any of the three may be unknown."""

    cliente_ragione_sociale: str | None = None
    cliente_piva: str | None = None
    cliente_sede: str | None = None


class LetteraDraft(BaseModel):
    """Every field of `lettera-di-incarico.md` an admin writes on «Condizioni», all
    optional: the shape the prefill suggests. `LetteraFields` is the one a letter is
    written from."""

    model_config = ConfigDict(extra="forbid")

    ruolo: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    attivita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    risultati: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    accettazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    data_inizio: date | None = None
    data_fine: date | None = None
    impegno: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    periodo_verifica: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    luogo: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    coordinamento: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    referente_cliente: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    referente_rebase: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    modalita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    compenso: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    unita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    lavoro_extra: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    spese: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    giorni_pagamento: int | None = Field(default=None, ge=1, le=DAYS_LIMIT)
    fine_mese: bool | None = None
    scadenze_fatturazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    giorni_preavviso: int | None = Field(default=None, ge=1, le=PREAVVISO_MAX_DAYS)
    dati_personali: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_finalita: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_categorie: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_interessati: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    dati_autorizzazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    esclusiva: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    portfolio: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    assicurazione: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    altre_condizioni: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)
    rapporti_precedenti: SafeStr | None = Field(default=None, max_length=LETTERA_TEXT_MAX_LENGTH)

    @field_validator(*LETTERA_TEXT_FIELDS, mode="after")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        """A paragraph may span lines; control characters and a value of blanks may not.
        The web sends `null` for an empty box, which prints as a labelled blank line."""
        return None if value is None else clean_multiline(value, what="un valore")


class LetteraFields(LetteraDraft):
    """The letter as it is written: the role, the work, the start, the fee and the
    payment term are required; every other field may stay a blank line on the page."""

    ruolo: SafeStr = Field(min_length=1, max_length=POSIZIONE_MAX_LENGTH)
    attivita: SafeStr = Field(min_length=1, max_length=LETTERA_TEXT_MAX_LENGTH)
    data_inizio: date
    compenso: Decimal = Field(max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX)
    giorni_pagamento: int = Field(ge=1, le=DAYS_LIMIT)
    fine_mese: bool

    @field_validator("data_fine", mode="after")
    @classmethod
    def _end_not_before_start(cls, value: date | None, info: ValidationInfo) -> date | None:
        """`data_inizio` is declared before `data_fine`, so it is already in `info.data`
        by the time this runs: the error can name `data_fine`, the field an admin
        actually filled in, instead of the whole letter."""
        start = info.data.get("data_inizio")
        if value is not None and start is not None and value < start:
            raise PydanticCustomError("date_order", "la fine prevista viene prima dell'inizio")
        return value

    @model_validator(mode="after")
    def _payment_term_within_the_law(self) -> "LetteraFields":
        """`fine_mese` is declared after `giorni_pagamento`, so a `field_validator` on
        `giorni_pagamento` cannot read it yet; this stays a model-level check, but
        raises a `ValidationError` built with an explicit `loc` so it still names
        `giorni_pagamento`, the field the law (81/2017) actually limits, rather than
        the whole letter."""
        if self.fine_mese and self.giorni_pagamento > DAYS_LIMIT_MONTH_END:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "payment_term",
                            "contati da fine mese, i giorni di pagamento sono al massimo 30 "
                            "(legge 81/2017)",
                        ),
                        loc=("giorni_pagamento",),
                        input=self.giorni_pagamento,
                    )
                ],
            )
        return self

    def to_fields(self) -> dict[str, Value]:
        """The Markdown's own keys and the values the page prints: a date the Italian way,
        the fee as the JSON number `checked` insists on, everything else as it is."""
        fields: dict[str, Value] = {}
        for name in type(self).model_fields:
            value = getattr(self, name)
            key = name.replace("_", "-")
            if isinstance(value, date):
                fields[key] = italian_date(value)
            elif isinstance(value, Decimal):
                fields[key] = int(value) if value == value.to_integral_value() else float(value)
            else:
                fields[key] = value
        return fields


# «Giorni previsti» (REB-497): `ck_matches_giorni_previsti`'s bounds, the sentence that
# refuses a number outside them, and the one that refuses what is not a whole number.
GIORNI_PREVISTI_MIN, GIORNI_PREVISTI_MAX = 1, 366
GIORNI_PREVISTI_RANGE = "I giorni previsti vanno da 1 a 366."
GIORNI_PREVISTI_WHOLE = "I giorni previsti sono un numero intero da 1 a 366."
_WHOLE_NUMBER: TypeAdapter[int] = TypeAdapter(int)


class MatchCreate(BaseModel):
    """What «Chi e per chi» and «Condizioni», steps 1 and 2 of «Crea match», ask. The
    tax data step 1 asks are saved by their own route when the admin leaves that step,
    and read back from `freelancer_fiscal`.

    `id` is optional and client-generated (REB-406): one per wizard run, sent with both
    «Salva senza inviare» and «Invia per la firma», so a retry after the response is
    lost writes nothing new -- `MatchService.create` reads it back and returns the match
    already written."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    company_id: UUID
    cliente: ClienteData
    lettera: LetteraFields
    # An admin's estimate of the engagement's billable days (REB-497), read back
    # unchanged; `Match.giorni_previsti` carries the same `CHECK`.
    giorni_previsti: int | None = None

    @field_validator("giorni_previsti", mode="before")
    @classmethod
    def _expected_days_are_whole(cls, value: object) -> object:
        """A fraction or a word refused in the admin's words too: the field's own
        parsing runs before the range check below, and would answer Pydantic's English
        («Input should be a valid integer, got a number with a fractional part»). What
        that parsing takes (`40`, `40.0`, `"40"`) goes on as the whole number."""
        if value is None:
            return None
        try:
            return _WHOLE_NUMBER.validate_python(value)
        except ValidationError:
            raise PydanticCustomError("giorni_previsti_intero", GIORNI_PREVISTI_WHOLE) from None

    @field_validator("giorni_previsti", mode="after")
    @classmethod
    def _expected_days_within_a_year(cls, value: int | None) -> int | None:
        """From one day to a year, the column's own `CHECK`, refused in the admin's words
        (REB-502): «Crea match» and the MCP tools show the sentence as it is, where
        `Field(ge=, le=)` would answer Pydantic's English."""
        if value is not None and not GIORNI_PREVISTI_MIN <= value <= GIORNI_PREVISTI_MAX:
            raise PydanticCustomError("giorni_previsti_range", GIORNI_PREVISTI_RANGE)
        return value


class ContractDocumentRead(BaseModel):
    """A document as the pages and the MCP tools read it: never the PDF bytes and never
    `data`, which carries rebase's signer and the freelancer's tax identifiers.
    `situazione`, `prossima_azione` and `altre_azioni` are `match_words`' (REB-477)."""

    id: UUID
    kind: str
    freelancer_id: UUID
    match_id: UUID | None
    numero: str | None
    text_version: str
    testo_bozza: bool
    stato: str
    created_at: datetime
    created_by: UUID
    sent_at: datetime | None
    signed_at: datetime | None
    notice_at: datetime | None
    # Why it became `annullato`: the freelancer's own reason when they refused it, or who
    # cancelled it (REB-407).
    cancel_reason: str | None
    ha_pdf_firmato: bool
    attivo: bool
    rinnovo: date | None
    ultimo_giorno_disdetta: date | None
    nuova_versione: bool
    situazione: str
    prossima_azione: Action | None
    altre_azioni: list[Action]


class MatchRead(BaseModel):
    """`giorni_previsti`, the three `lettera_*` values and the eight `pigro_*` fields
    are `Match`'s own (REB-497): the `lettera_*` ones are what `create` copied off
    `data.lettera` at the time, not necessarily what the current `lettera` prints, were
    it ever regenerated. `pigro_stato` is `None` until the match turns `attivo`, one of
    `PIGRO_STATES` after."""

    id: UUID
    freelancer_id: UUID
    company_id: UUID
    nome_azienda: str
    figura_richiesta: str
    cliente_ragione_sociale: str
    cliente_piva: str
    cliente_sede: str
    stato: str
    created_at: datetime
    created_by: UUID
    cancelled_at: datetime | None
    updated_at: datetime
    lettera: ContractDocumentRead
    situazione: str
    prossima_azione: Action | None
    altre_azioni: list[Action]
    giorni_previsti: int | None
    lettera_data_inizio: date | None
    lettera_data_fine: date | None
    lettera_compenso: Decimal | None
    pigro_stato: str | None
    pigro_slug: str | None
    pigro_deal_id: UUID | None
    pigro_url: str | None
    pigro_linked_at: datetime | None
    pigro_attempted_at: datetime | None
    pigro_errore: str | None
    pigro_mail_sent_at: datetime | None


class FreelancerContracts(BaseModel):
    """«Match e contratti»: the framework agreement at the top (the active one, else the
    newest not cancelled), every framework agreement newest first, the matches newest
    first, and the tax data the page edits."""

    freelancer_id: UUID
    quadro: ContractDocumentRead | None
    quadri: list[ContractDocumentRead]
    matches: list[MatchRead]
    fiscale: FiscalRead | None


class MatchPrefill(BaseModel):
    """What «Chi e per chi» and «Condizioni» start from. `quadro_necessario`: this
    match writes a framework agreement. `lettera_in_attesa`: the letter waits for a
    framework's signature."""

    fiscale: FiscalRead | None
    cliente: ClienteDraft
    lettera: LetteraDraft
    quadro_attivo: ContractDocumentRead | None
    quadro_necessario: bool
    lettera_in_attesa: bool


class MatchCheck(BaseModel):
    """What saving a match would do, in sentences (REB-476), with nothing written:
    `riepilogo` is the letter in three or four sentences, `cosa_succede` which document
    leaves first. Missing tax data are reported here, not refused: `create` refuses
    them."""

    riepilogo: list[str]
    cosa_succede: str
    quadro_necessario: bool
    dati_fiscali_mancanti: bool


class ContractPdf(BaseModel):
    """A document's bytes and the name a browser saves them under."""

    filename: str
    content: bytes


class MatchListItem(BaseModel):
    """One row of the admin's «Match» list (REB-413): everything the table shows and
    nothing else -- no tax field of the freelancer's and no `budget_giornaliero` of the
    request's. `lettera_data_inizio`/`lettera_data_fine` are the letter's own
    `data-inizio`/`data-fine` exactly as it printed them (`italian_date`, already a
    finished sentence), not re-formatted here. The four `lettera_*` fields are `None`
    together, only were a match ever to have no letter at all -- `create` always
    writes one, so this is the list staying honest about a shape `get` does not need
    to allow for. `situazione` is the match's sentence, the same `MatchRead` carries
    (REB-477). `giorni_previsti`, `pigro_stato` and `pigro_url` are `Match`'s own
    (REB-497), the row's narrower share of what `MatchRead` carries in full."""

    id: UUID
    freelancer_id: UUID
    freelancer_nome: str
    freelancer_cognome: str
    freelancer_email: str
    nome_azienda: str
    figura_richiesta: str
    stato: str
    lettera_numero: str | None
    lettera_stato: str | None
    lettera_data_inizio: str | None
    lettera_data_fine: str | None
    created_at: datetime
    created_by_nome: str
    created_by_email: str
    situazione: str
    giorni_previsti: int | None
    pigro_stato: str | None
    pigro_url: str | None


class MatchList(BaseModel):
    """Newest first (REB-413). `totale` counts every match the filters select, the
    whole list, not just the page returned."""

    totale: int
    items: list[MatchListItem]


class ReportDayInvoice(BaseModel):
    """A day's hours on one invoice (REB-505): the invoice as `ReportInvoice` names it,
    by `numero` and `tipo`, and the day's entries on it summed."""

    numero: str
    tipo: str
    ore: Decimal


class ReportDay(BaseModel):
    """One day of hours on the match's deal (REB-498): the CRM answers one row per time
    entry, summed here. `fatture` names each invoice these hours sit on once, «12/2026»
    for an invoice and «proforma 3/2026» for anything else, empty when none does yet.
    `ore_per_fattura` holds the same invoices in the same order, each with its own share
    of the day, the hours on none left out (REB-505): a day of 8 hours with 3 on
    12/2026 gives 12/2026 its 3, so a month can count an invoice's hours exactly."""

    data: date
    ore: Decimal
    descrizioni: list[str]
    fatture: list[str]
    ore_per_fattura: list[ReportDayInvoice]


class ReportWeek(BaseModel):
    """An ISO week, «2026-W40», from its Monday to its Sunday."""

    settimana: str
    da: date
    a: date
    ore: Decimal


class ReportMonth(BaseModel):
    mese: str
    ore: Decimal


class ReportInvoice(BaseModel):
    """An invoice these hours sit on, with how many of them: «12/2026», or «senza
    numero» for one not numbered yet."""

    numero: str
    tipo: str
    data: date | None
    stato: str
    stato_pagamento: str
    ore: Decimal


class MatchReport(BaseModel):
    """«Consuntivo» (REB-498): the hours logged on the match's deal on Pigro over a
    period, read from the CRM on every request and never stored. `ore_previste` is
    `giorni_previsti` times eight, `giorni_equivalenti` the hours over eight,
    `avanzamento` the hours as a percentage of `ore_previste`, two places, `None`
    without an estimate. What counts as billed is the CRM's own word."""

    match_id: UUID
    pigro_url: str | None
    pigro_stato: str | None
    giorni_previsti: int | None
    ore_previste: Decimal | None
    totale_ore: Decimal
    giorni_equivalenti: Decimal
    avanzamento: Decimal | None
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    per_giorno: list[ReportDay]
    per_settimana: list[ReportWeek]
    per_mese: list[ReportMonth]
    fatture: list[ReportInvoice]


class SendReport(BaseModel):
    """What «Invia per la firma» did (REB-387 phase 3): the match as it is now, the kind
    of the document that left (`quadro`, `lettera`, or none when the letter waits for a
    framework agreement already out for signature), and whether its mail left too."""

    match: MatchRead
    inviato: str | None
    mail_inviata: bool | None


class MemberContract(BaseModel):
    """A contract as its freelancer reads it in «Contratti» (REB-392): never the PDF's
    bytes and never `data`. `cliente` and the two dates are a letter's, as the letter
    prints them; `signing_url` is there only while the document waits for the
    signature, since its path is the signer's token. `pigro_url` is the letter's
    match's own (REB-498), set only once that match is `collegato`: «Le tue ore su
    Pigro» has somewhere to point (spec § 3.6)."""

    id: UUID
    kind: str
    numero: str | None
    stato: str
    cliente: str | None
    inizio: str | None
    fine: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    ha_pdf_firmato: bool
    attivo: bool
    rinnovo: date | None
    ultimo_giorno_disdetta: date | None
    pigro_url: str | None


class MemberContracts(BaseModel):
    """«Contratti»: the framework agreement (the active one, else the newest that reached
    the person), the letters newest first, and `quadri_precedenti` (REB-392): the
    freelancer's other framework agreements that were signed, newest first, excluding
    the one in `quadro` -- a notice, or a newer one replacing it, must not make an
    earlier signed copy disappear from the page."""

    quadro: MemberContract | None
    quadri_precedenti: list[MemberContract] = Field(default_factory=list)
    lettere: list[MemberContract]
