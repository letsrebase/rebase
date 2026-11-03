"""Every Pydantic shape the proposal-review domain exposes -- spec §10.

`proposed_fields`/`accepted_fields` are stored as plain JSONB (`models.py`): the two
shapes below (`ContractProposalFields`, `WorkUnitProposalFields`) are what
`ProposalService` parses that column into and back out of, never a second pair of
database columns. Parsing happens in the service layer, not as a `ProposalCreate`
model validator, for the same reason `contracts/service.py`'s own
`check_payment_terms_together`/`check_renewal_notice` stay plain functions over a
`model_dump()` payload rather than schema validators: the two shapes are a discriminated
union keyed by a sibling field (`target_type`), which a single schema's own
`model_validator` could express, but doing it in the service keeps the translation
from "caller-supplied dict" to "the row this project stores" in one place readers
already know to look (mirrors `_accept_contract`/`_accept_work_unit` themselves,
which are exactly where a human's edited `accepted_fields` gets the identical
re-validation on the way to becoming a `Contract`/`WorkUnit`).
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.contracts.schemas import ContractCreate, RateCardCreate
from pigrocrm.core.validation import SafeStr
from pigrocrm.core.work_units.schemas import (
    QUANTITY_DECIMAL_PLACES,
    QUANTITY_MAX_DIGITS,
    ApprovalChannel,
)

ProposalTargetType = Literal["contract", "work_unit"]
ProposalExcerptType = Literal["quoted", "transcribed"]
ProposalStatus = Literal["pending", "accepted", "rejected"]
ProposalOrder = Literal["arrival", "decision"]

CONFIDENCE_MAX_DIGITS = 3
CONFIDENCE_DECIMAL_PLACES = 2


class ApprovalEvidence(BaseModel):
    """The four facts a `'work_unit'` proposal's own accept path needs to construct
    the `Approval` row (REB-359 §6) alongside the `WorkUnit` -- not part of mastro's
    documented `{contract_id, data, quantita, descrizione}` minimum (spec §10's own
    table), but required by the very next sentence of that same section: "the accept
    path creates the `approval` row... and writes the `work_unit`... both in one
    transaction." An approval needs a channel, a sender and a received-at regardless
    of who asks for it; nesting them here, next to the day they evidence, is where
    that fact belongs -- `Approval.document_id`/`excerpt`/`origin` are filled from
    the proposal's own columns instead (see `ProposalService._accept_work_unit`), so
    nothing here duplicates them.
    """

    model_config = ConfigDict(extra="forbid")

    channel: ApprovalChannel
    sender: SafeStr = Field(min_length=1)
    received_at: datetime
    message_id: SafeStr | None = None


class WorkUnitProposalFields(BaseModel):
    """`proposed_fields`'s shape for `target_type = 'giornata'`: mastro's own minimum
    (spec §10) plus `approval` (see `ApprovalEvidence`)."""

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    date: dt.date
    quantity: Decimal = Field(
        gt=0, max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )
    description: SafeStr = Field(min_length=1)
    approval: ApprovalEvidence


class ContractProposalFields(BaseModel):
    """`proposed_fields`'s shape for `target_type = 'contratto'`: "a candidate
    `contracts` row (plus its first `rate_cards` row)" (spec §10)."""

    model_config = ConfigDict(extra="forbid")

    contract: ContractCreate
    rate_card: RateCardCreate


class ProposalCreate(BaseModel):
    """`proposed_fields` stays a bare `dict` here -- the caller-facing schema takes
    whatever JSON the producer read out of the document, and
    `ProposalService.create` is what parses it against `target_type` into one of the
    two shapes above, turning a bad shape into the same clean `ValidationFailed` every
    other cross-field check in this schema gets."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    # Required by a CHECK unless target_type = 'contratto' -- see `models.py`'s own
    # constraint. Left settable here (rather than always `None`) because the CHECK
    # itself only forbids it being *missing* for `'work_unit'`, never forbids it being
    # *present* for `'contract'` -- `ProposalService.create` is where the tighter,
    # REB-344-scoped rule ("never supplied for `'contract'`, this spike does not
    # model a renewal-targeted proposal") is actually enforced.
    contract_id: UUID | None = None
    target_type: ProposalTargetType
    proposed_fields: dict[str, Any]
    excerpt: SafeStr = Field(min_length=1)
    excerpt_type: ProposalExcerptType = "quoted"
    confidence: Decimal = Field(
        ge=0, le=1, max_digits=CONFIDENCE_MAX_DIGITS, decimal_places=CONFIDENCE_DECIMAL_PLACES
    )
    confidence_reason: SafeStr | None = None


class ProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    contract_id: UUID | None
    target_type: str
    proposed_fields: dict[str, Any]
    excerpt: str
    excerpt_type: str
    confidence: Decimal
    confidence_reason: str | None
    status: str
    accepted_fields: dict[str, Any] | None
    result_id: UUID | None
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProposalListQuery(BaseModel):
    """No cursor pagination (unlike `ContractListQuery`/`DocumentListQuery`'s own
    Residuo R9 shape): a review queue of proposals awaiting a human's decision is
    bounded by how many an agent leaves pending, not by this schema's own history --
    `ix_proposals_status_created_at` is sized for exactly this filter."""

    model_config = ConfigDict(extra="forbid")

    status: ProposalStatus | None = None
    target_type: ProposalTargetType | None = None
    document_id: UUID | None = None
    contract_id: UUID | None = None
    # `arrival` is the review queue's own order (oldest first); `decision` is the decided
    # lists' (most recently decided first, the ones nobody has decided last), so a list
    # cut at `limit` keeps the newest decisions and not the oldest.
    order: ProposalOrder = "arrival"
    limit: int = Field(default=50, ge=1, le=200)


class ProposalPage(BaseModel):
    items: list[ProposalRead]


class ProposalAccept(BaseModel):
    """`accepted_fields` is omitted when the human accepts the proposal exactly as
    read; supplied when they correct a field first. Either way it is what
    `ProposalService.accept` stores on `accepted_fields` and what actually builds the
    `Contract`/`WorkUnit` -- never `proposed_fields` itself, per spec §10's "kept
    separately... never overwritten".

    There is no `decided_by`: who decided is the authenticated actor, read by the service,
    never a field a caller (or an agent steered by an email) gets to write (REB-806)."""

    model_config = ConfigDict(extra="forbid")

    accepted_fields: dict[str, Any] | None = None


class ProposalReject(BaseModel):
    """`reason` has no column of its own on `proposals` (spec §10's table has none):
    it is recorded on the timeline (`ActivityService.record`), exactly as every other
    "why" this schema keeps is -- `decided_by`/`decided_at` are the permanent record of
    who and when."""

    model_config = ConfigDict(extra="forbid")

    reason: SafeStr | None = None
