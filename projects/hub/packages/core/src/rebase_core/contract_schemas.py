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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.contracts.fields import DAYS_LIMIT, DAYS_LIMIT_MONTH_END, Value, italian_date
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

# The letter's text fields an admin writes at step 4, in the Markdown's order.
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

# The letter's fields the hub fills itself, never the admin at step 4: the number, the
# framework's date, the two parties, and the four signing fields.
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
    """What step 2 of «Crea match» and «Match e contratti» save for a freelancer."""

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
    """The client as the letter prints it (step 3)."""

    model_config = ConfigDict(extra="forbid")

    cliente_ragione_sociale: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    cliente_piva: SafeStr = Field(min_length=1, max_length=CLIENTE_PIVA_MAX_LENGTH)
    cliente_sede: SafeStr = Field(min_length=1, max_length=SEDE_MAX_LENGTH)

    @field_validator("cliente_ragione_sociale", "cliente_piva", "cliente_sede", mode="after")
    @classmethod
    def _line(cls, value: str) -> str:
        return _one_line(value, "un valore")


class ClienteDraft(BaseModel):
    """What the prefill suggests for step 3: any of the three may be unknown."""

    cliente_ragione_sociale: str | None = None
    cliente_piva: str | None = None
    cliente_sede: str | None = None


class LetteraDraft(BaseModel):
    """Every field of `lettera-di-incarico.md` an admin writes at step 4, all optional:
    the shape the prefill suggests. `LetteraFields` is the one a letter is written from."""

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

    @model_validator(mode="after")
    def _dates_and_term(self) -> "LetteraFields":
        if self.data_fine is not None and self.data_fine < self.data_inizio:
            raise ValueError("la fine prevista viene prima dell'inizio")
        if self.fine_mese and self.giorni_pagamento > DAYS_LIMIT_MONTH_END:
            raise ValueError(
                "contati da fine mese, i giorni di pagamento sono al massimo 30 (legge 81/2017)"
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


class MatchCreate(BaseModel):
    """Steps 1, 3 and 4 of «Crea match». The tax data of step 2 are saved by their own
    route when the admin leaves that step, and read back from `freelancer_fiscal`."""

    model_config = ConfigDict(extra="forbid")

    company_id: UUID
    cliente: ClienteData
    lettera: LetteraFields


class ContractDocumentRead(BaseModel):
    """A document as the pages and the MCP tools read it: never the PDF bytes and never
    `data`, which carries rebase's signer and the freelancer's tax identifiers."""

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
    ha_pdf_firmato: bool
    attivo: bool
    rinnovo: date | None
    ultimo_giorno_disdetta: date | None
    nuova_versione: bool


class MatchRead(BaseModel):
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
    """What steps 2 to 4 start from. `quadro_necessario`: this match writes a framework
    agreement. `lettera_in_attesa`: the letter waits for a framework's signature."""

    fiscale: FiscalRead | None
    cliente: ClienteDraft
    lettera: LetteraDraft
    quadro_attivo: ContractDocumentRead | None
    quadro_necessario: bool
    lettera_in_attesa: bool


class ContractPdf(BaseModel):
    """A document's bytes and the name a browser saves them under."""

    filename: str
    content: bytes
