"""What the matches and contracts pages send and read (REB-387, phase 2).

Kept out of the family schema modules (`freelancer_schemas.py` and the others):
everything here is one flow, the admin's «Crea match» and «Match e contratti», and the
MCP tools that read the same rows. A letter's field names are English; `TEMPLATE_KEYS` names the
Markdown's own Italian variable of each (`start_date` is `{{data-inizio}}`), so
`LetterFields.to_fields` is a rename and a formatting step, nothing else. The company's
`daily_budget` has no field anywhere here on purpose: what rebase agrees with the
client never reaches a freelancer's document (spec § 1h), and `test_contract_schemas.py`
holds every model to that.
"""

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
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
from rebase_core.match_words import PAY_MODE_WORDS, Action
from rebase_core.models import (
    COMPANY_MAX_LENGTH,
    CUSTOMER_VAT_NUMBER_MAX_LENGTH,
    DOMICILE_MAX_LENGTH,
    HEADQUARTERS_MAX_LENGTH,
    JOB_TITLE_MAX_LENGTH,
)
from rebase_core.referral_schemas import MatchReferral
from rebase_core.regimes import OCCASIONAL, VAT_REGISTERED, Regime
from rebase_core.schema_common import (
    MAX_RATE,
    MIN_RATE,
    PROJECT_MAX_LENGTH,
    ResponseModel,
    clean_multiline,
)
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import ContractKind, ContractState, MatchState, PigroState

# A person's codice fiscale is sixteen letters and digits; a ditta's is its eleven digits.
_TAX_CODE = re.compile(r"[A-Z0-9]{16}|[0-9]{11}")
_VAT_NUMBER = re.compile(r"[0-9]{11}")
# Wide enough for the typed value with its spaces, which are dropped before the check.
_IDENTIFIER_INPUT_MAX_LENGTH = 40
LETTER_TEXT_MAX_LENGTH = PROJECT_MAX_LENGTH
NOTICE_MAX_DAYS = 365
# `date` is a field name in the report models below: a class body cannot read it as a type.
Day = date

# «Come si paga»: the two ways a letter prices its fee, and the only values `mode` may
# take. A closed set, not free text, because the referral reward's base (`reward_base`)
# is decided on it by equality.
PayMode = Literal["to_work_unit", "to_body"]
DAY_RATE: PayMode = "to_work_unit"
LUMP_SUM: PayMode = "to_body"
PAY_MODES: tuple[PayMode, ...] = (DAY_RATE, LUMP_SUM)
# The day-rate unit a letter starts from, as the page prints it (`PAY_MODE_WORDS`).
DAY_RATE_UNIT = PAY_MODE_WORDS[DAY_RATE]

# A letter field's name in the Markdown: the contract text's own variable, Italian by
# design (`{{compenso}}`), for each English field of the model.
TEMPLATE_KEYS: dict[str, str] = {
    "role": "ruolo",
    "activities": "attivita",
    "results": "risultati",
    "acceptance": "accettazione",
    "start_date": "data-inizio",
    "end_date": "data-fine",
    "commitment": "impegno",
    "review_period": "periodo-verifica",
    "location": "luogo",
    "coordination": "coordinamento",
    "customer_contact": "referente-cliente",
    "rebase_contact": "referente-rebase",
    "mode": "modalita",
    "fee": "compenso",
    "unit": "unita",
    "extra_work": "lavoro-extra",
    "expenses": "spese",
    "payment_days": "giorni-pagamento",
    "month_end": "fine-mese",
    "billing_deadlines": "scadenze-fatturazione",
    "notice_days": "giorni-preavviso",
    "personal_data": "dati-personali",
    "purpose_data": "dati-finalita",
    "category_data": "dati-categorie",
    "data_subjects": "dati-interessati",
    "authorization_data": "dati-autorizzazione",
    "exclusivity": "esclusiva",
    "portfolio": "portfolio",
    "insurance": "assicurazione",
    "other_terms": "altre-condizioni",
    "previous_reports": "rapporti-precedenti",
}

# The letter's text fields an admin writes on «Condizioni», in the Markdown's order.
LETTER_TEXT_FIELDS = (
    "role",
    "activities",
    "results",
    "acceptance",
    "commitment",
    "review_period",
    "location",
    "coordination",
    "customer_contact",
    "rebase_contact",
    "unit",
    "extra_work",
    "expenses",
    "billing_deadlines",
    "personal_data",
    "purpose_data",
    "category_data",
    "data_subjects",
    "authorization_data",
    "exclusivity",
    "portfolio",
    "insurance",
    "other_terms",
    "previous_reports",
)

# The letter's fields the hub fills itself, never the admin on «Condizioni»: the number,
# the framework's date, the two parties, the referral's own attribution (P-REB-44,
# `rebase_core.referrals.ReferralService.referrer_name`) and the four signing fields.
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
        "azienda-segnalata-da",
        "luogo-firma",
        "data-firma",
        "firma-rebase",
        "firma-professionista",
    }
)

# The occasional letter (REB-687) asks the same form and names its collaborator by the
# `codice fiscale`, never by a `partita IVA`: the same fields the hub fills, with
# `professionista-cf` in place of `professionista-piva`.
LETTER_AUTO_FIELDS_OCCASIONAL = (LETTER_AUTO_FIELDS - {"professionista-piva"}) | {
    "professionista-cf"
}
LETTER_AUTO_FIELDS_BY_REGIME: dict[str, frozenset[str]] = {
    VAT_REGISTERED: LETTER_AUTO_FIELDS,
    OCCASIONAL: LETTER_AUTO_FIELDS_OCCASIONAL,
}


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
    freelancer. `vat_number` omitted, `null` or blank is none (REB-687): the member works
    as a prestazione occasionale and signs only the occasional texts."""

    model_config = ConfigDict(extra="forbid")

    tax_code: SafeStr = Field(min_length=1, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    vat_number: SafeStr | None = Field(default=None, max_length=_IDENTIFIER_INPUT_MAX_LENGTH)
    domicile: SafeStr = Field(min_length=1, max_length=DOMICILE_MAX_LENGTH)
    certified_email: EmailStr | None = None

    @field_validator("tax_code", mode="after")
    @classmethod
    def _tax_code(cls, value: str) -> str:
        compact = _compact(value)
        if not _TAX_CODE.fullmatch(compact):
            raise ValueError("il codice fiscale ha 16 caratteri, o 11 cifre per una ditta")
        return compact

    @field_validator("vat_number", mode="after")
    @classmethod
    def _vat_number(cls, value: str | None) -> str | None:
        """Nothing typed is no `partita IVA`, stored as `NULL`; anything typed is the
        eleven digits, with its spaces and an `IT` prefix dropped (`IT` alone is not
        nothing, it is a `partita IVA` missing its digits)."""
        compact = _compact(value or "")
        if not compact:
            return None
        digits = compact.removeprefix("IT")
        if not _VAT_NUMBER.fullmatch(digits):
            raise ValueError("la partita IVA ha 11 cifre")
        return digits

    @field_validator("domicile", mode="after")
    @classmethod
    def _domicile(cls, value: str) -> str:
        return _one_line(value, "il domicilio")


class FiscalRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    freelancer_id: UUID
    tax_code: str
    vat_number: str | None
    domicile: str
    certified_email: str | None
    updated_by: UUID
    updated_at: datetime


class CustomerDate(BaseModel):
    """The client as the letter prints it, asked on «Chi e per chi»."""

    model_config = ConfigDict(extra="forbid")

    customer_legal_name: SafeStr = Field(min_length=1, max_length=COMPANY_MAX_LENGTH)
    customer_vat_number: SafeStr = Field(min_length=1, max_length=CUSTOMER_VAT_NUMBER_MAX_LENGTH)
    customer_registered_office: SafeStr = Field(min_length=1, max_length=HEADQUARTERS_MAX_LENGTH)

    @field_validator(
        "customer_legal_name", "customer_vat_number", "customer_registered_office", mode="after"
    )
    @classmethod
    def _line(cls, value: str) -> str:
        return _one_line(value, "un valore")


class CustomerDraft(ResponseModel):
    """What the prefill suggests on «Chi e per chi»: any of the three may be unknown."""

    customer_legal_name: str | None = None
    customer_vat_number: str | None = None
    customer_registered_office: str | None = None


class LetterDraft(ResponseModel):
    """Every field of `engagement-letter.md` an admin writes on «Condizioni», all
    optional: the shape the prefill suggests. `LetterFields` is the one a letter is
    written from."""

    model_config = ConfigDict(extra="forbid")

    role: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    activities: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    results: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    acceptance: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    start_date: date | None = None
    end_date: date | None = None
    commitment: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    review_period: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    location: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    coordination: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    customer_contact: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    rebase_contact: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    mode: PayMode | None = None
    fee: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE
    )
    unit: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    extra_work: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    expenses: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    payment_days: int | None = Field(default=None, ge=1, le=DAYS_LIMIT)
    month_end: bool | None = None
    billing_deadlines: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    notice_days: int | None = Field(default=None, ge=1, le=NOTICE_MAX_DAYS)
    personal_data: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    purpose_data: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    category_data: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    data_subjects: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    authorization_data: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    exclusivity: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    portfolio: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    insurance: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    other_terms: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)
    previous_reports: SafeStr | None = Field(default=None, max_length=LETTER_TEXT_MAX_LENGTH)

    @field_validator("mode", mode="before")
    @classmethod
    def _pay_mode(cls, value: Any) -> Any:
        """Exactly one of `PAY_MODES`, spelled as stored: no other text, case or spacing
        is read as one. `None` stays blank on a draft; `LetterFields` requires it."""
        if value is not None and value not in PAY_MODES:
            raise PydanticCustomError(
                "pay_mode", "la modalità di pagamento è «a giornata» oppure «a corpo»"
            )
        return value

    @field_validator(*LETTER_TEXT_FIELDS, mode="after")
    @classmethod
    def _text(cls, value: str | None) -> str | None:
        """A paragraph may span lines; control characters and a value of blanks may not.
        The web sends `null` for an empty box, which prints as a labelled blank line."""
        return None if value is None else clean_multiline(value, what="un valore")


class LetterFields(LetterDraft):
    """The letter as it is written: the role, the work, the start, the fee and the
    payment term are required; every other field may stay a blank line on the page."""

    role: SafeStr = Field(min_length=1, max_length=JOB_TITLE_MAX_LENGTH)
    activities: SafeStr = Field(min_length=1, max_length=LETTER_TEXT_MAX_LENGTH)
    start_date: date
    mode: PayMode
    fee: Decimal = Field(max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE)
    payment_days: int = Field(ge=1, le=DAYS_LIMIT)
    month_end: bool

    @field_validator("end_date", mode="after")
    @classmethod
    def _end_not_before_start(cls, value: date | None, info: ValidationInfo) -> date | None:
        """`start_date` is declared before `end_date`, so it is already in `info.data`
        by the time this runs: the error can name `end_date`, the field an admin
        actually filled in, instead of the whole letter."""
        start = info.data.get("start_date")
        if value is not None and start is not None and value < start:
            raise PydanticCustomError("date_order", "la fine prevista viene prima dell'inizio")
        return value

    @model_validator(mode="after")
    def _payment_term_within_the_law(self) -> "LetterFields":
        """`month_end` is declared after `payment_days`, so a `field_validator` on
        `payment_days` cannot read it yet; this stays a model-level check, but
        raises a `ValidationError` built with an explicit `loc` so it still names
        `payment_days`, the field the law (81/2017) actually limits, rather than
        the whole letter."""
        if self.month_end and self.payment_days > DAYS_LIMIT_MONTH_END:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [
                    InitErrorDetails(
                        type=PydanticCustomError(
                            "payment_term",
                            "contati da fine mese, i giorni di pagamento sono al massimo 30 "
                            "(legge 81/2017)",
                        ),
                        loc=("payment_days",),
                        input=self.payment_days,
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
            key = TEMPLATE_KEYS[name]
            if name == "mode" and value is not None:
                fields[key] = PAY_MODE_WORDS[value]
            elif isinstance(value, date):
                fields[key] = italian_date(value)
            elif isinstance(value, Decimal):
                fields[key] = int(value) if value == value.to_integral_value() else float(value)
            else:
                fields[key] = value
        return fields


# «Giorni previsti» (REB-497): `ck_matches_expected_days`'s bounds, the sentence that
# refuses a number outside them, and the one that refuses what is not a whole number.
EXPECTED_DAYS_MIN, EXPECTED_DAYS_MAX = 1, 366
EXPECTED_DAYS_RANGE = "I giorni previsti vanno da 1 a 366."
EXPECTED_DAYS_WHOLE = "I giorni previsti sono un numero intero da 1 a 366."
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
    # «Tipo di lettera» (REB-687): the letter's regime, and so the match's. An ordinary
    # letter needs saved tax data with a partita IVA; left out, the letter is ordinary.
    regime: Regime = VAT_REGISTERED
    customer: CustomerDate
    letter: LetterFields
    # An admin's estimate of the engagement's billable days (REB-497), read back
    # unchanged; `Match.expected_days` carries the same `CHECK`.
    expected_days: int | None = None

    @field_validator("expected_days", mode="before")
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
            raise PydanticCustomError("expected_days_whole", EXPECTED_DAYS_WHOLE) from None

    @field_validator("expected_days", mode="after")
    @classmethod
    def _expected_days_within_a_year(cls, value: int | None) -> int | None:
        """From one day to a year, the column's own `CHECK`, refused in the admin's words
        (REB-502): «Crea match» and the MCP tools show the sentence as it is, where
        `Field(ge=, le=)` would answer Pydantic's English."""
        if value is not None and not EXPECTED_DAYS_MIN <= value <= EXPECTED_DAYS_MAX:
            raise PydanticCustomError("expected_days_range", EXPECTED_DAYS_RANGE)
        return value


class ContractDocumentRead(ResponseModel):
    """A document as the pages and the MCP tools read it: never the PDF bytes and never
    `data`, which carries rebase's signer and the freelancer's tax identifiers.
    `situation`, `next_action` and `other_actions` are `match_words`' (REB-477)."""

    id: UUID
    kind: ContractKind
    # Which family it belongs to and its type as the pages name it (REB-687):
    # «Contratto quadro per prestazioni occasionali», «Lettera di incarico occasionale».
    regime: Regime
    label: str
    freelancer_id: UUID
    match_id: UUID | None
    number: str | None
    text_version: str
    draft_text: bool
    status: ContractState
    created_at: datetime
    created_by: UUID
    sent_at: datetime | None
    signed_at: datetime | None
    notice_at: datetime | None
    # Why it became `cancelled`: the freelancer's own reason when they refused it, or who
    # cancelled it (REB-407).
    cancel_reason: str | None
    has_pdf_signed: bool
    active: bool
    renewal: date | None
    last_notice_day: date | None
    new_version: bool
    situation: str
    next_action: Action | None
    other_actions: list[Action]


class MatchRead(ResponseModel):
    """`expected_days`, the three `lettera_*` values and the eight `pigro_*` fields
    are `Match`'s own (REB-497): the `lettera_*` ones are what `create` copied off
    `data.letter` at the time, not necessarily what the current `letter` prints, were
    it ever regenerated. `pigro_status` is `None` until the match turns `active`, one of
    `PIGRO_STATES` after."""

    id: UUID
    freelancer_id: UUID
    company_id: UUID
    company_name: str
    requested_role: str
    customer_legal_name: str
    customer_vat_number: str
    customer_registered_office: str
    status: MatchState
    created_at: datetime
    created_by: UUID
    cancelled_at: datetime | None
    updated_at: datetime
    letter: ContractDocumentRead
    situation: str
    next_action: Action | None
    other_actions: list[Action]
    expected_days: int | None
    letter_start_date: date | None
    letter_end_date: date | None
    letter_fee: Decimal | None
    pigro_status: PigroState | None
    pigro_slug: str | None
    pigro_deal_id: UUID | None
    pigro_url: str | None
    pigro_linked_at: datetime | None
    pigro_attempted_at: datetime | None
    pigro_error: str | None
    pigro_mail_sent_at: datetime | None


class FreelancerContracts(ResponseModel):
    """«Match e contratti»: the framework agreements at the top, one per regime (REB-687):
    `framework` the ordinary one and `occasional_framework` the occasional one, each the
    active one, else the newest that left; every framework agreement newest first, the
    matches newest first, and the tax data the page edits."""

    freelancer_id: UUID
    framework: ContractDocumentRead | None
    occasional_framework: ContractDocumentRead | None
    frameworks: list[ContractDocumentRead]
    matches: list[MatchRead]
    tax: FiscalRead | None


class MatchPrefill(ResponseModel):
    """What «Chi e per chi» and «Condizioni» start from. `regime` is the letter type the
    hub proposes (REB-687): `occasional` when the saved tax data have no partita IVA,
    `vat_number` otherwise, none saved included. `required_framework`: this match writes a
    framework agreement of that regime. `letter_pending`: the letter waits for a
    framework's signature."""

    tax: FiscalRead | None
    customer: CustomerDraft
    letter: LetterDraft
    regime: Regime
    active_framework: ContractDocumentRead | None
    required_framework: bool
    letter_pending: bool


class MatchCheck(ResponseModel):
    """What saving a match would do, in sentences (REB-476), with nothing written:
    `recap` is the letter in three or four sentences, `what_happens` which document
    leaves first. Missing tax data are reported here, not refused: `create` refuses
    them."""

    recap: list[str]
    what_happens: str
    required_framework: bool
    missing_tax_data: bool
    regime: Regime


class ContractPdf(BaseModel):
    """A document's bytes and the name a browser saves them under."""

    filename: str
    content: bytes


class MatchListItem(ResponseModel):
    """One row of the admin's «Match» list (REB-413): everything the table shows and
    nothing else -- no tax field of the freelancer's and no `daily_budget` of the
    request's. `letter_start_date`/`letter_end_date` are the letter's own
    `data-inizio`/`data-fine` exactly as it printed them (`italian_date`, already a
    finished sentence), not re-formatted here. The four `lettera_*` fields are `None`
    together, only were a match ever to have no letter at all -- `create` always
    writes one, so this is the list staying honest about a shape `get` does not need
    to allow for. `situation` is the match's sentence, the same `MatchRead` carries
    (REB-477). `expected_days`, `pigro_status` and `pigro_url` are `Match`'s own
    (REB-497), the row's narrower share of what `MatchRead` carries in full. REB-609
    adds `company_id` (a link, unless `company_deleted`: a deleted request's page
    answers not found), the letter's fee as it printed it (`letter_fee`
    and `unit_letter`, the letter's `mode`/`unit`; a fee is not a tax field) and
    `referrals`, one entry per referred side of the match: what it earns, projected or
    real, never the client's budget itself."""

    id: UUID
    freelancer_id: UUID
    company_id: UUID
    company_deleted: bool
    freelancer_name: str
    freelancer_last_name: str
    freelancer_email: str
    company_name: str
    requested_role: str
    status: MatchState
    number_letter: str | None
    status_letter: ContractState | None
    letter_start_date: str | None
    letter_end_date: str | None
    letter_fee: Decimal | None
    unit_letter: str | None
    created_at: datetime
    created_by_name: str
    created_by_email: str
    situation: str
    expected_days: int | None
    pigro_status: PigroState | None
    pigro_url: str | None
    referrals: list[MatchReferral]


class MatchList(ResponseModel):
    """Newest first (REB-413). `total` counts every match the filters select, the
    whole list, not just the page returned."""

    total: int
    items: list[MatchListItem]


class ReportDayInvoice(ResponseModel):
    """A day's hours on one invoice (REB-505): the invoice as `ReportInvoice` names it,
    by `number` and `kind`, and the day's entries on it summed."""

    number: str
    kind: str
    hours: Decimal


class ReportDay(ResponseModel):
    """One day of hours on the match's deal (REB-498): the CRM answers one row per time
    entry, summed here. `invoices` names each invoice these hours sit on once, «12/2026»
    for an invoice and «proforma 3/2026» for anything else, empty when none does yet.
    `hours_by_invoice` holds the same invoices in the same order, each with its own share
    of the day, the hours on none left out (REB-505): a day of 8 hours with 3 on
    12/2026 gives 12/2026 its 3, so a month can count an invoice's hours exactly."""

    date: Day
    hours: Decimal
    descriptions: list[str]
    invoices: list[str]
    hours_by_invoice: list[ReportDayInvoice]


class ReportWeek(ResponseModel):
    """An ISO week, «2026-W40», from its Monday to its Sunday."""

    week: str
    from_date: Day
    to_date: Day
    hours: Decimal


class ReportMonth(ResponseModel):
    month: str
    hours: Decimal


class ReportInvoice(ResponseModel):
    """An invoice these hours sit on, with how many of them: «12/2026», or
    «senza numero» for one not numbered yet."""

    number: str
    kind: str
    date: Day | None
    status: str
    payment_status: str
    hours: Decimal


class MatchReport(ResponseModel):
    """«Consuntivo» (REB-498): the hours logged on the match's deal on Pigro over a
    period, read from the CRM on every request and never stored. `expected_hours` is
    `expected_days` times eight, `equivalent_days` the hours over eight,
    `progress` the hours as a percentage of `expected_hours`, two places, `None`
    without an estimate. What counts as billed is the CRM's own word."""

    match_id: UUID
    pigro_url: str | None
    pigro_status: PigroState | None
    expected_days: int | None
    expected_hours: Decimal | None
    hours_total: Decimal
    equivalent_days: Decimal
    progress: Decimal | None
    invoiced_hours: Decimal
    hours_uninvoiced: Decimal
    by_day: list[ReportDay]
    by_week: list[ReportWeek]
    by_month: list[ReportMonth]
    invoices: list[ReportInvoice]


class SendReport(ResponseModel):
    """What «Invia per la firma» did (REB-387 phase 3): the match as it is now, the kind
    of the document that left (`framework`, `letter`, or none when the letter waits for a
    framework agreement already out for signature), and whether its mail left too."""

    match: MatchRead
    sent: ContractKind | None
    mail_sent: bool | None


class MemberContract(ResponseModel):
    """A contract as its freelancer reads it in «Contratti» (REB-392): never the PDF's
    bytes and never `data`. `customer` and the two dates are a letter's, as the letter
    prints them; `signing_url` is there only while the document waits for the
    signature, since its path is the signer's token. `pigro_url` is the letter's
    match's own (REB-498), set only once that match is `linked`: «Le tue ore su
    Pigro» has somewhere to point (spec § 3.6)."""

    id: UUID
    kind: ContractKind
    # Which family it belongs to and its type as the page names it (REB-687).
    regime: Regime
    label: str
    number: str | None
    status: ContractState
    customer: str | None
    start: str | None
    end: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    has_pdf_signed: bool
    active: bool
    renewal: date | None
    last_notice_day: date | None
    pigro_url: str | None


class MemberContracts(ResponseModel):
    """«Contratti»: the framework agreements on top, one per regime (REB-687): `framework`
    the ordinary one and `occasional_framework` the occasional one, each the active one,
    else the newest that reached the person; the letters newest first; and
    `previous_frameworks` (REB-392): the freelancer's other framework agreements that were
    signed, newest first, excluding the two on top -- a notice, or a newer one replacing
    it, must not make an earlier signed copy disappear from the page."""

    framework: MemberContract | None
    occasional_framework: MemberContract | None = None
    previous_frameworks: list[MemberContract] = Field(default_factory=list)
    letters: list[MemberContract]
