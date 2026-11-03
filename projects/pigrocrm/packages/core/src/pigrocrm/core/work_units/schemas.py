"""Every Pydantic shape the day-lifecycle domain exposes -- spec §§5-6."""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.validation import SafeStr

WorkUnitStatus = Literal[
    "proposed",
    "approved",
    "worked",
    "worked_without_approval",
    "invoiced",
    "paid",
    "disputed",
    "revoked",
    "rejected",
    "non_billable",
]
ApprovalChannel = Literal["email", "certified_mail", "registered_mail", "courier", "other"]

QUANTITY_MAX_DIGITS = 6
QUANTITY_DECIMAL_PLACES = 2


class WorkUnitCreate(BaseModel):
    """`status` defaults to `'proposed'`, mastro's own default entry point, and is
    otherwise restricted to `WORK_UNIT_ENTRY_STATUSES` here too -- a friendlier
    `ValidationFailed` ahead of the trigger's own `IntegrityError` for the one case a
    schema can catch without touching the database at all. `approval_id` is accepted
    at creation (the `'work_unit'` proposal-accept path writes a day straight to
    `'approved'` in one transaction, spec §10) but `invoice_line_id` is not: nothing
    creates a day already billed.
    """

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    date: dt.date
    quantity: Decimal = Field(
        gt=0, max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )
    description: SafeStr = Field(min_length=1)
    status: WorkUnitStatus = "proposed"
    approval_id: UUID | None = None
    note: SafeStr | None = None


class WorkUnitRecord(BaseModel):
    """What a person records over REST (REB-745): a day proposed or worked, and nothing the
    ledger decides for itself. Narrower than `WorkUnitCreate` on purpose: `approved` needs an
    approval and is what `POST /api/approvals` writes, and an `approval_id` named by the
    caller could point at another contract's evidence. The proposal-accept path keeps the
    full `WorkUnitCreate`.
    """

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    date: dt.date
    quantity: Decimal = Field(
        gt=0, max_digits=QUANTITY_MAX_DIGITS, decimal_places=QUANTITY_DECIMAL_PLACES
    )
    description: SafeStr = Field(min_length=1)
    status: Literal["proposed", "worked"] = "worked"
    note: SafeStr | None = None


class WorkUnitTransitionIn(BaseModel):
    """What a caller supplies to move a day -- everything else (`previous_status`,
    `actor`, the row itself) is the trigger's own to write."""

    model_config = ConfigDict(extra="forbid")

    status: WorkUnitStatus
    reason: SafeStr = Field(min_length=1)


class WorkUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    date: dt.date
    quantity: Decimal
    description: str
    status: str
    approval_id: UUID | None
    invoice_line_id: UUID | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class WorkUnitTransitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    previous_status: str | None
    new_status: str
    actor: dict[str, Any]
    reason: str
    created_at: datetime
    seq: int


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    channel: ApprovalChannel
    sender: SafeStr = Field(min_length=1)
    received_at: datetime
    message_id: SafeStr | None = None
    document_id: UUID
    excerpt: SafeStr = Field(min_length=1)
    # `{"kind": "manuale"}` / `{"kind": "agente", "proposal_id": ...}` -- spec §6,
    # trimmed to the two kinds this slice needs (`carried_forward` is a
    # renewal-automation feature this spike leaves out).
    origin: dict[str, Any] = Field(default_factory=lambda: {"kind": "manual"})


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    channel: str
    sender: str
    received_at: datetime
    message_id: str | None
    document_id: UUID
    excerpt: str
    origin: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# How many days one list answers. The register is read a month or a contract at a time, so
# this is a ceiling against an unbounded read and not a page size; `WorkUnitList.truncated`
# says when it cut.
LIST_LIMIT_MAX = 500
LIST_LIMIT_DEFAULT = 200


class WorkUnitListQuery(BaseModel):
    """Which days to read. `WorkUnitService.list` refuses a query with none of `contract_id`,
    `da` or `a`: the whole ledger of a space is not a question any screen asks, and an
    unbounded read of it is how a list becomes a slow page. The check lives there and not in
    a validator here because the router builds this model itself, and a `ValidationError`
    raised in a handler body is a 500, not a 422."""

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID | None = None
    legal_entity_id: UUID | None = None
    da: dt.date | None = None
    a: dt.date | None = None
    status: WorkUnitStatus | None = None
    limit: int = Field(default=LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX)


class WorkUnitRow(WorkUnitRead):
    """A day with the labels a list or a calendar cell prints: the contract's title and
    the customer's name, resolved by the same read so a month of days costs one query and
    not one per row. A soft-deleted customer still answers with its name: archiving a
    client did not unhappen the work."""

    contract_title: str
    customer_id: UUID
    customer_name: str


class WorkUnitList(BaseModel):
    items: list[WorkUnitRow]
    # `True` when more days matched than `limit`: the newest `limit` are here.
    truncated: bool = False


class ApprovalDetail(ApprovalRead):
    """An approval with the title of the archived original it interprets, so the day's
    page can name the evidence without a second read per document."""

    title_document: str | None = None


TermViolation = Literal["before_start", "after_end"]


class WorkUnitDetail(WorkUnitRow):
    """One day as its page shows it: the contract it belongs to, the approval it rests
    on, its log in the order it happened, and the moves the state machine allows from
    where it is.

    `actions` is computed here from `WORK_UNIT_TRANSITIONS` so the browser never carries a
    copy of the graph. It leaves out the two edges that are not a person's click: `invoiced`
    is what an invoice does to a day, and `approved` is what recording an approval does.
    The database refuses every edge outside the graph regardless of this list.
    """

    requires_prior_approval: bool
    start_contract: dt.date
    contract_end: dt.date | None
    # The same fact the alert engine reads: a day outside the contract's own term.
    out_of_term: TermViolation | None
    approval: ApprovalDetail | None
    transitions: list[WorkUnitTransitionRead]
    actions: list[WorkUnitStatus]


APPROVAL_DAYS_MAX = 100


class ApprovalRecord(BaseModel):
    """A client's approval as a person records it (REB-747): who sent it, when, the
    sentence that approves, the proof it rests on, and the days it covers.

    The proof is exactly one of an archived document (a file or pasted text uploaded to the
    contract first) or a message already synced from the client's mailbox, which the
    service files as a document of the contract by itself. Both shapes end as the
    `approvals.document_id` the table requires. `work_unit_ids` are the days the approval
    names, and recording moves each of them: a proposed day becomes approved, a day worked
    without approval is recovered to worked. `origin` is always `manual` here: an
    agent's path is `accept_proposal`.
    """

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    channel: ApprovalChannel
    sender: SafeStr = Field(min_length=1)
    received_at: datetime
    excerpt: SafeStr = Field(min_length=1)
    document_id: UUID | None = None
    gmail_message_id: UUID | None = None
    work_unit_ids: list[UUID] = Field(min_length=1, max_length=APPROVAL_DAYS_MAX)

    @model_validator(mode="after")
    def _one_proof(self) -> "ApprovalRecord":
        if (self.document_id is None) == (self.gmail_message_id is None):
            raise ValueError("indica una sola prova: un documento oppure un messaggio di posta")
        if len(set(self.work_unit_ids)) != len(self.work_unit_ids):
            raise ValueError("una giornata e' indicata due volte")
        return self


class ApprovalRecorded(BaseModel):
    """What recording answers: the approval, and each day it covers as it is now."""

    approval: ApprovalDetail
    work_units: list[WorkUnitRead]
