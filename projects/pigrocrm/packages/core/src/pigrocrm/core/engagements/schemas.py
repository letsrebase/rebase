"""What the hub sends through the engagements door and what the door answers (spec
2026-09-25 § 2.3), and the report of a match's hours it reads back (§ 2.4); and the body
and answer of the leads route (spec 2026-10-30 § 2, § 3).

The bounds are the CRM's own rules for a customer (`OrganizationService._check_fiscal`) and
the hub's for a name, applied at the door, so a value the space would refuse is a 422
naming the field, never a customer that half exists.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from pigrocrm.core.contact_points.schemas import MAX_CONTACT_POINTS, ContactPointIn
from pigrocrm.core.deals.schemas import MAX_VALUE_LINES, DealValueLineIn
from pigrocrm.core.notes.schemas import NOTE_MAX_LENGTH
from pigrocrm.core.validation import SafeStr

NAME_MAX_LENGTH = 120  # the hub's NAME_MAX_LENGTH
SPACE_NAME_MAX_LENGTH = 200  # TenantSignup.name
# The deal's name carries the role and the company cut to these, so the longest letter
# (a 20-character number, a 200-character role, a 255-character company) still fits
# `deals.name`'s 255.
ROLE_IN_DEAL_NAME = 80
COMPANY_IN_DEAL_NAME = 100
# A letter's fee is per day; the deal's rate is per hour.
HOURS_PER_DAY = Decimal(8)
# The longest period one report covers, `a - da` in days: the hub walks a longer
# engagement in windows of this many days whose bounds touch.
REPORT_MAX_DAYS = 800


FeeUnit = Literal["day", "hour", "flat"]


class EngagementFreelancer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    first_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    last_name: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)


class EngagementLetter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: SafeStr = Field(min_length=1, max_length=20)
    role: SafeStr = Field(min_length=1, max_length=200)
    company_name: SafeStr = Field(min_length=1, max_length=255)
    start_date: dt.date
    end_date: dt.date | None = None
    # The fee as the letter prints it, per `fee_unit`: a day (what the hub sends today and
    # the default), an hour, or one flat amount for the whole engagement.
    fee: Decimal = Field(gt=0, max_digits=9, decimal_places=2)
    fee_unit: FeeUnit = "day"
    expected_days: int | None = Field(default=None, ge=1, le=366)


class EngagementRebase(BaseModel):
    """rebase's own fiscal data, as the customer «rebase» in the freelancer's space."""

    model_config = ConfigDict(extra="forbid")

    legal_name: SafeStr = Field(min_length=1, max_length=255)
    vat_number: str | None = Field(default=None, pattern=r"^\d{11}$")
    tax_code: SafeStr | None = Field(default=None, max_length=16)
    address: SafeStr | None = Field(default=None, max_length=255)
    certified_email: EmailStr | None = None
    # `SafeStr` like `OrganizationCreate.sdi_code`: a NUL byte is a 422 here, not a
    # driver error in the space.
    sdi_code: SafeStr | None = Field(default=None, min_length=7, max_length=7)


class EngagementUpsert(BaseModel):
    """The body of `PUT /api/rebase/engagements/{match_id}`."""

    model_config = ConfigDict(extra="forbid")

    freelancer: EngagementFreelancer
    letter: EngagementLetter
    rebase: EngagementRebase


class EngagementRead(BaseModel):
    """What the door set up for a match: the space, the customer, the deal and the contract,
    with the two links the hub shows. `created` says whether this call wrote the deal's row (201)
    or found it already there (200); `space_created` whether the space was opened for
    this match, said by the call that writes the row (a retry of a call that stopped on
    the way included) and false on the calls after it."""

    slug: str
    url: str
    customer_id: UUID
    deal_id: UUID
    deal_url: str
    # The contract of the letter (REB-1001); `None` for a match whose space was set up
    # before contracts and keeps its deal and its hours alone.
    contract_id: UUID | None = None
    space_created: bool
    created: bool


class ReportInvoice(BaseModel):
    """An invoice some of the report's hours sit on. `data` is its `issue_date`;
    `hours` only on the report's `invoices` list, the hours of this report on it, and
    `None` on an entry's own `invoice`."""

    id: UUID
    type: str  # "invoice" | "proforma"
    year: int | None
    number: int | None
    status: str
    payment_status: str
    date: dt.date | None
    hours: Decimal | None = None


class ReportEntry(BaseModel):
    """One time entry of the deal: a day with two entries is two rows, and the hub sums."""

    date: dt.date
    hours: Decimal
    description: str
    billable: bool
    invoice: ReportInvoice | None


class ReportDeal(BaseModel):
    """The letter's deal as it stands, `status` as the deal's own page reads it
    (`DealTimeSummary.status`)."""

    id: UUID
    name: str
    hourly_rate: Decimal | None
    budgeted_hours: Decimal | None
    status: str


class ReportWorkUnit(BaseModel):
    """One day of the letter's contract in the freelancer's space (`quantity` is days or
    hours as the rate card's unit says), with the state the freelancer's ledger holds it in.
    `billed` is whether it sits on an invoice line."""

    date: dt.date
    quantity: Decimal
    status: str
    description: str
    billed: bool


class EngagementReport(BaseModel):
    """The answer of `GET /api/rebase/engagements/{match_id}/report`. `billed_hours` is
    the CRM's own billed (`billed_entry_ids`: on a line of an issued `invoice`), so it
    agrees with the deal's summary; `unbilled_hours` is every other hour of the
    period; `invoices` has one row per invoice among the entries, newest first."""

    slug: str
    deal_url: str
    deal: ReportDeal
    days: list[ReportEntry]
    total_hours: Decimal
    billed_hours: Decimal
    unbilled_hours: Decimal
    invoices: list[ReportInvoice]
    # The days recorded as work units on the letter's contract (REB-1002), empty for a space
    # set up before contracts, whose hours are `days`.
    work_units: list[ReportWorkUnit] = []


# The bounds below are the CRM's own: `organizations.legal_name` 255, `people` 120/40/255,
# `deals.name` 255 and the value lines' own bounds. The hub cuts or drops what
# does not fit before sending (spec 2026-10-30 § 3), so a value past them is a 422 naming
# the field, never a request that half exists.
LEAD_COMPANY_MAX_LENGTH = 255
LEAD_PERSON_NAME_MAX_LENGTH = 120
LEAD_PHONE_MAX_LENGTH = 40
LEAD_LINKEDIN_MAX_LENGTH = 255
LEAD_DEAL_NAME_MAX_LENGTH = 255


class LeadContactPerson(BaseModel):
    """The company's contact person, as the wizard collected them. `email` is the identity the
    door matches on (the hub's login address) and `phone` and `linkedin_url` are the primary
    ones, all as before. `contact_points` is every other way to reach them, in the shape of
    `ContactPointIn`: optional, and a point the CRM's rules refuse is left out of what is
    written, never the reason the request is refused."""

    model_config = ConfigDict(extra="forbid")

    first_name: SafeStr = Field(min_length=1, max_length=LEAD_PERSON_NAME_MAX_LENGTH)
    last_name: SafeStr | None = Field(default=None, max_length=LEAD_PERSON_NAME_MAX_LENGTH)
    email: EmailStr
    phone: SafeStr | None = Field(default=None, max_length=LEAD_PHONE_MAX_LENGTH)
    linkedin_url: SafeStr | None = Field(default=None, max_length=LEAD_LINKEDIN_MAX_LENGTH)
    contact_points: list[ContactPointIn] | None = Field(default=None, max_length=MAX_CONTACT_POINTS)


class LeadDeal(BaseModel):
    """The deal as the hub built it, AI draft included. Its value is the list of lines, the
    deals' own (`DealValueLineIn`, so the same limits): the deal's total is derived from
    them and never sent. `hourly_rate` and `budgeted_hours` are deliberately not here: in
    the CRM they value the hours logged on a deal, and a prospect's typed budget is not
    that."""

    model_config = ConfigDict(extra="forbid")

    name: SafeStr = Field(min_length=1, max_length=LEAD_DEAL_NAME_MAX_LENGTH)
    value_lines: list[DealValueLineIn] | None = Field(default=None, max_length=MAX_VALUE_LINES)
    expected_close_date: dt.date | None = None
    # The hub's longest note is about 6,000 characters (its fields are bounded); a sentence
    # is added to it, so the room under the note's own bound is kept.
    notes: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH - 1_000)


class LeadUpsert(BaseModel):
    """The body of `PUT /api/rebase/leads/{request_id}`. `deal_id` is the admin path: it
    links the request to a deal the CRM already has and creates nothing else."""

    model_config = ConfigDict(extra="forbid")

    company: SafeStr = Field(min_length=1, max_length=LEAD_COMPANY_MAX_LENGTH)
    contact: LeadContactPerson
    deal: LeadDeal
    deal_id: UUID | None = None


class LeadRead(BaseModel):
    """What the door filed for a request. `created` says whether this call wrote the deal
    (201) or found it by its marker, or linked an explicit one (200). `person_id` is
    `None` only when a retry finds the deal and nobody with the contact's address any
    more, or an explicit deal whose customer has no such person."""

    customer_id: UUID
    person_id: UUID | None
    deal_id: UUID
    deal_url: str
    created: bool
