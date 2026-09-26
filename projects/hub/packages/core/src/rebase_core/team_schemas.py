"""What the team builder writes and reads (REB-509, spec § 2, § 2.1).

`Card` is the schema Claude's structured output is validated against, before its JSON
ever reaches a `freelancer_cards` row; `FreelancerCardRead` and `CardsRefreshed` are
what the card writer answers (REB-510, `cards.py`). The public and admin read models
-- `TeamProposalRead`, `TeamRequestRead` and the rest of § 3.3 and § 3.5 -- come with
the tasks that build the routes reading them (C4 to D4), kept out of `schemas.py`,
already the size of a chapter, the same reasoning `contract_schemas.py` gives for its
own flow.
"""

from datetime import datetime
from typing import Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from rebase_core.validation import SafeStr

CARD_SENIORITIES = ("junior", "mid", "senior", "lead")
TEAM_REQUEST_STATES = ("nuova", "contattata", "chiusa")
TEAM_PROPOSAL_ORIGINS = ("pubblico", "cloud", "admin")
TEAM_REQUEST_ORIGINS = ("pubblico", "cloud")
TALENT_ANSWERS = ("si", "no")


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
