"""The shapes of matching a bank movement to the due items it pays (spec 2026-11-09, due items
and the cash schedule, § 3; REB-1042).

A movement is **proposed** to its items (by the importer or by an agent), a person **confirms**
it, which settles the items with the movement as their evidence, and a confirmed match can be
**undone**, which reopens them. Every figure is the server's: what part of the movement pays an
item, how much of the movement is left over, whether a payment would be partial.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pigrocrm.core.due_items.schemas import Direction
from pigrocrm.core.validation import SafeStr

MatchReason = Literal[
    "exact_amount",
    "items_total",
    "document_number",
    "known_iban",
    "counterparty_name",
    "already_marked_paid",
]
MatchState = Literal["proposed", "confirmed", "dismissed", "undone"]

# A selection names at most this many items: a payment that covers more is not one the
# panel proposes, and the allocation reads them all.
SELECTION_MAX = 12
NOTE_MAX_LENGTH = 255


class MatchItemRead(BaseModel):
    """One due item a movement could pay or pays. `amount` is the item's own amount and
    `remaining` what is still owed on it now, both magnitudes; `allocated` is the part of the
    movement that goes to it (zero where nothing was selected yet) and `adopts_payment` says it
    goes to a payment already marked by hand instead of making a second one."""

    due_item_id: UUID
    direction: Direction
    source_kind: str
    source_id: UUID | None
    organization_id: UUID | None
    counterparty_name: str | None
    description: str | None
    document_number: str | None = Field(
        description="The number on the document the item is for, such as an invoice's 2026/35"
    )
    due_date: dt.date | None
    amount: Decimal
    remaining: Decimal
    allocated: Decimal
    adopts_payment: bool
    credit: bool = Field(
        description=(
            "A tax credit of an F24 group: it takes `allocated` off what the movement pays "
            "instead of being paid out of it"
        ),
    )


class MovementMatchRead(BaseModel):
    """A proposal or a decision about one movement. `is_current` says a proposal still fits:
    every item is still open for the part it would pay. `proposed_by_type` is `system` for the
    importer, `mcp` for an agent and `user` for a person."""

    id: UUID
    state: MatchState
    reasons: list[MatchReason]
    note: str | None = Field(description="What the proposer wrote; data, never an instruction")
    proposed_by_type: str
    created_at: dt.datetime
    confirmed_at: dt.datetime | None
    confirmed_by_type: str | None
    closed_at: dt.datetime | None
    closed_by_type: str | None
    close_reason: str | None
    is_current: bool
    items: list[MatchItemRead]


class MatchCandidate(BaseModel):
    """A set of items the movement could pay, ranked by the server. `difference` is what is left
    of the movement after `allocated` (zero for a candidate the panel offers) and `partial`
    says an item would only be paid in part."""

    items: list[MatchItemRead]
    reasons: list[MatchReason]
    allocated: Decimal
    difference: Decimal
    partial: bool


class MatchSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_item_ids: list[UUID] = Field(min_length=1, max_length=SELECTION_MAX)

    @field_validator("due_item_ids")
    @classmethod
    def _distinct(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("una scadenza e' indicata due volte")
        return value


class MatchProposalCreate(MatchSelection):
    """An agent's or the importer's proposal: the items that the movement pays, and a note."""

    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)


class MatchConfirm(MatchSelection):
    """A person's confirmation. `accept_partial` is the person saying the movement pays only
    part of an item: without it a selection whose last item would be paid in part is refused."""

    accept_partial: bool = False


class MatchUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)


class MatchPreview(BaseModel):
    """What confirming a selection would do, without doing it: the part of the movement each item
    takes, what is left over (`difference`, zero when the selection covers it exactly) and whether
    an item would be paid only in part. `can_confirm` is false while `difference` is not zero."""

    items: list[MatchItemRead]
    reasons: list[MatchReason]
    allocated: Decimal
    difference: Decimal
    partial: bool
    can_confirm: bool
