"""What the hub sends through the engagements door and what the door answers (spec
2026-09-25 § 2.3), and the report of a match's hours it reads back (§ 2.4); and the body
and answer of the leads route (spec 2026-10-30 § 2, § 3).

The bounds are the CRM's own rules for a customer (`CustomerService._check_fiscal`) and
the hub's for a name, applied at the door, so a value the space would refuse is a 422
naming the field, never a customer that half exists.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH
from pigrocrm.core.validation import SafeStr

NAME_MAX_LENGTH = 120  # the hub's NAME_MAX_LENGTH
SPACE_NAME_MAX_LENGTH = 200  # TenantSignup.nome
# The deal's name carries the role and the company cut to these, so the longest letter
# (a 20-character number, a 200-character role, a 255-character company) still fits
# `deals.nome`'s 255.
ROLE_IN_DEAL_NAME = 80
COMPANY_IN_DEAL_NAME = 100
# A letter's fee is per day; the deal's rate is per hour.
HOURS_PER_DAY = Decimal(8)
# The longest period one report covers, `a - da` in days: the hub walks a longer
# engagement in windows of this many days whose bounds touch.
REPORT_MAX_DAYS = 800


class EngagementFreelancer(BaseModel):
    email: EmailStr
    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)


class EngagementLetter(BaseModel):
    numero: SafeStr = Field(min_length=1, max_length=20)
    ruolo: SafeStr = Field(min_length=1, max_length=200)
    azienda: SafeStr = Field(min_length=1, max_length=255)
    data_inizio: date
    data_fine: date | None = None
    # The daily fee, as the letter prints it.
    compenso: Decimal = Field(gt=0, max_digits=9, decimal_places=2)
    giorni_previsti: int | None = Field(default=None, ge=1, le=366)


class EngagementRebase(BaseModel):
    """rebase's own fiscal data, as the customer «rebase» in the freelancer's space."""

    ragione_sociale: SafeStr = Field(min_length=1, max_length=255)
    partita_iva: str | None = Field(default=None, pattern=r"^\d{11}$")
    codice_fiscale: SafeStr | None = Field(default=None, max_length=16)
    indirizzo: SafeStr | None = Field(default=None, max_length=255)
    pec: EmailStr | None = None
    # `SafeStr` like `CustomerCreate.codice_sdi`: a NUL byte is a 422 here, not a
    # driver error in the space.
    codice_sdi: SafeStr | None = Field(default=None, min_length=7, max_length=7)


class EngagementUpsert(BaseModel):
    """The body of `PUT /api/rebase/engagements/{match_id}`."""

    model_config = ConfigDict(extra="forbid")

    freelancer: EngagementFreelancer
    lettera: EngagementLetter
    rebase: EngagementRebase


class EngagementRead(BaseModel):
    """What the door set up for a match: the space, the customer and the deal, with the
    two links the hub shows. `creato` says whether this call wrote the deal's row (201)
    or found it already there (200); `spazio_creato` whether the space was opened for
    this match, said by the call that writes the row (a retry of a call that stopped on
    the way included) and false on the calls after it."""

    slug: str
    url: str
    customer_id: UUID
    deal_id: UUID
    deal_url: str
    spazio_creato: bool
    creato: bool


class ReportInvoice(BaseModel):
    """An invoice some of the report's hours sit on. `data` is its `data_emissione`;
    `ore` only on the report's `fatture` list, the hours of this report on it, and
    `None` on an entry's own `fattura`."""

    id: UUID
    tipo: str  # "fattura" | "proforma"
    anno: int | None
    numero: int | None
    stato: str
    stato_pagamento: str
    data: date | None
    ore: Decimal | None = None


class ReportEntry(BaseModel):
    """One time entry of the deal: a day with two entries is two rows, and the hub sums."""

    data: date
    ore: Decimal
    descrizione: str
    fatturabile: bool
    fattura: ReportInvoice | None


class ReportDeal(BaseModel):
    """The letter's deal as it stands, `stato` as the deal's own page reads it
    (`DealTimeSummary.stato`)."""

    id: UUID
    nome: str
    tariffa_oraria: Decimal | None
    ore_preventivate: Decimal | None
    stato: str


class EngagementReport(BaseModel):
    """The answer of `GET /api/rebase/engagements/{match_id}/report`. `ore_fatturate` is
    the CRM's own billed (`billed_entry_ids`: on a line of an issued `fattura`), so it
    agrees with the deal's summary; `ore_non_fatturate` is every other hour of the
    period; `fatture` has one row per invoice among the entries, newest first."""

    slug: str
    deal_url: str
    deal: ReportDeal
    giorni: list[ReportEntry]
    totale_ore: Decimal
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    fatture: list[ReportInvoice]


# The bounds below are the CRM's own: `customers.ragione_sociale` 255, `people` 120/40/255,
# `deals.nome` 255 and `deals.valore_previsto` Numeric(12, 2). The hub cuts or drops what
# does not fit before sending (spec 2026-10-30 § 3), so a value past them is a 422 naming
# the field, never a request that half exists.
LEAD_COMPANY_MAX_LENGTH = 255
LEAD_PERSON_NAME_MAX_LENGTH = 120
LEAD_PHONE_MAX_LENGTH = 40
LEAD_LINKEDIN_MAX_LENGTH = 255
LEAD_DEAL_NAME_MAX_LENGTH = 255


class LeadReferente(BaseModel):
    """The company's contact person, as the wizard collected them."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=LEAD_PERSON_NAME_MAX_LENGTH)
    cognome: SafeStr | None = Field(default=None, max_length=LEAD_PERSON_NAME_MAX_LENGTH)
    email: EmailStr
    telefono: SafeStr | None = Field(default=None, max_length=LEAD_PHONE_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LEAD_LINKEDIN_MAX_LENGTH)


class LeadDeal(BaseModel):
    """The deal as the hub built it, AI draft included. `tariffa_oraria` and
    `ore_preventivate` are deliberately not here: in the CRM they value the hours logged
    on a deal, and a prospect's typed budget is not that."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=LEAD_DEAL_NAME_MAX_LENGTH)
    valore_previsto: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    data_chiusura_prevista: date | None = None
    # The hub's longest note is about 6,000 characters (its fields are bounded); a sentence
    # is added to it, so the room under the note's own bound is kept.
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH - 1_000)


class LeadUpsert(BaseModel):
    """The body of `PUT /api/rebase/leads/{request_id}`. `deal_id` is the admin path: it
    links the request to a deal the CRM already has and creates nothing else."""

    model_config = ConfigDict(extra="forbid")

    azienda: SafeStr = Field(min_length=1, max_length=LEAD_COMPANY_MAX_LENGTH)
    referente: LeadReferente
    deal: LeadDeal
    deal_id: UUID | None = None


class LeadRead(BaseModel):
    """What the door filed for a request. `creato` says whether this call wrote the deal
    (201) or found it by its marker, or linked an explicit one (200). `person_id` is
    `None` only when a retry finds the deal and nobody with the referente's address any
    more, or an explicit deal whose customer has no such person."""

    customer_id: UUID
    person_id: UUID | None
    deal_id: UUID
    deal_url: str
    creato: bool
