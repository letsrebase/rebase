"""Every Pydantic shape the proposal-review domain exposes -- spec §10.

`campi_proposti`/`campi_accettati` are stored as plain JSONB (`models.py`): the two
shapes below (`ContrattoProposalFields`, `GiornataProposalFields`) are what
`ProposalService` parses that column into and back out of, never a second pair of
database columns. Parsing happens in the service layer, not as a `ProposalCreate`
model validator, for the same reason `contracts/service.py`'s own
`check_payment_terms_together`/`check_renewal_notice` stay plain functions over a
`model_dump()` payload rather than schema validators: the two shapes are a discriminated
union keyed by a sibling field (`target_type`), which a single schema's own
`model_validator` could express, but doing it in the service keeps the translation
from "caller-supplied dict" to "the row this project stores" in one place readers
already know to look (mirrors `_accept_contratto`/`_accept_giornata` themselves,
which are exactly where a human's edited `campi_accettati` gets the identical
re-validation on the way to becoming a `Contract`/`WorkUnit`).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.contracts.schemas import ContractCreate, RateCardCreate
from pigrocrm.core.validation import SafeStr
from pigrocrm.core.work_units.schemas import (
    QUANTITA_DECIMAL_PLACES,
    QUANTITA_MAX_DIGITS,
    ApprovalCanale,
)

ProposalTargetType = Literal["contratto", "giornata"]
ProposalTipoEstratto = Literal["citato", "trascritto"]
ProposalStato = Literal["in_attesa", "accettata", "rifiutata"]

CONFIDENZA_MAX_DIGITS = 3
CONFIDENZA_DECIMAL_PLACES = 2


class ApprovalEvidence(BaseModel):
    """The four facts a `'giornata'` proposal's own accept path needs to construct
    the `Approval` row (REB-359 §6) alongside the `WorkUnit` -- not part of mastro's
    documented `{contract_id, data, quantita, descrizione}` minimum (spec §10's own
    table), but required by the very next sentence of that same section: "the accept
    path creates the `approval` row... and writes the `work_unit`... both in one
    transaction." An approval needs a channel, a sender and a received-at regardless
    of who asks for it; nesting them here, next to the day they evidence, is where
    that fact belongs -- `Approval.document_id`/`estratto`/`origine` are filled from
    the proposal's own columns instead (see `ProposalService._accept_giornata`), so
    nothing here duplicates them.
    """

    model_config = ConfigDict(extra="forbid")

    canale: ApprovalCanale
    mittente: SafeStr = Field(min_length=1)
    ricevuto_il: datetime
    message_id: SafeStr | None = None


class GiornataProposalFields(BaseModel):
    """`campi_proposti`'s shape for `target_type = 'giornata'`: mastro's own minimum
    (spec §10) plus `approvazione` (see `ApprovalEvidence`)."""

    model_config = ConfigDict(extra="forbid")

    contract_id: UUID
    data: date
    quantita: Decimal = Field(
        gt=0, max_digits=QUANTITA_MAX_DIGITS, decimal_places=QUANTITA_DECIMAL_PLACES
    )
    descrizione: SafeStr = Field(min_length=1)
    approvazione: ApprovalEvidence


class ContrattoProposalFields(BaseModel):
    """`campi_proposti`'s shape for `target_type = 'contratto'`: "a candidate
    `contracts` row (plus its first `rate_cards` row)" (spec §10)."""

    model_config = ConfigDict(extra="forbid")

    contract: ContractCreate
    rate_card: RateCardCreate


class ProposalCreate(BaseModel):
    """`campi_proposti` stays a bare `dict` here -- the caller-facing schema takes
    whatever JSON the producer read out of the document, and
    `ProposalService.create` is what parses it against `target_type` into one of the
    two shapes above, turning a bad shape into the same clean `ValidationFailed` every
    other cross-field check in this schema gets."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    # Required by a CHECK unless target_type = 'contratto' -- see `models.py`'s own
    # constraint. Left settable here (rather than always `None`) because the CHECK
    # itself only forbids it being *missing* for `'giornata'`, never forbids it being
    # *present* for `'contratto'` -- `ProposalService.create` is where the tighter,
    # REB-344-scoped rule ("never supplied for `'contratto'`, this spike does not
    # model a renewal-targeted proposal") is actually enforced.
    contract_id: UUID | None = None
    target_type: ProposalTargetType
    campi_proposti: dict[str, Any]
    estratto: SafeStr = Field(min_length=1)
    tipo_estratto: ProposalTipoEstratto = "citato"
    confidenza: Decimal = Field(
        ge=0, le=1, max_digits=CONFIDENZA_MAX_DIGITS, decimal_places=CONFIDENZA_DECIMAL_PLACES
    )
    motivo_confidenza: SafeStr | None = None


class ProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    contract_id: UUID | None
    target_type: str
    campi_proposti: dict[str, Any]
    estratto: str
    tipo_estratto: str
    confidenza: Decimal
    motivo_confidenza: str | None
    stato: str
    campi_accettati: dict[str, Any] | None
    id_risultato: UUID | None
    deciso_da: str | None
    deciso_il: datetime | None
    created_at: datetime
    updated_at: datetime


class ProposalListQuery(BaseModel):
    """No cursor pagination (unlike `ContractListQuery`/`DocumentListQuery`'s own
    Residuo R9 shape): a review queue of proposals awaiting a human's decision is
    bounded by how many an agent leaves pending, not by this schema's own history --
    `ix_proposals_stato_created_at` is sized for exactly this filter."""

    model_config = ConfigDict(extra="forbid")

    stato: ProposalStato | None = None
    target_type: ProposalTargetType | None = None
    document_id: UUID | None = None
    contract_id: UUID | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ProposalPage(BaseModel):
    items: list[ProposalRead]


class ProposalAccept(BaseModel):
    """`campi_accettati` is omitted when the human accepts the proposal exactly as
    read; supplied when they correct a field first. Either way it is what
    `ProposalService.accept` stores on `campi_accettati` and what actually builds the
    `Contract`/`WorkUnit` -- never `campi_proposti` itself, per spec §10's "kept
    separately... never overwritten"."""

    model_config = ConfigDict(extra="forbid")

    deciso_da: SafeStr = Field(min_length=1)
    campi_accettati: dict[str, Any] | None = None


class ProposalReject(BaseModel):
    """`motivo` has no column of its own on `proposals` (spec §10's table has none):
    it is recorded on the timeline (`ActivityService.record`), exactly as every other
    "why" this schema keeps is -- `deciso_da`/`deciso_il` are the permanent record of
    who and when."""

    model_config = ConfigDict(extra="forbid")

    deciso_da: SafeStr = Field(min_length=1)
    motivo: SafeStr | None = None
