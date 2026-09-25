"""What the hub sends through the engagements door and what the door answers (spec
2026-09-25 § 2.3).

The bounds are the CRM's own rules for a customer (`CustomerService._check_fiscal`) and
the hub's for a name, applied at the door, so a value the space would refuse is a 422
naming the field, never a customer that half exists.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
    or found it already there (200); `spazio_creato` whether it opened the space."""

    slug: str
    url: str
    customer_id: UUID
    deal_id: UUID
    deal_url: str
    spazio_creato: bool
    creato: bool
