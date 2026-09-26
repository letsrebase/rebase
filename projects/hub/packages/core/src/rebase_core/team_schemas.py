"""What the team builder writes and reads (REB-509, spec § 2, § 2.1).

`Card` is the schema Claude's structured output is validated against, before its JSON
ever reaches a `freelancer_cards` row; `FreelancerCardRead` and `CardsRefreshed` are
what the card writer answers (REB-510, `cards.py`). `TeamProposalCreate` is what a
visitor asks the engine for and `TeamProposalRead` what it answers (REB-511,
`team_builder.py`, § 3.3), with `Band` from `bands.py`. The request's models, § 3.2 and
§ 3.5, are what a visitor's «Assumi team» sends and what the admin reads and edits
(REB-512, `team_requests.py`); the tasks after it (D1 to D4) extend them here, kept out
of `schemas.py`, already the size of a chapter, the same reasoning `contract_schemas.py`
gives for its own flow.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from rebase_core.bands import Band

# The five enum tuples live once, in `models.py`, the way `contract_schemas.py` and
# `match_words.py` already import their own shared constants from there rather than
# repeating them: re-exported here so a caller of this module still finds them beside
# `Card`.
from rebase_core.models import (
    AZIENDA_MAX_LENGTH,
    CARD_SENIORITIES,
    TALENT_ANSWERS,
    TEAM_PROPOSAL_ORIGINS,
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
    TELEFONO_MAX_LENGTH,
)
from rebase_core.schemas import PROGETTO_MAX_LENGTH, clean_multiline, clean_text
from rebase_core.validation import SafeStr

__all__ = [
    "CARD_SENIORITIES",
    "Band",
    "Card",
    "CardsRefreshed",
    "CloudRequestCreate",
    "CloudTalentList",
    "CloudTalentQuery",
    "CloudTalentRead",
    "FreelancerCardRead",
    "TALENT_ANSWERS",
    "TEAM_PROPOSAL_ORIGINS",
    "TEAM_REQUEST_ORIGINS",
    "TEAM_REQUEST_STATES",
    "TeamMemberRead",
    "TeamProposalCreate",
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


class Card(BaseModel):
    """The anonymous card § 2.1 asks Claude for: a role, a seniority, years of
    experience, skills, sectors, languages, a place or none, and a two-sentence Italian
    summary that names no person, no company and no link. `extra="forbid"` the way
    every schema Claude's output is validated against must be: a property the model
    invents that the caller silently accepted would be data nobody asked for and nobody
    can see written down. `luogo` has no default on purpose: the card writer always
    knows whether the CV names a place, and a key silently missing from Claude's JSON is
    a shape failure the writer records on the row, not a card with an unremarkable empty
    field."""

    model_config = ConfigDict(extra="forbid")

    ruolo: SafeStr = Field(min_length=1, max_length=120)
    seniority: Literal["junior", "mid", "senior", "lead"]
    anni: int = Field(ge=0, le=60)
    competenze: list[SafeStr] = Field(max_length=20)
    settori: list[SafeStr] = Field(max_length=10)
    lingue: list[SafeStr] = Field(max_length=8)
    luogo: SafeStr | None = Field(max_length=120)
    sintesi: SafeStr = Field(min_length=1, max_length=400)


class FreelancerCardRead(BaseModel):
    """A freelancer's anonymous card as the admin reads it (spec § 5.1): the last card
    written, the CV it came from, the model and when, and the last failure, which may
    sit beside an older card. `modalita` and `fascia` are not on the card: they are
    `Freelancer.remoto` and the client's band of `Freelancer.tariffa_giornaliera`
    (`bands.band_for`), read when the card is shown, so a mode or a rate the person
    edits is right at once (§ 2.1), and the page never computes a band of its own.
    `fascia` is `None` without a rate. `card`, `cv_sha256`, `model` and `generated_at`
    stay `None` until a CV produces a card; `error` is set or not on its own."""

    freelancer_id: UUID
    card: Card | None
    modalita: str | None
    fascia: Band | None
    cv_sha256: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None


class CardsRefreshed(NamedTuple):
    """What one `rebase cards-refresh` batch did: cards written, and CVs that failed
    (a refusal, a cut or malformed answer, a provider error, a scan with no text)."""

    written: int
    failed: int


# ---- the proposal (REB-511, spec § 3.3) ----------------------------------------------------

DESCRIZIONE_MIN_LENGTH = 40
DESCRIZIONE_MAX_LENGTH = 4000
NOTA_MAX_LENGTH = 500


class TeamProposalCreate(BaseModel):
    """What a visitor, a cloud user or an admin asks the engine for: the project's
    description, and on «Rigenera» the proposal it replaces and a note on it («togli il
    designer»). Stripped before it is measured, so forty spaces are not a description."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    descrizione: SafeStr = Field(
        min_length=DESCRIZIONE_MIN_LENGTH, max_length=DESCRIZIONE_MAX_LENGTH
    )
    nota: SafeStr | None = Field(default=None, max_length=NOTA_MAX_LENGTH)
    previous_id: UUID | None = None


class TeamMemberRead(BaseModel):
    """One person of a proposed team (§ 3.3): `posizione` is their index in this
    proposal (1, 2, 3), the only handle the public read gives; `freelancer_id` is
    `None` there, so a visitor cannot follow a talent across proposals, and so is the
    card's `luogo`, which only the engine and the admin read. `modalita` is
    `Freelancer.remoto` and `fascia` the band of their current rate, both read when the
    proposal is, the way the card is shown everywhere (§ 2.1)."""

    posizione: int
    freelancer_id: UUID | None
    ruolo: str
    motivazione: str
    giorni_settimana: int | None
    scheda: Card
    modalita: str | None
    fascia: Band | None


class TeamProposalRead(BaseModel):
    """A proposal as the page shows it (§ 3.3). `luogo` is what the engine read about
    place in the description, `{"locale": bool, "dove": str | None}`, and stays on the
    public read: it is the visitor's own words. `economia` is `{"giorno": Band | None,
    "mese": Band | None, "giorni_mese": 22}`, the team's bands from its members',
    `None` when any member has no rate."""

    id: UUID
    riassunto: str
    luogo: dict[str, Any]
    team: list[TeamMemberRead]
    economia: dict[str, Any]
    previous_id: UUID | None
    origine: str
    created_at: datetime


# ---- the request (REB-512, spec § 3.2, § 3.5) -----------------------------------------------

# The engine's own ceiling on a summary (`team_builder.ProposalAnswer`), kept by the admin's
# edit so what the talents read is never longer than what Claude may write.
RIASSUNTO_MAX_LENGTH = 1500


class TeamRequestCreate(BaseModel):
    """What «Assumi team» sends from the public page: the proposal, and who to call back.
    The company's name and the phone follow the company wizard's own rules
    (`CompanyCreate`): trimmed, no control character, never only spaces."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    azienda: SafeStr = Field(min_length=1, max_length=AZIENDA_MAX_LENGTH)
    email: EmailStr
    telefono: SafeStr = Field(min_length=6, max_length=TELEFONO_MAX_LENGTH)

    @field_validator("azienda", "telefono", mode="after")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return clean_text(value, what="un valore")


class TeamRequestCreated(BaseModel):
    """What the public route answers, and all it answers: the request's id."""

    id: UUID


class TeamRequestTalentRead(BaseModel):
    """One talent of a request, as the admin reads it: the name, the role proposed, the
    freelancer's own rate and the client's band from it, and the availability mail's
    progress (D1), all `None` until the first send. `contattabile` is false for a talent
    whose card was deleted or `scartato`: the availability mail skips them."""

    freelancer_id: UUID
    nome: str
    cognome: str
    ruolo: str
    tariffa_giornaliera: Decimal | None
    fascia: Band | None
    mail_sent_at: datetime | None
    risposta: str | None
    risposta_at: datetime | None
    contattabile: bool


class TeamRequestRead(BaseModel):
    """A request's page in «Richieste team» (§ 3.5). `proposal` is the admin's read of
    the proposal (ids and the card's place kept), `None` for a single-talent request
    from the cloud; `riassunto` and `descrizione` are the proposal's, the summary being
    the copy the admin edits before the talents read it."""

    id: UUID
    proposal: TeamProposalRead | None
    riassunto: str | None
    descrizione: str | None
    origine: str
    azienda: str
    email: str
    telefono: str | None
    user_id: UUID | None
    company_id: UUID | None
    stato: str
    note: str | None
    talenti: list[TeamRequestTalentRead]
    contacted_at: datetime | None
    closed_at: datetime | None
    created_at: datetime


class TeamRequestListItem(BaseModel):
    """One row of «Richieste team»: who, from where, when, where it stands, and «N sì su
    M» from the talents' answers."""

    id: UUID
    azienda: str
    origine: str
    stato: str
    created_at: datetime
    contacted_at: datetime | None
    talenti_totale: int
    talenti_si: int


class TeamRequestList(BaseModel):
    """A page of the list, newest first; `next_cursor` is `None` on the last page."""

    items: list[TeamRequestListItem]
    next_cursor: str | None = None


class TeamRequestStatus(BaseModel):
    """«Segna come contattata», «Chiudi»: one of `TEAM_REQUEST_STATES`, checked by the
    service so the API and the MCP server refuse the same words the same way."""

    model_config = ConfigDict(extra="forbid")

    stato: str = Field(min_length=1, max_length=20)


class TeamRequestNote(BaseModel):
    """The admin's own note on a request; `null` or only spaces clears it."""

    model_config = ConfigDict(extra="forbid")

    note: SafeStr | None = Field(default=None, max_length=PROGETTO_MAX_LENGTH)


class TeamRequestSummary(BaseModel):
    """«Salva il riassunto»: the summary the talents will read (D1's mail), never empty,
    in lines and paragraphs but with no other control character, the rule the company's
    own project description follows (`clean_multiline`)."""

    model_config = ConfigDict(extra="forbid")

    riassunto: SafeStr = Field(min_length=1, max_length=RIASSUNTO_MAX_LENGTH)

    @field_validator("riassunto", mode="after")
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
    risposta: Literal["si", "no"]


class TeamAvailabilityOutcome(BaseModel):
    """The answer recorded, or `invalid` for a token unknown, spent or expired alike."""

    esito: Literal["si", "no", "invalid"]


# ---- the talent cloud (REB-519, spec § 4.2) -------------------------------------------------

# A skill typed in the one search box: longer than any skill a card holds, and bounded,
# as every free text that reaches `ILIKE` is (`search.SEARCH_MAX_LENGTH`).
COMPETENZA_MAX_LENGTH = 100


class CloudTalentQuery(BaseModel):
    """The cloud's filters (spec § 4.2), each optional and all together: a role of the
    cards' own (`CloudTalentList.ruoli`), a seniority, one skill searched inside the
    card's skills, a work mode, and a band of the client's price per day, its bottom at
    or above `fascia_min` and its top at or below `fascia_max`, in whole euro. A blank
    word narrows nothing. The words are checked by the service, so the API and the MCP
    server refuse the same ones the same way."""

    model_config = ConfigDict(str_strip_whitespace=True)

    ruolo: str | None = Field(default=None, max_length=120)
    seniority: str | None = Field(default=None, max_length=20)
    competenza: str | None = Field(default=None, max_length=COMPETENZA_MAX_LENGTH)
    modalita: str | None = Field(default=None, max_length=20)
    fascia_min: int | None = Field(default=None, ge=0)
    fascia_max: int | None = Field(default=None, ge=0)


class CloudTalentRead(BaseModel):
    """A talent as an admitted company reads them in the cloud (spec § 4.2): who they
    are, the links they gave, whether rebase vetted them, the anonymous card (its
    `luogo` left out: the CV is one click away), the work mode, the client's band and
    whether there is a CV to open. Never the freelancer's own rate, their state, the
    admin's notes and comments, nor their address or phone: those are not fields here,
    so no read can carry them."""

    freelancer_id: UUID
    nome: str
    cognome: str
    linkedin_url: str | None
    links: list[str]
    vetted: bool
    card: Card
    modalita: str | None
    fascia: Band | None
    ha_cv: bool


class CloudTalentList(BaseModel):
    """The cloud's page: the talents the filters leave, vetted first then by name, at
    most `cloud.CLOUD_LIST_CAP`, and `capped` when there were more (the page says so,
    since the list has no second page); `ruoli` is every role of the cloud's cards once,
    sorted, the role filter's choices whatever the filters narrowed."""

    items: list[CloudTalentRead]
    ruoli: list[str]
    capped: bool


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
            raise ValueError("serve una proposta oppure un talento, uno dei due")
        return self
