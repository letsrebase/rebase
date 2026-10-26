"""Every Pydantic shape the day-lifecycle domain exposes -- spec §§5-6."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

WorkUnitStato = Literal[
    "proposto",
    "approvato",
    "lavorato",
    "lavorato_senza_approvazione",
    "fatturato",
    "pagato",
    "contestato",
    "revocato",
    "rifiutato",
    "non_fatturabile",
]
ApprovalCanale = Literal["email", "posta_certificata", "raccomandata", "corriere", "altro"]

QUANTITA_MAX_DIGITS = 6
QUANTITA_DECIMAL_PLACES = 2


class WorkUnitCreate(BaseModel):
    """`stato` defaults to `'proposto'`, mastro's own default entry point, and is
    otherwise restricted to `WORK_UNIT_ENTRY_STATI` here too -- a friendlier
    `ValidationFailed` ahead of the trigger's own `IntegrityError` for the one case a
    schema can catch without touching the database at all. `approval_id` is accepted
    at creation (the `'giornata'` proposal-accept path writes a day straight to
    `'approvato'` in one transaction, spec §10) but `invoice_line_id` is not: nothing
    creates a day already billed.
    """

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    data: date
    quantita: Decimal = Field(
        gt=0, max_digits=QUANTITA_MAX_DIGITS, decimal_places=QUANTITA_DECIMAL_PLACES
    )
    descrizione: SafeStr = Field(min_length=1)
    stato: WorkUnitStato = "proposto"
    approval_id: UUID | None = None
    note: SafeStr | None = None


class WorkUnitTransitionIn(BaseModel):
    """What a caller supplies to move a day -- everything else (`stato_precedente`,
    `attore`, the row itself) is the trigger's own to write."""

    model_config = ConfigDict(extra="forbid")

    stato: WorkUnitStato
    motivo: SafeStr = Field(min_length=1)


class WorkUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    data: date
    quantita: Decimal
    descrizione: str
    stato: str
    approval_id: UUID | None
    invoice_line_id: UUID | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class WorkUnitTransitionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_unit_id: UUID
    stato_precedente: str | None
    stato_nuovo: str
    attore: dict[str, Any]
    motivo: str
    created_at: datetime
    seq: int


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    canale: ApprovalCanale
    mittente: SafeStr = Field(min_length=1)
    ricevuto_il: datetime
    message_id: SafeStr | None = None
    document_id: UUID
    estratto: SafeStr = Field(min_length=1)
    # `{"kind": "manuale"}` / `{"kind": "agente", "proposal_id": ...}` -- spec §6,
    # trimmed to the two kinds this slice needs (`carried_forward` is a
    # renewal-automation feature this spike leaves out).
    origine: dict[str, Any] = Field(default_factory=lambda: {"kind": "manuale"})


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    canale: str
    mittente: str
    ricevuto_il: datetime
    message_id: str | None
    document_id: UUID
    estratto: str
    origine: dict[str, Any]
    created_at: datetime
    updated_at: datetime


# How many days one list answers. The register is read a month or a contract at a time, so
# this is a ceiling against an unbounded read and not a page size; `WorkUnitList.troncato`
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
    azienda_id: UUID | None = None
    da: date | None = None
    a: date | None = None
    stato: WorkUnitStato | None = None
    limit: int = Field(default=LIST_LIMIT_DEFAULT, ge=1, le=LIST_LIMIT_MAX)


class WorkUnitRow(WorkUnitRead):
    """A day with the labels a list or a calendar cell prints: the contract's title and
    the customer's name, resolved by the same read so a month of days costs one query and
    not one per row. A soft-deleted customer still answers with its name: archiving a
    client did not unhappen the work."""

    contract_titolo: str
    customer_id: UUID
    customer_nome: str


class WorkUnitList(BaseModel):
    items: list[WorkUnitRow]
    # `True` when more days matched than `limit`: the newest `limit` are here.
    troncato: bool = False


class ApprovalDetail(ApprovalRead):
    """An approval with the title of the archived original it interprets, so the day's
    page can name the evidence without a second read per document."""

    documento_titolo: str | None = None


TermViolation = Literal["prima_dell_inizio", "dopo_la_fine"]


class WorkUnitDetail(WorkUnitRow):
    """One day as its page shows it: the contract it belongs to, the approval it rests
    on, its log in the order it happened, and the moves the state machine allows from
    where it is.

    `azioni` is computed here from `WORK_UNIT_TRANSITIONS` so the browser never carries a
    copy of the graph. It leaves out the two edges that are not a person's click: `fatturato`
    is what an invoice does to a day, and `approvato` is what recording an approval does.
    The database refuses every edge outside the graph regardless of this list.
    """

    requires_prior_approval: bool
    contratto_inizio: date
    contratto_fine: date | None
    # The same fact the alert engine reads: a day outside the contract's own term.
    fuori_termine: TermViolation | None
    approval: ApprovalDetail | None
    transizioni: list[WorkUnitTransitionRead]
    azioni: list[WorkUnitStato]
