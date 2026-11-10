"""What the team builder writes and reads (REB-509, spec § 2, § 2.1).

`Card` is the schema Claude's structured output is validated against, before its JSON
ever reaches a `freelancer_cards` row; `FreelancerCardRead` and `CardsRefreshed` are
what the card writer answers (REB-510, `cards.py`). `TeamProposalCreate` is what a
visitor asks the engine for and `TeamProposalRead` what it answers (REB-511,
`team_builder.py`, § 3.3), with `Band` from `bands.py`. The request's models, § 3.2 and
§ 3.5, are what a visitor's «Assumi team» sends and what the admin reads and edits
(REB-512, `team_requests.py`); the tasks after it (D1 to D4) extend them here, kept out
of the family schema modules, the same reasoning `contract_schemas.py`
gives for its own flow.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.bands import Band

# The five enum tuples live once, in `models.py`, the way `contract_schemas.py` and
# `match_words.py` already import their own shared constants from there rather than
# repeating them: re-exported here so a caller of this module still finds them beside
# `Card`.
from rebase_core.models import (
    CARD_SENIORITIES,
    COMPANY_MAX_LENGTH,
    PHONE_MAX_LENGTH,
    TALENT_ANSWERS,
    TEAM_PROPOSAL_ERRORS,
    TEAM_PROPOSAL_ORIGINS,
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
)
from rebase_core.schema_common import (
    PROJECT_MAX_LENGTH,
    ResponseModel,
    clean_multiline,
    clean_ref,
    clean_text,
)
from rebase_core.validation import SafeStr
from rebase_core.vocabulary import (
    RemoteOption,
    Seniority,
    TalentAnswer,
    TeamProposalError,
    TeamProposalOrigin,
    TeamRequestOrigin,
    TeamRequestState,
)

__all__ = [
    "CARD_SENIORITIES",
    "Band",
    "Card",
    "CardRead",
    "CardsRefreshed",
    "CloudRequestCreate",
    "CloudTalentList",
    "CloudTalentQuery",
    "CloudTalentRead",
    "CardPersonRead",
    "CardProvenance",
    "ConflictRead",
    "FreelancerCardAdminRead",
    "FreelancerCardRead",
    "PersonDifference",
    "SourceProvenance",
    "TALENT_ANSWERS",
    "TEAM_PROPOSAL_ERRORS",
    "TEAM_PROPOSAL_ORIGINS",
    "TEAM_REQUEST_ORIGINS",
    "TEAM_REQUEST_STATES",
    "TeamMemberRead",
    "TeamProposalCreate",
    "TeamProposalList",
    "TeamProposalListItem",
    "TeamProposalListMember",
    "TeamProposalRead",
    "TeamRequestCreate",
    "TeamRequestCreated",
    "TeamRequestList",
    "TeamRequestListItem",
    "TeamRequestNote",
    "TeamRequestRead",
    "TeamRequestStatus",
    "TeamRequestSummary",
    "TeamRequestTalentRead",
]


class Card(ResponseModel):
    """The anonymous card § 2.1 asks Claude for: a role, a seniority, years of
    experience, skills, sectors, languages, a place or none, and a two-sentence Italian
    summary that names no person, no company and no link. `extra="forbid"` the way
    every schema Claude's output is validated against must be: a property the model
    invents that the caller silently accepted would be data nobody asked for and nobody
    can see written down. `location` has no default on purpose: the card writer always
    knows whether the CV names a place, and a key silently missing from Claude's JSON is
    a shape failure the writer records on the row, not a card with an unremarkable empty
    field."""

    model_config = ConfigDict(extra="forbid")

    role: SafeStr = Field(min_length=1, max_length=120)
    # `experience_years` before `seniority`, which is read from it: the schema's order is the order
    # the model writes in. `None` when the CV dates no role (REB-799): the model is told
    # to say so rather than guess, and `seniority` is then a reading of the
    # responsibility alone, shown as uncertain (`CardRead.seniority_uncertain`). No default,
    # like `location`.
    experience_years: int | None = Field(ge=0, le=60)
    seniority: Literal["junior", "mid", "senior", "lead"]
    skills: list[SafeStr] = Field(max_length=20)
    industries: list[SafeStr] = Field(max_length=10)
    languages: list[SafeStr] = Field(max_length=8)
    location: SafeStr | None = Field(max_length=120)
    profile_summary: SafeStr = Field(min_length=1, max_length=400)


class CardRead(Card):
    """A `Card` as everything but the card writer reads it (REB-799): `seniority` and
    `experience_years` are the effective ones, the admin's when there is one (`freelancers.
    seniority_admin`, `anni_admin`), the card's own otherwise. `seniority_incerta` says
    the seniority is a guess the CV's dates could not back: `experience_years` is null on the card
    and nobody corrected the seniority. Computed by `card_level.card_read`, never stored
    and never asked of Claude: the schema Claude answers is `Card`'s."""

    seniority_uncertain: bool = False


class FreelancerCardRead(ResponseModel):
    """A freelancer's anonymous card as the admin reads it (spec § 5.1): the last card
    written, the CV it came from, the model and when, and the last failure, which may
    sit beside an older card. `mode` and `band` are not on the card: they are
    `Freelancer.remote` and the client's band of `Freelancer.daily_rate`
    (`bands.band_for`), read when the card is shown, so a mode or a rate the person
    edits is right at once (§ 2.1), and the page never computes a band of its own.
    `band` is `None` without a rate. `card`, `cv_sha256`, `model` and `generated_at`
    stay `None` until a CV produces a card; `error` is set or not on its own. `card` is
    the effective one (`CardRead`); `seniority_admin` and `experience_years_admin` are the admin's
    own values, `None` while nobody corrected them (REB-799), so the page can tell a
    correction from Claude's reading."""

    freelancer_id: UUID
    card: CardRead | None
    mode: RemoteOption | None
    band: Band | None
    cv_sha256: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None
    seniority_admin: Seniority | None = None
    experience_years_admin: int | None = None


class SourceProvenance(ResponseModel):
    """One stored source of a card's person, as the admin reads it: its key (`github`,
    `link:<n>`), what it is, where it was read (the login or the link the person gave),
    the outcome of the last read and when it was made. Never the text."""

    key: str
    kind: Literal["github", "link"]
    ref: str
    status: str
    fetched_at: datetime


class ConflictRead(ResponseModel):
    """A disagreement the code found while writing a card (`card_draft.conflicts`):
    `different_experience_years`, the hub's completed engagements add up to more years than the
    card's
    `experience_years_card` (the CV's dates) by more than a year; `discarded_experience_years`, the
    model gave
    `experience_years_card` years on the strength of sources that date no profession (GitHub, a
    page) and the code dropped it. `experience_years_hub` is the hub's completed months in whole
    years, `None` when the hub had no say."""

    kind: Literal["different_experience_years", "discarded_experience_years"]
    experience_years_card: int | None
    experience_years_hub: int | None


class CardProvenance(ResponseModel):
    """What a card was written from and by what, for the admin only (REB-839): never part
    of `Card`, of a public page, of the cloud or of the talent list.

    `field_sources` is what the model *said* supported each field, kept only for sources that
    existed (`verify_sources`): the page shows it as «secondo il modello». `{}` and
    `prompt_version` 1 for a card written before provenance existed. `sources` is what is
    stored now, with its read times; `source_hashes` what the card was written from;
    `stale` why the card is no longer what the inputs would give: `cv` (the hourly loop
    writes it again), `source` (the hub's facts, the position or a source changed) or
    `prompt` (a newer prompt version); the last two wait for an admin's start."""

    prompt_version: int
    provider: str | None
    field_sources: dict[str, list[str]]
    conflicts: list[ConflictRead]
    sources: list[SourceProvenance]
    source_hashes: dict[str, str]
    stale: Literal["cv", "source", "prompt"] | None


class CardPersonRead(ResponseModel):
    """What the CV (and the sources beside it) says of who the person is, read out by the
    same call that writes the anonymous card (REB-855): for the admin only, stored on the
    card's row (`freelancer_cards.person`) and never written into `users`. Everything is
    what the document states literally, normalised by `card_draft.clean_person`
    (`email` in lower case and valid, `linkedin_url` in its stored shape, `phone` as
    written and trimmed, only `http(s)` links): a value that did not pass is `None`, or
    absent from the lists. `companies` are the employers and clients the CV names. Never
    part of `Card`, of `FreelancerCardRead`, of the cloud, the talent list or the team
    builder."""

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    linkedin_url: str | None = None
    other_links: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)


class PersonDifference(ResponseModel):
    """A field where the CV and the profile both say something and disagree
    (`card_draft.differences`): `field` is `nome`, `last_name`, `email`, `phone` or
    `linkedin_url`, `cv` what the CV says and `profile` what the account holds. A field one
    side leaves empty is no disagreement."""

    field: Literal["first_name", "last_name", "email", "phone", "linkedin_url"]
    cv: str
    profile: str


class FreelancerCardAdminRead(FreelancerCardRead):
    """`FreelancerCardRead`, the provenance of the card and the named card, as the admin's
    page reads them (`GET /freelancers/{id}/card`). `provenance` is `None` while the person
    has no card row at all. `person` is what the CV says of who the person is (REB-855),
    `None` for a card written before prompt version 3 and for a person whose CV the model
    never read; it is kept even when the anonymous card was refused. `differences` lists
    where it disagrees with the profile."""

    provenance: CardProvenance | None = None
    person: CardPersonRead | None = None
    differences: list[PersonDifference] = Field(default_factory=list)


class CardsRefreshed(NamedTuple):
    """What one `rebase cards-refresh` batch did: cards written, CVs that failed (a
    refusal, a cut or malformed answer, a card still giving the person's gender away after
    its one rewrite (REB-802), a provider error, a scan with no text). `stopped`: the
    batch ended at a provider outage, on a first call or on a rewrite, counted among
    `failed`."""

    written: int
    failed: int
    stopped: bool = False
    # Cards sent as one Message Batch instead (REB-853): none are written or failed yet,
    # they stay stale until the batch's results are applied.
    batched: int = 0


# ---- the proposal (REB-511, spec § 3.3) ----------------------------------------------------

DESCRIPTION_MIN_LENGTH = 40
DESCRIPTION_MAX_LENGTH = 4000
NOTE_MAX_LENGTH = 500
PEOPLE_MAX = 10


class TeamProposalCreate(BaseModel):
    """What a visitor, a cloud user or an admin asks the engine for: the project's
    description, how many people they want (`people`, REB-591: the page sends 1 unless
    the visitor picks more; left out, the engine sizes the team from the description
    alone, which is what the MCP tool does), and on «Rigenera» the proposal it replaces
    and a note on it («togli il designer»). Stripped before it is measured, so forty
    spaces are not a description."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    description: SafeStr = Field(
        min_length=DESCRIPTION_MIN_LENGTH, max_length=DESCRIPTION_MAX_LENGTH
    )
    # Strict: a JSON `true` or `"3"` is not a headcount, and would otherwise reach the
    # prompt and the event as one.
    people: int | None = Field(default=None, ge=1, le=PEOPLE_MAX, strict=True)
    note: SafeStr | None = Field(default=None, max_length=NOTE_MAX_LENGTH)
    previous_id: UUID | None = None


class TeamMemberRead(ResponseModel):
    """One person of a proposed team (§ 3.3): `position` is their index in this
    proposal (1, 2, 3), the only handle the public read gives; `freelancer_id` is
    `None` there, so a visitor cannot follow a talent across proposals, and so is the
    card's `location`, which only the engine and the admin read. `first_name` and `last_name` are
    the person's, for the cloud and the admin, which show who each member is (§ 4.2);
    `None` on the public read, like the id. `mode` is `Freelancer.remote` and
    `band` the band of their current rate, both read when the proposal is, the way
    the card is shown everywhere (§ 2.1)."""

    position: int
    freelancer_id: UUID | None
    first_name: str | None = None
    last_name: str | None = None
    role: str
    reasoning: str
    week_days: int | None
    card: CardRead
    mode: RemoteOption | None
    band: Band | None


class TeamPlace(ResponseModel):
    """What the engine read about place in a proposal's description: whether the client
    wants someone local, and where (`None` when no place was named, or on the public read
    when the visitor did not write it)."""

    on_site: bool
    place: str | None


class TeamEconomics(ResponseModel):
    """The team's bands from its members', per day and per month at `days_month` days;
    `None` when any member has no rate."""

    day: Band | None
    month: Band | None
    days_month: int


class TeamProposalRead(ResponseModel):
    """A proposal as the page shows it (§ 3.3). `location` is what the engine read about
    place in the description (`TeamPlace`), and stays on the public read: it is the
    visitor's own words. `economics` is the team's bands from its members' (`TeamEconomics`).
    `description` and `people` are what the proposal was asked with (REB-675): the public
    page opened on a proposal by its id fills its box from them and sends them back on
    «Rigenera»."""

    id: UUID
    description: str
    people: int | None
    summary: str
    location: TeamPlace
    team: list[TeamMemberRead]
    economics: TeamEconomics
    previous_id: UUID | None
    origin: TeamProposalOrigin
    created_at: datetime


class TeamProposalListMember(ResponseModel):
    """One person a listed proposal held (REB-607): their index in the proposal, the
    role it proposed them for, and who they are, so the admin can open their profile
    from the list. `first_name` and `last_name` are read when the list is, and are `None` for
    a talent removed with «Elimina» since, whose admin page no longer opens: the row
    keeps them all by id, since the list is about what was proposed and not about who
    is still in the catalogue."""

    position: int
    freelancer_id: UUID
    role: str
    first_name: str | None
    last_name: str | None


class TeamProposalListItem(ResponseModel):
    """One «Proponi il team» as the admin's list reads it (0028, DECISIONS.md
    2026-09-29): what was asked (`description`, `people`, `nota`, `previous_id` on a
    «Rigenera»), who asked (`origin`, `user_id`), when, and what came of it. `error`
    is `None` on a proposal that answered and the refusal's code
    (`TEAM_PROPOSAL_ERRORS`) on an attempt that did not; `summary` is what the
    proposal said, `None` on an attempt; `members` is how many people the proposal held,
    0 on an attempt and on a team nobody fit, and `team` names each of them in the
    proposal's order (REB-607); `request_id` is the «Assumi team» filed on it, `None`
    while there is none, which is the case the list exists to count."""

    id: UUID
    description: str
    people: int | None
    note: str | None
    previous_id: UUID | None
    origin: TeamProposalOrigin
    user_id: UUID | None
    error: TeamProposalError | None
    summary: str | None
    members: int
    team: list[TeamProposalListMember]
    request_id: UUID | None
    created_at: datetime


class TeamProposalList(ResponseModel):
    """A page of the list, newest first; `next_cursor` is `None` on the last page."""

    items: list[TeamProposalListItem]
    next_cursor: str | None = None


# ---- the request (REB-512, spec § 3.2, § 3.5) -----------------------------------------------

# The engine's own ceiling on a summary (`team_builder.ProposalAnswer`), kept by the admin's
# edit so what the talents read is never longer than what Claude may write.
SUMMARY_MAX_LENGTH = 1500


class TeamRequestCreate(BaseModel):
    """What «Assumi team» sends from the public page: the proposal, and who to call back.
    The company's name and the phone follow the company wizard's own rules
    (`CompanyCreate`): trimmed, no control character, never only spaces."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    company: SafeStr = Field(min_length=1, max_length=COMPANY_MAX_LENGTH)
    email: EmailStr
    phone: SafeStr = Field(min_length=6, max_length=PHONE_MAX_LENGTH)
    # `?ref=` from the visitor's link (REB-600), cleaned like the signup's (REB-554): a
    # string that is not a code is no code, never a 422. Stored on the request and credited
    # only when a company exists for this contact.
    ref: SafeStr | None = None

    @field_validator("company", "phone", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")

    @field_validator("ref", mode="before")
    @classmethod
    def _ref(cls, value: object) -> str | None:
        return clean_ref(value)


class TeamRequestCreated(ResponseModel):
    """What the public route answers, and all it answers: the request's id."""

    id: UUID


class TeamRequestTalentRead(ResponseModel):
    """One talent of a request, as the admin reads it: the name, the role proposed, the
    freelancer's own rate and the client's band from it, and the availability mail's
    progress (D1), all `None` until the first send. `contactable` is false for a talent
    whose card was deleted or `discarded`: the availability mail skips them."""

    freelancer_id: UUID
    first_name: str
    last_name: str
    role: str
    daily_rate: Decimal | None
    band: Band | None
    mail_sent_at: datetime | None
    answer: TalentAnswer | None
    answered_at: datetime | None
    contactable: bool


class TeamRequestRead(ResponseModel):
    """A request's page in «Richieste team» (§ 3.5). `proposal` is the admin's read of
    the proposal (ids and the card's place kept), `None` for a single-talent request
    from the cloud; `summary` and `description` are the proposal's, the summary being
    the copy the admin edits before the talents read it."""

    id: UUID
    proposal: TeamProposalRead | None
    summary: str | None
    description: str | None
    origin: TeamRequestOrigin
    company: str
    email: str
    phone: str | None
    user_id: UUID | None
    company_id: UUID | None
    status: TeamRequestState
    note: str | None
    talents: list[TeamRequestTalentRead]
    contacted_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class TeamRequestListItem(ResponseModel):
    """One row of «Richieste team»: who, from where, when, where it stands, and «N yes out of
    M» from the talents' answers."""

    id: UUID
    company: str
    origin: TeamRequestOrigin
    status: TeamRequestState
    created_at: datetime
    contacted_at: datetime | None
    total_talents: int
    yes_talents: int


class TeamRequestList(ResponseModel):
    """A page of the list, newest first; `next_cursor` is `None` on the last page."""

    items: list[TeamRequestListItem]
    next_cursor: str | None = None


class TeamRequestStatus(BaseModel):
    """«Segna come contattata», «Chiudi»: one of `TEAM_REQUEST_STATES`, checked by the
    service so the API and the MCP server refuse the same words the same way."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=20)


class TeamRequestNote(BaseModel):
    """The admin's own note on a request; `null` or only spaces clears it."""

    model_config = ConfigDict(extra="forbid")

    note: SafeStr | None = Field(default=None, max_length=PROJECT_MAX_LENGTH)


class TeamRequestSummary(BaseModel):
    """«Salva il riassunto»: the summary the talents will read (D1's mail), never empty,
    in lines and paragraphs but with no other control character, the rule the company's
    own project description follows (`clean_multiline`)."""

    model_config = ConfigDict(extra="forbid")

    summary: SafeStr = Field(min_length=1, max_length=SUMMARY_MAX_LENGTH)

    @field_validator("summary", mode="after")
    @classmethod
    def _paragraphs(cls, value: str) -> str:
        return clean_multiline(value, what="un riassunto")


# ---- the talents' availability (REB-517, spec § 3.2, § 3.6) -------------------------------

# `token_urlsafe(32)` is 43 characters: room to spare, and nothing unbounded is hashed.
AVAILABILITY_TOKEN_MAX_LENGTH = 128


class TeamAvailabilityAnswer(BaseModel):
    """What the answer page posts on «Conferma»: the token from the mail's link and the
    answer the link carried. An empty or unknown token is not a 422 but the same
    `invalid` a spent one gets, so the page says one sentence for all of them."""

    model_config = ConfigDict(extra="forbid")

    t: str = Field(max_length=AVAILABILITY_TOKEN_MAX_LENGTH)
    answer: Literal["yes", "no"]

    @field_validator("answer", mode="before")
    @classmethod
    def _old_links_say_si(cls, value: object) -> object:
        """The links in mails already sent carry `r=si`; the page forwards it as it is."""
        return "yes" if value == "si" else value


class TeamAvailabilityOutcome(ResponseModel):
    """The answer recorded, or `invalid` for a token unknown, spent or expired alike."""

    outcome: Literal["yes", "no", "invalid"]


# ---- the talent cloud (REB-519, spec § 4.2) -------------------------------------------------

# A skill typed in the one search box: longer than any skill a card holds, and bounded,
# as every free text that reaches `ILIKE` is (`search.SEARCH_MAX_LENGTH`).
SKILL_MAX_LENGTH = 100


class CloudTalentQuery(BaseModel):
    """The cloud's filters (spec § 4.2), each optional and all together: a role of the
    cards' own (`CloudTalentList.roles`), a seniority, one skill searched inside the
    card's skills, a work mode, and a band of the client's price per day, its bottom at
    or above `min_band` and its top at or below `max_band`, in whole euro. A blank
    word narrows nothing. The words are checked by the service, so the API and the MCP
    server refuse the same ones the same way."""

    model_config = ConfigDict(str_strip_whitespace=True)

    role: str | None = Field(default=None, max_length=120)
    seniority: str | None = Field(default=None, max_length=20)
    skill: str | None = Field(default=None, max_length=SKILL_MAX_LENGTH)
    mode: str | None = Field(default=None, max_length=20)
    min_band: int | None = Field(default=None, ge=0)
    max_band: int | None = Field(default=None, ge=0)


class CloudTalentRead(ResponseModel):
    """A talent as an admitted company reads them in the cloud (spec § 4.2): who they
    are, the links they gave, whether rebase vetted them, the anonymous card (its
    `location` left out: the CV is one click away), the work mode, the client's band and
    whether there is a CV to open. Never the freelancer's own rate, their state, the
    admin's notes, nor their address or phone: those are not fields here,
    so no read can carry them."""

    freelancer_id: UUID
    first_name: str
    last_name: str
    linkedin_url: str | None
    links: list[str]
    vetted: bool
    card: CardRead
    mode: RemoteOption | None
    band: Band | None
    has_cv: bool


class CloudTalentList(ResponseModel):
    """The cloud's page: the talents the filters leave, vetted first then by name, at
    most `cloud.CLOUD_LIST_CAP`, and `capped` when there were more (the page says so,
    since the list has no second page); `roles` is every role of the cloud's cards once,
    sorted, the role filter's choices whatever the filters narrowed."""

    items: list[CloudTalentRead]
    roles: list[str]
    capped: bool


# What a cloud request that names both or neither of its two targets is refused with.
CLOUD_REQUEST_ONE_OF = "serve una proposta oppure un talento, uno dei due"


class CloudRequestCreate(BaseModel):
    """What the cloud files, with no form: «Assumi team» on the builder's proposal
    (`proposal_id`) or «Richiedi» on one card (`freelancer_id`), exactly one of the two.
    Who asks, and for which company, is the caller's grant (spec § 4.2)."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID | None = None
    freelancer_id: UUID | None = None

    @model_validator(mode="after")
    def _one_of_the_two(self) -> "CloudRequestCreate":
        if (self.proposal_id is None) == (self.freelancer_id is None):
            raise ValueError(CLOUD_REQUEST_ONE_OF)
        return self
