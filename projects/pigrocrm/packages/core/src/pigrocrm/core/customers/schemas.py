from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.validation import SafeStr

# Mirror Customer's column widths (models.py). Without these, an over-length value
# sails past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (StringDataRightTruncation) -- not a subclass of IntegrityError, so no handler catches
# it, and it poisons the session. The same gap fields/schemas.py's KEY_MAX_LENGTH and
# pipeline/schemas.py's NOME_MAX_LENGTH/CODE_MAX_LENGTH already close; this is the
# fourth time this project has hit it.
#
# `partita_iva` is deliberately absent from this list -- see the comment on
# `CustomerCreate.partita_iva` below for why bounding it here would be actively wrong,
# not merely redundant.
RAGIONE_SOCIALE_MAX_LENGTH = 255
CODICE_FISCALE_MAX_LENGTH = 16
CODICE_SDI_MAX_LENGTH = 7
PEC_MAX_LENGTH = 320
INDIRIZZO_MAX_LENGTH = 255
CAP_MAX_LENGTH = 10
COMUNE_MAX_LENGTH = 120
PROVINCIA_MAX_LENGTH = 2
NAZIONE_MAX_LENGTH = 2
EMAIL_MAX_LENGTH = 320
TELEFONO_MAX_LENGTH = 40
SITO_WEB_MAX_LENGTH = 255
STATO_MAX_LENGTH = 40
# The same bounds as the fiscal profile's `giorni_scadenza` (REB-326).
GIORNI_PAGAMENTO_MIN = 0
GIORNI_PAGAMENTO_MAX = 365


class CustomerCreate(BaseModel):
    ragione_sociale: SafeStr = Field(max_length=RAGIONE_SOCIALE_MAX_LENGTH)
    # No `max_length` here, unlike every other fiscal field below: the service's
    # `_check_fiscal` already requires an exact `^\d{11}$` match on every create and
    # update, which structurally rejects any value whose length is not 11 -- a stricter
    # condition than `max_length=11` alone, which would accept a too-short digit string
    # a Pydantic bound cannot distinguish from a valid one. Adding `max_length=11` here
    # as well would not close any additional gap; it would instead intercept an
    # over-length value *before* `_check_fiscal` runs and raise pydantic's own
    # `ValidationError` instead of this project's `ValidationFailed` -- a regression a
    # 12-digit input must not trigger. `SafeStr` still applies: it only rejects a NUL
    # byte, an orthogonal concern to length that `_check_fiscal`'s digit-only regex
    # would also reject, just as a domain `ValidationFailed` instead of a schema error.
    partita_iva: SafeStr | None = None
    codice_fiscale: SafeStr | None = Field(default=None, max_length=CODICE_FISCALE_MAX_LENGTH)
    # No `max_length` either, for the same reason as `partita_iva`: `_check_fiscal`
    # already requires exactly 7 characters on every create and update.
    codice_sdi: SafeStr | None = None
    pec: SafeStr | None = Field(default=None, max_length=PEC_MAX_LENGTH)
    indirizzo: SafeStr | None = Field(default=None, max_length=INDIRIZZO_MAX_LENGTH)
    cap: SafeStr | None = Field(default=None, max_length=CAP_MAX_LENGTH)
    comune: SafeStr | None = Field(default=None, max_length=COMUNE_MAX_LENGTH)
    provincia: SafeStr | None = Field(default=None, max_length=PROVINCIA_MAX_LENGTH)
    nazione: SafeStr = Field(default="IT", max_length=NAZIONE_MAX_LENGTH)
    # Plain `str`, not `EmailStr`: unlike `UserCreate.email`, nothing in this codebase
    # validates a customer's email format today, and this fix wave does not add that.
    # `SafeStr` still closes the NUL-byte gap that a plain `str` would otherwise leave
    # open -- `EmailStr` happens to reject a NUL byte as a side effect of its own
    # syntax check (verified against the installed email-validator), but this field has
    # no such check to piggyback on.
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    telefono: SafeStr | None = Field(default=None, max_length=TELEFONO_MAX_LENGTH)
    sito_web: SafeStr | None = Field(default=None, max_length=SITO_WEB_MAX_LENGTH)
    stato: SafeStr | None = Field(default=None, max_length=STATO_MAX_LENGTH)
    note: SafeStr | None = None
    # Payment terms (REB-326): the days after the invoice date, and the end-of-month
    # slide. Absent days mean the fiscal profile's `giorni_scadenza`; the bounds are the
    # profile's own (`fiscal/schemas.py`), because a term the profile could not hold is
    # not one a customer can either.
    giorni_pagamento: int | None = Field(
        default=None, ge=GIORNI_PAGAMENTO_MIN, le=GIORNI_PAGAMENTO_MAX
    )
    pagamento_fine_mese: bool = False
    custom_fields: dict[str, Any] = {}


class CustomerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ragione_sociale: SafeStr | None = Field(default=None, max_length=RAGIONE_SOCIALE_MAX_LENGTH)
    # See CustomerCreate.partita_iva: _check_fiscal covers this field's length exactly,
    # so no Pydantic bound is added here either.
    partita_iva: SafeStr | None = None
    codice_fiscale: SafeStr | None = Field(default=None, max_length=CODICE_FISCALE_MAX_LENGTH)
    # See CustomerCreate.codice_sdi: _check_fiscal covers this field's length exactly.
    codice_sdi: SafeStr | None = None
    pec: SafeStr | None = Field(default=None, max_length=PEC_MAX_LENGTH)
    indirizzo: SafeStr | None = Field(default=None, max_length=INDIRIZZO_MAX_LENGTH)
    cap: SafeStr | None = Field(default=None, max_length=CAP_MAX_LENGTH)
    comune: SafeStr | None = Field(default=None, max_length=COMUNE_MAX_LENGTH)
    provincia: SafeStr | None = Field(default=None, max_length=PROVINCIA_MAX_LENGTH)
    nazione: SafeStr | None = Field(default=None, max_length=NAZIONE_MAX_LENGTH)
    email: SafeStr | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)
    telefono: SafeStr | None = Field(default=None, max_length=TELEFONO_MAX_LENGTH)
    sito_web: SafeStr | None = Field(default=None, max_length=SITO_WEB_MAX_LENGTH)
    stato: SafeStr | None = Field(default=None, max_length=STATO_MAX_LENGTH)
    note: SafeStr | None = None
    giorni_pagamento: int | None = Field(
        default=None, ge=GIORNI_PAGAMENTO_MIN, le=GIORNI_PAGAMENTO_MAX
    )
    pagamento_fine_mese: bool | None = None
    custom_fields: dict[str, Any] | None = None


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ragione_sociale: str
    partita_iva: str | None
    codice_fiscale: str | None
    codice_sdi: str | None
    pec: str | None
    indirizzo: str | None
    cap: str | None
    comune: str | None
    provincia: str | None
    nazione: str
    email: str | None
    telefono: str | None
    sito_web: str | None
    stato: str | None
    note: str | None
    giorni_pagamento: int | None
    pagamento_fine_mese: bool
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# Residuo R9. Three keys and no more: every admitted column costs a `(column, id)`
# B-tree index (migration 0022), and a nullable one costs two. The list is short for
# that reason, not out of caution. `created_at` is the default because it reproduces
# today's behaviour exactly -- UUIDv7 order *is* creation order -- which is what keeps
# this change contained to the type of `cursor`.
CUSTOMER_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Customer.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Customer.updated_at, kind="datetime", nullable=False),
        SortSpec(
            key="ragione_sociale",
            column=Customer.ragione_sociale,
            kind="text",
            nullable=False,
        ),
    ),
    default_key="created_at",
)


class CustomerListQuery(BaseModel):
    # `SafeStr`, not a bare `str`, on every free-text parameter: these values reach
    # psycopg as query parameters, and a NUL byte there raises a raw `ValueError` out
    # of the driver rather than any exception this project handles.
    search: SafeStr | None = None
    stato: SafeStr | None = None
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
    # Validated against CUSTOMER_SORTS by the repository, which raises a domain
    # `ValidationFailed` naming `sort`. Not a `Literal` here on purpose: a Literal
    # would answer with pydantic's own error shape, and both the web client's
    # `fieldErrorFrom` and an MCP agent read this project's `field` key instead.
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class CustomerPage(BaseModel):
    items: list[CustomerRead]
    next_cursor: str | None
