"""Every Pydantic shape of the due items (spec 2026-11-09, due items, § 1).

`DueItemCreate` is the writer's input and not a request body: nothing in this milestone lets
a person type a due item, the services that own a source (invoices, purchases, taxes) write
the item their document implies. What reaches a route or a tool is the settlement, the
reversal and the two reads.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.due_items.models import (
    CURRENCY_PATTERN,
    LATEST_DAY_OF_MONTH,
    TEXT_MAX_LENGTH,
)
from pigrocrm.core.validation import SafeStr

AMOUNT_MAX_DIGITS = 12
MONEY_SCALE = 2
OFFSET_DAYS_MAX = 366

Direction = Literal["in", "out"]
State = Literal["forecast", "expected", "settled", "dropped"]
RuleKind = Literal["fixed", "after_settlement", "month_after_settlement_on_day", "on_settlement"]
CounterpartyType = Literal["organization", "tax_office"]


class DueRule(BaseModel):
    """How an item is dated (§ 1.3). `depends_on` is another item of the same legal entity."""

    model_config = ConfigDict(extra="forbid")

    kind: RuleKind = "fixed"
    depends_on: UUID | None = None
    offset_days: int = Field(default=0, ge=0, le=OFFSET_DAYS_MAX)
    day_of_month: int | None = Field(default=None, ge=1, le=LATEST_DAY_OF_MONTH)
    not_after: dt.date | None = None

    @model_validator(mode="after")
    def _columns_match_the_kind(self) -> "DueRule":
        if self.kind == "fixed":
            if (
                self.depends_on is not None
                or self.offset_days
                or self.day_of_month is not None
                or self.not_after is not None
            ):
                raise ValueError("una scadenza fissa non dipende da nient'altro")
            return self
        if self.depends_on is None:
            # An item that waits for a dependency not recorded yet, and is dated by its
            # backstop meanwhile (collaborators design, « Pairing »).
            if self.kind == "after_settlement" and self.not_after is not None:
                if self.offset_days or self.day_of_month is not None:
                    raise ValueError("una scadenza in attesa ha solo il limite massimo")
                return self
            raise ValueError("questa regola indica la scadenza da cui dipende")
        if (self.kind == "month_after_settlement_on_day") != (self.day_of_month is not None):
            raise ValueError("il giorno del mese serve alla regola del mese dopo, e solo a quella")
        if self.offset_days and self.kind != "after_settlement":
            raise ValueError("i giorni di scarto servono solo alla regola «dopo il pagamento»")
        return self


class DueItemCreate(BaseModel):
    """The writer's input. `own_date` is the date the document states, or the floor of a
    dependent rule; it is not allowed with the two rules that ignore it."""

    model_config = ConfigDict(extra="forbid")

    legal_entity_id: UUID
    direction: Direction
    counterparty_type: CounterpartyType = "organization"
    organization_id: UUID | None = None
    tax: SafeStr | None = Field(default=None, min_length=1, max_length=30)
    amount: Decimal = Field(max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE)
    currency: str = Field(default="EUR", pattern=CURRENCY_PATTERN)
    description: SafeStr | None = Field(default=None, max_length=TEXT_MAX_LENGTH)
    state: Literal["forecast", "expected"] = "expected"
    source_kind: str = Field(min_length=1, max_length=30)
    source_id: UUID | None = None
    source_rule: SafeStr | None = Field(default=None, min_length=1, max_length=60)
    own_date: dt.date | None = None
    rule: DueRule = Field(default_factory=DueRule)

    @model_validator(mode="after")
    def _consistent(self) -> "DueItemCreate":
        if self.amount == 0:
            raise ValueError("l'importo di una scadenza non e' zero")
        if self.counterparty_type == "organization":
            if self.organization_id is None or self.tax is not None:
                raise ValueError(
                    "una scadenza verso un'organizzazione ne indica l'id e nessuna imposta"
                )
        elif self.tax is None or self.organization_id is not None:
            raise ValueError(
                "una scadenza verso l'erario indica l'imposta e nessuna organizzazione"
            )
        if (self.state == "forecast") != (self.source_rule is not None):
            raise ValueError("una previsione indica la regola che l'ha prodotta, e solo lei")
        if self.own_date is not None and self.rule.kind in (
            "month_after_settlement_on_day",
            "on_settlement",
        ):
            raise ValueError("questa regola ignora la data propria: va lasciata vuota")
        return self


class SettlementCreate(BaseModel):
    """One payment against a due item. `amount` is in the item's currency and has the item's
    sign; a payment in another currency says what left the account too."""

    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE)
    settled_on: dt.date
    value_date: dt.date | None = None
    account_amount: Decimal | None = Field(
        default=None, max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    account_currency: str | None = Field(default=None, pattern=CURRENCY_PATTERN)
    note: SafeStr | None = Field(default=None, max_length=TEXT_MAX_LENGTH)

    @model_validator(mode="after")
    def _consistent(self) -> "SettlementCreate":
        if self.amount == 0:
            raise ValueError("un pagamento non e' zero")
        if (self.account_amount is None) != (self.account_currency is None):
            raise ValueError("l'importo sul conto e la sua valuta vanno indicati insieme")
        if self.account_amount is not None and (self.account_amount > 0) != (self.amount > 0):
            raise ValueError("l'importo sul conto ha il segno del pagamento e non e' zero")
        return self


class SettlementReversal(BaseModel):
    """Why a payment is undone, optionally."""

    model_config = ConfigDict(extra="forbid")

    reason: SafeStr | None = Field(default=None, max_length=TEXT_MAX_LENGTH)


class SettlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    due_item_id: UUID
    amount: Decimal
    account_amount: Decimal | None
    account_currency: str | None
    settled_on: dt.date
    value_date: dt.date | None
    note: str | None
    recorded_by_id: UUID | None
    recorded_by_type: str
    created_at: dt.datetime
    reversed_at: dt.datetime | None
    reversed_by_id: UUID | None
    reversed_by_type: str | None
    reversal_reason: str | None


class DueRuleRead(BaseModel):
    kind: RuleKind
    depends_on_id: UUID | None
    offset_days: int
    day_of_month: int | None
    not_after: dt.date | None


class DueItemRead(BaseModel):
    """`amount` is in the document's currency. `settled` is what live settlements add up to
    and `remaining` is what is still owed (zero once settled, and for a dropped item).
    `due_date` is the date the schedule reads; `due_date_estimated` says it follows a
    dependency that is not settled yet."""

    id: UUID
    legal_entity_id: UUID
    direction: Direction
    state: State
    counterparty_type: CounterpartyType
    organization_id: UUID | None
    organization_name: str | None
    tax: str | None
    amount: Decimal
    currency: str
    settled: Decimal
    remaining: Decimal
    description: str | None
    source_kind: str
    source_id: UUID | None
    source_rule: str | None
    own_date: dt.date | None
    due_date: dt.date | None
    due_date_estimated: bool
    rule: DueRuleRead
    dropped_reason: str | None
    created_at: dt.datetime
    updated_at: dt.datetime


class DueItemDetail(DueItemRead):
    """The item with every settlement it ever had, reversed ones included."""

    settlements: list[SettlementRead]


class DueItemListQuery(BaseModel):
    """`state` omitted answers the open items (`forecast` and `expected`); `source_kind`
    names a restricted kind only for a caller who may read it."""

    direction: Direction | None = None
    state: State | None = None
    legal_entity_id: UUID | None = None
    organization_id: UUID | None = None
    source_kind: SafeStr | None = Field(default=None, max_length=30)
    due_from: dt.date | None = None
    due_to: dt.date | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class DueItemPage(BaseModel):
    items: list[DueItemRead]
    next_cursor: UUID | None
