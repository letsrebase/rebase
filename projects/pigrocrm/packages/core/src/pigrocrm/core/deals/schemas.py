from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.db import CURSOR_MAX_LENGTH, SortDirection, SortSpec, SortWhitelist
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.validation import SafeStr

# Mirrors Deal's column width (models.py). Without this, an over-length value sails
# past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (StringDataRightTruncation) -- not a subclass of IntegrityError, so no handler
# catches it, and it poisons the session. The same gap CustomerCreate's own
# *_MAX_LENGTH constants (customers/schemas.py) and PersonCreate's own (people/
# schemas.py) close -- this project has hit the class of bug often enough that it is
# now a standing global constraint, not a one-off fix.
#
# `note` is deliberately absent: it is `Text` in models.py, which has no column
# width to mirror.
NOME_MAX_LENGTH = 255

# Mirror Deal's Numeric(p, s) column widths (models.py): valore_previsto and
# valore_preventivato are Numeric(12, 2), ore_preventivate is Numeric(8, 2).
# Without max_digits/decimal_places here, a value beyond a column's capacity sails
# past Pydantic, reaches flush(), and comes back as a raw sqlalchemy.exc.DataError
# (NumericValueOutOfRange) -- not a subclass of IntegrityError, so no handler
# catches it, and it poisons the session. Same failure mode as an over-length
# string reaching a String(n) column (NOME_MAX_LENGTH above); this is the sixth
# time this project has hit the family, and the first on a Numeric column -- Deal
# is the first entity with one, so there was no template to copy here.
#
# decimal_places=2 does double duty: it also closes a second, subtler gap. Postgres
# silently rounds a sub-scale value (e.g. 0.005) to the column's scale (0.01) on
# write, but this project's session_factory sets expire_on_commit=False
# (db/session.py), so the in-memory object -- and the DealRead built straight from
# it -- keeps reporting the original, unrounded 0.005: the immediate response would
# lie about what was actually written. Rejecting the value outright, rather than
# rounding it silently, means this service never has to decide on the caller's
# behalf which cent they meant.
VALORE_MAX_DIGITS = 12
ORE_MAX_DIGITS = 8
DECIMAL_PLACES = 2

# `tariffa_oraria` is Numeric(12,6) -- a factor, not an amount. See the column's own
# comment in models.py for why slice 3 fixes the precision.
FACTOR_MAX_DIGITS = 12
FACTOR_DECIMAL_PLACES = 6

# No Pydantic `ge`/`le` bound on `probabilita` below, unlike `posizione`/`position`
# elsewhere in this sweep -- see the final-review item on bounding every Integer
# column. The reason is the same one documented on `CustomerCreate.partita_iva`
# (customers/schemas.py) for why that field has no `max_length` either: the
# service's own `_check_numbers` already requires `0 <= probabilita <= 100` on every
# create and update, which structurally rejects any value Postgres's `Integer`
# column could not hold, including `2**40` -- there is no gap left for a schema
# bound to close. Adding `Field(ge=0, le=100)` here would not close an additional
# gap; it would instead intercept an out-of-range value *before* `_check_numbers`
# runs and raise pydantic's own `ValidationError` instead of this project's
# `ValidationFailed` -- confirmed by actually adding it and watching
# `test_probability_outside_range_is_rejected` fail with exactly that swapped
# exception type, a regression a value like 150 must not trigger.


class DealCreate(BaseModel):
    nome: SafeStr = Field(max_length=NOME_MAX_LENGTH)
    customer_id: UUID
    pipeline_stage_id: UUID | None = None
    valore_previsto: Decimal | None = Field(
        default=None, max_digits=VALORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    probabilita: int | None = None
    data_chiusura_prevista: date | None = None
    # Validated against `users` by `DealService` (`create`/`update` both call
    # `_check_owner`), the same way `customer_id` is validated against `customers`:
    # a real foreign key with no schema-level way to bound it further than "a UUID".
    owner_id: UUID | None = None
    note: SafeStr | None = None
    ore_preventivate: Decimal | None = Field(
        default=None, max_digits=ORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    valore_preventivato: Decimal | None = Field(
        default=None, max_digits=VALORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    tariffa_oraria: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )
    custom_fields: dict[str, Any] = {}


class DealUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `customer_id` and `pipeline_stage_id` are deliberately absent here, the same
    # way `code` is absent from `PipelineStageUpdate`: reassigning either through a
    # generic update would bypass the validation and probability-settling logic that
    # `create`/`move_stage` exist to enforce. `move_stage` is the only supported way
    # to change a deal's stage.
    nome: SafeStr | None = Field(default=None, max_length=NOME_MAX_LENGTH)
    valore_previsto: Decimal | None = Field(
        default=None, max_digits=VALORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    probabilita: int | None = None
    data_chiusura_prevista: date | None = None
    owner_id: UUID | None = None
    note: SafeStr | None = None
    ore_preventivate: Decimal | None = Field(
        default=None, max_digits=ORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    valore_preventivato: Decimal | None = Field(
        default=None, max_digits=VALORE_MAX_DIGITS, decimal_places=DECIMAL_PLACES
    )
    tariffa_oraria: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_DECIMAL_PLACES, ge=0
    )
    custom_fields: dict[str, Any] | None = None


class DealRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # The customer's azienda when the deal was created (REB-623); read-only.
    azienda_id: UUID
    nome: str
    customer_id: UUID
    pipeline_stage_id: UUID
    valore_previsto: Decimal | None
    probabilita: int
    data_chiusura_prevista: date | None
    owner_id: UUID | None
    note: str | None
    ore_preventivate: Decimal | None
    valore_preventivato: Decimal | None
    # Bare `Decimal | None`, no bound: a Read schema validates values the database
    # produced, so a bound here would reject a row the column legitimately holds.
    tariffa_oraria: Decimal | None
    # Derived, never supplied: on no Create or Update schema, because a caller who could
    # set it could claim a closure that never happened, straight into the conversion rate.
    chiuso_il: date | None
    # The customer's `ragione_sociale`, denormalised onto the read shape exactly as
    # `PersonRead.customer_ragione_sociale` is (see that field's own comment): the name
    # lives on `customers` and renaming a customer must not need a second write here.
    # `DealService` fills it from one batched lookup per page
    # (`DealRepository.customer_names`), which is why the default is `None` -- a bare
    # `model_validate(deal)` reads a `Deal` that has no such attribute at all, so without
    # a default every read path would raise.
    #
    # Typed `str | None` although `deals.customer_id` is NOT NULL: the *association*
    # always exists, but this schema validates whatever the lookup found, and a name that
    # could not be resolved must read as absent rather than crash the whole page.
    #
    # Present on `DealRead` and on no write schema: a caller sets `customer_id`, never
    # the name. Also absent from `native_fields` (schema_registry.py derives that list
    # from `DealCreate`), which is correct twice over -- it is not writable, and it is not
    # a column an administrator could collide with by slugifying a custom field into it.
    customer_ragione_sociale: str | None = None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# Residuo R9. Mirrors CUSTOMER_SORTS (customers/schemas.py) -- see there for why three
# keys and why `created_at` is the default.
DEAL_SORTS = SortWhitelist(
    specs=(
        SortSpec(key="created_at", column=Deal.created_at, kind="datetime", nullable=False),
        SortSpec(key="updated_at", column=Deal.updated_at, kind="datetime", nullable=False),
        SortSpec(key="nome", column=Deal.nome, kind="text", nullable=False),
    ),
    default_key="created_at",
)


class DealListQuery(BaseModel):
    # See CustomerListQuery for why every free-text parameter here is `SafeStr` and why
    # `cursor` is an opaque bounded string rather than a UUID.
    search: SafeStr | None = None
    customer_id: UUID | None = None
    azienda_id: UUID | None = None
    stage_id: UUID | None = None
    custom: dict[str, Any] | None = None
    # The two drill-throughs of the operational dashboard's signal cards (§6.2). Booleans
    # and not free-text filters: each selects one fixed predicate, and the card that links
    # here counts rows with that same predicate function -- `invoiced_not_won_predicate`
    # and `won_with_unbilled_hours_predicate` -- so a card and its list cannot drift apart.
    # Same shape as `DocumentListQuery.solo_deal_non_vinto`.
    fatturato_non_vinto: bool = False
    da_fatturare: bool = False
    # Upper-bounded so a caller (an MCP agent especially) cannot request an
    # unbounded page; matches CustomerListQuery.limit/PersonListQuery.limit exactly.
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=CURSOR_MAX_LENGTH)
    sort: SafeStr | None = None
    dir: SortDirection = "asc"


class DealPage(BaseModel):
    items: list[DealRead]
    next_cursor: str | None
