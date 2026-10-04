"""Every Create/Update/Read/ListQuery/Page for the four tables of this slice.

`max_length` mirrors every `String(n)`; `max_digits`/`decimal_places` mirror every
`Numeric(p, s)`; `SafeStr` guards every user-supplied string. Without those an
over-long value, an over-capacity number or a NUL byte reaches `flush()` and comes
back as a raw `DataError` -- not an `IntegrityError`, so no handler catches it, and the
caller's session is poisoned. This project has closed that family of defects seven
times; it is not reopened on four new tables.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.money import FACTOR_SCALE, HOURS_SCALE, MONEY_SCALE
from pigrocrm.core.validation import SafeStr

# Mirror the columns in models.py.
DESCRIZIONE_MAX_LENGTH = 2000  # `Text`, so no column width to mirror; bounded anyway
FORNITORE_MAX_LENGTH = 200
CATEGORIA_NOME_MAX_LENGTH = 60
CATEGORIA_CODE_MAX_LENGTH = 30
HOURS_MAX_DIGITS = 8
MONEY_MAX_DIGITS = 12
FACTOR_MAX_DIGITS = 12

# `ore` is bounded here as well as by `ck_time_entries_ore_range`, not instead of it:
# the schema bound turns a 422 into a message naming the field before a statement is
# ever issued, and the CHECK covers every other write path including raw SQL. `gt=0`
# not `ge=0`: a zero-hour entry is not an entry, and the way to cancel a wrong one is
# a reversible soft delete (§2.2, last row), not a zero.
ORE_MIN = Decimal("0.01")
ORE_MAX = Decimal("24.00")

CATEGORIA_POSIZIONE_MIN = 0
CATEGORIA_POSIZIONE_MAX = 100_000
ANNO_MIN = 2000
ANNO_MAX = 2200

RateOrigin = Literal["manuale", "deal", "utente", "assente"]
# `costo_origine` never takes `deal`: an hour's cost is a property of who works it,
# not of the client they work it for (§5.1). Declared as its own narrower Literal
# rather than reusing RateOrigin, so the impossible value is unrepresentable instead
# of merely unwritten.
CostOrigin = Literal["manuale", "utente", "assente"]


class TimeEntryCreate(BaseModel):
    deal_id: UUID
    user_id: UUID
    data: date
    ore: Decimal = Field(
        max_digits=HOURS_MAX_DIGITS, decimal_places=HOURS_SCALE, ge=ORE_MIN, le=ORE_MAX
    )
    descrizione: SafeStr = Field(max_length=DESCRIZIONE_MAX_LENGTH)
    fatturabile: bool = True
    # Level 1 of §5.1's resolution: an explicit value in the request wins, and the
    # frozen column is written with it and `origine = "manuale"`. Named identically to
    # the columns on purpose -- `native_fields("time_entry")` is derived from this
    # model, so a differently-named input would leave `tariffa_applicata` outside the
    # A13 guard's reach and a custom field could still collide with it.
    tariffa_applicata: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    costo_applicato: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    note_interne: SafeStr | None = None
    custom_fields: dict[str, Any] = {}


class TimeEntryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # `deal_id` and `user_id` are both here: §4.3 lists `deal_id` among the fields
    # frozen once billed, which means it is mutable while it is not, and criterion 8
    # requires reassigning an existing entry to a user to be attempted and refused
    # when that user is deactivated -- an operation that needs the field to exist.
    deal_id: UUID | None = None
    user_id: UUID | None = None
    data: date | None = None
    ore: Decimal | None = Field(
        default=None,
        max_digits=HOURS_MAX_DIGITS,
        decimal_places=HOURS_SCALE,
        ge=ORE_MIN,
        le=ORE_MAX,
    )
    descrizione: SafeStr | None = Field(default=None, max_length=DESCRIZIONE_MAX_LENGTH)
    fatturabile: bool | None = None
    tariffa_applicata: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    costo_applicato: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    note_interne: SafeStr | None = None
    custom_fields: dict[str, Any] | None = None


class TimeEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    user_id: UUID
    data: date
    ore: Decimal
    descrizione: str
    fatturabile: bool
    tariffa_applicata: Decimal | None
    costo_applicato: Decimal | None
    tariffa_origine: RateOrigin
    costo_origine: CostOrigin
    # Derived and returned already computed, because §6 forbids the browser from doing
    # any economic arithmetic. `None` when the corresponding factor is `None` -- never
    # `0.00`, which would say the work was free.
    valore_riga: Decimal | None = None
    costo_riga: Decimal | None = None
    invoice_line_id: UUID | None
    note_interne: str | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TimeEntryListQuery(BaseModel):
    deal_id: UUID | None = None
    # Through the deal: an entry's azienda is its deal's (REB-623).
    azienda_id: UUID | None = None
    user_id: UUID | None = None
    da: date | None = None
    a: date | None = None
    fatturabile: bool | None = None
    # Three states, not two: `True` = already on an invoice line, `False` = not yet,
    # `None` = do not filter. It is the "how much do I have to invoice?" query.
    fatturato: bool | None = None
    custom: dict[str, Any] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class TimeEntryPage(BaseModel):
    items: list[TimeEntryRead]
    next_cursor: UUID | None


class TimerStart(BaseModel):
    """What a timer knows when it starts. Everything optional but the intent: a deal
    can be chosen later, a description typed later, and `fatturabile` defaults the way a
    freelancer's hour does."""

    deal_id: UUID | None = None
    descrizione: SafeStr = Field(default="", max_length=DESCRIZIONE_MAX_LENGTH)
    fatturabile: bool = True


class TimerUpdate(BaseModel):
    """Changed while running: the person realises which deal this is, or what to call it."""

    deal_id: UUID | None = None
    descrizione: SafeStr | None = Field(default=None, max_length=DESCRIZIONE_MAX_LENGTH)
    fatturabile: bool | None = None


class TimerStop(BaseModel):
    """What may still be decided at the moment of stopping. `data` is the calendar day the
    entry lands on; absent, it is today in the emitter's zone (`today_local`), never the
    UTC day the server happens to be in -- a timer stopped at 00:30 in Rome belongs to the
    day that has just begun there."""

    deal_id: UUID | None = None
    descrizione: SafeStr | None = Field(default=None, max_length=DESCRIZIONE_MAX_LENGTH)
    data: date | None = None


class TimerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    deal_id: UUID | None
    descrizione: str
    fatturabile: bool
    started_at: datetime


class CostCreate(BaseModel):
    deal_id: UUID | None = None
    # Read only for a cost without a deal (REB-623, spec §1.7): a cost with a deal takes
    # the deal's azienda and refuses a different one; without a deal, `None` is a shared
    # cost («condivisa») and an id ties it to one azienda.
    azienda_id: UUID | None = None
    category_id: UUID
    data: date
    importo: Decimal = Field(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE)
    descrizione: SafeStr = Field(max_length=DESCRIZIONE_MAX_LENGTH)
    fornitore: SafeStr | None = Field(default=None, max_length=FORNITORE_MAX_LENGTH)
    document_id: UUID | None = None
    custom_fields: dict[str, Any] = {}


class CostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID | None = None
    azienda_id: UUID | None = None
    category_id: UUID | None = None
    data: date | None = None
    importo: Decimal | None = Field(
        default=None, max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    descrizione: SafeStr | None = Field(default=None, max_length=DESCRIZIONE_MAX_LENGTH)
    fornitore: SafeStr | None = Field(default=None, max_length=FORNITORE_MAX_LENGTH)
    document_id: UUID | None = None
    custom_fields: dict[str, Any] | None = None


class CostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID | None
    azienda_id: UUID | None
    category_id: UUID
    data: date
    importo: Decimal
    descrizione: str
    fornitore: str | None
    document_id: UUID | None
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CostListQuery(BaseModel):
    deal_id: UUID | None = None
    # One azienda's costs (REB-623): a shared cost, `NULL`, appears only when none is
    # asked for, which is the view over every azienda.
    azienda_id: UUID | None = None
    # `True` selects only general expenses (`deal_id IS NULL`). Needed because
    # `deal_id=None` already means "do not filter", and §7.4 gives general expenses a
    # row of their own that has to be selectable.
    solo_generali: bool = False
    category_id: UUID | None = None
    da: date | None = None
    a: date | None = None
    custom: dict[str, Any] | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class CostPage(BaseModel):
    items: list[CostRead]
    next_cursor: UUID | None


class CostCategoryCreate(BaseModel):
    nome: SafeStr = Field(max_length=CATEGORIA_NOME_MAX_LENGTH)
    posizione: int = Field(default=0, ge=CATEGORIA_POSIZIONE_MIN, le=CATEGORIA_POSIZIONE_MAX)
    # `code` is never caller-supplied: it is the stable identity of a *seeded*
    # category, and letting a user claim one would let them collide with a future seed.
    # Absent from this schema on purpose, exactly as `code` is absent from
    # `PipelineStageUpdate`.


class CostCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nome: SafeStr | None = Field(default=None, max_length=CATEGORIA_NOME_MAX_LENGTH)
    posizione: int | None = Field(
        default=None, ge=CATEGORIA_POSIZIONE_MIN, le=CATEGORIA_POSIZIONE_MAX
    )


class CostCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    posizione: int
    code: str | None
    archiviata: bool
    created_at: datetime
    updated_at: datetime


class PeriodLockCreate(BaseModel):
    anno: int = Field(ge=ANNO_MIN, le=ANNO_MAX)
    mese: int = Field(ge=1, le=12)


class PeriodLockRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    anno: int
    mese: int
    chiuso_il: datetime
    chiuso_da: UUID | None


class RateDescription(BaseModel):
    """What `log_time` *would* freeze onto a new entry right now.

    Exists so an agent can read before it writes (slice 1 §8.4) without having to
    guess which of the three levels answers -- and so the UI can show it next to the
    hours field instead of leaving the user to discover it after saving.
    """

    deal_id: UUID
    user_id: UUID
    tariffa: Decimal | None
    tariffa_origine: RateOrigin
    costo: Decimal | None
    costo_origine: CostOrigin


class DealTimeSummary(BaseModel):
    """The hours half of a deal's economics, computed in 4A and readable with no
    invoices in the database.

    Deliberately **not** carrying `ricavi` or `valore_maturato`: revenue is the invoice
    (§3, decision 2) and there is no second notion of it. 4B's `DealPnl` adds `ricavi`
    and defines `valore_maturato = ricavi + valore_ore_non_fatturate` on top of this,
    rather than 4A shipping a zero that would read as a real figure.
    """

    deal_id: UUID
    stato: Literal["in corso", "da fatturare", "chiuso"]
    ore_totali: Decimal
    ore_fatturabili_non_fatturate: Decimal
    valore_ore_non_fatturate: Decimal
    costo_lavoro: Decimal
    ore_senza_tariffa: int
    voci: int


class RecalculateRatesRequest(BaseModel):
    da: date
    a: date


class UserRatesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tariffa_oraria_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
    costo_orario_default: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )


class DealRateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tariffa_oraria: Decimal | None = Field(
        default=None, max_digits=FACTOR_MAX_DIGITS, decimal_places=FACTOR_SCALE, ge=0
    )
