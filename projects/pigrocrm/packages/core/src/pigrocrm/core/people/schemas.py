from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH
from pigrocrm.core.people.models import Person
from pigrocrm.core.validation import SafeStr

# Mirror Person's column widths (models.py). Without these, an over-length value
# sails past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (StringDataRightTruncation) -- not a subclass of IntegrityError, so no handler catches
# it, and it poisons the session. The same gap CustomerCreate's own *_MAX_LENGTH
# constants close on Customer (customers/schemas.py); this project has hit the class of
# bug often enough that it is now a standing global constraint, not a one-off fix.
#
# `note` is deliberately absent: it is the text of the person's first note, a row of the
# `notes` thread, not a column.
NAME_MAX_LENGTH = 120
LAST_NAME_MAX_LENGTH = 120
EMAIL_MAX_LENGTH = 320
PHONE_MAX_LENGTH = 40
ROLE_MAX_LENGTH = 120
LINKEDIN_MAX_LENGTH = 255


def _normalise_email(value: str | None) -> str | None:
    return value.strip().lower() if isinstance(value, str) else value


class PersonCreate(BaseModel):
    first_name: SafeStr = Field(max_length=NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, max_length=LAST_NAME_MAX_LENGTH)
    # Plain `str`, not `EmailStr`: format is checked separately by the service's own
    # `_check_email` regex below, not by the schema. `SafeStr` closes the NUL-byte gap
    # that regex does not: `[^@\s]` matches a NUL byte just as readily as a letter.
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    phone: SafeStr | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    role: SafeStr | None = Field(default=None, max_length=ROLE_MAX_LENGTH)
    linkedin: SafeStr | None = Field(default=None, max_length=LINKEDIN_MAX_LENGTH)
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    customer_id: UUID | None = None
    custom_fields: dict[str, Any] = {}

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        # `mode="before"` runs ahead of both the `max_length` constraint above and
        # SafeStr's own NUL check -- like `FieldDefinitionCreate.key`'s slugify
        # validator (fields/schemas.py) -- so both apply to the normalised value, not
        # to whatever raw string arrived. Stripping/lowering never removes a NUL byte,
        # so SafeStr still catches one introduced anywhere in the original input.
        return _normalise_email(value)


class PersonUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: SafeStr | None = Field(default=None, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, max_length=LAST_NAME_MAX_LENGTH)
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    phone: SafeStr | None = Field(default=None, max_length=PHONE_MAX_LENGTH)
    role: SafeStr | None = Field(default=None, max_length=ROLE_MAX_LENGTH)
    linkedin: SafeStr | None = Field(default=None, max_length=LINKEDIN_MAX_LENGTH)
    customer_id: UUID | None = None
    custom_fields: dict[str, Any] | None = None
    # Detaching is its own explicit flag rather than an overloaded `customer_id=None`.
    # It was born as a workaround -- `exclude_none` could not express "set customer_id
    # back to null" -- and task 4B-1, which closed A14, could have retired it. It did
    # not: `detach` is already the shape every caller and the MCP schema below are
    # written against, and it names an intention ("unlink this person") that a bare
    # `null` does not. `PersonService.update` keeps `customer_id` out of the generic
    # dump for the same reason, so the two spellings cannot disagree.
    #
    # The `description` is not decorative: this field's own JSON Schema entry is
    # what an MCP client renders for `changes.detach` (see apps/mcp's
    # `PersonChanges` schema override), and the bare name "detach" does not say
    # what it detaches -- unlike `customer_id`, which reads as what it is.
    detach: bool = Field(
        default=False,
        description=(
            "Se true, rimuove il collegamento della persona al cliente attuale invece di "
            "assegnarne uno nuovo con customer_id."
        ),
    )

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        return _normalise_email(value)


class PersonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    role: str | None
    linkedin: str | None
    customer_id: UUID | None
    # The person's "azienda di riferimento", denormalised onto the read shape: it is not
    # a column on `people` and never becomes one -- the name lives on `customers` and
    # renaming a customer must not need a second write here. `PersonService` fills it
    # from one batched lookup per page (`PersonRepository.customer_names`), which is why
    # the default is `None`: `model_validate(person)` reads a `Person` that has no such
    # attribute at all, so without a default every read path would raise.
    #
    # Present on `PersonRead` and on no write schema: a caller sets the *association*
    # (`customer_id`, or `PersonUpdate.detach`), never the name. Also absent from
    # `native_fields` (schema_registry.py derives that list from `PersonCreate`), which
    # is correct twice over -- it is not writable, and it is not a column an
    # administrator could collide with by slugifying a custom field into it.
    customer_legal_name: str | None = None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# Residuo R9. Mirrors CUSTOMER_SORTS (customers/schemas.py) -- see there for why three
# keys and why `created_at` is the default.
PERSON_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Person.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Person.updated_at, kind="datetime", nullable=False),
        # The one nullable sort column in the whole whitelist, and the only spec for which
        # `order_by` emits NULLS LAST at all -- on a NOT NULL column that spelling matches
        # no index and costs a sequential scan. It is also why `people` carries a second,
        # descending index (`ix_people_last_name_desc_id`), and why `keyset_predicate` keeps
        # its `OR cognome IS NULL` disjunction here instead of the row-value seek the other
        # eleven use.
        SortSpec(key="last_name", column=Person.last_name, kind="text", nullable=True),
    ),
    default_key="created_at",
)


class PersonListQuery(BaseModel):
    # See CustomerListQuery for why every free-text parameter here is `SafeStr` and why
    # `cursor` is an opaque bounded string rather than a UUID.
    search: SafeStr | None = None
    customer_id: UUID | None = None
    custom: dict[str, Any] | None = None
    # Upper-bounded so a caller (an MCP agent especially) cannot request an
    # unbounded page; matches CustomerListQuery.limit exactly.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class PersonPage(BaseModel):
    items: list[PersonRead]
    next_cursor: str | None
