from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.contact_points.schemas import (
    MAX_CONTACT_POINTS,
    ContactPointIn,
    ContactPointRead,
)
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.organizations.roles import (
    ORGANIZATION_ROLES,
    RoleFilter,
)
from pigrocrm.core.validation import SafeStr

# Mirror Organization's column widths (models.py). Without these, an over-length value
# sails past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (StringDataRightTruncation) -- not a subclass of IntegrityError, so no handler catches
# it, and it poisons the session. The same gap fields/schemas.py's KEY_MAX_LENGTH and
# pipeline/schemas.py's NOME_MAX_LENGTH/CODE_MAX_LENGTH already close; this is the
# fourth time this project has hit it.
#
# `vat_number` is deliberately absent from this list -- see the comment on
# `OrganizationCreate.vat_number` below for why bounding it here would be actively wrong.
LEGAL_NAME_MAX_LENGTH = 255
TAX_CODE_MAX_LENGTH = 16
SDI_CODE_MAX_LENGTH = 7
CERTIFIED_EMAIL_MAX_LENGTH = 320
ADDRESS_MAX_LENGTH = 255
POSTAL_CODE_MAX_LENGTH = 10
CITY_MAX_LENGTH = 120
PROVINCE_MAX_LENGTH = 2
COUNTRY_MAX_LENGTH = 2
EMAIL_MAX_LENGTH = 320
WEBSITE_MAX_LENGTH = 255
STATUS_MAX_LENGTH = 40
# The same bounds as the fiscal profile's `due_days` (REB-326).
PAYMENT_DAYS_MIN = 0
PAYMENT_DAYS_MAX = 365
KIND_MAX_LENGTH = 20

CONTACT_POINTS_DESCRIPTION = (
    "The customer's contact points (email, phone, linkedin), each with a label and an optional "
    "note. On an update the list replaces the stored one. At most one point per kind is "
    "primary (the first of a kind is when none is flagged). The PEC address stays in "
    "certified_email."
)


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: SafeStr = Field(max_length=LEGAL_NAME_MAX_LENGTH)
    # No `max_length` here, unlike the other fiscal fields below: `_check_fiscal` bounds it
    # on every create and update (exactly eleven digits for an Italian number, at most
    # `VAT_NUMBER_MAX_LENGTH` characters for any other), and answers a `ValidationFailed`
    # a person can read. A pydantic bound would intercept an over-length value first and
    # answer in pydantic's own error shape. `SafeStr` still rejects a NUL byte.
    vat_number: SafeStr | None = None
    tax_code: SafeStr | None = Field(default=None, max_length=TAX_CODE_MAX_LENGTH)
    # No `max_length` either, for the same reason as `vat_number`: `_check_fiscal`
    # already requires exactly 7 characters on every create and update.
    sdi_code: SafeStr | None = None
    certified_email: SafeStr | None = Field(default=None, max_length=CERTIFIED_EMAIL_MAX_LENGTH)
    address: SafeStr | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    postal_code: SafeStr | None = Field(default=None, max_length=POSTAL_CODE_MAX_LENGTH)
    city: SafeStr | None = Field(default=None, max_length=CITY_MAX_LENGTH)
    province: SafeStr | None = Field(default=None, max_length=PROVINCE_MAX_LENGTH)
    country: SafeStr = Field(default="IT", max_length=COUNTRY_MAX_LENGTH)
    # The legal entity that bills this customer (REB-623, spec §1.6). `None` asks the
    # service for the one `country` proposes; an id names one, which must be active.
    legal_entity_id: UUID | None = None
    # The owner (REB-773). Omitted, it is whoever creates the customer; naming someone
    # else is an admin's act, like changing it later.
    owner_id: UUID | None = None
    contact_points: list[ContactPointIn] = Field(
        default_factory=list, max_length=MAX_CONTACT_POINTS, description=CONTACT_POINTS_DESCRIPTION
    )
    website: SafeStr | None = Field(default=None, max_length=WEBSITE_MAX_LENGTH)
    status: SafeStr | None = Field(default=None, max_length=STATUS_MAX_LENGTH)
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    # Payment terms (REB-326): the days after the invoice date, and the end-of-month
    # slide. Absent days mean the fiscal profile's `due_days`; the bounds are the
    # profile's own (`fiscal/schemas.py`), because a term the profile could not hold is
    # not one a customer can either.
    payment_days: int | None = Field(default=None, ge=PAYMENT_DAYS_MIN, le=PAYMENT_DAYS_MAX)
    payment_end_of_month: bool = False
    # The same two terms in the other direction: what it grants us as a supplier.
    supplier_payment_days: int | None = Field(
        default=None, ge=PAYMENT_DAYS_MIN, le=PAYMENT_DAYS_MAX
    )
    supplier_payment_end_of_month: bool = False
    # The role and fiscal-kind values are checked by the service, which answers in Italian
    # and names the field, where a `Literal` here would answer with pydantic's own error.
    roles: list[SafeStr] = Field(
        default_factory=lambda: ["customer"],
        max_length=len(ORGANIZATION_ROLES),
    )
    supplier_kind: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    supplier_area: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    supplier_supplies: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    customer_kind: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    is_withholding_agent: bool = False
    custom_fields: dict[str, Any] = {}


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: SafeStr | None = Field(default=None, max_length=LEGAL_NAME_MAX_LENGTH)
    # See OrganizationCreate.vat_number: _check_fiscal bounds this field, so no Pydantic bound.
    vat_number: SafeStr | None = None
    tax_code: SafeStr | None = Field(default=None, max_length=TAX_CODE_MAX_LENGTH)
    # See OrganizationCreate.codice_sdi: _check_fiscal covers this field's length exactly.
    sdi_code: SafeStr | None = None
    certified_email: SafeStr | None = Field(default=None, max_length=CERTIFIED_EMAIL_MAX_LENGTH)
    address: SafeStr | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    postal_code: SafeStr | None = Field(default=None, max_length=POSTAL_CODE_MAX_LENGTH)
    city: SafeStr | None = Field(default=None, max_length=CITY_MAX_LENGTH)
    province: SafeStr | None = Field(default=None, max_length=PROVINCE_MAX_LENGTH)
    country: SafeStr | None = Field(default=None, max_length=COUNTRY_MAX_LENGTH)
    # Editable afterwards (§1.6); moving it moves nothing already created (§1.7).
    legal_entity_id: UUID | None = None
    # An admin's change (REB-773); an explicit `null` leaves the customer with no owner.
    owner_id: UUID | None = None
    contact_points: list[ContactPointIn] | None = Field(
        default=None, max_length=MAX_CONTACT_POINTS, description=CONTACT_POINTS_DESCRIPTION
    )
    website: SafeStr | None = Field(default=None, max_length=WEBSITE_MAX_LENGTH)
    status: SafeStr | None = Field(default=None, max_length=STATUS_MAX_LENGTH)
    payment_days: int | None = Field(default=None, ge=PAYMENT_DAYS_MIN, le=PAYMENT_DAYS_MAX)
    payment_end_of_month: bool | None = None
    supplier_payment_days: int | None = Field(
        default=None, ge=PAYMENT_DAYS_MIN, le=PAYMENT_DAYS_MAX
    )
    supplier_payment_end_of_month: bool | None = None
    roles: list[SafeStr] | None = Field(default=None, max_length=len(ORGANIZATION_ROLES))
    supplier_kind: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    supplier_area: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    supplier_supplies: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    customer_kind: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    is_withholding_agent: bool | None = None
    custom_fields: dict[str, Any] | None = None


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # `None`: shared across the legal entities of the space.
    legal_entity_id: UUID | None
    owner_id: UUID | None
    legal_name: str
    vat_number: str | None
    tax_code: str | None
    sdi_code: str | None
    certified_email: str | None
    address: str | None
    postal_code: str | None
    city: str | None
    province: str | None
    country: str
    # The primary point of each kind, derived and read only; a write sends `contact_points`.
    email: str | None
    phone: str | None
    contact_points: list[ContactPointRead]
    website: str | None
    status: str | None
    payment_days: int | None
    payment_end_of_month: bool
    supplier_payment_days: int | None
    supplier_payment_end_of_month: bool
    roles: list[str]
    supplier_kind: str | None
    supplier_area: str | None
    supplier_supplies: str | None
    customer_kind: str | None
    is_withholding_agent: bool
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# Residuo R9. Three keys and no more: every admitted column costs a `(column, id)`
# B-tree index (migration 0022), and a nullable one costs two. The list is short for
# that reason, not out of caution. `created_at` is the default because it reproduces
# today's behaviour exactly -- UUIDv7 order *is* creation order -- which is what keeps
# this change contained to the type of `cursor`.
ORGANIZATION_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Organization.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Organization.updated_at, kind="datetime", nullable=False),
        SortSpec(
            key="legal_name",
            column=Organization.legal_name,
            kind="text",
            nullable=False,
        ),
    ),
    default_key="created_at",
)


class OrganizationListQuery(BaseModel):
    # `SafeStr`, not a bare `str`, on every free-text parameter: these values reach
    # psycopg as query parameters, and a NUL byte there raises a raw `ValueError` out
    # of the driver rather than any exception this project handles.
    search: SafeStr | None = None
    # One legal entity's organizations (REB-623); `None` is every legal entity the caller may see.
    legal_entity_id: UUID | None = None
    status: SafeStr | None = None
    # One role, or `any` for the registry across roles. The default is `customer`: every
    # existing read of the registry sees customers only, unless it says otherwise (design
    # 2026-11-09 suppliers § 1, «Role-blind reads»).
    role: RoleFilter = "customer"
    custom: dict[str, Any] | None = None
    # Upper-bounded so a caller (an MCP agent especially) cannot request an
    # unbounded page; the router will impose the same ceiling at the HTTP layer.
    limit: int = Field(default=50, ge=1, le=200)
    # `str`, not `UUID`, since slice 6: ordering by a non-unique column needs the pair
    # `(sort value, id)`, and the pair is opaque so that a null is representable -- an
    # empty string in a query parameter is indistinguishable from a null, and rows are
    # lost on exactly that distinction. Clients echo `next_cursor` back verbatim and
    # never parse it. See `db/sort.py`.
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    # Validated against ORGANIZATION_SORTS by the repository, which raises a domain
    # `ValidationFailed` naming `sort`. Not a `Literal` here on purpose: a Literal
    # would answer with pydantic's own error shape, and both the web client's
    # `fieldErrorFrom` and an MCP agent read this project's `field` key instead.
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class OrganizationPage(BaseModel):
    items: list[OrganizationRead]
    next_cursor: str | None


class PersonFromSuggestion(BaseModel):
    """Somebody a customer proposal named, ticked for import (REB-223)."""

    model_config = ConfigDict(extra="forbid")

    address: SafeStr = Field(max_length=EMAIL_MAX_LENGTH)
    # The display name the mail headers gave, or "" when they gave none.
    name: SafeStr = Field(default="", max_length=LEGAL_NAME_MAX_LENGTH)


class OrganizationFromSuggestion(BaseModel):
    """One ticked proposal: the domain it was proposed for, the company name as the
    person corrected it, and the people to create under it."""

    model_config = ConfigDict(extra="forbid")

    domain: SafeStr = Field(max_length=WEBSITE_MAX_LENGTH)
    legal_name: SafeStr = Field(max_length=LEGAL_NAME_MAX_LENGTH)
    people: list[PersonFromSuggestion] = Field(default_factory=list, max_length=50)

    @field_validator("legal_name")
    @classmethod
    def _named(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("serve il nome dell'azienda")
        return stripped


class OrganizationsFromSuggestions(BaseModel):
    """What `POST /api/organizations/from-suggestions` takes: the proposals a person ticked,
    created together or not at all."""

    model_config = ConfigDict(extra="forbid")

    organizations: list[OrganizationFromSuggestion] = Field(min_length=1, max_length=50)


class DuplicateOrganizationRead(BaseModel):
    """One row of a duplicate group: enough to tell which to keep (the oldest is first)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_name: str
    vat_number: str
    roles: list[str]
    legal_entity_id: UUID | None
    created_at: datetime


class DuplicateGroupRead(BaseModel):
    """Live organizations of one country that share a VAT number once it is normalised
    (upper case, letters and digits only)."""

    country: str
    vat_key: str
    organizations: list[DuplicateOrganizationRead]
