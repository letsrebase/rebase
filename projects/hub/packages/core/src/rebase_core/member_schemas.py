"""The member area: a person's own card and request, the lookup the site makes and the
magic-link requests."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from rebase_core.contact_points import CONTACT_POINTS_MAX, ContactPointInput, ContactPointRead
from rebase_core.regimes import Regime
from rebase_core.schema_common import ResponseModel, is_complete
from rebase_core.signup_schemas import SignupUtm
from rebase_core.vocabulary import ContractKind, ContractState, RemoteOption, UserRole


class MemberProfile(BaseModel):
    """The row as its owner reads it: what they gave, and nothing the admin wrote.
    No `status`, no `note`, no attribution, and never the CV bytes."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    email: str
    linkedin_url: str | None
    # `None` on a card an admin wrote from a signup and the person has not completed
    # yet (ORB-155): no CV, no rate, no position, no remote option until they say so.
    # `cv_filename` and `cv_size` are `None` on a card the wizard made too, where the
    # CV is an optional step.
    cv_filename: str | None
    cv_size: int | None
    daily_rate: Decimal | None
    job_title: str | None
    remote: RemoteOption | None
    links: list[str]
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return is_complete(self)


class ContactPointsUpdate(BaseModel):
    """A person's own list of extra contact points, replacing the stored one (REB-943). The
    address they sign in with is not in it: it is shown as the primary email and changes
    nowhere in the member area."""

    model_config = ConfigDict(extra="forbid")

    contact_points: list[ContactPointInput] = Field(max_length=CONTACT_POINTS_MAX)


class MemberRequest(ResponseModel):
    """One company request as the person who filed it reads it (REB-602): the eight
    answers they gave, plus its id, so the member area can open that very request for
    editing, and when it was filed. Never `status`, `note` or the company's own name:
    what the admin wrote about a request stays the admin's, the same isolation
    `MemberProfile` keeps for the card."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    requested_role: str
    project: str
    period_from: date
    duration: str
    daily_budget: Decimal
    remote: RemoteOption
    onsite_days: int | None
    headcount: int
    created_at: datetime


class MeRead(ResponseModel):
    """Whoever `rebase_user` resolves to, member or admin, replacing `MemberProfile`
    on `GET /me` (REB-278): a `users` row is not necessarily an applicant with a card
    any more, so `has_card` says whether one exists, and the seven card fields answer
    blank -- `None`, `False`, `[]` -- when it does not, the shape a signed-in admin
    with no card now gets. `role` is `member` or `admin` (`USER_ROLES`). `phone`
    (REB-380) is a top-level identity field like `nome`/`last_name`/`linkedin_url`,
    never blanked by `has_card`/`has_company`: it lives on `users` regardless of
    which of the two a person carries.

    The company side is a list, `requests` (REB-602): every request the person filed
    that an admin has not soft-deleted, newest first, each one editable by its own id.
    `has_company` is true exactly when the list is not empty, since the wizard and edit
    guards read the flag and not the list. A person can carry a card, requests, both
    or neither."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str
    last_name: str
    email: str
    linkedin_url: str | None
    phone: str | None
    # Every way to reach the person (REB-943): the address they sign in with first, as the
    # primary email without an `id`, then the points they or an admin added.
    contact_points: list[ContactPointRead] = Field(default_factory=list)
    role: UserRole
    created_at: datetime
    updated_at: datetime
    has_card: bool
    cv_filename: str | None = None
    cv_size: int | None = None
    daily_rate: Decimal | None = None
    job_title: str | None = None
    remote: RemoteOption | None = None
    links: list[str] = Field(default_factory=list)
    requests: list[MemberRequest] = Field(default_factory=list)
    # REB-518: whether any talent cloud grant of this person is live, which puts «Talent
    # cloud» in the member area's nav (`rebase_core.cloud.TalentCloudService.for_user`).
    talent_cloud: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def complete(self) -> bool:
        return is_complete(self)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_company(self) -> bool:
        return bool(self.requests)


class MemberLookupRequest(BaseModel):
    """The address PigroCRM asks about, in a body and never in the URL: a query string is
    written by every access log and proxy on the way, a body is not. A plain `str`, not
    `EmailStr`: a malformed address is simply not a member's, never an error."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)


class MemberLookup(ResponseModel):
    """What the hub tells PigroCRM about an address (ORB-173): whether a freelancer with
    it exists, and if so the two names the CRM's signup would otherwise ask for again.
    Never the id, never the rest of the card: the CRM learns that the person is a
    member and how to greet them, nothing about who is at the keyboard."""

    member: bool
    first_name: str | None = None
    last_name: str | None = None


class LinkRequest(BaseModel):
    """The address to send a link to, and since REB-426 the attribution the login page
    arrived with, in the wizards' own shape and bounds: optional, and never a reason for
    the answer to differ between a known address and an unknown one."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    utm: SignupUtm | None = None


class EnterRequest(BaseModel):
    """The raw token from the link. `token_urlsafe(32)` is 43 characters; the bounds
    leave room without accepting a paragraph."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")


class ContractPdf(BaseModel):
    """A document's bytes and the name a browser saves them under."""

    filename: str
    content: bytes


class MemberContract(ResponseModel):
    """A contract as its freelancer reads it in «Contratti» (REB-392): never the PDF's
    bytes and never `data`. `customer` and the two dates are a letter's, as the letter
    prints them; `signing_url` is there only while the document waits for the
    signature, since its path is the signer's token. `pigro_url` is the engagement's own in
    the CRM, set only once it is `linked`: «Le tue ore su Pigro» has somewhere to point."""

    id: UUID
    kind: ContractKind
    # Which family it belongs to and its type as the page names it (REB-687).
    regime: Regime
    label: str
    number: str | None
    status: ContractState
    customer: str | None
    start: str | None
    end: str | None
    sent_at: datetime | None
    signed_at: datetime | None
    signing_url: str | None
    has_pdf_signed: bool
    active: bool
    renewal: date | None
    last_notice_day: date | None
    pigro_url: str | None


class MemberContracts(ResponseModel):
    """«Contratti»: the framework agreements on top, one per regime (REB-687): `framework`
    the ordinary one and `occasional_framework` the occasional one, each the active one,
    else the newest that reached the person; the letters newest first; and
    `previous_frameworks` (REB-392): the freelancer's other framework agreements that were
    signed, newest first, excluding the two on top -- a notice, or a newer one replacing
    it, must not make an earlier signed copy disappear from the page."""

    framework: MemberContract | None
    occasional_framework: MemberContract | None = None
    previous_frameworks: list[MemberContract] = Field(default_factory=list)
    letters: list[MemberContract]
