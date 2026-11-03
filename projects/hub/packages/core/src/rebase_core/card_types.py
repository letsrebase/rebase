"""The words every part of the card writer shares (REB-892, split out of `cards.py`): the
kinds of failure and of outcome, the sentence of ours that stands for each failure, and the
plain records `CardWriter` passes between its steps.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, NamedTuple
from uuid import UUID

from rebase_core.card_draft import Conflict
from rebase_core.card_guard import KnownIdentifiers
from rebase_core.hub_facts import HubFacts
from rebase_core.llm import LlmRequest
from rebase_core.sources import SourceSet
from rebase_core.team_schemas import Card, CardPersonaRead

NO_TEXT = "Il CV non ha testo leggibile."

Failure = Literal[
    "no_text",
    "refusal",
    "max_tokens",
    "shape",
    "identifying",
    "source_identifying",
    "gendered",
    "unavailable",
]
Outcome = Literal[
    "written",
    "failed",
    "unavailable",
    "unchanged",
    "deleted",
    "superseded",
]

# Who started a write (REB-861): the door the person used. `cli` is `rebase cards-regenerate`.
Fonte = Literal["web", "mcp", "cli"]

# What the admin reads beside the card: ours, short, and never what the model said.
FAILURES: dict[Failure, str] = {
    "no_text": NO_TEXT,
    "refusal": "Claude ha rifiutato di scrivere la scheda da questo CV.",
    "max_tokens": "La risposta di Claude si è interrotta prima della fine della scheda.",
    "shape": "La risposta di Claude non era una scheda valida.",
    "identifying": "La scheda cita la persona o un indirizzo.",
    "source_identifying": (
        "La scheda cita un account, un repository, un sito, un datore di lavoro, un nome o "
        "un recapito della persona, anche dopo la riscrittura."
    ),
    "gendered": "La scheda rivela il genere della persona anche dopo la riscrittura.",
    # True of every outage, the backlog's (asked again by the next run) and a
    # «Rigenera scheda» on the card's own CV (whose card stays) alike; a new CV's
    # previous card is retired meanwhile, and comes back with the next answer.
    "unavailable": "Claude non ha risposto: «Rigenera scheda» riprova.",
}


def failure_sentence(kind: Failure) -> str:
    """The sentence of ours that stands for a kind of failure, never the model's words."""
    return FAILURES[kind]


@dataclass(frozen=True)
class Composed:
    """What `CardWriter.compose` made of one person's inputs: the `card`, or the kind of
    `failure` when there is none (`no_text` has no model), and the tokens every call of
    it billed. `output_tokens` is without the thinking tokens, which are `thinking_tokens`.
    `fonti`, `conflicts` and `input_hashes` are what the card row stores beside the card
    (REB-839): the sources the model says supported each field, checked; the
    disagreements the code found; and the hash of every input the prompt was built from.
    `persona` (REB-855) is the named card the same answer held: kept with the card, and also
    when the guard refused the card, since the contacts are not the card's. The employers
    the model reported are in it, as `aziende`, and nowhere else."""

    card: Card | None
    failure: Failure | None
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    fonti: dict[str, list[str]] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)
    persona: CardPersonaRead | None = None


@dataclass(frozen=True)
class Prepared:
    """What `CardWriter.prepare` built for one person before any model is asked: the
    `request` (the prompt, with the answer shape), what `known` could name them, the
    `sources` and `facts` it read, the hash of every input (`hashes`) and of the CV
    (`cv_sha256`). A Message Batch keeps the two hashes beside the request, so its answer is
    applied only to a card whose inputs are still these."""

    request: LlmRequest
    known: KnownIdentifiers
    sources: SourceSet
    facts: HubFacts
    hashes: dict[str, str]
    cv_sha256: str


class StaleCard(NamedTuple):
    """A card that no longer is what the current prompt and inputs would write, and that
    waits for an admin's start: `prompt` a newer `PROMPT_VERSION`, `source` the hub's
    facts, the position or a source changed."""

    freelancer_id: UUID
    nome: str
    reason: Literal["prompt", "source"]
    generated_at: datetime | None


@dataclass(frozen=True)
class Regeneration:
    """A write a person started (REB-861), carried by the writer that does it so that each
    card it writes, or fails to, is signed in that talent's trail (`card_regenerated`).
    `bulk` and `start_action_id` tie a card to the `cards_regenerate` of its start.
    `batch_since` is when the Message Batch of a run was sent: its batch-priced answers count
    from then, wherever in the apply they were recorded (the rest of a card's cost is the
    calls made since its own claim began, `CardWriter.card_done`). `retries` says an
    `unavailable` card is left for another pass and is not yet the end of its story (the
    batch's apply)."""

    admin_id: UUID
    fonte: Fonte
    bulk: bool = False
    start_action_id: UUID | None = None
    batch_since: datetime | None = None
    retries: bool = False
