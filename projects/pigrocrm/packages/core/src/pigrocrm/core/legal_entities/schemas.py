from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.validation import SafeStr

# Mirror LegalEntity's column widths exactly (emitter/models.py). Without these an
# over-long value reaches Postgres and raises sqlalchemy.exc.DataError, which is not
# an IntegrityError subclass, so no handler catches it and the session is poisoned.
NAME_MAX_LENGTH = 80
LEGAL_NAME_MAX_LENGTH = 255
TAX_CODE_MAX_LENGTH = 16
ADDRESS_MAX_LENGTH = 255
POSTAL_CODE_MAX_LENGTH = 10
CITY_MAX_LENGTH = 120
PROVINCE_MAX_LENGTH = 2
COUNTRY_MAX_LENGTH = 2
CERTIFIED_EMAIL_MAX_LENGTH = 320
PHONE_MAX_LENGTH = 40
EMAIL_MAX_LENGTH = 320
WEBSITE_MAX_LENGTH = 255
STORAGE_KEY_MAX_LENGTH = 255
TAX_REGIME_MAX_LENGTH = 200
# `email_signature` is a `Text` column, so it has no width for this to mirror and nothing
# above breaks if it is exceeded. Bounded anyway, on `templates/schemas.py`'s own
# reasoning about a string nested in JSONB: an unbounded text field on a signature
# block is still an unbounded text field, and a signature has no more use for 100 000
# characters than a template variable name does.
EMAIL_SIGNATURE_MAX_LENGTH = 2_000


class LegalEntityFields(BaseModel):
    """The fields a write names: the body of `LegalEntityUpsert` minus the version it
    carries since REB-622, and the body `LegalEntityCreate` extends, since a row being
    created has no version to have been built on.

    `name` is optional on purpose: a space with one legal entity never typed one, and the
    service derives it from `legal_name` (cut to the column width) when it is
    missing, which is also what the migration did for the row every space already had.

    `vat_number` and `sdi_code` carry no `max_length`, exactly as `CustomerCreate`
    does: the service's `_check_fiscal` already requires an exact 11-digit / 7-character
    match, which is stricter, and adding a Pydantic bound would make a 12-digit input
    raise pydantic's own `ValidationError` instead of this project's `ValidationFailed`
    -- a regression, not a fix.
    """

    model_config = ConfigDict(extra="forbid")

    name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    legal_name: SafeStr = Field(max_length=LEGAL_NAME_MAX_LENGTH)
    vat_number: SafeStr | None = None
    tax_code: SafeStr | None = Field(default=None, max_length=TAX_CODE_MAX_LENGTH)
    address: SafeStr | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)
    postal_code: SafeStr | None = Field(default=None, max_length=POSTAL_CODE_MAX_LENGTH)
    city: SafeStr | None = Field(default=None, max_length=CITY_MAX_LENGTH)
    province: SafeStr | None = Field(default=None, max_length=PROVINCE_MAX_LENGTH)
    country: SafeStr = Field(default="IT", max_length=COUNTRY_MAX_LENGTH)
    certified_email: SafeStr | None = Field(default=None, max_length=CERTIFIED_EMAIL_MAX_LENGTH)
    sdi_code: SafeStr | None = None
    phone: SafeStr | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    website: SafeStr | None = Field(default=None, max_length=WEBSITE_MAX_LENGTH)
    # No `logo_key` and no `signature_key` since REB-627: the two images are written by
    # `LegalEntityAssets` under keys the server chooses, so a whole-row `PUT` can neither
    # clear them nor point them at somebody else's file. `update` leaves them as they are.
    email_signature: SafeStr | None = Field(default=None, max_length=EMAIL_SIGNATURE_MAX_LENGTH)
    tax_regime: SafeStr | None = Field(default=None, max_length=TAX_REGIME_MAX_LENGTH)


class LegalEntityUpsert(LegalEntityFields):
    """One shape for the first save and for every update, plus the version check of
    REB-622 (spec 2026-10-03 §11): `updated_at` is the row the caller built its draft
    on, read off `LegalEntityRead`, and a save whose value is not the row's own is
    refused with `StaleRow` (409) instead of overwriting another admin's fields. Left
    out, nothing is checked: the MCP tools read right before they write. Sent as
    `null` it is a draft built on no row, which this table, where the row always
    exists, refuses."""

    updated_at: datetime | None = Field(
        default=None,
        description=(
            "L'`updated_at` letto sulla riga da cui parte questa modifica: se nel "
            "frattempo qualcun altro ha salvato, la richiesta è rifiutata con 409 "
            "`stale_row`. Omesso, nessun controllo."
        ),
    )


class LegalEntityCreate(LegalEntityFields):
    """A second legal entity, born with its fiscal profile (spec 2026-10-03 §1.2, §3, §9
    milestone 5): the upsert's fields, a required short name, and the profile body
    `PUT /api/aziende/{id}/fiscal-profile` takes. One request, one transaction, so no
    legal entity ever exists that `issue` would refuse with `NotFound("fiscal_profile")`.

    `name` is required here where the whole-row upsert derives it: the first legal entity of a
    space was never named, but the second is created to be told apart from the first,
    in the sidebar and on every list, and a derived name is a legal name cut to
    eighty characters."""

    name: SafeStr = Field(max_length=NAME_MAX_LENGTH)
    fiscal_profile: FiscalProfileUpsert


class LegalEntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    is_default: bool
    active: bool
    legal_name: str
    vat_number: str | None
    tax_code: str | None
    address: str | None
    postal_code: str | None
    city: str | None
    province: str | None
    country: str
    certified_email: str | None
    sdi_code: str | None
    phone: str | None
    email: str | None
    website: str | None
    logo_key: str | None
    signature_key: str | None
    email_signature: str | None
    tax_regime: str | None
    created_at: datetime
    updated_at: datetime


class LegalEntityDeactivated(LegalEntityRead):
    """What `DELETE /api/aziende/{id}` answers (spec §3): the row, switched off, and
    how many live customers still point at it. Nothing new is born under such a
    customer until it is moved («sposta prima il cliente su un'azienda attiva»), so the
    count is the work the person has left to do, said once, in the same answer."""

    linked_customers: int


# The fields `as_template_values` and the MCP `describe` leave out: identity, state and
# timestamps are facts about the row, not about the business a template prints.
TEMPLATE_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {"id", "is_default", "active", "created_at", "updated_at"}
)
