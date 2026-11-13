"""What the CRM's root door answers the hub about one freelancer (REB-1107, collaborators design
§ 5), as the hub reads it. The CRM's own vocabulary stays at this seam (`framework_agreement`,
`engagement_letter`, `ordinary`); `member_contracts` maps it onto the hub's. `extra="ignore"`: a
field the CRM adds later is not the hub's business until the hub reads it.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CrmWeek(_Answer):
    """One ISO week of a freelancer's availability. `status` is `free`, `committed` or `unknown`
    («sconosciuta»: an engagement with no `days_per_week` touches it, never to be read as free)."""

    week: str
    starts_on: date
    ends_on: date
    status: str
    label: str
    committed_days: Decimal
    free_days: Decimal | None


class CrmAvailability(_Answer):
    """`freelancer_id` is the CRM organization's, `None` for a card the CRM holds nothing about,
    whose every week is free."""

    freelancer_id: UUID | None
    hub_freelancer_id: UUID
    date_from: date
    date_to: date
    weeks: list[CrmWeek]


class CrmDocument(_Answer):
    id: UUID
    kind: str
    letter_type: str
    number: str | None
    state: str
    customer: str | None
    start: str | None
    end: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    has_signed_copy: bool
    filename: str | None
    active: bool
    renewal: date | None
    last_notice_day: date | None
    pigro_url: str | None


class CrmContracts(_Answer):
    documents: list[CrmDocument]


class CrmFiscalIdentifiers(_Answer):
    tax_code: str | None
    vat_number: str | None
    certified_email: str | None


class CrmMergeCheck(_Answer):
    """`framework_regimes` are the CRM's letter types both cards hold a live framework agreement
    of, `shared_clients` how many clients both have a live engagement with."""

    framework_regimes: list[str]
    shared_clients: int
    tax_code_conflict: bool
