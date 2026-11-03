"""Where a card came from and whether it still matches (REB-892, split out of
`cards.py`): what supported each field and what the code found wrong (`provenance`), the
hash of every input the prompt was built from (`hash_inputs`), and why a stored card is
stale (`stale_reason`).
"""

import hashlib
from typing import Literal

from rebase_core.card_draft import CardDraft, Conflict, conflicts, dated_by, verify_fonti
from rebase_core.card_prompt import PROMPT_VERSION, source_keys
from rebase_core.hub_facts import HubFacts
from rebase_core.models import FreelancerCard
from rebase_core.sources import SourceSet
from rebase_core.team_schemas import Card


def provenance(
    draft: CardDraft, sources: SourceSet, facts: HubFacts
) -> tuple[Card, dict[str, list[str]], list[Conflict]]:
    """The card to keep, what supported it, and what the code found wrong with it.
    `fonti` is cut to the sources that exist for the person (`verify_fonti`). Years are
    set by dated professional periods only: when a source beside the CV and the hub was
    read and the sources named for `anni` include neither, the years are dropped, and
    that is recorded (`anni_scartata`). With no such source there is nothing but the CV
    and the hub to take them from. A hub that shows more real work than the card counts is
    recorded too (`conflicts`)."""
    fonti = verify_fonti(draft.fonti.as_dict(), set(source_keys(sources, facts)))
    card = draft.card
    found: list[Conflict] = []
    if card.anni is not None and sources.readable() and not dated_by(fonti):
        found.append(Conflict("anni_scartata", card.anni, None))
        card = card.model_copy(update={"anni": None})
        fonti["anni"] = []
    return card, fonti, [*found, *conflicts(card.anni, facts.completed_months)]


def sha(material: str) -> str:
    return hashlib.sha256(material.encode()).hexdigest()


def hash_inputs(posizione: str | None, sources: SourceSet, facts: HubFacts) -> dict[str, str]:
    """The hash of every input a card is written from, beside the CV's: `wizard` is the
    position the person gave (the rate and the work mode are not read by the prompt, and
    are shown from the row), `hub` the hub's facts without what moves by itself, and each
    readable source its own (`SourceSet.hashes`). `hub` is absent when the hub has nothing
    to say, so the first fact that appears is a change."""
    hashes = {"wizard": sha(posizione or ""), **sources.hashes()}
    if not facts.empty:
        hashes["hub"] = sha(facts.hash_material())
    return hashes


def stale_reason(
    row: FreelancerCard, cv_hash: str | None, hashes: dict[str, str]
) -> Literal["cv", "source", "prompt"] | None:
    """Why a stored card is no longer what the person's inputs would write, or `None`.
    `cv`: the CV is neither the card's nor the failed one (the hourly loop writes it
    again). `prompt`: the card was written by another prompt version (a card with none
    recorded is version 1). `source`: the hub's facts, the position or a source are not
    the ones it was written from; `hashes` is only read for a card of the current
    version. The last two wait for an admin's start. No card, nothing to be stale."""
    if row.card is None or cv_hash is None:
        return None
    if cv_hash not in (row.cv_sha256, row.error_cv_sha256):
        return "cv"
    if (row.prompt_version or 1) != PROMPT_VERSION:
        return "prompt"
    if (row.source_hashes or {}) != hashes:
        return "source"
    return None
