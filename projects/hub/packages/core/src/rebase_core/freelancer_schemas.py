"""The freelancer card: what the wizard and the member area accept, the admin's read and
override, and the detail with the person's logins and guide downloads."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field, field_validator

from rebase_core.linkedin import normalise_linkedin
from rebase_core.login_schemas import GuideDownloadRead, LoginRead
from rebase_core.models import JOB_TITLE_MAX_LENGTH, NAME_MAX_LENGTH
from rebase_core.note_schemas import NoteRead
from rebase_core.schema_common import (
    LINK_MAX_LENGTH,
    LINKEDIN_INPUT_MAX_LENGTH,
    LINKS_MAX,
    MAX_RATE,
    MIN_RATE,
    Remote,
    ResponseModel,
    clean_ref,
    clean_text,
    https_url,
    is_complete,
)
from rebase_core.signup_schemas import SignupListItem, SignupUtm
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import CompilataDa, FreelancerStatus, RemoteOption, Seniority

FreelancerStatus = Literal["new", "contacted", "active", "discarded"]


class FreelancerFields(BaseModel):
    """The seven answers the wizard asks for and the person may later change. One set of
    rules for the wizard (`FreelancerCreate`) and the member area (`MemberUpdate`), so
    the two can never accept different things."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    daily_rate: Decimal = Field(max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE)
    posizione: SafeStr = Field(min_length=1, max_length=JOB_TITLE_MAX_LENGTH)
    remote: Remote
    links: list[SafeStr] = Field(default_factory=list, max_length=LINKS_MAX)

    @field_validator("nome", "last_name", "posizione", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", mode="after")
    @classmethod
    def _links(cls, value: list[str]) -> list[str]:
        cleaned = [https_url(link) for link in value if link.strip()]
        if any(len(link) > LINK_MAX_LENGTH for link in cleaned):
            raise ValueError(f"un link può avere al massimo {LINK_MAX_LENGTH} caratteri")
        return cleaned


class FreelancerCreate(FreelancerFields):
    """What the wizard collects. The CV travels beside this body, not inside it: the API
    takes it as a multipart file and hands the bytes to the service with this schema."""

    email: EmailStr
    utm: SignupUtm | None = None
    # A member's own referral link, when the funnel carried one (P-REB-44): a sibling of
    # `distinct_id` on `CompanyCreate`, not folded into `utm` -- `Freelancer(**utm_dict)`
    # spreads every key of `SignupUtm.model_dump()` straight onto real columns, and a
    # code is not one of them.
    ref: SafeStr | None = None

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: object) -> str | None:
        return clean_ref(value)


class MemberUpdate(FreelancerFields):
    """What a member changes about themselves: the wizard's answers, never the email
    (it is the identity the link proved) and never the admin's fields."""


SOURCES_MAX = 10


class FreelancerDraft(BaseModel):
    """What an admin found about a signup on the public web (ORB-155): a name, maybe a
    LinkedIn profile, a position, some links. The same rules as `FreelancerFields` on
    the same names, with the answers nothing public states -- the rate, the remote
    option, even the position -- optional, and `None` meaning «not found». The CV is
    not here at all: it comes from the person. `sources` is required and non-empty, because
    a card written from research with no source is a card nobody can check."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    posizione: SafeStr | None = Field(default=None, max_length=JOB_TITLE_MAX_LENGTH)
    daily_rate: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE
    )
    remote: Remote | None = None
    links: list[SafeStr] = Field(default_factory=list, max_length=LINKS_MAX)
    sources: list[SafeStr] = Field(min_length=1, max_length=SOURCES_MAX)

    @field_validator("nome", "last_name", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")

    @field_validator("posizione", mode="after")
    @classmethod
    def _trimmed_or_none(cls, value: str | None) -> str | None:
        return None if value is None else clean_text(value, what="una posizione")

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", "sources", mode="after")
    @classmethod
    def _urls(cls, value: list[str]) -> list[str]:
        return FreelancerFields._links(value)


class FreelancerRead(ResponseModel):
    """The row as an admin reads it. Never the CV bytes: those have their own download,
    so a list of two hundred people is not two hundred PDFs in one response.

    `note` is the thread (REB-856), newest first, and only `get` fills it: a list of two
    hundred people is not two hundred threads either, so the list leaves it empty."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    last_name: str
    email: str
    linkedin_url: str | None
    # The phone on the person's account (`users.phone`, REB-855), `None` while they have
    # given none; the admin's page shows the CV's beside it, from the card's persona.
    phone: str | None = None
    # `None` on a card born from a signup that the person has not completed (ORB-155),
    # and on one whose owner skipped the wizard's optional CV step.
    cv_filename: str | None
    cv_mime: str | None
    cv_size: int | None
    daily_rate: Decimal | None
    posizione: str | None
    remote: RemoteOption | None
    links: list[str]
    status: FreelancerStatus
    # Who wrote the seven answers last: `person` or `admin` (`COMPILATA_DA`).
    compilata_da: CompilataDa
    # How many times the person entered through a magic link, and the last time (ORB-158).
    # Filled by the service with one grouped join; `0` and `None` for a card never opened.
    logins: int = 0
    last_access: datetime | None = None
    # Where the lead came from, in Ivan's words (ORB-161): «form» when the address also
    # left its email on the landing («Iscrizioni»), «landing» otherwise. Derived by the
    # service from the two tables, never stored, so it cannot drift.
    provenance: str = "landing"
    origin: str | None = None
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    utm_id: str | None = None
    created_at: datetime
    updated_at: datetime
    # `None` while the card is live; a moment once an admin soft-deletes it (REB-347).
    deleted_at: datetime | None = None
    # When an admin marked the talent «Verificato» (REB-518), `None` while nobody has or
    # after the mark was taken off; who did it is in the audit trail.
    vetted_at: datetime | None = None
    # What an admin said of the person's level (REB-799), `None` while nobody has: it
    # beats the anonymous card's own seniority and years wherever the card is read.
    seniority_admin: Seniority | None = None
    experience_years_admin: int | None = None
    note: list[NoteRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return is_complete(self)


class FreelancerList(ResponseModel):
    """The cards, and since ORB-163 the leads beside them: signups whose address has no
    card yet, as «Developer e CTO» shows them with a «Lead» state. `total` counts the
    cards the filter selects, `total_lead` the leads; `lead` is empty when a `status`
    other than «lead» is asked for, `items` when «lead» is."""

    total: int
    items: list[FreelancerRead]
    total_lead: int = 0
    lead: list[SignupListItem] = Field(default_factory=list)


class FreelancerOverride(BaseModel):
    """What an admin may set or clear on a `Freelancer` beyond `status`
    (REB-347): every field a person could have written through the wizard or the
    member area, plus the three identity fields (`nome`/`last_name`/`linkedin_url`)
    that live on the linked `users` row since REB-281, and `compilata_da`, which
    nothing else lets an admin touch by hand, and `seniority_admin`/`experience_years_admin`, the
    level that beats the anonymous card's (REB-799).

    Every field is optional, since one call changes only the ones it names --
    `rebase_core.audit.supplied_changes` reads which ones that is from
    `model_fields_set`, never from whether the value is `None`: an explicit `null`
    clears a nullable column and is refused on a `NOT NULL` one by
    `rebase_core.audit.reject_cleared_columns`. The CV has its own route
    (`FreelancerService.clear_cv`, clear-only): its bytes are personal data an audit
    payload must never carry, so it is not part of this schema at all."""

    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LINKEDIN_INPUT_MAX_LENGTH)
    daily_rate: Decimal | None = Field(
        default=None, max_digits=7, decimal_places=2, ge=MIN_RATE, le=MAX_RATE
    )
    posizione: SafeStr | None = Field(default=None, max_length=JOB_TITLE_MAX_LENGTH)
    remote: Remote | None = None
    links: list[SafeStr] | None = Field(default=None, max_length=LINKS_MAX)
    status: FreelancerStatus | None = None
    compilata_da: Literal["person", "admin"] | None = None
    # The level an admin sets over the card's own (REB-799): `null` takes the correction
    # off, and the card's reading shows again.
    seniority_admin: Literal["junior", "mid", "senior", "lead"] | None = None
    experience_years_admin: int | None = Field(default=None, ge=0, le=60)

    @field_validator("nome", "last_name", "posizione", mode="after")
    @classmethod
    def _trimmed(cls, value: str | None) -> str | None:
        return clean_text(value, what="un valore") if value is not None else None

    @field_validator("linkedin_url", mode="after")
    @classmethod
    def _linkedin(cls, value: str | None) -> str | None:
        return normalise_linkedin(value)

    @field_validator("links", mode="after")
    @classmethod
    def _links(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [https_url(link) for link in value if link.strip()]
        if any(len(link) > LINK_MAX_LENGTH for link in cleaned):
            raise ValueError(f"un link può avere al massimo {LINK_MAX_LENGTH} caratteri")
        return cleaned


class FreelancerDetail(FreelancerRead):
    """`FreelancerRead` plus every other place the hub already knows this address
    (REB-284): the sign-up's own UTM set (`signup_utm`, never the card's own utm
    fields above -- an admin-drafted card copies the signup's UTM at creation but a
    wizard card carries its own, and the two can differ), the last handful of logins
    and guide downloads, and the PigroCRM space slug when the address owns one. Only
    `FreelancerService.get` fills these: the list stays `FreelancerRead` alone, since
    two hundred people are not two hundred fan-outs to four sources."""

    signup_utm: SignupUtm | None = None
    last_logins: list[LoginRead] = Field(default_factory=list)
    last_download_guide: list[GuideDownloadRead] = Field(default_factory=list)
    pigro_slug: str | None = None


class CvFile(BaseModel):
    """The bytes and the two headers a download needs."""

    filename: str
    mime: str
    content: bytes
