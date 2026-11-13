"""What the collaborators module reads and writes (design § 1 and § 3).

The values that come from a closed set (`state`, `fee_unit`, `letter_type`, `payment_rule`) are
plain strings here and checked by the service, which answers in Italian and names the field,
where a `Literal` would answer with pydantic's own error shape.
"""

import datetime as dt
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.collaborators.models import OBJECTION_MAX_LENGTH, ROLE_MAX_LENGTH
from pigrocrm.core.documents.schemas import DocumentRead
from pigrocrm.core.validation import SafeStr

KIND_MAX_LENGTH = 20
# The columns are Numeric(12, 2) and Numeric(8, 2): a longer number is refused before the database.
FEE_MAX_DIGITS = 12
EXPECTED_DAYS_MAX_DIGITS = 8

WarningCode = Literal["overlap", "over_capacity"]
AvailabilityStatus = Literal["free", "committed", "unknown"]

# The longest window one availability read covers: two years of Mondays.
AVAILABILITY_MAX_WEEKS = 104
AVAILABILITY_DEFAULT_WEEKS = 12


class EngagementCreate(BaseModel):
    """A new engagement is a draft. `days_per_week` is required (the service says so in words);
    only an imported one may lack it. `letter_type` omitted follows the freelancer's fiscal kind:
    `occasional` for an occasional freelancer, `ordinary` otherwise."""

    model_config = ConfigDict(extra="forbid")

    freelancer_id: UUID
    hub_match_id: UUID | None = None
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    role: SafeStr = Field(max_length=ROLE_MAX_LENGTH)
    starts_on: dt.date
    ends_on: dt.date | None = None
    days_per_week: Decimal | None = Field(
        default=None, description="From 0.5 to 5 days, in steps of 0.5; required"
    )
    fee: Decimal = Field(max_digits=FEE_MAX_DIGITS)
    fee_unit: SafeStr = Field(max_length=KIND_MAX_LENGTH, description="day | hour | flat")
    expected_days: Decimal | None = Field(
        default=None,
        max_digits=EXPECTED_DAYS_MAX_DIGITS,
        description="For an hourly fee, the hours divided by 8",
    )
    letter_type: SafeStr | None = Field(
        default=None, max_length=KIND_MAX_LENGTH, description="ordinary | occasional"
    )
    payment_rule: SafeStr = Field(
        max_length=KIND_MAX_LENGTH,
        description="fixed_term (payment_days from the invoice) | on_collection (collection_days)",
    )
    payment_days: int | None = None
    payment_end_of_month: bool = False
    collection_days: int | None = None


class EngagementUpdate(BaseModel):
    """A draft can change any of these. Once it is out for signature or signed only
    `days_per_week` and the client, deal and contract links can change: they are what an
    imported engagement lacks, and none of them is printed on the letter."""

    model_config = ConfigDict(extra="forbid")

    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    role: SafeStr | None = Field(default=None, max_length=ROLE_MAX_LENGTH)
    starts_on: dt.date | None = None
    ends_on: dt.date | None = None
    days_per_week: Decimal | None = None
    fee: Decimal | None = Field(default=None, max_digits=FEE_MAX_DIGITS)
    fee_unit: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    expected_days: Decimal | None = Field(default=None, max_digits=EXPECTED_DAYS_MAX_DIGITS)
    letter_type: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    payment_rule: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    payment_days: int | None = None
    payment_end_of_month: bool | None = None
    collection_days: int | None = None


class EngagementWarning(BaseModel):
    """Something to know, not a refusal: the engagement is saved either way."""

    code: WarningCode
    message: str
    engagement_ids: list[UUID]
    # The most days a week the freelancer is committed to in the overlap, this engagement
    # included, or `None` when one of the others has no `days_per_week`.
    peak_days_per_week: Decimal | None


WaitingCode = Literal[
    "letter_not_created",
    "letter_not_sent",
    "report_undecided",
    "missing_days_per_week",
    "missing_client",
]
EngagementBucket = Literal["todo", "ongoing", "closed"]


class EngagementWaiting(BaseModel):
    """One thing the engagement waits for a person to do. A draft waits until its letter is
    written (`letter_not_created`) and sent (`letter_not_sent`); a live one waits for the
    `days_per_week` or the client link an import left out; an active or concluded one waits for
    a person to decide a month received and not yet decided (`report_undecided`, with the month
    and the last day an objection counts)."""

    code: WaitingCode
    month: dt.date | None = None
    tacit_on: dt.date | None = None


class EngagementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hub_match_id: UUID | None
    freelancer_id: UUID
    client_organization_id: UUID | None
    deal_id: UUID | None
    contract_id: UUID | None
    # `role`, `fee` and the client links are `None` (and `redacted` is true) for an administrator
    # scoped to legal entities that cannot see the engagement's client: they are the client's
    # business, and the freelancer is shared.
    redacted: bool = False
    role: str | None
    starts_on: dt.date
    ends_on: dt.date | None
    days_per_week: Decimal | None
    fee: Decimal | None
    fee_unit: str
    expected_days: Decimal | None
    letter_type: str
    payment_rule: str
    payment_days: int | None
    payment_end_of_month: bool
    collection_days: int | None
    state: str
    # `YYYY-NNN`, drawn when the engagement was created; null on one written before letters moved
    # here.
    letter_number: str | None
    # What an admin still has to fill in: `days_per_week` and `client`.
    missing: list[str]
    # Filled on a create, an update and a get of a live engagement.
    warnings: list[EngagementWarning] = Field(default_factory=list)
    # What waits for a person, and the names the list draws. `client_name` is `None` with no
    # client and when `redacted`; `letter_document_id` is the stored letter, once it is written.
    waiting: list[EngagementWaiting] = Field(default_factory=list)
    freelancer_name: str | None = None
    client_name: str | None = None
    letter_document_id: UUID | None = None
    # The CRM's day, which decides what is late, what has ended and whether a start is still ahead:
    # a screen never asks the browser's clock.
    today: dt.date | None = None
    created_at: datetime
    updated_at: datetime


class EngagementListQuery(BaseModel):
    freelancer_id: UUID | None = None
    hub_match_id: UUID | None = None
    state: SafeStr | None = Field(default=None, max_length=KIND_MAX_LENGTH)
    client_organization_id: UUID | None = None
    deal_id: UUID | None = None
    contract_id: UUID | None = None
    bucket: EngagementBucket | None = Field(
        default=None,
        description=(
            "todo: waits for a person; ongoing: out for signature or active with nothing "
            "waiting; closed: concluded or cancelled with nothing waiting"
        ),
    )
    limit: int = Field(default=50, ge=1, le=200)
    cursor: UUID | None = None


class EngagementPage(BaseModel):
    items: list[EngagementRead]
    next_cursor: UUID | None


class EngagementCounts(BaseModel):
    """How many engagements each bucket of the list holds; `todo` is the count on the entry."""

    todo: int
    ongoing: int
    closed: int


class EngagementClose(BaseModel):
    """Closing an engagement ends it on `ended_on` (today when omitted): an open-ended or
    longer engagement stops there, so the weeks after no longer count as committed."""

    model_config = ConfigDict(extra="forbid")

    ended_on: dt.date | None = None


class EngagementStateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: SafeStr = Field(max_length=KIND_MAX_LENGTH)
    ended_on: dt.date | None = None


class AvailabilityWeek(BaseModel):
    """One ISO week. `unknown` when a live engagement (draft, out for signature or active)
    overlapping it has no `days_per_week`: never `free`. `committed_days` then counts only the
    ones that have it."""

    week: str = Field(description="ISO week, 2026-W46")
    starts_on: dt.date
    ends_on: dt.date
    status: AvailabilityStatus
    label: str
    committed_days: Decimal
    free_days: Decimal | None
    engagement_ids: list[UUID]


class FreelancerAvailability(BaseModel):
    freelancer_id: UUID
    hub_freelancer_id: UUID | None
    date_from: dt.date
    date_to: dt.date
    weeks: list[AvailabilityWeek]


class FreelancerTaxData(BaseModel):
    """The tax data the contracts print, and the fiscal kind (design § 1). Every field omitted
    is left as it is."""

    model_config = ConfigDict(extra="forbid")

    tax_code: SafeStr | None = None
    vat_number: SafeStr | None = None
    domicile: SafeStr | None = Field(default=None, description="Street address")
    certified_email: SafeStr | None = None
    fiscal_kind: SafeStr | None = Field(
        default=None,
        max_length=KIND_MAX_LENGTH,
        description="professional | flat_rate | occasional",
    )


LETTER_VARIABLE_MAX_LENGTH = 4_000
LETTER_VARIABLES_MAX = 60


class LetterRequest(BaseModel):
    """The fields an administrator writes for one letter: the text's own free-text fields
    (`attivita`, `risultati`, `luogo`, ...), by the name the template declares them under. What the
    engagement, the freelancer, the client and the legal entity already say is never typed here
    and cannot be overridden."""

    model_config = ConfigDict(extra="forbid")

    variables: dict[str, SafeStr | None] = Field(
        default_factory=dict,
        max_length=LETTER_VARIABLES_MAX,
        description="Free-text fields of the template, by name; a field left out prints as a blank",
    )


class LetterPreview(BaseModel):
    """A letter rendered and not stored: what an administrator checks before it exists as a
    document. `blank` lists the fields the page prints as blank lines (the signatures among
    them), `draft` says the text itself is still a draft and could not be sent."""

    kind: str = Field(description="framework_agreement | engagement_letter")
    letter_type: str
    template_id: UUID
    text_version: str
    draft: bool
    number: str | None
    blank: list[str]
    markdown: str
    pdf: bytes = Field(exclude=True, repr=False)


class LetterRead(BaseModel):
    """A letter as stored: the document, which regime and version of the text wrote it, its
    number and the fields still blank."""

    document: DocumentRead
    engagement_id: UUID | None
    letter_type: str
    text_version: str
    number: str | None
    blank: list[str]


class FrameworkAgreementRequest(BaseModel):
    """Which regime a framework agreement is written for. Omitted, it follows the freelancer's
    fiscal kind: `occasional` for an occasional freelancer, `ordinary` otherwise."""

    model_config = ConfigDict(extra="forbid")

    letter_type: SafeStr | None = Field(
        default=None, max_length=KIND_MAX_LENGTH, description="ordinary | occasional"
    )


# ---- the live report and the monthly «Rendiconto» (design § 3 and § 4) -------------------------

MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# `requested`: received, nobody decided and the tacit date has not passed. `approved`: a person
# decided it. `tacit`: no decision and the tacit date passed with no objection, which counts as
# approved (framework 6.2) without any row having been written.
MonthlyReportState = Literal["requested", "approved", "tacit"]
ReportDaySource = Literal["work_unit", "time_entry"]
ReportUnit = Literal["day", "hour"]


class MonthlyReportRequest(BaseModel):
    """Records that rebase received the freelancer's report of `month`. `received_on` is the day
    it arrived (default today): the tacit date counts seven working days from it."""

    model_config = ConfigDict(extra="forbid")

    month: str = Field(pattern=MONTH_PATTERN, description="The month the report covers, 2026-10")
    received_on: dt.date | None = None


class MonthlyReportApproval(BaseModel):
    """A person's decision on a month. `held_dates` are the days the client contested: they
    are held and the rest is payable. Holding a day needs the reason in `objection`, and is not
    possible once the tacit date has passed with no objection."""

    model_config = ConfigDict(extra="forbid")

    held_dates: list[dt.date] = Field(default_factory=list, max_length=31)
    objection: SafeStr | None = Field(default=None, max_length=OBJECTION_MAX_LENGTH)


class MonthlyReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    month: dt.date
    label: str = Field(description="The month, 2026-10")
    state: str = Field(description="requested | approved, as stored")
    effective_state: MonthlyReportState
    counts_as_approved: bool
    received_on: dt.date
    tacit_on: dt.date
    decided_on: dt.date | None
    decided_by_name: str | None = Field(
        default=None, description="The person who decided it, by name"
    )
    objection: str | None
    held_dates: list[dt.date]
    created_at: datetime
    updated_at: datetime


class MonthlyReportPage(BaseModel):
    items: list[MonthlyReportRead]


class ReportDay(BaseModel):
    """One day (or one entry of hours) of the freelancer's report. `quantity` is in the report's
    unit. `counts` says the freelancer's ledger holds it as work done (a proposed or disputed day
    does not), `in_term` that it falls within the engagement's dates; `payable` is both, in a
    month whose «Rendiconto» counts as approved, and not held."""

    date: dt.date
    quantity: Decimal
    source: ReportDaySource
    status: str | None
    description: str
    counts: bool
    in_term: bool
    held: bool
    payable: bool


class ReportMonth(BaseModel):
    month: dt.date
    label: str
    monthly_report: MonthlyReportRead | None
    quantity: Decimal = Field(description="Everything that counts and is in term, in `unit`")
    held_quantity: Decimal
    payable_quantity: Decimal
    payable_amount: Decimal | None = Field(
        description="`payable_quantity` times the fee; none for a flat fee"
    )


class EngagementDaysReport(BaseModel):
    """The engagement's days read live from the freelancer's space, with what is payable.
    `unit` is `hour` for an hourly letter, which pays approved hours, and `day` otherwise (a
    time entry's hours are days at 8 a day). `available` is false until the freelancer's space
    has been set up for the engagement; `note` then says why."""

    engagement_id: UUID
    unit: ReportUnit
    fee_unit: str
    date_from: dt.date
    date_to: dt.date
    available: bool
    note: str | None
    days: list[ReportDay]
    months: list[ReportMonth]
    total_quantity: Decimal
    payable_quantity: Decimal
    held_quantity: Decimal
    payable_amount: Decimal | None


# ---- signature (design § 2, REB-1019) -------------------------------------------------------

SignatureState = Literal["generated", "pending", "sent", "signed", "cancelled", "notice_served"]


class SignatureRead(BaseModel):
    """Where a framework agreement or a letter stands once it has left: its state, when it went
    out and was signed, why it was cancelled, and whether the signed PDF is stored. A framework
    agreement says whether it is active and the next renewal and last day for a notice (the twelve
    months of article 9.1); the signing link is a capability and is never in a read."""

    document_id: UUID
    kind: str = Field(description="framework_agreement | engagement_letter")
    letter_type: str | None
    engagement_id: UUID | None
    number: str | None
    text_version: str | None
    state: SignatureState
    sent_at: datetime | None
    signed_at: datetime | None
    notice_at: datetime | None
    cancel_reason: str | None
    has_signed_copy: bool
    signed_by_hand: bool = Field(
        description="The signed PDF was uploaded, not sealed by the signing site"
    )
    active: bool = Field(description="A framework agreement that is signed and has no notice")
    renewal: dt.date | None
    last_notice_day: dt.date | None


class SendReport(BaseModel):
    """What «send for signature» did. `sent` is the kind of the document that left (the framework
    agreement first when the freelancer has none active, else the letter) and `None` when the
    letter waits for an agreement already out. `mail_sent` says the link reached the mail
    provider: `False` leaves the document `sent` for «resend the mail»."""

    engagement: EngagementRead
    sent: str | None
    mail_sent: bool | None
    documents: list[SignatureRead]


class SignedCopyRequest(BaseModel):
    """The day the document was signed, for a signed PDF uploaded by hand (default today)."""

    model_config = ConfigDict(extra="forbid")

    signed_on: dt.date | None = None


# ---- what the engagement pays the freelancer, month by month (REB-1035) -----------------------

PaymentState = Literal["forecast", "expected", "settled"]


class EngagementPaymentRead(BaseModel):
    """One due item the engagement wrote for the freelancer. `month` is the first day of the month
    the amount pays for, known only for a forecast (the billing point it was written for); an item
    that replaced one has none. `waiting_for_invoice` is the client's invoice, `2026/71`, whose
    settlement the date still follows. The amounts are `None` when the engagement's client is not
    in this administrator's view, as the fee is."""

    due_item_id: UUID
    month: dt.date | None
    state: PaymentState
    amount: Decimal | None
    settled: Decimal | None
    remaining: Decimal | None
    currency: str
    due_date: dt.date | None
    due_date_estimated: bool
    waiting_for_invoice: str | None
    description: str | None


class EngagementPaymentPage(BaseModel):
    engagement_id: UUID
    items: list[EngagementPaymentRead]


# ---- the freelancer's own card, read through the door (REB-1035, brief «The freelancer's own
# view») ------------------------------------------------------------------------------------------

OwnMonthState = Literal["in_progress", "received", "approved"]


class OwnEngagementMonth(BaseModel):
    """One month as the freelancer sees it. `in_progress`: rebase has not received the report
    (the month is running or the report has not arrived); `received`: rebase has it and `answer_by`
    is the last day for the answer; `approved`: a person approved it or nobody objected in time,
    with the dates rebase does not recognise in `held_dates`. The reason of an objection never
    leaves rebase's space: it can carry the client's words."""

    month: dt.date
    label: str
    state: OwnMonthState
    received_on: dt.date | None
    answer_by: dt.date | None
    held_dates: list[dt.date]


class OwnEngagementRead(BaseModel):
    """What the freelancer reads of their own engagement with rebase, and nothing else: the
    letter, their fee and dates, the payment rule and the months. Never the client's name, price
    or budget, another engagement's figures, a forecast or an objection's reason."""

    letter_number: str | None
    state: str
    starts_on: dt.date
    ends_on: dt.date | None
    fee: Decimal
    fee_unit: str
    days_per_week: Decimal | None
    payment_rule: str
    payment_days: int | None
    payment_end_of_month: bool
    collection_days: int | None
    months: list[OwnEngagementMonth]
