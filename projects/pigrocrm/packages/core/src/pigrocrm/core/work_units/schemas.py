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
