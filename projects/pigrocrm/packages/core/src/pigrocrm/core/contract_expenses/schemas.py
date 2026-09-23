"""Every Pydantic shape the rebillable-expenses domain exposes -- spec §7.

`contract_id` is deliberately absent from `ContractExpenseCreate`/`ContractExpenseUpdate`:
it arrives as a path parameter on the nested resource (`/api/contracts/{contract_id}/expenses`,
mirroring `RateCardCreate`'s identical omission) and the service injects it, so a
caller cannot move an expense onto a different contract by editing the body.
`rimborsabile` and `invoice_line_id` are absent from both for the same reason
`WorkUnitCreate` omits `invoice_line_id`: nothing here ever sets a computed or a
billed-onto column directly.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pigrocrm.core.validation import SafeStr

# Mirror the columns in models.py. `Numeric(12, 2)`, the same precision as every
# other money column on this schema.
IMPORTO_MAX_DIGITS = 12
MONEY_SCALE = 2
DESCRIZIONE_MAX_LENGTH = 2000  # `Text`, so no column width to mirror; bounded anyway
RIFERIMENTO_MAX_LENGTH = 2000


class ContractExpenseCreate(BaseModel):
    """`riferimento_autorizzazione` required exactly when `pre_autorizzata` is
    true, forbidden otherwise -- the friendlier pre-database validation ahead of
    `ck_contract_expenses_riferimento_matches_pre_autorizzata`'s own `IntegrityError`,
    the same shape `WorkUnitCreate`'s own entry-state check gives the trigger it sits
    ahead of.
    """

    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    data: date
    importo: Decimal = Field(gt=0, max_digits=IMPORTO_MAX_DIGITS, decimal_places=MONEY_SCALE)
    descrizione: SafeStr = Field(min_length=1, max_length=DESCRIZIONE_MAX_LENGTH)
    pre_autorizzata: bool = False
    riferimento_autorizzazione: SafeStr | None = Field(
        default=None, max_length=RIFERIMENTO_MAX_LENGTH
    )
    document_id: UUID | None = None

    @model_validator(mode="after")
    def _riferimento_matches_pre_autorizzata(self) -> "ContractExpenseCreate":
        if self.pre_autorizzata and not self.riferimento_autorizzazione:
            raise ValueError(
                "riferimento_autorizzazione è obbligatorio quando pre_autorizzata è vero"
            )
        if not self.pre_autorizzata and self.riferimento_autorizzazione:
            raise ValueError(
                "riferimento_autorizzazione va lasciato vuoto quando pre_autorizzata è falso"
            )
        return self


class ContractExpenseUpdate(BaseModel):
    """A partial patch -- `pre_autorizzata`/`riferimento_autorizzazione`'s
    together-ness is checked against the *merged* row by the service
    (`ContractExpenseService.update`), not here, since either field alone may be
    absent from one call without the other changing."""

    model_config = ConfigDict(extra="forbid")

    category_id: UUID | None = None
    data: date | None = None
    importo: Decimal | None = Field(
        default=None, gt=0, max_digits=IMPORTO_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    descrizione: SafeStr | None = Field(
        default=None, min_length=1, max_length=DESCRIZIONE_MAX_LENGTH
    )
    pre_autorizzata: bool | None = None
    riferimento_autorizzazione: SafeStr | None = Field(
        default=None, max_length=RIFERIMENTO_MAX_LENGTH
    )
    document_id: UUID | None = None


class ContractExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    category_id: UUID
    data: date
    importo: Decimal
    descrizione: str
    pre_autorizzata: bool
    riferimento_autorizzazione: str | None
    rimborsabile: bool
    invoice_line_id: UUID | None
    document_id: UUID | None
    created_at: datetime
    updated_at: datetime
