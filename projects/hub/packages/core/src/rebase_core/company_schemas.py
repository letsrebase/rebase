"""The company request: what the wizard accepts, the admin's read and override, and the
talent-cloud grant a company shows."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.linkedin import normalise_linkedin
from rebase_core.models import (
    AZIENDA_MAX_LENGTH,
    DURATA_MAX_LENGTH,
    GIORNI_PRESENZA_MAX,
    GIORNI_PRESENZA_MIN,
    NAME_MAX_LENGTH,
    POSIZIONE_MAX_LENGTH,
    TELEFONO_MAX_LENGTH,
)
from rebase_core.note_schemas import NoteRead
from rebase_core.schema_common import (
    DISTINCT_ID_MAX_LENGTH,
    LINKEDIN_INPUT_MAX_LENGTH,
    PROGETTO_MAX_LENGTH,
    TARIFFA_MAX,
    TARIFFA_MIN,
    Remoto,
    clean_multiline,
    clean_rif,
    clean_text,
)
from rebase_core.signup_schemas import SignupUtm
from rebase_core.validation import SafeStr

CompanyStato = Literal["nuovo", "contattato", "in_corso", "chiuso"]


class CompanyFields(BaseModel):
    """The eight answers about a request that its referente may write and later
    change: what `CompanyCreate` collects together with the company's own identity,
    and what `CompanyUpdate` alone accepts once signed in, mirroring
    `FreelancerFields`' split for the freelancer side (REB-314). REB-380 widens the
    original four (`progetto`/`periodo_da`/`durata`/`budget_giornaliero`) with the
    work arrangement, the headcount and a scannable role label."""

    model_config = ConfigDict(extra="forbid")

    progetto: SafeStr = Field(min_length=1, max_length=PROGETTO_MAX_LENGTH)
    periodo_da: date
    durata: SafeStr = Field(min_length=1, max_length=DURATA_MAX_LENGTH)
    budget_giornaliero: Decimal = Field(
        max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    remoto: Remoto
    giorni_presenza: int | None = Field(
        default=None, ge=GIORNI_PRESENZA_MIN, le=GIORNI_PRESENZA_MAX
    )
    numero_risorse: int = Field(ge=1)
    figura_richiesta: SafeStr = Field(min_length=1, max_length=POSIZIONE_MAX_LENGTH)

    @field_validator("durata", "figura_richiesta", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")

    @field_validator("progetto", mode="after")
    @classmethod
    def _progetto(cls, value: str) -> str:
        """Multi-line is the point of a project description, so newlines stay."""
        return clean_multiline(value, what="una descrizione del progetto")

    @model_validator(mode="after")
    def _giorni_presenza_matches_remoto(self) -> "CompanyFields":
        """The database's own `ck_companies_giorni_presenza_together`, enforced here
        too so the wizard and the member area refuse the same request the database
        would, with a sentence rather than an `IntegrityError`."""
        if (self.remoto == "ibrido") != (self.giorni_presenza is not None):
            raise ValueError("giorni_presenza va indicato solo, e sempre, per il lavoro ibrido")
        return self


class CompanyCreate(CompanyFields):
    """What the wizard collects: the eight `CompanyFields` answers plus the company's
    own identity and its referente's, get-or-created by email."""

    nome_azienda: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    referente_nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    referente_cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    telefono: SafeStr = Field(min_length=6, max_length=TELEFONO_MAX_LENGTH)
    email: EmailStr
    utm: SignupUtm | None = None
    distinct_id: SafeStr | None = Field(default=None, max_length=DISTINCT_ID_MAX_LENGTH)
    # A member's own referral link, when the funnel carried one (P-REB-44).
    rif: SafeStr | None = None

    @field_validator("rif", mode="before")
    @classmethod
    def _rif(cls, value: object) -> str | None:
        return clean_rif(value)

    @field_validator(
        "nome_azienda", "referente_nome", "referente_cognome", "telefono", mode="after"
    )
    @classmethod
    def _trimmed_identity(cls, value: str) -> str:
        return clean_text(value, what="un valore")


class CompanyAdminCreate(CompanyCreate):
    """What an admin opens a request with, on the referente's behalf (REB-870): the
    wizard's fields, validated the same way, plus the CRM deal the company already has,
    when it has one. With it the CRM links that deal and creates nothing."""

    deal_id: UUID | None = None


class CompanyUpdate(CompanyFields):
    """What a company contact changes about one of their requests (REB-314, by id since
    REB-602): the eight project answers, never `stato`, `note`, `nome_azienda`,
    `telefono` or the referente's identity -- the same field-isolation `MemberUpdate`
    keeps for the freelancer card."""


class TalentCloudGrantRead(BaseModel):
    """One grant of the talent cloud (REB-518, spec § 4.1), as the admin reads it: the
    request it was opened from and its company's name, the referente by name and
    address (the admin sees names everywhere), who opened it and when, and who closed
    it and when, `None` while it is live."""

    id: UUID
    user_id: UUID
    company_id: UUID
    azienda: str
    referente: str
    email: str
    granted_by: UUID
    granted_by_nome: str | None
    granted_at: datetime
    revoked_by: UUID | None = None
    revoked_by_nome: str | None = None
    revoked_at: datetime | None = None


class CompanyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome_azienda: str
    referente: str
    email: str
    telefono: str | None
    figura_richiesta: str
    progetto: str
    periodo_da: date
    durata: str
    budget_giornaliero: Decimal
    remoto: str
    giorni_presenza: int | None
    numero_risorse: int
    stato: str
    # The request's place in rebase's CRM (REB-868): the state (`PIGRO_STATES`; `None` for a
    # request from before the link), the cause of the last failure, the page's sentence for
    # it, the deal's address and the time of the last attempt.
    crm_stato: str | None = None
    crm_errore: str | None = None
    crm_situazione: str | None = None
    crm_deal_url: str | None = None
    crm_attempted_at: datetime | None = None
    origine: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # Same soft-delete as `FreelancerRead.deleted_at`.
    deleted_at: datetime | None = None
    # The thread, newest first; filled by `get` only, as on `FreelancerRead`.
    note: list[NoteRead] = Field(default_factory=list)
    # The talent cloud this request's referente holds for it, while it is open
    # (REB-518): filled by `get` only, for «Apri il talent cloud» and «Revoca».
    talent_cloud_grant: TalentCloudGrantRead | None = None


class CompanyList(BaseModel):
    """Newest-or-best-match first (REB-285 adds search, filters and the cursor beside
    `stato`). `totale` and `per_stato` follow `TalentoList`'s own reasoning; both are
    additive to the shape `GET /api/hub/companies` already answered, so an older caller
    that ignores unknown fields sees nothing change."""

    totale: int
    items: list[CompanyRead]
    per_stato: dict[str, int]
    next_cursor: str | None = None


class CompanyOverride(BaseModel):
    """What an admin may set or clear on a `Company` request beyond `stato`
    (REB-347): the eight project answers a referente could have written
    (`CompanyFields`), the company's own name, and the referente's identity fields on
    the linked `users` row -- both admin-only even for a self-edit, as
    `MemberService.update_company`'s own docstring says. Same optional-and-`null`
    contract as `FreelancerOverride`. `remoto`/`numero_risorse`/`figura_richiesta`
    are `NOT NULL` columns (REB-380), so `rebase_core.audit.reject_cleared_columns`
    refuses an override that tries to null them, the same as any other `NOT NULL`
    field here; `giorni_presenza` stays nullable, like on `Freelancer`."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    nome_azienda: SafeStr | None = Field(default=None, max_length=AZIENDA_MAX_LENGTH)
    figura_richiesta: SafeStr | None = Field(default=None, max_length=POSIZIONE_MAX_LENGTH)
    progetto: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)
    periodo_da: date | None = None
    durata: SafeStr | None = Field(default=None, max_length=DURATA_MAX_LENGTH)
    budget_giornaliero: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=TARIFFA_MIN, le=TARIFFA_MAX
    )
    remoto: Remoto | None = None
    giorni_presenza: int | None = Field(
        default=None, ge=GIORNI_PRESENZA_MIN, le=GIORNI_PRESENZA_MAX
    )
    numero_risorse: int | None = Field(default=None, ge=1)
    stato: CompanyStato | None = None

    @field_validator("nome", "cognome", "nome_azienda", "durata", "figura_richiesta", mode="after")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return clean_text(value, what="un valore") if value is not None else None

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("progetto", mode="after")
    @classmethod
    def _progetto(cls, value: str | None) -> str | None:
        return (
            clean_multiline(value, what="una descrizione del progetto")
            if value is not None
            else None
        )
