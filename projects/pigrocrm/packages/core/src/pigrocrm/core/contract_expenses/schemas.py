"""Every Pydantic shape the rebillable-expenses domain exposes -- spec §7.

`contract_id` is deliberately absent from `ContractExpenseCreate`/`ContractExpenseUpdate`:
it arrives as a path parameter on the nested resource (`/api/contracts/{contract_id}/expenses`,
mirroring `RateCardCreate`'s identical omission) and the service injects it, so a
caller cannot move an expense onto a different contract by editing the body.
`reimbursable` and `invoice_line_id` are absent from both for the same reason
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
AMOUNT_MAX_DIGITS = 12
MONEY_SCALE = 2
DESCRIPTION_MAX_LENGTH = 2000  # `Text`, so no column width to mirror; bounded anyway
REFERENCE_MAX_LENGTH = 2000


class ContractExpenseCreate(BaseModel):
    """`authorization_reference` required exactly when `pre_authorized` is
    true, forbidden otherwise -- the friendlier pre-database validation ahead of
    `ck_contract_expenses_reference_matches_pre_authorized`'s own `IntegrityError`,
    the same shape `WorkUnitCreate`'s own entry-state check gives the trigger it sits
    ahead of.
    """

    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    data: date
    amount: Decimal = Field(gt=0, max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE)
    description: SafeStr = Field(min_length=1, max_length=DESCRIPTION_MAX_LENGTH)
    pre_authorized: bool = False
    authorization_reference: SafeStr | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    document_id: UUID | None = None

    @model_validator(mode="after")
    def _reference_matches_pre_authorized(self) -> "ContractExpenseCreate":
        if self.pre_authorized and not self.authorization_reference:
            raise ValueError(
                "riferimento_autorizzazione è obbligatorio quando pre_autorizzata è vero"
            )
        if not self.pre_authorized and self.authorization_reference:
            raise ValueError(
                "riferimento_autorizzazione va lasciato vuoto quando pre_autorizzata è falso"
            )
        return self


class ContractExpenseUpdate(BaseModel):
    """A partial patch -- `pre_authorized`/`authorization_reference`'s
    together-ness is checked against the *merged* row by the service
    (`ContractExpenseService.update`), not here, since either field alone may be
    absent from one call without the other changing."""

    model_config = ConfigDict(extra="forbid")

    category_id: UUID | None = None
    data: date | None = None
    amount: Decimal | None = Field(
        default=None, gt=0, max_digits=AMOUNT_MAX_DIGITS, decimal_places=MONEY_SCALE
    )
    description: SafeStr | None = Field(
        default=None, min_length=1, max_length=DESCRIPTION_MAX_LENGTH
    )
    pre_authorized: bool | None = None
    authorization_reference: SafeStr | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    document_id: UUID | None = None


class ContractExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    contract_id: UUID
    category_id: UUID
    data: date
    amount: Decimal
    description: str
    pre_authorized: bool
    authorization_reference: str | None
    reimbursable: bool
    invoice_line_id: UUID | None
    document_id: UUID | None
    created_at: datetime
    updated_at: datetime
