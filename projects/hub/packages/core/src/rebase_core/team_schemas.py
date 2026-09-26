"""What the team builder writes and reads (REB-509, spec § 2, § 2.1).

Only `Card` lives here for now: the schema Claude's structured output is validated
against, before its JSON ever reaches a `freelancer_cards` row. The public and admin
read models -- `TeamProposalRead`, `TeamRequestRead` and the rest of § 3.3 and § 3.5 --
come with the tasks that build the routes reading them (C3 to D4), kept out of
`schemas.py`, already the size of a chapter, the same reasoning `contract_schemas.py`
gives for its own flow.
"""

from typing import Literal

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
