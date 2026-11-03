"""The company request: what the wizard accepts, the admin's read and override, and the
talent-cloud grant a company shows."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.linkedin import normalise_linkedin
from rebase_core.models import (
    COMPANY_MAX_LENGTH,
    DURATION_MAX_LENGTH,
    JOB_TITLE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    ONSITE_DAYS_MAX,
    ONSITE_DAYS_MIN,
    PHONE_MAX_LENGTH,
)
from rebase_core.note_schemas import NoteRead
from rebase_core.schema_common import (
    DISTINCT_ID_MAX_LENGTH,
    LINKEDIN_INPUT_MAX_LENGTH,
    MAX_RATE,
    MIN_RATE,
    PROJECT_MAX_LENGTH,
    Remote,
    ResponseModel,
    clean_multiline,
    clean_ref,
    clean_text,
)
from rebase_core.signup_schemas import SignupUtm
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import CompanyStatus, PigroState, RemoteOption


class CompanyFields(BaseModel):
    """The eight answers about a request that its contact may write and later
    change: what `CompanyCreate` collects together with the company's own identity,
    and what `CompanyUpdate` alone accepts once signed in, mirroring
    `FreelancerFields`' split for the freelancer side (REB-314). REB-380 widens the
    original four (`project`/`period_from`/`duration`/`daily_budget`) with the
    work arrangement, the headcount and a scannable role label."""

    model_config = ConfigDict(extra="forbid")

    project: SafeStr = Field(min_length=1, max_length=PROJECT_MAX_LENGTH)
    period_from: date
    duration: SafeStr = Field(min_length=1, max_length=DURATION_MAX_LENGTH)
    daily_budget: Decimal = Field(max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE)
    remote: Remote
    onsite_days: int | None = Field(default=None, ge=ONSITE_DAYS_MIN, le=ONSITE_DAYS_MAX)
    headcount: int = Field(ge=1)
    requested_role: SafeStr = Field(min_length=1, max_length=JOB_TITLE_MAX_LENGTH)

    @field_validator("duration", "requested_role", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")

    @field_validator("project", mode="after")
    @classmethod
    def _project(cls, value: str) -> str:
        """Multi-line is the point of a project description, so newlines stay."""
        return clean_multiline(value, what="una descrizione del progetto")

    @model_validator(mode="after")
    def _onsite_days_matches_remote(self) -> "CompanyFields":
        """The database's own `ck_companies_onsite_days_together`, enforced here
        too so the wizard and the member area refuse the same request the database
        would, with a sentence rather than an `IntegrityError`."""
        if (self.remote == "hybrid") != (self.onsite_days is not None):
            raise ValueError("onsite_days va indicato solo, e sempre, per il lavoro ibrido")
        return self


class CompanyCreate(CompanyFields):
    """What the wizard collects: the eight `CompanyFields` answers plus the company's
    own identity and its contact's, get-or-created by email."""

    company_name: SafeStr = Field(min_length=1, max_length=COMPANY_MAX_LENGTH)
    contact_first_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    contact_last_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    phone: SafeStr = Field(min_length=6, max_length=PHONE_MAX_LENGTH)
    email: EmailStr
    utm: SignupUtm | None = None
    distinct_id: SafeStr | None = Field(default=None, max_length=DISTINCT_ID_MAX_LENGTH)
    # A member's own referral link, when the funnel carried one (P-REB-44).
    ref: SafeStr | None = None

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: object) -> str | None:
        return clean_ref(value)

    @field_validator(
        "company_name", "contact_first_name", "contact_last_name", "phone", mode="after"
    )
    @classmethod
    def _trimmed_identity(cls, value: str) -> str:
        return clean_text(value, what="un valore")


class CompanyAdminCreate(CompanyCreate):
    """What an admin opens a request with, on the contact's behalf (REB-870): the
    wizard's fields, validated the same way, plus the CRM deal the company already has,
    when it has one. With it the CRM links that deal and creates nothing."""

    deal_id: UUID | None = None


class CompanyUpdate(CompanyFields):
    """What a company contact changes about one of their requests (REB-314, by id since
    REB-602): the eight project answers, never `status`, `note`, `company_name`,
    `phone` or the contact's identity -- the same field-isolation `MemberUpdate`
    keeps for the freelancer card."""


class TalentCloudGrantRead(ResponseModel):
    """One grant of the talent cloud (REB-518, spec § 4.1), as the admin reads it: the
    request it was opened from and its company's name, the contact by name and
    address (the admin sees names everywhere), who opened it and when, and who closed
    it and when, `None` while it is live."""

    id: UUID
    user_id: UUID
    company_id: UUID
    company: str
    contact_person: str
    email: str
    granted_by: UUID
    granted_by_name: str | None
    granted_at: datetime
    revoked_by: UUID | None = None
    revoked_by_name: str | None = None
    revoked_at: datetime | None = None


class CompanyRead(ResponseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    contact_person: str
    email: str
    phone: str | None
    requested_role: str
    project: str
    period_from: date
    duration: str
    daily_budget: Decimal
    remote: RemoteOption
    onsite_days: int | None
    headcount: int
    status: CompanyStatus
    # The request's place in rebase's CRM (REB-868): the state (`PIGRO_STATES`; `None` for a
    # request from before the link), the cause of the last failure, the page's sentence for
    # it, the deal's address and the time of the last attempt.
    crm_status: PigroState | None = None
    crm_error: str | None = None
    crm_situation: str | None = None
    crm_deal_url: str | None = None
    crm_attempted_at: datetime | None = None
    origin: str | None = None
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
    # The talent cloud this request's contact holds for it, while it is open
    # (REB-518): filled by `get` only, for «Apri il talent cloud» and «Revoca».
    talent_cloud_grant: TalentCloudGrantRead | None = None


class CompanyList(ResponseModel):
    """Newest-or-best-match first (REB-285 adds search, filters and the cursor beside
    `status`). `total` and `by_status` follow `TalentList`'s own reasoning; both are
    additive to the shape `GET /api/hub/companies` already answered, so an older caller
    that ignores unknown fields sees nothing change."""

    total: int
    items: list[CompanyRead]
    by_status: dict[str, int]
    next_cursor: str | None = None


class CompanyOverride(BaseModel):
    """What an admin may set or clear on a `Company` request beyond `status`
    (REB-347): the eight project answers a contact could have written
    (`CompanyFields`), the company's own name, and the contact's identity fields on
    the linked `users` row -- both admin-only even for a self-edit, as
    `MemberService.update_company`'s own docstring says. Same optional-and-`null`
    contract as `FreelancerOverride`. `remote`/`headcount`/`requested_role`
    are `NOT NULL` columns (REB-380), so `rebase_core.audit.reject_cleared_columns`
    refuses an override that tries to null them, the same as any other `NOT NULL`
    field here; `onsite_days` stays nullable, like on `Freelancer`."""

    model_config = ConfigDict(extra="forbid")

    first_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    company_name: SafeStr | None = Field(default=None, max_length=COMPANY_MAX_LENGTH)
    requested_role: SafeStr | None = Field(default=None, max_length=JOB_TITLE_MAX_LENGTH)
    project: SafeStr | None = Field(default=None, max_length=PROJECT_MAX_LENGTH)
    period_from: date | None = None
    duration: SafeStr | None = Field(default=None, max_length=DURATION_MAX_LENGTH)
    daily_budget: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE
    )
    remote: Remote | None = None
    onsite_days: int | None = Field(default=None, ge=ONSITE_DAYS_MIN, le=ONSITE_DAYS_MAX)
    headcount: int | None = Field(default=None, ge=1)
    status: CompanyStatus | None = None

    @field_validator(
        "first_name", "last_name", "company_name", "duration", "requested_role", mode="after"
    )
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return clean_text(value, what="un valore") if value is not None else None

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("project", mode="after")
    @classmethod
    def _project(cls, value: str | None) -> str | None:
        return (
            clean_multiline(value, what="una descrizione del progetto")
            if value is not None
            else None
        )
