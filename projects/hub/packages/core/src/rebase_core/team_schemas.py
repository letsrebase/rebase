"""What the team builder writes and reads (REB-509, spec § 2, § 2.1).

`Card` is the schema Claude's structured output is validated against, before its JSON
ever reaches a `freelancer_cards` row; `FreelancerCardRead` and `CardsRefreshed` are
what the card writer answers (REB-510, `cards.py`). `TeamProposalCreate` is what a
visitor asks the engine for and `TeamProposalRead` what it answers (REB-511,
`team_builder.py`, § 3.3), with `Band` from `bands.py`. The request's models, § 3.5,
come with the tasks that build the routes reading them (C5 to D4), kept out of
`schemas.py`, already the size of a chapter, the same reasoning `contract_schemas.py`
gives for its own flow.
"""

from datetime import datetime
from typing import Any, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.bands import Band

# The five enum tuples live once, in `models.py`, the way `contract_schemas.py` and
# `match_words.py` already import their own shared constants from there rather than
# repeating them: re-exported here so a caller of this module still finds them beside
# `Card`.
from rebase_core.models import (
    CARD_SENIORITIES,
    TALENT_ANSWERS,
    TEAM_PROPOSAL_ORIGINS,
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
)
from rebase_core.validation import SafeStr

__all__ = [
    "CARD_SENIORITIES",
    "Band",
    "Card",
    "CardsRefreshed",
    "FreelancerCardRead",
    "TALENT_ANSWERS",
    "TEAM_PROPOSAL_ORIGINS",
    "TEAM_REQUEST_ORIGINS",
    "TEAM_REQUEST_STATES",
    "TeamMemberRead",
    "TeamProposalCreate",
    "TeamProposalRead",
]


class Card(BaseModel):
    """The anonymous card § 2.1 asks Claude for: a role, a seniority, years of
    experience, skills, sectors, languages, a place or none, and a two-sentence Italian
    summary that names no person, no company and no link. `extra="forbid"` the way
    every schema Claude's output is validated against must be: a property the model
    invents that the caller silently accepted would be data nobody asked for and nobody
    can see written down. `luogo` has no default on purpose: the card writer always
    knows whether the CV names a place, and a key silently missing from Claude's JSON is
    a shape failure (`LlmUnavailable`), not a card with an unremarkable empty field."""

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
    sit beside an older card. `modalita` is not on the card: it is `Freelancer.remoto`,
    read when the card is shown, so a mode the person edits is right at once (§ 2.1).
    `card`, `cv_sha256`, `model` and `generated_at` stay `None` until a CV produces a
    card; `error` is set or not on its own."""

    freelancer_id: UUID
    card: Card | None
    modalita: str | None
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
